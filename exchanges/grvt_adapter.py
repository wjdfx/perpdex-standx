import asyncio
import json
import logging
import os
import sys
import signal
import time
from typing import List, Tuple, Dict, Callable, Any
import pandas as pd

from .interfaces import ExchangeInterface
from .order_converter import normalize_order_to_ccxt, normalize_orders_list

# GRVT SDK imports
try:
    from pysdk.grvt_ccxt_pro import GrvtCcxtPro
    from pysdk.grvt_ccxt_ws import GrvtCcxtWS
    from pysdk.grvt_ccxt_env import GrvtEnv
    from pysdk.grvt_ccxt_ws import GrvtWSEndpointType
    GRVT_SDK_AVAILABLE = True
except ImportError:
    GRVT_SDK_AVAILABLE = False
    logging.warning("GRVT SDK not available. Please install grvt-pysdk.")

logger = logging.getLogger(__name__)


class GrvtCcxtWSWithRefresh(GrvtCcxtWS):
    """
    Subclass of GrvtCcxtWS that ensures the auth cookie is refreshed
    before attempting to connect to a channel.
    """

    async def connect_channel(self, grvt_endpoint_type: GrvtWSEndpointType) -> bool:
        """Ensure cookie is fresh before connecting (handles initial connection and reconnections)."""
        try:
            # refresh_cookie checks expiration internally. 
            # This is called on EVERY connection attempt, including reconnections.
            old_cookie = self._cookie.get('gravity') if self._cookie else None
            await self.refresh_cookie()
            new_cookie = self._cookie.get('gravity') if self._cookie else None
            
            if old_cookie != new_cookie:
                logger.info(f"GrvtCcxtWSWithRefresh: Cookie refreshed before connecting {grvt_endpoint_type}")
            else:
                logger.debug(f"GrvtCcxtWSWithRefresh: Cookie still valid for {grvt_endpoint_type}")
                
        except Exception as e:
            logger.error(f"Failed to refresh cookie before connecting {grvt_endpoint_type}: {e}")

        return await super().connect_channel(grvt_endpoint_type)


