"""
仓位通知处理模块

封装仓位变化的通知逻辑，简化主策略代码
"""

import asyncio
import logging
from typing import Optional

from .dingtalk_notify import DingTalkNotifier

logger = logging.getLogger(__name__)


class PositionNotifier:
    """
    仓位变化通知处理器
    
    根据仓位变化自动判断通知类型并发送
    """
    
    def __init__(self, notifier: DingTalkNotifier, address: str, symbol: str):
        """
        初始化仓位通知处理器
        
        Args:
            notifier: 钉钉通知器实例
            address: 钱包地址
            symbol: 交易对
        """
        self.notifier = notifier
        self.address = address
        self.symbol = symbol
        self._prev_position_qty: float = 0.0
    
    def on_position_change(
        self,
        new_qty: float,
        current_price: Optional[float] = None,
    ) -> None:
        """
        处理仓位变化，自动判断并发送对应通知
        
        Args:
            new_qty: 新仓位数量
            current_price: 当前市场价格
        """
        if new_qty == self._prev_position_qty:
            return
        
        delta = new_qty - self._prev_position_qty
        side = "buy" if delta > 0 else "sell"
        filled_qty = abs(delta)
        price = current_price or 0
        
        # 判断是开仓还是平仓（仓位绝对值是否增加）
        is_increasing = abs(new_qty) > abs(self._prev_position_qty)
        
        if new_qty == 0:
            # 清仓：减仓通知 + 清仓通知
            self._send_reduce_notification(side, price, filled_qty, new_qty, current_price)
            self._send_cleared_notification(price, filled_qty, current_price)
        elif is_increasing:
            # 开仓通知
            self._send_open_notification(side, price, filled_qty, new_qty, current_price)
        else:
            # 减仓通知
            self._send_reduce_notification(side, price, filled_qty, new_qty, current_price)
        
        self._prev_position_qty = new_qty
    
    def _send_open_notification(
        self,
        side: str,
        price: float,
        qty: float,
        position_qty: float,
        current_price: Optional[float],
    ) -> None:
        """发送开仓通知"""
        asyncio.create_task(self.notifier.notify_position_open(
            address=self.address,
            symbol=self.symbol,
            side=side,
            price=price,
            qty=qty,
            position_qty=position_qty,
            current_price=current_price,
        ))
    
    def _send_reduce_notification(
        self,
        side: str,
        price: float,
        qty: float,
        position_qty: float,
        current_price: Optional[float],
    ) -> None:
        """发送减仓通知"""
        asyncio.create_task(self.notifier.notify_position_reduce(
            address=self.address,
            symbol=self.symbol,
            side=side,
            price=price,
            qty=qty,
            position_qty=position_qty,
            current_price=current_price,
        ))
    
    def _send_cleared_notification(
        self,
        price: float,
        qty: float,
        current_price: Optional[float],
    ) -> None:
        """发送清仓通知"""
        asyncio.create_task(self.notifier.notify_position_cleared(
            address=self.address,
            symbol=self.symbol,
            price=price,
            qty=qty,
            current_price=current_price,
        ))
    
    @property
    def prev_position_qty(self) -> float:
        """获取上一次仓位数量"""
        return self._prev_position_qty
    
    @prev_position_qty.setter
    def prev_position_qty(self, value: float) -> None:
        """设置上一次仓位数量（用于初始化）"""
        self._prev_position_qty = value
