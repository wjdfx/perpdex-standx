"""StandX order converter to CCXT-like shape used by strategy modules."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Union

logger = logging.getLogger(__name__)


def map_time_in_force(time_in_force: str) -> str:
    mapping = {
        "gtc": "GTC",
        "ioc": "IOC",
        "fok": "FOK",
        "gtx": "GTC",
        "alo": "GTC",
    }
    return mapping.get(str(time_in_force).lower(), "GTC")


def normalize_order_to_ccxt(order: Dict[str, Any]) -> Dict[str, Any]:
    try:
        status_mapping = {
            "new": "open",
            "open": "open",
            "partially_filled": "open",
            "filled": "closed",
            "closed": "closed",
            "canceled": "canceled",
            "cancelled": "canceled",
            "expired": "expired",
            "rejected": "rejected",
        }

        side = str(order.get("side", "buy")).lower()
        price = float(order.get("price", order.get("avg_price", 0)) or 0)
        amount = float(order.get("qty", order.get("amount", 0)) or 0)
        filled = float(order.get("fill_qty", order.get("filled_qty", order.get("filled", 0))) or 0)
        average = float(order.get("fill_avg_price", order.get("avg_price", price)) or 0)
        remaining = max(amount - filled, 0)

        created_at = order.get("created_at")
        timestamp_ms = None
        dt_str = None
        if created_at:
            try:
                dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
                timestamp_ms = int(dt.timestamp() * 1000)
                dt_str = dt.isoformat()
            except Exception:
                dt_str = str(created_at)

        symbol = str(order.get("symbol", "ETH-USD")).replace("-", "/")

        return {
            "id": str(order.get("id", order.get("ord_id", ""))),
            "clientOrderId": str(order.get("cl_ord_id", order.get("client_order_id", ""))),
            "datetime": dt_str,
            "timestamp": timestamp_ms,
            "lastTradeTimestamp": None,
            "status": status_mapping.get(str(order.get("status", "open")).lower(), "open"),
            "symbol": symbol,
            "type": str(order.get("order_type", "limit")).lower(),
            "timeInForce": map_time_in_force(str(order.get("time_in_force", "gtc"))),
            "side": side,
            "price": price,
            "average": average,
            "amount": amount,
            "filled": filled,
            "remaining": remaining,
            "cost": filled * (average or price),
            "trades": [],
            "fee": {},
            "reduceOnly": bool(order.get("reduce_only", False)),
            "postOnly": str(order.get("time_in_force", "")).lower() == "alo",
            "info": order,
        }
    except Exception as e:
        logger.error("Failed to normalize StandX order: %s", e, exc_info=True)
        return {
            "id": str(order.get("id", "")),
            "clientOrderId": str(order.get("cl_ord_id", "")),
            "status": "unknown",
            "symbol": "ETH/USD",
            "side": "buy",
            "price": 0,
            "amount": 0,
            "filled": 0,
            "remaining": 0,
            "cost": 0,
            "info": order,
        }


def normalize_orders_list(orders: Union[List[Dict[str, Any]], Dict[str, Any]]) -> List[Dict[str, Any]]:
    if isinstance(orders, dict):
        if "result" in orders and isinstance(orders["result"], list):
            return [normalize_order_to_ccxt(row) for row in orders["result"]]
        return [normalize_order_to_ccxt(orders)]
    if isinstance(orders, list):
        return [normalize_order_to_ccxt(row) for row in orders if isinstance(row, dict)]
    return []
