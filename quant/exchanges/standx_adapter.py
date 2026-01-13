import asyncio
import json
import logging
import os
import time
import uuid
import base64
import aiohttp
import pandas as pd
from typing import List, Tuple, Dict, Callable, Any
from .interfaces import ExchangeInterface
from .order_converter import normalize_order_to_ccxt, normalize_orders_list
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization
from eth_utils import to_checksum_address
import jwt
import requests
import hashlib

logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)


class StandXAdapter(ExchangeInterface):
    """
    StandX exchange adapter implementing the ExchangeInterface.
    Uses StandX REST and WebSocket APIs for perpetual futures trading.
    """

    # Market ID to StandX symbol mapping
    MARKET_ID_TO_SYMBOL = {
        0: "ETH-USD",  # Default
        1: "BTC-USD",
        2: "SOL-USD",
        # Add more mappings as needed
    }

    def __init__(
        self,
        market_id: int = 1,
        symbol: str = None,
        private_key: str = None,
        wallet_address: str = None,
        chain: str = "bsc",
        keystore_path: str = None,
        keystore_password: str = None
    ):
        self.market_id = market_id
        self.symbol = symbol or self.MARKET_ID_TO_SYMBOL.get(market_id, "BTC-USD")
        self.chain = chain

        # Handle private key loading with keystore support first
        self.private_key = self._load_private_key(
            private_key=private_key,
            keystore_path=keystore_path,
            keystore_password=keystore_password
        )

        # Set wallet address: use provided address, env variable, or extract from private key
        provided_wallet_address = wallet_address or os.getenv('STANDX_WALLET_ADDRESS', '')
        if provided_wallet_address:
            self.wallet_address = self._normalize_wallet_address(provided_wallet_address)
        elif self.private_key:
            # Extract wallet address from private key if not explicitly provided
            self.wallet_address = self._extract_wallet_address_from_private_key()
        else:
            self.wallet_address = ''

        # Generate Ed25519 key pair and requestId
        self.ed25519_private_key, self.ed25519_public_key, self.request_id = self._generate_ed25519_key_pair()
        self.request_sign_version = "v1"

        # Base URLs
        self.base_url = "https://perps.standx.com"
        self.api_url = f"{self.base_url}/api"
        self.auth_url = "https://api.standx.com/v1/offchain"

        # WebSocket URLs
        self.ws_market_url = "wss://perps.standx.com/ws-stream/v1"
        self.ws_order_url = "wss://perps.standx.com/ws-api/v1"

        # HTTP session
        self.session = None
        self.auth_token = None
        self.session_id = None

        # WebSocket clients
        self.ws_market_client = None
        self.ws_order_client = None
        self.ws_task = None
        self.ws_market_ready = asyncio.Event()
        self.ws_market_authed = False
        self.ws_order_ready = asyncio.Event()
        self.ws_order_authed = asyncio.Event()
        self.pending_order_futures: Dict[str, asyncio.Future] = {}

        self.callbacks: Dict[str, Callable] = {}
        self.ws_initialized = False

        # Initialize HTTP session
        self._initialize_http_session()

        logger.info(f"StandX Adapter initialized with symbol={self.symbol}, chain={self.chain}")
        logger.info(f"Generated Ed25519 key pair with requestId: {self.request_id}")

    def _generate_ed25519_key_pair(self) -> Tuple[bytes, bytes, str]:
        """Generate Ed25519 key pair and create requestId (base58 public key)."""
        try:
            private_key = ed25519.Ed25519PrivateKey.generate()
            public_key = private_key.public_key()

            public_key_bytes = public_key.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw
            )
            private_key_bytes = private_key.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption()
            )

            import base58
            request_id = base58.b58encode(public_key_bytes).decode("utf-8")

            return private_key_bytes, public_key_bytes, request_id
        except Exception as e:
            logger.exception("Failed to generate Ed25519 key pair (required for StandX auth)")
            raise

    def _initialize_http_session(self):
        """Initialize HTTP session with headers."""
        self.session = aiohttp.ClientSession(
            headers={
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
        )

    def _normalize_wallet_address(self, address: str) -> str:
        """Return checksummed wallet address if possible (SIWE expects canonical form)."""
        if not address:
            return ''
        try:
            return to_checksum_address(address)
        except Exception as exc:
            logger.warning(f"Wallet address not checksummed ({exc}); using raw address")
            return address

    def _load_private_key(self, private_key: str = None, keystore_path: str = None, keystore_password: str = None) -> str:
        """
        Load private key from various sources with priority:
        1. Direct private_key parameter
        2. Environment variable STANDX_PRIVATE_KEY
        3. Keystore file (with password prompt if needed)
        
        Args:
            private_key: Direct private key string
            keystore_path: Path to keystore file
            keystore_password: Password for keystore decryption
        
        Returns:
            Hex-encoded private key string
        """
        # Priority 1: Direct private key parameter
        if private_key:
            return private_key
        
        # Priority 2: Environment variable
        env_private_key = os.getenv('STANDX_PRIVATE_KEY', '')
        if env_private_key:
            return env_private_key
        
        # Priority 3: Keystore file
        if keystore_path or os.getenv('STANDX_KEYSTORE_PATH'):
            try:
                # Import the keystore loading function
                from eth_keystore import load_private_key_from_keystore
                
                # Use provided keystore path or environment variable
                final_keystore_path = keystore_path or os.getenv('STANDX_KEYSTORE_PATH')
                
                # Use provided password or prompt if needed
                final_password = keystore_password
                if not final_password:
                    # Try environment variable first
                    final_password = os.getenv('STANDX_KEYSTORE_PASSWORD')
                
                logger.info(f"Loading private key from keystore: {final_keystore_path}")
                loaded_private_key = load_private_key_from_keystore(
                    keystore_path=final_keystore_path,
                    password=final_password
                )
                
                logger.info("Successfully loaded private key from keystore")
                return loaded_private_key
                
            except ImportError:
                logger.error("eth_keystore module not found. Install required dependencies.")
                raise
            except Exception as e:
                logger.error(f"Failed to load private key from keystore: {e}")
                raise
        
        # Fallback: Empty string if no source available
        logger.warning("No private key source available (private_key, STANDX_PRIVATE_KEY, or keystore)")
        return ''

    def _extract_wallet_address_from_private_key(self) -> str:
        """
        Extract wallet address from the loaded private key.
        
        Returns:
            Checksummed wallet address string
        """
        if not self.private_key:
            return ''
        
        try:
            private_key_hex = self.private_key[2:] if self.private_key.startswith('0x') else self.private_key
            private_key_bytes = bytes.fromhex(private_key_hex)
            
            from eth_account import Account
            account = Account.from_key(private_key_bytes)
            wallet_address = account.address
            
            return self._normalize_wallet_address(wallet_address)
            
        except ImportError:
            logger.warning("eth_account not available, cannot extract wallet address from private key")
            return ''
        except Exception as e:
            logger.warning(f"Failed to extract wallet address from private key: {e}")
            return ''

    def _get_symbol_for_market_id(self, market_id: int) -> str:
        """Get StandX symbol for given market_id."""
        return self.MARKET_ID_TO_SYMBOL.get(market_id, self.symbol)

    async def _ensure_authenticated(self) -> bool:
        """Ensure we have a valid authentication token."""
        if self.auth_token:
            return True
        
        # Create authentication token
        success, error = await self.create_auth_token()
        if not success:
            logger.error(f"Authentication failed: {error}")
            return False
        
        return True

    async def _prepare_signin_request(self) -> Tuple[str, str]:
        """Request signature data from StandX server (prepare-signin)."""
        try:
            url = f"{self.auth_url}/prepare-signin?chain={self.chain}"
            payload = {
                "address": self.wallet_address,
                "requestId": self.request_id
            }

            logger.debug(f"Sending prepare-signin request: {payload}")
            async with self.session.post(url, json=payload) as response:
                text = await response.text()
                if response.status != 200:
                    error_msg = f"HTTP {response.status}: {text}"
                    logger.error(f"prepare-signin request failed: {error_msg}")
                    return '', error_msg

                data = await response.json()
                if data.get("success"):
                    signed_data = data.get("signedData", "")
                    logger.info("Successfully obtained signedData from StandX")
                    return signed_data, None

                error_msg = data.get("message", "Unknown error")
                logger.error(f"prepare-signin failed: {error_msg}")
                return '', error_msg

        except Exception as e:
            logger.error(f"prepare-signin request error: {e}")
            return '', str(e)

    def _sign_message_with_wallet(self, message: str) -> str:
        """Sign message with wallet private key using Ethereum personal_sign (matches docs)."""
        try:
            if not self.private_key:
                logger.error("Wallet private key not available")
                return ''

            private_key_hex = self.private_key[2:] if self.private_key.startswith('0x') else self.private_key
            private_key_bytes = bytes.fromhex(private_key_hex)

            from eth_account import Account
            from eth_account.messages import encode_defunct

            account = Account.from_key(private_key_bytes)
            message_hash = encode_defunct(text=message)
            signed_message = account.sign_message(message_hash)

            signature_hex = signed_message.signature.hex()
            if not signature_hex.startswith("0x"):
                signature_hex = f"0x{signature_hex}"
            logger.debug(
                "Successfully signed message (personal_sign), signature length=%s",
                len(signature_hex)
            )
            return signature_hex

        except ImportError:
            logger.error("eth_account library not available. Install with: pip install eth-account")
            return ''
        except Exception as e:
            logger.error(f"Failed to sign message with wallet: {e}")
            return ''

    def _parse_jwt_payload(self, jwt_token: str) -> Dict[str, Any]:
        """Parse JWT token and return payload without verification."""
        try:
            # Split JWT token
            parts = jwt_token.split('.')
            if len(parts) != 3:
                logger.error("Invalid JWT token format")
                return {}
            
            # Decode payload (base64url)
            payload_b64 = parts[1]
            # Add padding if needed
            payload_b64 += '=' * (4 - len(payload_b64) % 4)
            payload_bytes = base64.urlsafe_b64decode(payload_b64)
            payload = json.loads(payload_bytes.decode('utf-8'))
            
            logger.debug(f"Parsed JWT payload: {payload}")
            return payload
            
        except Exception as e:
            logger.error(f"Failed to parse JWT payload: {e}")
            return {}

    async def _login_request(self, signature: str, signed_data: str, expires_seconds: int = 604800) -> Tuple[str, str]:
        """Submit signature to StandX and get JWT access token."""
        try:
            url = f"{self.auth_url}/login?chain={self.chain}"

            payload = {
                "signature": signature if signature.startswith("0x") else f"0x{signature}",
                "signedData": signed_data,
                "expiresSeconds": expires_seconds
            }

            logger.debug("Login request payload prepared (len signature=%s, len signedData=%s, expiresSeconds=%s)",
                         len(payload["signature"]), len(signed_data), expires_seconds)
            logger.debug(f"Sending login request to StandX: {url}")

            async with self.session.post(url, json=payload) as response:
                text = await response.text()
                logger.debug(f"Login response status: {response.status}")
                logger.debug(f"Login response body: {text}")

                if response.status != 200:
                    error_msg = f"HTTP {response.status}: {text}"
                    logger.error(f"Login request failed: {error_msg}")
                    return '', error_msg

                data = await response.json()
                if 'token' in data:
                    token = data.get('token', '')
                    address = data.get('address', '')
                    logger.info(f"Successfully obtained JWT token for address: {address}")
                    logger.info(f"Token expires in {expires_seconds} seconds")
                    return token, None

                error_msg = data.get('message', 'Unknown error')
                logger.error(f"Login failed: {error_msg}")
                return '', error_msg

        except Exception as e:
            logger.error(f"Login request error: {e}")
            return '', str(e)

    async def _make_authenticated_request(self, method: str, endpoint: str, **kwargs) -> dict:
         """Make an authenticated HTTP request."""
         if not await self._ensure_authenticated():
             return {"code": -1, "message": "Authentication failed"}
         
         url = f"{self.api_url}{endpoint}"
         headers = {
             'Authorization': f'Bearer {self.auth_token}'
         }
         
         # Add body signature headers if needed
         if 'body_signature' in kwargs and kwargs['body_signature']:
             headers.update(kwargs['body_signature'])
             del kwargs['body_signature']
         
         try:
             async with self.session.request(method, url, headers=headers, **kwargs) as response:
                 if response.status == 204:  # No content
                     return {}

                 async def _safe_json():
                     try:
                         return await response.json()
                     except Exception:
                         try:
                             text = await response.text()
                             return {"text": text}
                         except Exception:
                             return {"text": ""}

                 if response.status != 200:
                     error_data = await _safe_json()
                     logger.error(f"Request failed with status {response.status}: {error_data}")
                     return {"code": response.status, "message": str(error_data)}
                 else:
                     data = await _safe_json()
                     return data
         except Exception as e:
             logger.error(f"Request to {url} failed: {e}")
             self.initialize_client()
             return {"code": -1, "message": str(e)}

    async def _generate_body_signature(self, payload: dict) -> Dict[str, str]:
        """Generate body signature headers per StandX docs."""
        try:
            import base64

            request_id = str(uuid.uuid4())
            timestamp = int(time.time() * 1000)
            # 注意：签名必须与实际发送的 HTTP body 完全一致。aiohttp 在使用
            # `json=` 参数时默认调用 `json.dumps(payload)`，其分隔符为
            # (',', ': ') 并且 `ensure_ascii=True`。这里使用同样的序列化方式，
            # 确保签名串与请求体字符串逐字节匹配，避免出现 "invalid body signature"。
            payload_str = json.dumps(payload)
            version = self.request_sign_version

            if not self.ed25519_private_key or len(self.ed25519_private_key) == 0:
                raise ValueError("Ed25519 private key unavailable for body signature")

            private_key = ed25519.Ed25519PrivateKey.from_private_bytes(self.ed25519_private_key)
            message = f"{version},{request_id},{timestamp},{payload_str}"
            signature = private_key.sign(message.encode('utf-8'))
            signature_b64 = base64.b64encode(signature).decode('utf-8')

            logger.debug("Successfully generated body signature")
            return {
                "x-request-sign-version": version,
                "x-request-id": request_id,
                "x-request-timestamp": str(timestamp),
                "x-request-signature": signature_b64
            }

        except Exception as e:
            logger.error(f"Failed to generate body signature: {e}")
            raise

    async def place_multi_orders(self, orders: List[Tuple[bool, float, float]]) -> Tuple[bool, List[str]]:
        """
        Place multiple limit orders.
        orders: [(is_ask, price, amount), ...]
        Returns: (success, order_ids)
        """
        if not orders:
            logger.warning("place_multi_orders: No orders provided")
            return True, []
        
        try:
            order_ids = []
            for i, (is_ask, price, amount) in enumerate(orders):
                logger.debug(f"place_multi_orders: Placing order {i+1}/{len(orders)}: is_ask={is_ask}, price={price}, amount={amount}")
                success, order_id = await self.place_single_order(is_ask, price, amount)
                if not success:
                    logger.error(f"place_multi_orders: Failed to place order {i+1}: is_ask={is_ask}, price={price}, amount={amount}")
                    # Cancel any previously placed orders
                    if order_ids:
                        logger.info(f"place_multi_orders: Cancelling {len(order_ids)} previously placed orders due to failure")
                        await self.cancel_grid_orders(order_ids)
                    return False, []
                order_ids.append(order_id)
            
            logger.info(f"place_multi_orders: Successfully placed {len(order_ids)} orders")
            return True, order_ids
        except Exception as e:
            logger.error(f"place_multi_orders error: {e}")
            return False, []

    async def place_single_order(self, is_ask: bool, price: float, amount: float, client_order_id: str = None) -> Tuple[bool, str]:
        """
        Place single limit order via StandX WS order:new。
        Returns: (success, order_id)
        """
        try:
            if client_order_id is None:
                client_order_id = f"standx_{int(time.time() * 1000) % 1000000}"

            payload = {
                "symbol": self.symbol,
                "side": "sell" if is_ask else "buy",
                "order_type": "limit",
                "qty": str(amount),
                "price": str(price),
                "time_in_force": "alo",
                "reduce_only": False,
                "cl_ord_id": client_order_id
            }

            body_signature = await self._generate_body_signature(payload)
            response = await self._send_order_ws_request(
                "order:new",
                payload,
                header=body_signature
            )

            if isinstance(response, dict) and response.get("code") == 0:
                logger.debug(f"Order placed successfully via WS: {client_order_id}")
                return True, client_order_id
            else:
                logger.error(f"Failed to create order via WS: {response}")
                return False, ''
        except Exception as e:
            logger.error(f"place_single_order error: {e}")
            return False, ''

    async def place_single_market_order(self, is_ask: bool, price: float, amount: float) -> Tuple[bool, str]:
        """
        Place single market order via StandX WS order:new。
        """
        try:
            client_order_id = f"standx_mkt_{int(time.time() * 1000) % 1000000}"

            payload = {
                "symbol": self.symbol,
                "side": "sell" if is_ask else "buy",
                "order_type": "market",
                "qty": str(amount),
                "time_in_force": "ioc",
                "reduce_only": False,
                "cl_ord_id": client_order_id
            }

            body_signature = await self._generate_body_signature(payload)
            response = await self._send_order_ws_request(
                "order:new",
                payload,
                header=body_signature
            )

            if isinstance(response, dict) and response.get("code") == 0:
                logger.info(f"Market order placed successfully via WS: {client_order_id}")
                return True, client_order_id
            else:
                logger.error(f"Failed to create market order via WS: {response}")
                return False, ''
        except Exception as e:
            logger.error(f"place_single_market_order error: {e}")
            return False, ''

    async def cancel_grid_orders(self, order_ids: List[str]) -> bool:
        """
        Cancel orders via StandX WS order:cancel（逐单发送，兼容原批量接口）。
        """
        if not order_ids:
            logger.warning("cancel_grid_orders: No order_ids provided")
            return True

        try:
            for cl_ord_id in order_ids:
                payload = {"cl_ord_id": cl_ord_id}
                body_signature = await self._generate_body_signature(payload)
                response = await self._send_order_ws_request(
                    "order:cancel",
                    payload,
                    header=body_signature
                )
                if not (isinstance(response, dict) and response.get("code") == 0):
                    logger.error(f"Failed to cancel order {cl_ord_id} via WS: {response}")
                    return False
            logger.debug(f"Successfully cancelled {len(order_ids)} orders via WS")
            return True
        except Exception as e:
            logger.error(f"cancel_grid_orders error: {e}")
            return False

    async def modify_grid_order(self, order_id: str, new_price: float, new_amount: float) -> bool:
        """
        Modify order - StandX supports order modification.
        """
        try:
            # To modify an order in StandX, we need to cancel and create a new one
            # First cancel the existing order
            cancel_success = await self.cancel_grid_orders([order_id])
            if not cancel_success:
                logger.error(f"Failed to cancel order {order_id} for modification")
                return False
            
            # Then create a new order with updated parameters
            # We need to determine if it was a buy or sell order
            # For now, we'll assume we can get this from the order ID or other context
            # In a real implementation, we would query the order first
            
            # Placeholder: create new order (we don't know the original side)
            # This is a limitation - we would need to track order sides separately
            logger.warning("modify_grid_order: Cannot determine original order side. Implementation needs enhancement.")
            return False
            
            # In a complete implementation:
            # success, new_order_id = await self.place_single_order(original_side, new_price, new_amount)
            # return success
            
        except Exception as e:
            logger.error(f"modify_grid_order error: {e}")
            return False

    async def get_orders(self) -> List[dict]:
        """
        Get current orders.
        """
        try:
            response = await self._make_authenticated_request(
                "GET",
                "/query_open_orders",
                params={"symbol": self.symbol}
            )
            
            # Handle different response formats
            if isinstance(response, list):
                # Direct array response format
                return normalize_orders_list(response)
            elif isinstance(response, dict) and "result" in response:
                # Standard response with result field
                orders = response["result"]
                return normalize_orders_list(orders)
            elif isinstance(response, dict) and response.get("code") == 0 and "result" in response:
                orders = response["result"]
                return normalize_orders_list(orders)
            else:
                logger.error(f"get_orders error: {response}")
                return []
        except Exception as e:
            logger.error(f"get_orders error: {e}")
            return []

    async def get_trades(self, limit: int = 1) -> List[dict]:
        """
        Get recent trades.
        """
        try:
            response = await self._make_authenticated_request(
                "GET",
                "/query_trades",
                params={
                    "symbol": self.symbol,
                    "limit": limit
                }
            )
            
            # Handle different response formats
            if isinstance(response, list):
                # Direct array response format
                return response
            elif isinstance(response, dict) and "result" in response:
                # Standard response with result field
                return response["result"]
            elif isinstance(response, dict) and response.get("code") == 0 and "result" in response:
                return response["result"]
            else:
                logger.error(f"get_trades error: {response}")
                return []
        except Exception as e:
            logger.error(f"get_trades error: {e}")
            return []

    async def get_positions(self) -> Dict[str, dict]:
        """
        Get positions.
        """
        try:
            response = await self._make_authenticated_request(
                "GET",
                "/query_positions",
                params={"symbol": self.symbol}
            )
            
            # Handle different response formats
            if isinstance(response, list):
                # Direct array response format
                result = {}
                for pos in response:
                    if isinstance(pos, dict) and 'symbol' in pos:
                        result[pos['symbol']] = pos
                return result
            elif isinstance(response, dict) and "result" in response:
                # Standard response with result field
                positions = response["result"]
                result = {}
                for pos in positions:
                    if 'symbol' in pos:
                        result[pos['symbol']] = pos
                return result
            elif isinstance(response, dict) and response.get("code") == 0 and "result" in response:
                positions = response["result"]
                result = {}
                for pos in positions:
                    if 'symbol' in pos:
                        result[pos['symbol']] = pos
                return result
            else:
                logger.error(f"get_positions error: {response}")
                return {}
        except Exception as e:
            logger.error(f"get_positions error: {e}")
            return {}

    async def get_account(self) -> dict:
        """
        Get account info including collateral.
        """
        try:
            response = await self._make_authenticated_request(
                "GET",
                "/query_balance"
            )
            
            # Handle different response formats
            if isinstance(response, dict):
                return response
            elif isinstance(response, list) and len(response) > 0:
                return response[0] if len(response) == 1 else {"data": response}
            else:
                logger.error(f"get_account: Unexpected response format: {response}")
                return {}
        except Exception as e:
            logger.error(f"get_account error: {e}")
            return {}

    @staticmethod
    def _resolution_to_seconds(resolution: str) -> int:
        mapping = {
            "1m": 60,
            "3m": 180,
            "5m": 300,
            "15m": 900,
            "30m": 1800,
            "1h": 3600,
            "4h": 14400,
            "1d": 86400,
        }
        if resolution not in mapping:
            raise ValueError(f"Unsupported resolution: {resolution}")
        return mapping[resolution]
    
    async def candle_stick(self, market_id: int, resolution: str, count_back: int = 100) -> pd.DataFrame:
        """
        Get candlestick data.
        """
        # try:
        #     # Get symbol for the market_id
        #     symbol = self._get_symbol_for_market_id(market_id)
            
        #     # Map resolution to StandX format
        #     resolution_map = {
        #         '1m': '1',
        #         '5m': '5',
        #         '15m': '15',
        #         '1h': '60',
        #         '1d': '1D'
        #     }
        #     standx_resolution = resolution_map.get(resolution, '1')
            
        #     # Get current time and calculate start time based on resolution
        #     end_time = int(time.time())
            
        #     # Convert resolution to minutes for calculation
        #     resolution_minutes_map = {
        #         '1': 1,
        #         '5': 5,
        #         '15': 15,
        #         '60': 60,  # 1 hour
        #         '1D': 1440  # 1 day = 24 * 60 minutes
        #     }
        #     resolution_minutes = resolution_minutes_map.get(standx_resolution, 1)
            
        #     # Calculate start time: go back count_back candles, each candle is resolution_minutes long
        #     start_time = end_time - (resolution_minutes * 60 * count_back)
            
        #     response = await self._make_authenticated_request(
        #         "GET",
        #         "/kline/history",
        #         params={
        #             "symbol": symbol,
        #             "resolution": standx_resolution,
        #             "from": start_time,
        #             "to": end_time,
        #             "countBack": count_back
        #         }
        #     )
        #     logger.info(f"请求参数：{start_time}, {end_time}, {count_back}")
        #     logger.info(f"candle_stick response: {response}")
            
        #     if response.get("s") == "ok":
        #         # Convert to DataFrame
        #         candle_data = []
        #         for i in range(len(response["t"])):
        #             candle_data.append({
        #                 'time': pd.to_datetime(response["t"][i], unit='s'),
        #                 'open': response["o"][i],
        #                 'high': response["h"][i],
        #                 'low': response["l"][i],
        #                 'close': response["c"][i],
        #                 'volume': response["v"][i]
        #             })
                
        #         df = pd.DataFrame(candle_data)
        #         if not df.empty:
        #             df.set_index('time', inplace=True)
        #             # Convert numeric columns to proper types
        #             numeric_cols = ['open', 'high', 'low', 'close', 'volume']
        #             for col in numeric_cols:
        #                 if col in df.columns:
        #                     df[col] = pd.to_numeric(df[col], errors='coerce')
                
        #         logger.info(f"candle_stick: Retrieved {len(candle_data)} candles for {symbol}")
        #         return df
        #     else:
        #         logger.warning(f"candle_stick: No candle data available for {symbol}")
        #         return pd.DataFrame()
        resolution_seconds = self._resolution_to_seconds(resolution)
        end_time = int(time.time())
        start_time = end_time - resolution_seconds * count_back
        
        try:
            from common.config import BASE_URL
            symbol = self._get_symbol_for_market_id(market_id)
            url = f"{BASE_URL}/api/v1/candles"
            params = {
                "market_id": market_id,
                "resolution": resolution,
                "start_timestamp": start_time,
                "end_timestamp": end_time,
                "count_back": count_back,
            }
            headers = {"accept": "application/json"}
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers) as resp:
                    data = await resp.json()
                    if data.get("code") != 200:
                        logger.error(f"获取K线数据失败: {data.get('message', 'Unknown error')}")
                        return None
                    candlesticks = data["c"]
                    candle_data = []
                    for candle in candlesticks:
                        candle_data.append(
                            {
                                "time": candle["t"],
                                "open": candle["o"],
                                "high": candle["h"],
                                "low": candle["l"],
                                "close": candle["c"],
                                "volume": candle["v"],
                            }
                        )
                    df = pd.DataFrame(candle_data)
                    df["time"] = pd.to_datetime(df["time"], unit="ms")
                    return df
        except Exception as e:
            logger.error(f"candle_stick error for market_id {market_id} ({symbol}): {e}")
            return pd.DataFrame()

    async def modify_order(self, order_id: int, new_price: float, new_amount: float) -> bool:
        """
        Modify order - StandX supports order modification via cancel/create.
        """
        # Similar to modify_grid_order but with order_id as int
        return await self.modify_grid_order(str(order_id), new_price, new_amount)

    async def get_orders_by_rest(self) -> List[dict]:
        """
        Get orders via REST API.
        """
        return await self.get_orders()

    async def get_trades_by_rest(self, ask_filter: int, limit: int) -> List[dict]:
        """
        Get trades via REST API.
        ask_filter: 0=all trades, 1=buy trades, 2=sell trades
        """
        try:
            # StandX doesn't directly support ask_filter in the API
            # We'll fetch all trades and filter client-side
            all_trades = await self.get_trades(limit)
            
            if ask_filter == 0:
                return all_trades
            elif ask_filter == 1:  # Buy trades
                return [trade for trade in all_trades if trade.get('side') == 'buy']
            elif ask_filter == 2:  # Sell trades
                return [trade for trade in all_trades if trade.get('side') == 'sell']
            else:
                return all_trades
        except Exception as e:
            logger.error(f"get_trades_by_rest error: {e}")
            return []

    async def subscribe(self, callbacks: Dict[str, Callable[[str, Any], None]], proxy: str = None) -> None:
        """
        Subscribe to events.
        callbacks: {
            'market_stats': func(market_id, stats),
            'orders': func(account_id, orders),
            'positions': func(account_id, positions)
        }
        proxy: Proxy URL, e.g., "http://127.0.0.1:7890"
        """
        self.callbacks = callbacks
        self.proxy = proxy
        
        # Initialize WebSocket if not done
        if not self.ws_initialized:
            await self._initialize_ws()
        
        # Subscribe to market stats
        if 'market_stats' in callbacks:
            await self._subscribe_market_stats()
        
        # Subscribe to orders
        if 'orders' in callbacks:
            await self._subscribe_orders()
        
        # Subscribe to positions
        if 'positions' in callbacks:
            await self._subscribe_positions()
        
        logger.info(f"StandX subscribe: Registered callbacks for {list(callbacks.keys())}")

    async def _initialize_ws(self):
        """Initialize WebSocket clients."""
        try:
            # Initialize market data WebSocket
            await self._initialize_market_ws()
            
            # Initialize order response WebSocket
            await self._initialize_order_ws()
            
            self.ws_initialized = True
            logger.info("StandX WebSocket clients initialized")
        except Exception as e:
            logger.error(f"Failed to initialize WebSocket clients: {e}")

    async def _initialize_market_ws(self):
        """Initialize market data WebSocket."""
        try:
            # Connect to market data WebSocket
            async def market_ws_handler():
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(
                        self.ws_market_url,
                        heartbeat=30,
                        proxy=self.proxy if getattr(self, "proxy", None) else None
                    ) as ws:
                        self.ws_market_client = ws
                        self.ws_market_ready.set()
                        self.ws_market_authed = False

                        # Authenticate if needed
                        if self.auth_token:
                            auth_msg = {
                                "auth": {
                                    "token": self.auth_token,
                                    "streams": [
                                        {"channel": "order"},
                                        {"channel": "position"},
                                        {"channel": "balance"}
                                    ]
                                }
                            }
                            await ws.send_json(auth_msg)
                             
                            # Wait for auth response
                            auth_response = await ws.receive()
                            try:
                                resp_data = json.loads(auth_response.data)
                                code = resp_data.get("data", {}).get("code")
                            except Exception:
                                resp_data = auth_response.data
                                code = None
                            if code in (0, 200):
                                self.ws_market_authed = True
                                logger.info("Market WS authentication successful")
                            else:
                                logger.error(f"Market WS auth failed: {resp_data}")
                                return
                         
                        # Message handling loop
                        async for msg in ws:
                            logger.debug(f"Market WS received message: {msg}")
                            # StandX 服务端每 10s 发送 Ping；客户端需及时回应 Pong，避免 5 分钟无 Pong 被断开
                            if await self._handle_ws_ping_pong(ws, msg, ws_name="market"):
                                continue
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                await self._handle_ws_message(data)
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                logger.error(f"Market WS error: {ws.exception()}")
                                break

                    # 连接退出时重置标记
                    self.ws_market_authed = False
                    self.ws_market_client = None
                    self.ws_market_ready.clear()
             
            self.ws_task = asyncio.create_task(market_ws_handler())
        except Exception as e:
            logger.error(f"Failed to initialize market WebSocket: {e}")

    async def _wait_for_market_ws(self, timeout: float = 10.0) -> bool:
        """等待市场 WebSocket 就绪。"""
        try:
            await asyncio.wait_for(self.ws_market_ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            logger.error("Market WS 未在超时时间内准备就绪")
            return False

    async def _auth_market_ws_if_needed(self) -> bool:
        """在需要时向市场 WS 发送认证消息。"""
        if self.ws_market_authed:
            return True
        if not await self._ensure_authenticated():
            return False
        if not self.ws_market_client:
            return False
        try:
            auth_msg = {
                "auth": {
                    "token": self.auth_token
                }
            }
            await self.ws_market_client.send_json(auth_msg)
            # 不等待返回，成功与否由后续消息决定
            self.ws_market_authed = True
            return True
        except Exception as e:
            logger.error(f"向 Market WS 发送认证消息失败: {e}")
            return False

    async def _wait_for_order_ws(self, timeout: float = 10.0) -> bool:
        """等待订单 WebSocket 就绪。"""
        try:
            await asyncio.wait_for(self.ws_order_ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            logger.error("Order WS 未在超时时间内准备就绪")
            return False

    async def _ensure_order_ws(self):
        """确保订单 WS 已连接并完成认证。"""
        if self.ws_order_client and not self.ws_order_client.closed:
            if not self.ws_order_authed.is_set() and self.auth_token:
                await asyncio.wait_for(self.ws_order_authed.wait(), timeout=10)
            return

        await self._initialize_order_ws()
        await self._wait_for_order_ws()
        await asyncio.wait_for(self.ws_order_authed.wait(), timeout=10)

    async def _initialize_order_ws(self):
        """Initialize order response WebSocket."""
        try:
            import uuid
            self.session_id = str(uuid.uuid4())

            async def order_ws_handler():
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(
                        self.ws_order_url,
                        heartbeat=30
                    ) as ws:
                        self.ws_order_client = ws
                        self.ws_order_ready.set()
                        self.ws_order_authed.clear()

                        # 确保有 token
                        if not await self._ensure_authenticated():
                            logger.error("Order WS 无法获取认证 token")
                            return

                        # Authenticate
                        auth_msg = {
                            "session_id": self.session_id,
                            "request_id": str(uuid.uuid4()),
                            "method": "auth:login",
                            "params": json.dumps({"token": self.auth_token})
                        }
                        await ws.send_json(auth_msg)

                        # Message handling loop
                        async for msg in ws:
                            if await self._handle_ws_ping_pong(ws, msg, ws_name="order"):
                                continue
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                # 认证成功响应 code=0/200
                                if data.get("method") == "auth:login" or data.get("request_id") == auth_msg["request_id"]:
                                    code = data.get("code")
                                    if code is None:
                                        code = data.get("data", {}).get("code")
                                    if code in (0, 200):
                                        self.ws_order_authed.set()
                                        logger.info("Order WS authentication successful")
                                    else:
                                        logger.error(f"Order WS authentication failed: {data}")
                                await self._handle_order_ws_message(data)
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                logger.error(f"Order WS error: {ws.exception()}")
                                break

                # 连接退出时重置标记
                self.ws_order_client = None
                self.ws_order_ready.clear()
                self.ws_order_authed.clear()

            # Start order WebSocket in background
            asyncio.create_task(order_ws_handler())
        except Exception as e:
            logger.error(f"Failed to initialize order WebSocket: {e}")

    async def _handle_ws_ping_pong(self, ws, msg, ws_name: str = "market") -> bool:
        """
        处理 WS Ping/Pong，返回 True 表示消息已消费无需继续处理。
        StandX 文档要求：服务端每 10s 发送 Ping，若 5 分钟内未收到 Pong 将断开连接。
        """
        try:
            if msg.type == aiohttp.WSMsgType.PING:
                await ws.pong()
                logger.debug(f"{ws_name} WS: 收到 Ping，已发送 Pong")
                return True
            if msg.type == aiohttp.WSMsgType.PONG:
                logger.debug(f"{ws_name} WS: 收到 Pong")
                return True
        except Exception as e:
            logger.warning(f"{ws_name} WS: 处理 Ping/Pong 失败: {e}")
            return True
        return False

    async def _subscribe_market_stats(self):
        """Subscribe to market statistics (price channel)."""
        if not await self._wait_for_market_ws():
            return
        try:
            subscribe_msg = {
                "subscribe": {
                    "channel": "price",
                    "symbol": self.symbol
                }
            }
            await self.ws_market_client.send_json(subscribe_msg)
            logger.info(f"Subscribed to market stats for {self.symbol}")
        except Exception as e:
            logger.error(f"Failed to subscribe to market stats: {e}")

    async def _subscribe_orders(self):
        """Subscribe to authenticated order updates."""
        if not await self._wait_for_market_ws():
            return
        if not await self._auth_market_ws_if_needed():
            logger.error("Market WS 认证失败，无法订阅订单")
            return
        try:
            subscribe_msg = {
                "subscribe": {
                    "channel": "order"
                }
            }
            await self.ws_market_client.send_json(subscribe_msg)
            logger.info("Subscribed to order updates")
        except Exception as e:
            logger.error(f"Failed to subscribe to orders: {e}")

    async def _subscribe_positions(self):
        """Subscribe to authenticated position updates."""
        if not await self._wait_for_market_ws():
            return
        if not await self._auth_market_ws_if_needed():
            logger.error("Market WS 认证失败，无法订阅持仓")
            return
        try:
            subscribe_msg = {
                "subscribe": {
                    "channel": "position"
                }
            }
            await self.ws_market_client.send_json(subscribe_msg)
            logger.info("Subscribed to position updates")
        except Exception as e:
            logger.error(f"Failed to subscribe to positions: {e}")

    async def _handle_ws_message(self, message: dict):
        """Handle WebSocket messages."""
        channel = message.get("channel")
        data = message.get("data", {})
        
        if channel == "price" and "market_stats" in self.callbacks:
            # Convert market data to stats format
            stats = {
                "symbol": data.get("symbol"),
                "last_price": data.get("last_price"),
                "mark_price": data.get("mark_price"),
                "index_price": data.get("index_price"),
                "spread": data.get("spread"),
                "timestamp": data.get("time")
            }
            
            if asyncio.iscoroutinefunction(self.callbacks["market_stats"]):
                await self.callbacks["market_stats"](str(self.market_id), stats)
            else:
                self.callbacks["market_stats"](str(self.market_id), stats)
        
        elif channel == "order" and "orders" in self.callbacks:
            # Handle order updates
            orders_data = data if isinstance(data, list) else [data]
            normalized_orders = normalize_orders_list(orders_data)
            
            if asyncio.iscoroutinefunction(self.callbacks["orders"]):
                await self.callbacks["orders"](self.wallet_address, normalized_orders)
            else:
                self.callbacks["orders"](self.wallet_address, normalized_orders)
        
        elif channel == "position" and "positions" in self.callbacks:
            # Handle position updates
            positions_data = data if isinstance(data, list) else [data]
            positions_dict = {}
            
            for pos in positions_data:
                if 'symbol' in pos:
                    positions_dict[pos['symbol']] = pos
            
            if asyncio.iscoroutinefunction(self.callbacks["positions"]):
                await self.callbacks["positions"](self.wallet_address, positions_dict)
            else:
                self.callbacks["positions"](self.wallet_address, positions_dict)

    async def _send_order_ws_request(self, method: str, params: dict, header: Dict[str, str] = None, timeout: float = 10.0) -> dict:
        """通过订单 WS 发送请求并等待响应。"""
        await self._ensure_order_ws()
        if not self.ws_order_client:
            return {"code": -1, "message": "order ws unavailable"}

        request_id = str(uuid.uuid4())
        payload = {
            "session_id": self.session_id,
            "request_id": request_id,
            "method": method,
            "params": json.dumps(params)
        }
        if header:
            payload["header"] = header

        future = asyncio.get_event_loop().create_future()
        self.pending_order_futures[request_id] = future

        try:
            await self.ws_order_client.send_json(payload)
            response = await asyncio.wait_for(future, timeout=timeout)
            return response
        except asyncio.TimeoutError:
            logger.error(f"Order WS request timeout: {method} {request_id}")
            return {"code": 408, "message": "timeout", "request_id": request_id}
        except Exception as e:
            logger.error(f"Order WS request error: {e}")
            return {"code": -1, "message": str(e), "request_id": request_id}
        finally:
            self.pending_order_futures.pop(request_id, None)

    async def _handle_order_ws_message(self, message: dict):
        """Handle order response WebSocket messages."""
        request_id = message.get("request_id")

        # fulfill pending future if present
        pending = self.pending_order_futures.get(request_id)
        if pending and not pending.done():
            pending.set_result(message)

        code = message.get("code")
        if code == 0:
            logger.debug(f"Order response success for request {request_id}")
        else:
            logger.warning(f"Order response error for request {request_id}: {message.get('message')}")

    async def close(self):
            """
            Close connections."""
            try:
                # Close WebSocket connections
                if self.ws_task and not self.ws_task.done():
                    self.ws_task.cancel()
                    try:
                        await self.ws_task
                    except asyncio.CancelledError:
                        pass
                
                # Reset WebSocket state flags
                self.ws_initialized = False
                self.ws_market_authed = False
                self.ws_market_ready.clear()
                self.ws_market_client = None
                if self.ws_order_client:
                    await self.ws_order_client.close()
                self.ws_order_client = None
                self.ws_order_ready.clear()
                self.ws_order_authed.clear()
                self.pending_order_futures.clear()
                self.ws_task = None
                
                # Close HTTP session
                if self.session:
                    await self.session.close()
                     
                logger.info("StandX connections closed")
            except Exception as e:
                logger.error(f"Error closing connections: {e}")

    async def initialize_client(self) -> None:
        """
        Initialize the exchange client."""
        try:
            # Test authentication
            await self.create_auth_token()
            logger.info("StandX client initialized")
        except Exception as e:
            logger.error(f"Failed to initialize client: {e}")

    async def create_auth_token(self) -> Tuple[str, str]:
        """
        Create authentication token using StandX wallet signature flow.
        Returns: (auth_token, error)
        """
        try:
            # If we already have a valid token, return it
            if self.auth_token:
                return self.auth_token, None
            
            # Step 1: Request signature data from StandX server
            signed_data, error = await self._prepare_signin_request()
            if error:
                return "", error
            
            # Step 2: Parse JWT to get the message to sign
            payload = self._parse_jwt_payload(signed_data)
            if not payload:
                return "", "Failed to parse JWT payload"
            
            # Get the message from payload
            message = payload.get('message', '')
            if not message:
                return "", "No message found in JWT payload"
            
            # Step 3: Sign the message with wallet private key
            signature = self._sign_message_with_wallet(message)
            if not signature:
                return "", "Failed to sign message with wallet"
            
            # Step 4: Submit signature to get JWT token
            token, error = await self._login_request(signature, signed_data)
            if error:
                return "", error
            
            # Store the token
            self.auth_token = token
            logger.info("Successfully created authentication token")
            
            return token, None
            
        except Exception as e:
            logger.error(f"create_auth_token error: {e}")
            return "", str(e)

    async def get_account_info(self) -> dict:
        """
        Get detailed account information including positions."""
        try:
            account_data = await self.get_account()
            positions_data = await self.get_positions()
            
            # Combine account and positions data
            account_data["positions"] = positions_data
            return account_data
        except Exception as e:
            logger.error(f"get_account_info error: {e}")
            return {}


async def test_standx_adapter():
    """Test function for StandX adapter."""
    # Initialize adapter
    adapter = StandXAdapter(
        market_id=0,
        wallet_address=os.getenv('STANDX_WALLET_ADDRESS', ''),
        private_key=os.getenv('STANDX_PRIVATE_KEY', ''),  # Replace with actual private key
        chain="bsc"
    )
    
    # Test Ed25519 key generation
    print(f"Generated requestId: {adapter.request_id}")
    
    # Test authentication flow
    print("Testing authentication flow...")
    token, error = await adapter.create_auth_token()
    if error:
        print(f"Authentication failed: {error}")
        return
    else:
        print(f"Successfully obtained JWT token: {token[:50]}...")
    
    # Test body signature generation
    test_payload = {"symbol": "BTC-USD", "side": "buy", "qty": "0.1"}
    body_signature = await adapter._generate_body_signature(test_payload)
    print(f"Generated body signature: {body_signature}")
    
    # Initialize client
    await adapter.initialize_client()
    
    # Test getting account info
    account_info = await adapter.get_account_info()
    print("Account Info:", json.dumps(account_info, indent=2))
    
    # Test getting positions
    positions = await adapter.get_positions()
    print("Positions:", json.dumps(positions, indent=2))
    
    # Test getting orders
    orders = await adapter.get_orders()
    print("Orders:", json.dumps(orders, indent=2))
    
    # Close connections
    await adapter.close()

async def test_authentication_only():
    """Test only the authentication flow."""
    print("=== Testing StandX Authentication Flow ===")
    adapter = StandXAdapter(
        chain="bsc"
    )

    try:
        print(f"1. Generated Ed25519 key pair with requestId: {adapter.request_id}")

        # Test prepare-signin request
        print("2. Testing prepare-signin request...")
        signed_data, error = await adapter._prepare_signin_request()
        if error:
            print(f"   Prepare-signin failed: {error}")
            return
        print(f"   Successfully obtained signedData: {signed_data[:50]}...")

        # Test JWT parsing
        print("3. Testing JWT parsing...")
        payload = adapter._parse_jwt_payload(signed_data)
        if not payload:
            print("   Failed to parse JWT payload")
            return
        print(f"   Successfully parsed JWT payload: {list(payload.keys())}")

        # Test message signing
        print("4. Testing message signing...")
        message = payload.get('message', '')
        if not message:
            print("   No message found in payload")
            return
        signature = adapter._sign_message_with_wallet(message)
        if not signature:
            print("   Failed to sign message (expected for test with dummy private key)")
            print("   In real usage, provide a valid hex-encoded private key")
            signature = "0x" + "0" * 130  # Dummy signature for testing (65 bytes)
            print(f"   Using dummy signature for testing: {signature[:50]}...")

        # Test login request
        print("5. Testing login request...")
        token, error = await adapter._login_request(signature, signed_data)
        if error:
            print(f"   Login failed: {error}")
            return
        print(f"   Successfully obtained JWT token: {token[:50]}...")

        # Test body signature
        print("6. Testing body signature generation...")
        test_payload = {"test": "data"}
        body_signature = await adapter._generate_body_signature(test_payload)
        print(f"   Generated body signature headers: {list(body_signature.keys())}")

        print("\n=== Authentication Flow Test Completed Successfully ===")
    finally:
        await adapter.close()


async def main():
    from common.logging_config import setup_logging

    # 配置日志
    setup_logging()
    
    """Main function to run tests."""
    print("StandX Adapter Authentication Implementation")
    print("===========================================")
    
    # Run authentication test
    # await test_authentication_only()
    
    # Run full adapter test (commented out by default as it requires real credentials)
    await test_standx_adapter()

if __name__ == "__main__":
    asyncio.run(main())
