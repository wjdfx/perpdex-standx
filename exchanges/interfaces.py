from abc import ABC, abstractmethod
from typing import List, Tuple, Dict, Callable, Any
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd
else:
    class _PD:
        DataFrame = Any
    pd = _PD()

class ExchangeInterface(ABC):
    """
    Exchange interface for decoupling strategy from specific exchange clients.
    """

    @abstractmethod
    async def place_multi_orders(self, orders: List[Tuple[bool, float, float]]) -> Tuple[bool, List[str]]:
        """
        Place multiple limit orders.
        orders: [(is_ask, price, amount), ...]
        Returns: (success, order_ids)
        """
        pass

    @abstractmethod
    async def place_single_order(self, is_ask: bool, price: float, amount: float) -> Tuple[bool, str]:
        """
        Place single limit order.
        Returns: (success, order_id)
        """
        pass

    @abstractmethod
    async def place_single_market_order(self, is_ask: bool, price: float, amount: float) -> Tuple[bool, str]:
        """
        Place single market order.
        """
        pass

    @abstractmethod
    async def cancel_grid_orders(self, order_ids: List[str]) -> bool:
        """
        Batch cancel orders.
        """
        pass

    @abstractmethod
    async def modify_grid_order(self, order_id: str, new_price: float, new_amount: float) -> bool:
        """
        Modify order.
        """
        pass

    @abstractmethod
    async def get_orders(self) -> List[dict]:
        """
        Get current orders.
        """
        pass

    @abstractmethod
    async def get_trades(self, limit: int = 1) -> List[dict]:
        """
        Get recent trades.
        """
        pass

    @abstractmethod
    async def get_positions(self) -> Dict[str, dict]:
        """
        Get positions.
        """
        pass

    @abstractmethod
    async def get_account(self) -> dict:
        """
        Get account info including collateral.
        """
        pass

    @abstractmethod
    async def candle_stick(self, market_id: int, resolution: str, count_back: int = 200) -> pd.DataFrame:
        """
        Get candlestick data.
        """
        pass

    @abstractmethod
    async def modify_order(self, order_id: int, new_price: float, new_amount: float) -> bool:
        """
        Modify order.
        """
        pass

    @abstractmethod
    async def get_orders_by_rest(self) -> List[dict]:
        """
        Get orders via REST API.
        """
        pass

    @abstractmethod
    async def get_trades_by_rest(self, ask_filter: int, limit: int) -> List[dict]:
        """
        Get trades via REST API.
        """
        pass

    @abstractmethod
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
        pass

    @abstractmethod
    async def close(self):
        """
        Close connections.
        """
        pass

    @abstractmethod
    async def initialize_client(self) -> None:
        """
        Initialize the exchange client.
        """
        pass

    @abstractmethod
    async def create_auth_token(self) -> Tuple[str, str]:
        """
        Create authentication token.
        Returns: (auth_token, error)
        """
        pass

    @abstractmethod
    async def get_account_info(self) -> dict:
        """
        Get detailed account information including positions.
        """
        pass