class GrvtAdapter(ExchangeInterface):
    """
    GRVT exchange adapter implementing the ExchangeInterface.
    Uses GRVT Python SDK for REST and WebSocket operations.
    """

    # Market ID to GRVT symbol mapping
    MARKET_ID_TO_SYMBOL = {
        0: "ETH_USDT_Perp",  # Default
        1: "BTC_USDT_Perp",
        2: "SOL_USDT_Perp",
        # Add more mappings as needed
    }

    def __init__(
        self,
        market_id: int = 0,
        symbol: str = None  # If None, will use market_id mapping
    ):
        if not GRVT_SDK_AVAILABLE:
            raise ImportError("GRVT SDK not available. Please install grvt-pysdk.")

        self.market_id = market_id
        self.symbol = symbol or self.MARKET_ID_TO_SYMBOL.get(market_id, "ETH_USDT_Perp")

        # GRVT environment configuration
        grvt_env = os.getenv('GRVT_ENV', 'prod')
        try:
            self.env = GrvtEnv(grvt_env)
        except ValueError:
            logger.warning(f"Invalid GRVT_ENV '{grvt_env}', using testnet")
            self.env = GrvtEnv.PROD

        # GRVT client parameters
        params = {
            'api_key': os.getenv('GRVT_API_KEY', ''),
            'trading_account_id': os.getenv('GRVT_TRADING_ACCOUNT_ID', ''),
            'private_key': os.getenv('GRVT_PRIVATE_KEY', ''),
        }

        # Initialize GRVT REST client
        self.rest_client = GrvtCcxtPro(
            env=self.env,
            # logger=logger,
            parameters=params
        )
        
        # Initialize GRVT WebSocket client
        self.ws_client = GrvtCcxtWSWithRefresh(
            env=self.env,
            loop=asyncio.get_event_loop(),
            # logger=logger,
            parameters=params
        )

        self.callbacks: Dict[str, Callable] = {}
        self.ws_initialized = False

        # logger.info(f"GRVT Adapter initialized with env={self.env.value}, symbol={self.symbol}")

    def _get_symbol_for_market_id(self, market_id: int) -> str:
        """Get GRVT symbol for given market_id."""
        return self.MARKET_ID_TO_SYMBOL.get(market_id, self.symbol)

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

            logger.debug(f"place_multi_orders: Successfully placed {len(order_ids)} orders")
            return True, order_ids
        except Exception as e:
            logger.error(f"place_multi_orders error: {e}")
            return False, []

    async def place_single_order(self, is_ask: bool, price: float, amount: float) -> Tuple[bool, str]:
        """
        Place single limit order.
        Returns: (success, order_id)
        """
        try:
            client_order_id = int(time.time() * 1000) % 1000000
            side = 'sell' if is_ask else 'buy'
            params = {
                "client_order_id" : str(client_order_id),
                "post_only" : True,
            }
            response = await self.rest_client.create_limit_order(
                symbol=self.symbol,
                side=side,
                amount=amount,
                price=price,
                params=params,
            )

            if response and 'order_id' in response:
                return True, str(client_order_id)
            else:
                logger.error(f"Failed to create order: {response}")
                return False, ''
        except Exception as e:
            logger.error(f"place_single_order error: {e}")
            return False, ''

    async def place_single_market_order(self, is_ask: bool, price: float, amount: float) -> Tuple[bool, str]:
        """
        Place single market order.
        """
        try:
            client_order_id = int(time.time() * 1000) % 1000000
            side = 'sell' if is_ask else 'buy'
            params = {
                "client_order_id": str(client_order_id)
            }   
            response = await self.rest_client.create_order(
                symbol=self.symbol,
                order_type='market',
                side=side,
                amount=amount,
                params=params,
            )
            
            if response and 'order_id' in response:
                return True, str(client_order_id)
            else:
                logger.error(f"Failed to create market order: {response}")
                return False, ''
        except Exception as e:
            logger.error(f"place_single_market_order error: {e}")
            return False, ''

    async def cancel_grid_orders(self, order_ids: List[str]) -> bool:
        """
        Batch cancel orders.
        """
        if not order_ids:
            logger.warning("cancel_grid_orders: No order_ids provided")
            return True

        try:
            cancelled_count = 0
            failed_orders = []

            for order_id in order_ids:
                try:
                    params = {
                        "client_order_id" : order_id
                    }
                    success = await self.rest_client.cancel_order(params=params)
                    if success:
                        cancelled_count += 1
                        logger.debug(f"cancel_grid_orders: Successfully cancelled order {order_id}")
                    else:
                        failed_orders.append(order_id)
                        logger.error(f"cancel_grid_orders: Failed to cancel order {order_id}")
                except Exception as e:
                    failed_orders.append(order_id)
                    logger.error(f"cancel_grid_orders: Exception cancelling order {order_id}: {e}")

            if failed_orders:
                logger.error(f"cancel_grid_orders: Failed to cancel {len(failed_orders)} out of {len(order_ids)} orders: {failed_orders}")
                return False

            logger.info(f"cancel_grid_orders: Successfully cancelled {cancelled_count} orders")
            return True
        except Exception as e:
            logger.error(f"cancel_grid_orders error: {e}")
            return False

    async def modify_grid_order(self, order_id: str, new_price: float, new_amount: float) -> bool:
        """
        Modify order - NOT SUPPORTED by GRVT API.
        GRVT does not support order modification, only cancel and create new.
        """
        logger.warning("GRVT does not support order modification. Use cancel and create new order instead.")
        return False

    async def get_orders(self) -> List[dict]:
        """
        Get current orders.
        """
        try:
            orders = await self.rest_client.fetch_open_orders(symbol=self.symbol)
            return orders if orders else []
        except Exception as e:
            logger.error(f"get_orders error: {e}", stack_info=True)
            return []

    async def get_trades(self, limit: int = 1) -> List[dict]:
        """
        Get recent trades.
        """
        try:
            trades = await self.rest_client.fetch_my_trades(
                symbol=self.symbol,
                limit=limit
            )
            if 'result' in trades:
                return trades['result']
            return trades if isinstance(trades, list) else []
        except Exception as e:
            logger.error(f"get_trades error: {e}")
            return []

    async def get_positions(self) -> Dict[str, dict]:
        """
        Get positions.
        """
        try:
            positions = await self.rest_client.fetch_positions(symbols=[self.symbol])
            result = {}
            for pos in positions:
                if 'instrument' in pos:
                    result[pos['instrument']] = pos
            return result
        except Exception as e:
            logger.error(f"get_positions error: {e}")
            return {}

    async def get_account(self) -> dict:
        """
        Get account info including collateral.
        """
        try:
            account = await self.rest_client.get_account_summary()
            return account
        except Exception as e:
            logger.error(f"get_account error: {e}")
            return {}

    async def candle_stick(self, market_id: int, resolution: str, count_back: int = 200) -> pd.DataFrame:
        """
        Get candlestick data.
        """
        try:
            # Get symbol for the market_id
            symbol = self._get_symbol_for_market_id(market_id)

            # Map resolution to GRVT format
            resolution_map = {
                '1m': '1m',
                '5m': '5m',
                '15m': '15m',
                '1h': '1h',
                '4h': '4h',
                '1d': '1d'
            }
            grvt_resolution = resolution_map.get(resolution, '1m')

            response = await self.rest_client.fetch_ohlcv(
                symbol=symbol,
                timeframe=grvt_resolution,
                limit=count_back
            )

            if 'result' in response:
                candles = response['result']
                df = pd.DataFrame(candles)
                if not df.empty:
                    # Ensure required columns exist and format DataFrame properly
                    required_columns = ['open_time', 'open', 'high', 'low', 'close', 'volume']
                    if all(col in df.columns for col in required_columns):
                        # Convert timestamps if needed
                        if 'open_time' in df.columns:
                            df['time'] = pd.to_datetime(df['open_time'], unit='ns')
                        df.set_index('time', inplace=True)
                        # Ensure numeric columns are properly typed
                        numeric_cols = ['open', 'high', 'low', 'close', 'volume']
                        for col in numeric_cols:
                            if col in df.columns:
                                df[col] = pd.to_numeric(df[col], errors='coerce')
                        logger.info(f"candle_stick: Retrieved {len(df)} candles for {symbol}")
                        return df
                    else:
                        logger.warning(f"candle_stick: Missing required columns in response for {symbol}")
                        return pd.DataFrame()
                else:
                    logger.info(f"candle_stick: No candle data available for {symbol}")
                    return pd.DataFrame()
            else:
                logger.warning(f"candle_stick: No 'result' in response for {symbol}")
                return pd.DataFrame()
        except Exception as e:
            logger.error(f"candle_stick error for market_id {market_id} ({symbol}): {e}")
            return pd.DataFrame()

    async def modify_order(self, order_id: int, new_price: float, new_amount: float) -> bool:
        """
        Modify order - NOT SUPPORTED by GRVT API.
        """
        logger.warning("GRVT does not support order modification.")
        return False

    async def get_orders_by_rest(self) -> List[dict]:
        """
        Get orders via REST API.
        """
        return await self.get_orders()

    async def get_trades_by_rest(self, ask_filter: int, limit: int) -> List[dict]:
        """
        Get trades via REST API.
        ask_filter: 0=all trades, 1=buy trades, 2=sell trades (GRVT may not support filtering)
        """
        try:
            # Note: GRVT SDK fetch_my_trades may not support ask_filter parameter
            # We'll fetch all trades and filter client-side if needed
            trades = await self.rest_client.fetch_my_trades(
                symbol=self.symbol,
                limit=limit
            )

            if 'result' in trades:
                result_trades = trades['result']
            elif isinstance(trades, list):
                result_trades = trades
            else:
                logger.warning(f"get_trades_by_rest: Unexpected response format: {trades}")
                return []

            # Filter trades based on ask_filter if supported by the data
            if ask_filter > 0 and result_trades:
                # Check if trades have side information
                filtered_trades = []
                for trade in result_trades:
                    # GRVT trades may have 'side' field: 'buy' or 'sell'
                    # ask_filter: 1=buy (maker), 2=sell (ask)
                    if 'side' in trade:
                        if ask_filter == 1 and trade['side'] == 'buy':
                            filtered_trades.append(trade)
                        elif ask_filter == 2 and trade['side'] == 'sell':
                            filtered_trades.append(trade)
                    else:
                        # If no side info, include all trades with a warning
                        logger.warning(f"get_trades_by_rest: Trade missing 'side' field, including all trades")
                        filtered_trades = result_trades
                        break
                result_trades = filtered_trades

            # logger.info(f"get_trades_by_rest: Retrieved {len(result_trades)} trades with ask_filter={ask_filter}")
            return result_trades
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

        # Store proxy setting (though GRVT SDK may not support proxy directly)
        self.proxy = proxy
        if proxy:
            logger.warning(f"GRVT adapter: Proxy support ({proxy}) is not implemented in GRVT SDK. WebSocket connections may not use proxy.")

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

        logger.info(f"GRVT subscribe: Registered callbacks for {list(callbacks.keys())}, proxy={proxy}")

    async def _initialize_ws(self):
        """Initialize WebSocket client."""
        try:
            await self.ws_client.initialize()
            self.ws_initialized = True
            logger.info("GRVT WebSocket initialized")
        except Exception as e:
            logger.error(f"Failed to initialize WebSocket: {e}", exc_info=True)

    async def _subscribe_market_stats(self):
        """Subscribe to market statistics."""
        try:
            # Use the proper callback method
            await self.ws_client.subscribe(
                stream='ticker.s',
                callback=lambda message: self._market_stats_callback(message),
                ws_end_point_type=GrvtWSEndpointType.MARKET_DATA_RPC_FULL,
                params={'instrument': self.symbol}
            )
        except Exception as e:
            logger.error(f"Failed to subscribe to market stats: {e}")

    async def _subscribe_orders(self):
        """Subscribe to order updates."""
        try:
            # Use the proper callback method
            await self.ws_client.subscribe(
                stream='order',
                callback=lambda message: self._orders_callback(message),
                ws_end_point_type=GrvtWSEndpointType.TRADE_DATA_RPC_FULL,
                params={'instrument': self.symbol}
            )
        except Exception as e:
            logger.error(f"Failed to subscribe to orders: {e}")

    async def _subscribe_positions(self):
        """Subscribe to position updates."""
        try:
            # Use the proper callback method
            await self.ws_client.subscribe(
                stream='position',
                callback=lambda message: self._positions_callback(message),
                ws_end_point_type=GrvtWSEndpointType.TRADE_DATA_RPC_FULL,
                params={}
            )
        except Exception as e:
            logger.error(f"Failed to subscribe to positions: {e}")

    async def _market_stats_callback(self, message: dict):
        """Handle market stats updates."""
        if 'market_stats' in self.callbacks and self.callbacks['market_stats']:
            # Extract market stats from message
            stats = message.get('feed', {})
            # logger.info(f"Market Stats Raw Message: {message}")
            # logger.info(f"Extracted Stats: {stats}")
            
            if asyncio.iscoroutinefunction(self.callbacks['market_stats']):
                asyncio.create_task(self.callbacks['market_stats'](str(self.market_id), stats))
            else:
                self.callbacks['market_stats'](str(self.market_id), stats)

    async def _orders_callback(self, message: dict):
        """Handle order updates."""
        if 'orders' in self.callbacks and self.callbacks['orders']:
            # Extract orders from message
            orders_data = message.get('feed', [])
            # logger.info(f"Orders Raw Message: {message}")
            # logger.info(f"Extracted Orders: {orders_data}")
            
            # Ensure orders_data is a list
            if not isinstance(orders_data, list):
                # If it's a single order (dict) or other format, convert to list
                if isinstance(orders_data, dict):
                    orders_data = [orders_data]
                else:
                    # If it's a string or other unexpected type, try to parse it
                    try:
                        if isinstance(orders_data, str):
                            orders_data = json.loads(orders_data)
                            if isinstance(orders_data, dict):
                                orders_data = [orders_data]
                    except (json.JSONDecodeError, TypeError):
                        logger.warning(f"Unexpected orders data format: {type(orders_data)}, data: {orders_data}")
                        orders_data = []
            
            # Convert orders to CCXT format
            normalized_orders = normalize_order_to_ccxt(orders_data) if isinstance(orders_data, dict) else [normalize_order_to_ccxt(o) for o in orders_data]
            # 这里原代码使用了 normalize_orders_list，但前面 line 12 导入的是 normalize_orders_list，
            # 这里的 normalize_orders_list 已经在 grvt_adapter.py 顶层被使用过了，确认一下。
            # 重新检查发现 line 539 原代码使用的是 normalize_orders_list。
            
            normalized_orders = normalize_orders_list(orders_data)
            
            if asyncio.iscoroutinefunction(self.callbacks['orders']):
                asyncio.create_task(self.callbacks['orders'](self.rest_client.get_trading_account_id(), normalized_orders))
            else:
                self.callbacks['orders'](self.rest_client.get_trading_account_id(), normalized_orders)

    def _normalize_grvt_position(self, position: dict) -> dict:
        """Normalize GRVT position format to be compatible with quant_grid_long.py."""
        if not isinstance(position, dict):
            return position
            
        # Create a copy to avoid modifying the original
        normalized = position.copy()
        
        # Add 'sign' field based on size (1 for positive, -1 for negative, 0 for zero)
        try:
            size = float(position.get('size', 0))
            normalized['sign'] = 1 if size > 0 else (-1 if size < 0 else 0)
        except (ValueError, TypeError):
            normalized['sign'] = 0
            
        # Ensure all numeric fields are properly typed
        numeric_fields = ['size', 'notional', 'entry_price', 'exit_price', 'mark_price',
                         'unrealized_pnl', 'realized_pnl', 'total_pnl', 'roi', 'quote_index_price',
                         'est_liquidation_price', 'leverage', 'cumulative_fee', 'cumulative_realized_funding_payment']
        
        for field in numeric_fields:
            if field in normalized:
                try:
                    if isinstance(normalized[field], str):
                        normalized[field] = float(normalized[field])
                except (ValueError, TypeError):
                    # Keep original value if conversion fails
                    pass
                    
        return normalized
    
    async def _positions_callback(self, message: dict):
        """Handle position updates."""
        if 'positions' in self.callbacks and self.callbacks['positions']:
            # Extract positions from message
            positions = message.get('feed', [])
            # logger.info(f"Positions Raw Message: {message}")
            # logger.info(f"Extracted Positions: {positions}")
            
            # Handle GRVT format compatibility - ensure positions is a dict with instrument keys
            try:
                # If positions is a string, try to parse it
                if isinstance(positions, str):
                    positions = json.loads(positions)
                 
                # If positions is a list, convert to dict format
                if isinstance(positions, list):
                    # Convert list of positions to dict format
                    positions_dict = {}
                    for pos in positions:
                        # Handle GRVT position format - it's already a dict, no need to parse strings
                        if isinstance(pos, dict) and 'instrument' in pos:
                            # Normalize the position format
                            normalized_pos = self._normalize_grvt_position(pos)
                            positions_dict[pos['instrument']] = normalized_pos
                        elif isinstance(pos, str):
                            # Handle case where individual position is a string
                            try:
                                pos = json.loads(pos)
                                if isinstance(pos, dict) and 'instrument' in pos:
                                    normalized_pos = self._normalize_grvt_position(pos)
                                    positions_dict[pos['instrument']] = normalized_pos
                            except (json.JSONDecodeError, TypeError) as e:
                                logger.error(f"Failed to parse position string: {e}, position: {pos}")
                                continue
                        else:
                            logger.warning(f"Unexpected position format in list: {type(pos)}, data: {pos}")
                    positions = positions_dict
                elif not isinstance(positions, dict):
                    logger.warning(f"Unexpected positions format: {type(positions)}, data: {positions}")
                    positions = {}
                 
                # Ensure each position is properly formatted
                formatted_positions = {}
                for market_id, position in positions.items():
                    # GRVT positions are already in dict format, no need to parse strings
                    if isinstance(position, dict):
                        # Normalize the position format
                        normalized_pos = self._normalize_grvt_position(position)
                        formatted_positions[market_id] = normalized_pos
                    elif isinstance(position, str):
                        # Check if the string is a valid JSON object before attempting to parse
                        try:
                            # Only attempt JSON parsing if the string looks like JSON (starts with { or [)
                            if position.strip() and (position.strip()[0] == '{' or position.strip()[0] == '['):
                                position = json.loads(position)
                                if isinstance(position, dict):
                                    normalized_pos = self._normalize_grvt_position(position)
                                    formatted_positions[market_id] = normalized_pos
                            # else:
                            #     # This is a simple string value (like "ETH_USDT_Perp" or "CROSS"), not a JSON object
                            #     # Skip this invalid position format
                            #     logger.warning(f"Unexpected position format for market {market_id}: simple string value '{position}', expected dict")
                            #     continue
                        except (json.JSONDecodeError, TypeError, IndexError) as e:
                            logger.warning(f"Failed to parse position string for market {market_id}: {e}, position: {position}")
                            continue
                    else:
                        logger.warning(f"Unexpected position format for market {market_id}: {type(position)}, data: {position}")
                 
                positions = formatted_positions
                 
            except Exception as e:
                logger.error(f"Error processing positions: {e}")
                positions = {}
             
            if asyncio.iscoroutinefunction(self.callbacks['positions']):
                asyncio.create_task(self.callbacks['positions'](self.rest_client.get_trading_account_id(), positions))
            else:
                self.callbacks['positions'](self.rest_client.get_trading_account_id(), positions)

    async def close(self):
        """
        Close connections.
        """
        try:
            if hasattr(self.ws_client, 'close'):
                await self.ws_client.close()
        except Exception as e:
            logger.error(f"Error closing WebSocket: {e}")

    async def initialize_client(self) -> None:
        """
        Initialize the exchange client.
        """
        try:
            await self.rest_client.load_markets()
            logger.info("GRVT REST client initialized")
        except Exception as e:
            logger.error(f"Failed to initialize REST client: {e}", exc_info=True)

    async def create_auth_token(self) -> Tuple[str, str]:
        """
        Create authentication token.
        GRVT uses cookie-based auth, so this returns a placeholder.
        """
        return "grvt_cookie_auth", None

    async def get_account_info(self) -> dict:
        """
        Get detailed account information including positions.
        """
        try:
            account = await self.rest_client.get_account_summary()
            positions = await self.get_positions()
            # Convert positions to dict with instrument as key
            account['positions'] = {str(pos.get('instrument', '')): pos for pos in positions.values()}
            return account
        except Exception as e:
            logger.error(f"get_account_info error: {e}", stack_info=True)
            return {}
        
    async def callback_general(message: dict) -> None:
        message.get("params", {}).get("channel")
        logger.info(f"callback_general(): message:{message}")
        
