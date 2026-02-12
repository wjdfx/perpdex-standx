import asyncio
import base64
import json
import logging
import os
import time
import uuid
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any, Callable, Dict, List, Optional, Tuple

import aiohttp
import pandas as pd
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from .interfaces import ExchangeInterface
from .order_converter import normalize_orders_list

logger = logging.getLogger(__name__)


class StandXAdapter(ExchangeInterface):
    """StandX adapter (mainnet) using API token authentication."""

    MARKET_ID_TO_SYMBOL = {
        0: "ETH-USD",
    }

    def __init__(self, market_id: int = 0, symbol: Optional[str] = None):
        if market_id != 0:
            logger.warning("StandX strategy is pinned to ETH market. Override market_id=%s -> 0", market_id)
            market_id = 0

        self.market_id = 0
        self.symbol = symbol or self.MARKET_ID_TO_SYMBOL[0]
        self.base_url = os.getenv("STANDX_BASE_URL", os.getenv("BASE_URL", "https://perps.standx.com"))
        self.api_url = f"{self.base_url}/api"
        self.ws_stream_url = os.getenv("STANDX_WS_STREAM_URL", "wss://perps.standx.com/ws-stream/v1")

        self.auth_token = os.getenv("STANDX_API_TOKEN", "").strip()
        self.request_sign_version = os.getenv("STANDX_REQUEST_SIGN_VERSION", "v1").strip() or "v1"
        self.price_tick = self._safe_float_env("STANDX_PRICE_TICK", 0.1)
        self.qty_step = self._safe_float_env("STANDX_QTY_STEP", 0.001)
        self.http_timeout_sec = self._safe_float_env("STANDX_HTTP_TIMEOUT_SEC", 8.0)
        self._ed25519_private_key = self._load_request_sign_private_key(
            os.getenv("STANDX_REQUEST_SIGN_PRIVATE_KEY", "").strip()
        )

        self.proxy = None
        self.session: Optional[aiohttp.ClientSession] = None

        self.callbacks: Dict[str, Callable] = {}
        self.ws_market_client: Optional[aiohttp.ClientWebSocketResponse] = None
        self.ws_task: Optional[asyncio.Task] = None
        self.ws_ready = asyncio.Event()
        self.ws_authed = False

    @staticmethod
    def _safe_float_env(name: str, default: float) -> float:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        try:
            value = float(raw)
            return value if value > 0 else default
        except Exception:
            return default

    @staticmethod
    def _quantize_to_step(value: float, step: float) -> float:
        if step <= 0:
            return float(value)
        try:
            d_value = Decimal(str(value))
            d_step = Decimal(str(step))
            steps = (d_value / d_step).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            quantized = steps * d_step
            return float(quantized)
        except (InvalidOperation, ValueError):
            return float(value)

    def _format_price(self, price: float, override_tick: Optional[float] = None) -> str:
        tick = override_tick if override_tick and override_tick > 0 else self.price_tick
        value = self._quantize_to_step(price, tick)
        return format(value, "f")

    def _format_qty(self, qty: float) -> str:
        value = self._quantize_to_step(qty, self.qty_step)
        return format(value, "f")

    @staticmethod
    def _is_tick_error(response: Any) -> bool:
        if not isinstance(response, dict):
            return False
        msg = str(response.get("message", "")).lower()
        return "price tick" in msg or "not follow price tick" in msg

    @staticmethod
    def _response_code(response: Any) -> int:
        if isinstance(response, dict):
            code = response.get("code")
            if code is None and isinstance(response.get("result"), dict):
                code = response["result"].get("code")
            if code is None:
                return 0
            try:
                return int(code)
            except Exception:
                return -1
        return 0

    @staticmethod
    def _looks_like_exchange_order_id(order_id: str) -> bool:
        return str(order_id).isdigit()

    @staticmethod
    def _to_int_order_ids(order_ids: List[str]) -> List[int]:
        out: List[int] = []
        for oid in order_ids:
            if str(oid).isdigit():
                out.append(int(str(oid)))
        return out

    @staticmethod
    def _dedupe_ticks(candidates: List[float]) -> List[float]:
        out: List[float] = []
        for tick in candidates:
            if tick <= 0:
                continue
            if all(abs(tick - x) > 1e-12 for x in out):
                out.append(tick)
        return out

    @staticmethod
    def _b58decode(value: str) -> bytes:
        alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
        base = len(alphabet)
        num = 0
        for ch in value:
            idx = alphabet.find(ch)
            if idx < 0:
                raise ValueError("invalid base58 character")
            num = num * base + idx
        out = bytearray()
        while num > 0:
            num, rem = divmod(num, 256)
            out.append(rem)
        out = bytes(reversed(out))
        # restore leading zero bytes
        leading = 0
        for ch in value:
            if ch == "1":
                leading += 1
            else:
                break
        return b"\x00" * leading + out

    @staticmethod
    def _generate_ephemeral_ed25519_private_key() -> bytes:
        key = ed25519.Ed25519PrivateKey.generate()
        return key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )

    @classmethod
    def _load_request_sign_private_key(cls, raw_key: str) -> Optional[bytes]:
        """
        Accept Ed25519 private key in one of:
        - raw hex (64 chars)
        - base64 raw 32 bytes
        - PEM private key
        """
        if not raw_key:
            logger.warning(
                "STANDX_REQUEST_SIGN_PRIVATE_KEY is empty. "
                "Falling back to an ephemeral Ed25519 key for this session."
            )
            return cls._generate_ephemeral_ed25519_private_key()

        # Support PEM copied into .env with escaped newlines.
        raw_key = raw_key.replace("\\n", "\n").strip()
        if raw_key.startswith("ed25519:"):
            raw_key = raw_key[len("ed25519:") :].strip()

        try:
            if "BEGIN" in raw_key:
                key = serialization.load_pem_private_key(raw_key.encode(), password=None)
                if not isinstance(key, ed25519.Ed25519PrivateKey):
                    raise ValueError("not an ed25519 private key")
                return key.private_bytes(
                    encoding=serialization.Encoding.Raw,
                    format=serialization.PrivateFormat.Raw,
                    encryption_algorithm=serialization.NoEncryption(),
                )
        except Exception:
            pass

        cleaned = raw_key[2:] if raw_key.startswith("0x") else raw_key

        # hex
        if all(c in "0123456789abcdefABCDEF" for c in cleaned) and len(cleaned) in (64, 128):
            key_bytes = bytes.fromhex(cleaned)
            return key_bytes[:32]

        # base64
        try:
            decoded = base64.b64decode(cleaned)
            if len(decoded) in (32, 64):
                return decoded[:32]
        except Exception:
            pass

        # base58
        try:
            decoded = cls._b58decode(cleaned)
            if len(decoded) in (32, 64):
                return decoded[:32]
        except Exception:
            pass

        logger.warning(
            "Invalid STANDX_REQUEST_SIGN_PRIVATE_KEY format. "
            "Falling back to an ephemeral Ed25519 key for this session. "
            "This usually causes `invalid body signature` on trading endpoints."
        )
        return cls._generate_ephemeral_ed25519_private_key()

    @staticmethod
    def _serialize_payload(payload: Dict[str, Any]) -> str:
        # Keep exactly one serialization path for both request body and signature payload.
        return json.dumps(payload)

    async def _ensure_session(self) -> None:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(headers={"Content-Type": "application/json", "Accept": "application/json"})

    def _build_auth_headers(self) -> Dict[str, str]:
        if not self.auth_token:
            raise RuntimeError("STANDX_API_TOKEN is not configured")
        return {"Authorization": f"Bearer {self.auth_token}"}

    def _sign_body_headers(self, payload: Dict[str, Any]) -> Dict[str, str]:
        if not self._ed25519_private_key:
            raise RuntimeError("STANDX_REQUEST_SIGN_PRIVATE_KEY is not configured")

        request_id = str(uuid.uuid4())
        timestamp = int(time.time() * 1000)
        payload_str = self._serialize_payload(payload)
        message = f"{self.request_sign_version},{request_id},{timestamp},{payload_str}".encode("utf-8")

        private_key = ed25519.Ed25519PrivateKey.from_private_bytes(self._ed25519_private_key)
        signature = private_key.sign(message)
        signature_b64 = base64.b64encode(signature).decode("utf-8")

        return {
            "x-request-sign-version": self.request_sign_version,
            "x-request-id": request_id,
            "x-request-timestamp": str(timestamp),
            "x-request-signature": signature_b64,
        }

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
        signed: bool = False,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        await self._ensure_session()

        headers = self._build_auth_headers()
        if extra_headers:
            headers.update(extra_headers)
        if payload is not None and signed:
            headers.update(self._sign_body_headers(payload))

        url = f"{self.api_url}{endpoint}"
        kwargs: Dict[str, Any] = {"headers": headers}
        if params is not None:
            kwargs["params"] = params
        if payload is not None:
            kwargs["data"] = self._serialize_payload(payload)
        if self.proxy:
            kwargs["proxy"] = self.proxy
        kwargs["timeout"] = aiohttp.ClientTimeout(total=self.http_timeout_sec)

        try:
            async with self.session.request(method.upper(), url, **kwargs) as response:
                text = await response.text()
                if response.status >= 400:
                    logger.error("StandX request failed: %s %s -> %s %s", method, endpoint, response.status, text)
                    return {"code": response.status, "message": text}

                if not text:
                    return {}

                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"text": text}
        except asyncio.TimeoutError:
            logger.error("StandX request timeout: %s %s (%.2fs)", method, endpoint, self.http_timeout_sec)
            return {"code": 408, "message": "request timeout"}
        except Exception as e:
            logger.error("StandX request exception: %s %s -> %s", method, endpoint, e)
            return {"code": -1, "message": str(e)}

    @staticmethod
    def _unwrap_result(data: Any) -> Any:
        if isinstance(data, dict) and "result" in data:
            return data["result"]
        return data

    @staticmethod
    def _extract_position_qty(position: Dict[str, Any]) -> float:
        for key in ("position", "size", "amount", "qty"):
            if key in position and position[key] is not None:
                try:
                    return float(position[key])
                except Exception:
                    continue
        return 0.0

    def _normalize_positions(self, payload: Any) -> Dict[str, Dict[str, Any]]:
        raw = self._unwrap_result(payload)
        if isinstance(raw, dict):
            rows = list(raw.values())
        elif isinstance(raw, list):
            rows = raw
        else:
            rows = []

        result: Dict[str, Dict[str, Any]] = {}
        for pos in rows:
            if not isinstance(pos, dict):
                continue
            symbol = pos.get("symbol", self.symbol)
            qty = self._extract_position_qty(pos)
            side_raw = str(pos.get("side", "")).lower()
            sign = 0
            if qty < 0:
                sign = -1
            elif qty > 0:
                sign = 1
            if side_raw == "sell":
                sign = -1
            elif side_raw == "buy":
                sign = 1

            normalized = dict(pos)
            normalized["position"] = abs(qty)
            normalized["sign"] = sign
            normalized["side"] = "buy" if sign > 0 else "sell" if sign < 0 else "flat"
            result[symbol] = normalized
        return result

    async def place_multi_orders(self, orders: List[Tuple[bool, float, float]]) -> Tuple[bool, List[str]]:
        if not orders:
            return True, []

        placed_ids: List[str] = []
        for is_ask, price, amount in orders:
            ok, order_id = await self.place_single_order(is_ask, price, amount)
            if not ok:
                if placed_ids:
                    await self.cancel_grid_orders(placed_ids)
                return False, []
            placed_ids.append(order_id)
        return True, placed_ids

    async def place_single_order(self, is_ask: bool, price: float, amount: float) -> Tuple[bool, str]:
        base_order_id = f"grid_{int(time.time() * 1000)}"
        session_id = str(uuid.uuid4())
        tick_candidates = self._dedupe_ticks(
            [
                self.price_tick,
                0.5,
                0.1,
                1.0,
                0.05,
                0.01,
            ]
        )

        for i, tick in enumerate(tick_candidates):
            cl_ord_id = f"{base_order_id}_{i}_{uuid.uuid4().hex[:4]}"
            payload = {
                "symbol": self.symbol,
                "side": "sell" if is_ask else "buy",
                "order_type": "limit",
                "qty": self._format_qty(amount),
                "price": self._format_price(price, override_tick=tick),
                "time_in_force": "alo",
                "reduce_only": False,
                "cl_ord_id": cl_ord_id,
            }
            response = await self._request(
                "POST",
                "/new_order",
                payload=payload,
                signed=True,
                extra_headers={"x-session-id": session_id},
            )
            code = response.get("code", 0) if isinstance(response, dict) else 0
            if code in (0, 200, None):
                if abs(tick - self.price_tick) > 1e-12:
                    logger.warning("Auto-adjusted price tick from %s to %s", self.price_tick, tick)
                    self.price_tick = tick
                return True, cl_ord_id

            if not self._is_tick_error(response):
                logger.error("Failed to place limit order: %s", response)
                return False, ""

            logger.warning("Price tick mismatch (tick=%s), retrying with another tick", tick)

        logger.error("Failed to place limit order after tick retries")
        return False, ""

    async def place_single_market_order(self, is_ask: bool, price: float, amount: float) -> Tuple[bool, str]:
        cl_ord_id = f"grid_mkt_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        session_id = str(uuid.uuid4())
        payload = {
            "symbol": self.symbol,
            "side": "sell" if is_ask else "buy",
            "order_type": "market",
            "qty": self._format_qty(amount),
            "time_in_force": "ioc",
            "reduce_only": False,
            "cl_ord_id": cl_ord_id,
        }
        response = await self._request(
            "POST",
            "/new_order",
            payload=payload,
            signed=True,
            extra_headers={"x-session-id": session_id},
        )
        code = response.get("code", 0) if isinstance(response, dict) else 0
        if code not in (0, 200, None):
            logger.error("Failed to place market order: %s", response)
            return False, ""
        return True, cl_ord_id

    async def cancel_grid_orders(self, order_ids: List[str]) -> bool:
        if not order_ids:
            return True

        session_id = str(uuid.uuid4())
        ids = [str(x) for x in order_ids]
        int_ids = self._to_int_order_ids(ids)
        cl_ids = [x for x in ids if not self._looks_like_exchange_order_id(x)]

        # Try best-guess identifier first, then fallback.
        prefer_ord_id = all(self._looks_like_exchange_order_id(x) for x in ids)
        cancel_fields = (
            ["order_id_list", "cl_ord_id_list"]
            if prefer_ord_id
            else ["cl_ord_id_list", "order_id_list"]
        )

        cancel_ok = False
        last_response: Any = None
        for field in cancel_fields:
            if field == "order_id_list":
                if not int_ids:
                    continue
                payload = {field: int_ids}
            else:
                # cl_ord_id_list must be string ids
                if not cl_ids and prefer_ord_id:
                    # When all are numeric exchange order ids, skip this path.
                    continue
                payload = {field: cl_ids or ids}
            response = await self._request(
                "POST",
                "/cancel_orders",
                payload=payload,
                signed=True,
                extra_headers={"x-session-id": session_id},
            )
            last_response = response
            code = self._response_code(response)
            if code in (0, 200):
                cancel_ok = True
                break
            logger.warning("cancel_orders failed with key=%s, response=%s", field, response)

        if not cancel_ok:
            # Fallback: cancel one-by-one with both keys.
            all_single_ok = True
            for oid in ids:
                per_ok = False
                for key in ("order_id", "cl_ord_id"):
                    value: Any = oid
                    if key == "order_id":
                        if not self._looks_like_exchange_order_id(oid):
                            continue
                        value = int(oid)
                    response = await self._request(
                        "POST",
                        "/cancel_order",
                        payload={key: value},
                        signed=True,
                        extra_headers={"x-session-id": session_id},
                    )
                    code = self._response_code(response)
                    if code in (0, 200):
                        per_ok = True
                        break
                if not per_ok:
                    all_single_ok = False
                    logger.error("Failed to cancel order %s via fallback", oid)
            if not all_single_ok:
                logger.error("Failed to cancel orders: %s", last_response)
                return False

        # Verify cancellation against current open orders to avoid false-positive success.
        for _ in range(3):
            open_orders = await self.get_orders()
            open_ids = set()
            for o in open_orders:
                open_ids.add(str(o.get("id", "")))
                open_ids.add(str(o.get("clientOrderId", "")))
            remaining = [oid for oid in ids if oid in open_ids]
            if not remaining:
                return True
            await asyncio.sleep(0.25)

        logger.error("Cancel verification failed, still open: %s", ids)
        return False

    async def modify_grid_order(self, order_id: str, new_price: float, new_amount: float) -> bool:
        logger.warning("StandX modify is implemented as cancel + create")
        canceled = await self.cancel_grid_orders([order_id])
        if not canceled:
            return False

        # side is unknown here in current strategy flow; keep explicit failure.
        logger.error("modify_grid_order skipped because original side is unknown for order_id=%s", order_id)
        return False

    async def get_orders(self) -> List[dict]:
        response = await self._request("GET", "/query_open_orders", params={"symbol": self.symbol})
        raw = self._unwrap_result(response)
        if isinstance(raw, list):
            return normalize_orders_list(raw)
        return []

    async def get_trades(self, limit: int = 1) -> List[dict]:
        response = await self._request(
            "GET",
            "/query_trades",
            params={"symbol": self.symbol, "page": 1, "page_size": max(limit, 1)},
        )
        raw = self._unwrap_result(response)
        if isinstance(raw, list):
            return raw
        return []

    async def get_positions(self) -> Dict[str, dict]:
        response = await self._request("GET", "/query_positions", params={"symbol": self.symbol})
        return self._normalize_positions(response)

    async def get_account(self) -> dict:
        # API docs naming may differ by deployment; support both.
        response = await self._request("GET", "/query_balance")
        if isinstance(response, dict) and response.get("code") not in (0, 200, None):
            response = await self._request("GET", "/query_balances")

        raw = self._unwrap_result(response)
        if isinstance(raw, list):
            account = raw[0] if raw else {}
        elif isinstance(raw, dict):
            account = raw
        else:
            account = {}

        # Normalize key used by strategy.
        equity = (
            account.get("total_equity")
            or account.get("equity")
            or account.get("balance")
            or account.get("available_balance")
            or 0
        )
        account["total_equity"] = float(equity)
        account["collateral"] = float(equity)
        return account

    async def candle_stick(self, market_id: int, resolution: str, count_back: int = 200) -> pd.DataFrame:
        # Strategy uses GridTrading.candle_stick(). Keep this for interface compatibility.
        return pd.DataFrame()

    async def modify_order(self, order_id: int, new_price: float, new_amount: float) -> bool:
        return await self.modify_grid_order(str(order_id), new_price, new_amount)

    async def get_orders_by_rest(self) -> List[dict]:
        return await self.get_orders()

    async def get_trades_by_rest(self, ask_filter: int, limit: int) -> List[dict]:
        trades = await self.get_trades(limit=limit)
        if ask_filter == 0:
            return trades
        if ask_filter == 1:
            return [t for t in trades if str(t.get("side", "")).lower() == "buy"]
        if ask_filter == 2:
            return [t for t in trades if str(t.get("side", "")).lower() == "sell"]
        return trades

    async def subscribe(self, callbacks: Dict[str, Callable[[str, Any], None]], proxy: str = None) -> None:
        self.callbacks = callbacks
        self.proxy = proxy
        await self._start_market_ws()

    async def _start_market_ws(self) -> None:
        if self.ws_task and not self.ws_task.done():
            return

        self.ws_task = asyncio.create_task(self._market_ws_loop())
        await asyncio.wait_for(self.ws_ready.wait(), timeout=15)

    async def _market_ws_loop(self) -> None:
        while True:
            try:
                async with aiohttp.ClientSession() as ws_session:
                    async with ws_session.ws_connect(self.ws_stream_url, heartbeat=30, proxy=self.proxy) as ws:
                        self.ws_market_client = ws
                        self.ws_ready.set()
                        self.ws_authed = False

                        auth_msg = {"auth": {"token": self.auth_token}}
                        await ws.send_json(auth_msg)

                        # Subscribe channels used by strategy.
                        await ws.send_json({"subscribe": {"channel": "price", "symbol": self.symbol}})
                        await ws.send_json({"subscribe": {"channel": "order"}})
                        await ws.send_json({"subscribe": {"channel": "position"}})

                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    payload = json.loads(msg.data)
                                except Exception:
                                    continue
                                await self._handle_ws_message(payload)
                            elif msg.type == aiohttp.WSMsgType.PING:
                                await ws.pong()
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except Exception as e:
                logger.warning("StandX stream websocket disconnected: %s", e)
                self.ws_ready.clear()
                self.ws_authed = False
                await asyncio.sleep(2)

    async def _emit(self, key: str, *args) -> None:
        cb = self.callbacks.get(key)
        if cb is None:
            return
        if asyncio.iscoroutinefunction(cb):
            await cb(*args)
        else:
            cb(*args)

    async def _handle_ws_message(self, message: Dict[str, Any]) -> None:
        if isinstance(message, dict):
            data = message.get("data", {})
            channel = message.get("channel")

            # auth ack
            if "auth" in message or channel == "auth":
                self.ws_authed = True
                return

            if channel == "price":
                row = data[0] if isinstance(data, list) and data else data
                if isinstance(row, dict):
                    stats = {
                        "symbol": row.get("symbol", self.symbol),
                        "mark_price": row.get("mark_price") or row.get("last_price") or row.get("mid_price"),
                        "last_price": row.get("last_price") or row.get("mark_price"),
                    }
                    await self._emit("market_stats", str(self.market_id), stats)
                return

            if channel == "order":
                rows = data if isinstance(data, list) else [data]
                orders = normalize_orders_list(rows)
                await self._emit("orders", "standx", orders)
                return

            if channel == "position":
                rows = data if isinstance(data, list) else [data]
                positions = self._normalize_positions(rows)
                await self._emit("positions", "standx", positions)
                return

    async def close(self):
        if self.ws_task and not self.ws_task.done():
            self.ws_task.cancel()
            try:
                await self.ws_task
            except asyncio.CancelledError:
                pass
        self.ws_task = None

        if self.ws_market_client and not self.ws_market_client.closed:
            await self.ws_market_client.close()
        self.ws_market_client = None
        self.ws_ready.clear()

        if self.session and not self.session.closed:
            await self.session.close()
        self.session = None

    async def initialize_client(self) -> None:
        await self._ensure_session()
        if not self.auth_token:
            raise RuntimeError("STANDX_API_TOKEN is required")
        if not self._ed25519_private_key:
            raise RuntimeError("STANDX_REQUEST_SIGN_PRIVATE_KEY is required")

    async def create_auth_token(self) -> Tuple[str, str]:
        if not self.auth_token:
            return "", "STANDX_API_TOKEN is not configured"
        return self.auth_token, None

    async def get_account_info(self) -> dict:
        account = await self.get_account()
        account["positions"] = await self.get_positions()
        return account
