from typing import Optional

from .interfaces import ExchangeInterface
from .standx_adapter import StandXAdapter


def create_exchange_adapter(exchange_type: str = "standx", **kwargs) -> Optional[ExchangeInterface]:
    if exchange_type.lower() != "standx":
        return None

    market_id = kwargs.get("market_id", 0)
    symbol = kwargs.get("symbol")
    return StandXAdapter(market_id=market_id, symbol=symbol)