async def run():
    from dotenv import load_dotenv

    load_dotenv()
    
    from common.logging_config import setup_logging

    # 配置日志
    setup_logging()

    adapter = GrvtAdapter(market_id=0)
    await adapter.initialize_client()
    
    # result = await adapter.get_orders_by_rest()
    
    # print("get_orders_by_rest...................:", json.dumps(result, ensure_ascii=False, indent=2))
    
    # result = await adapter.get_account_info()
    
    # print("get_account_info...................:", json.dumps(result, ensure_ascii=False, indent=2))
    
    await adapter._initialize_ws()
    await adapter.subscribe({
        'market_stats': lambda market_id, stats: logger.info(f"Market Stats Callback: market_id={market_id}, stats={stats}"),
        'orders': lambda account_id, orders: logger.info(f"Orders Callback: account_id={account_id}, orders={orders}"),
        'positions': lambda account_id, positions: logger.info(f"Positions Callback: account_id={account_id}, positions={positions}"),
    })
    
async def test_ws():
    from dotenv import load_dotenv

    load_dotenv()
    
    loop = asyncio.get_event_loop()
    asyncio.set_event_loop(loop)
    
    adapter = GrvtAdapter(market_id=0)
    await adapter._initialize_ws()
    await adapter.subscribe({
        'market_stats': lambda market_id, stats: logger.info(f"Market Stats Callback: market_id={market_id}, stats={stats}"),
        'orders': lambda account_id, orders: logger.info(f"Orders Callback: account_id={account_id}, orders={orders}"),
        'positions': lambda account_id, positions: logger.info(f"Positions Callback: account_id={account_id}, positions={positions}"),
    })
    test_api = loop.run_until_complete(adapter.subscribe(loop))
    if not test_api:
        logger.error("Failed to subscribe to Websocket channels.")
        sys.exit(1)
    loop.run_until_complete(asyncio.sleep(5))
    loop.run_forever()
    loop.close()
    
    
if __name__ == "__main__":
    asyncio.run(run())