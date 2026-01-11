import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pandas as pd

from . import quota
from .exchanges.standx_adapter import StandXAdapter

logger = logging.getLogger(__name__)


@dataclass
class MakerConfig:
    """
    全局可调参数
    """

    symbol: str
    order_distance_bps: float  # 挂单距离 mark_price 的 bps
    cancel_distance_bps: float  # 价格靠近到这个距离时撤单
    rebalance_distance_bps: float  # 价格远离超过这个距离时撤单重挂
    order_size_btc: float  # 每笔挂单数量
    max_position_btc: float  # 最大持仓，超过则不再挂单
    max_atr: float  # 1m, 14 周期 ATR 上限

    max_orders_per_side: int  # 单侧同时间最大挂单数量
    side_order_gap_bps: float  # 单侧多笔挂单之间的间距（bps）
    fix_order_enable: bool = False  # 是否启用仓位修复单

    atr_period: int = 14
    atr_resolution: str = "1m"
    atr_count_back: int = 100
    atr_refresh_sec: int = 60

    proxy: Optional[str] = None

    @classmethod
    def from_env(cls):
        """从环境变量加载配置"""
        from common.config import (
            STANDX_MAKER_SYMBOL,
            STANDX_MAKER_ORDER_DISTANCE_BPS,
            STANDX_MAKER_CANCEL_DISTANCE_BPS,
            STANDX_MAKER_REBALANCE_DISTANCE_BPS,
            STANDX_MAKER_ORDER_SIZE_BTC,
            STANDX_MAKER_MAX_POSITION_BTC,
            STANDX_MAKER_MAX_ATR,
            STANDX_MAKER_MAX_ORDERS_PER_SIDE,
            STANDX_MAKER_SIDE_ORDER_GAP_BPS,
            STANDX_MAKER_FIX_ORDER_ENABLED,
        )
        return cls(
            symbol=STANDX_MAKER_SYMBOL,
            order_distance_bps=STANDX_MAKER_ORDER_DISTANCE_BPS,
            cancel_distance_bps=STANDX_MAKER_CANCEL_DISTANCE_BPS,
            rebalance_distance_bps=STANDX_MAKER_REBALANCE_DISTANCE_BPS,
            order_size_btc=STANDX_MAKER_ORDER_SIZE_BTC,
            max_position_btc=STANDX_MAKER_MAX_POSITION_BTC,
            max_atr=STANDX_MAKER_MAX_ATR,
            max_orders_per_side=STANDX_MAKER_MAX_ORDERS_PER_SIDE,
            side_order_gap_bps=STANDX_MAKER_SIDE_ORDER_GAP_BPS,
            fix_order_enable=STANDX_MAKER_FIX_ORDER_ENABLED,
        )


class OnlyMakerStrategy:
    """
    StandX 双边挂单做市策略：尽量挂单但避免被吃，价格靠近则撤单并重挂。
    """

    def __init__(self, adapter: StandXAdapter, config: MakerConfig):
        self.adapter = adapter
        self.cfg = config

        self.mark_price: Optional[float] = None
        self.position_qty: float = 0.0
        self.current_atr: Optional[float] = None

        # open_orders: {"bid": [{"id": str, "price": float}, ...], "ask": [...]}
        self.open_orders: Dict[str, List[Dict[str, Any]]] = {"bid": [], "ask": []}
        self.fix_order: Dict[str, Any] = None

        self._lock = asyncio.Lock()
        self._running = False
        self._atr_task: Optional[asyncio.Task] = None
        self._order_sync_task: Optional[asyncio.Task] = None
        self._market_stats_monitor_task: Optional[asyncio.Task] = None
        self.last_market_stats_time: Optional[float] = None

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------
    async def start(self):
        """初始化并开始策略。"""
        self._running = True
        await self.adapter.initialize_client()

        callbacks = {
            "market_stats": self.on_market_stats,
            "orders": self.on_orders,
            "positions": self.on_positions,
        }
        await self.adapter.subscribe(callbacks, proxy=self.cfg.proxy)

        # 启动 ATR 周期任务
        self._atr_task = asyncio.create_task(self._atr_loop())
        # 定时拉取 REST 订单快照，避免 WS 漏单
        self._order_sync_task = asyncio.create_task(self._check_status_loop())
        # 启动市场数据监控任务
        self._market_stats_monitor_task = asyncio.create_task(self._market_stats_monitor_loop())
        logger.info("OnlyMakerStrategy started")

    async def stop(self):
        """停止策略，撤单并关闭资源。"""
        self._running = False
        await self.cancel_all()
        if self._atr_task:
            self._atr_task.cancel()
            try:
                await self._atr_task
            except asyncio.CancelledError:
                pass
        if self._order_sync_task:
            self._order_sync_task.cancel()
            try:
                await self._order_sync_task
            except asyncio.CancelledError:
                pass
        if self._market_stats_monitor_task:
            self._market_stats_monitor_task.cancel()
            try:
                await self._market_stats_monitor_task
            except asyncio.CancelledError:
                pass
        await self.adapter.close()
        logger.info("OnlyMakerStrategy stopped")

    # ------------------------------------------------------------------
    # 回调
    # ------------------------------------------------------------------
    async def on_market_stats(self, market_id: str, stats: Dict[str, Any]):
        """行情回调，更新 mark_price 并驱动挂单逻辑。"""
        try:
            mark_price = stats.get("mark_price") or stats.get("last_price")
            if mark_price is None:
                return
            self.mark_price = float(mark_price)
            self.last_market_stats_time = time.time()
            logger.debug(f"当前价格：{mark_price}")
            await self._reconcile_orders()
        except Exception as exc:
            logger.exception(f"on_market_stats error: {exc}")

    async def on_orders(self, account_id: str, orders: Any):
        """订单回调，更新本地 open_orders 状态。"""
        try:
            logger.debug(f"on orders: {orders}")
        except Exception as exc:
            logger.exception(f"on_orders error: {exc}")

    async def on_positions(self, account_id: str, positions: Dict[str, Any]):
        """持仓回调，记录当前仓位大小。"""
        try:
            logger.info(f"仓位信息: {positions}")
            if not isinstance(positions, dict):
                return
            pos = positions.get(self.cfg.symbol) or positions.get(self.cfg.symbol.replace("-", "/"))
            if not pos:
                return
            qty = pos.get("qty")
            if qty is not None:
                self.position_qty = float(qty)
            
            if self.position_qty == 0 and self.fix_order is not None:
                # 无仓位时撤掉修复单
                cancel_order_ids = [self.fix_order['id']]
                await self.adapter.cancel_grid_orders(cancel_order_ids)
                logger.info(f"无仓位，修复订单撤单: {self.fix_order}")
                self.fix_order = None
                
            
        except Exception as exc:
            logger.exception(f"on_positions error: {exc}")

    # ------------------------------------------------------------------
    # 订单同步
    # ------------------------------------------------------------------
    async def _refresh_open_orders(self, orders: List[Dict[str, Any]]):
        """用最新订单列表刷新本地 open_orders。"""
        async with self._lock:
            bid_orders: List[Dict[str, Any]] = []
            ask_orders: List[Dict[str, Any]] = []
            
            if len(orders) == 0:
                return
            
            for order in orders or []:
                try:
                    logger.debug(f"order: {order}")
                    client_order_id = str(order.get("clientOrderId"))
                    if not client_order_id.startswith("maker_"):
                        if client_order_id.startswith("fix_"):
                            # 修复单
                            self.fix_order = {
                                "id": order.get("id"),
                                "price": float(order.get("price", 0)),
                                "amount": float(order.get("amount", 0)),
                            }
                        continue
                    symbol = (order.get("symbol") or "").replace("/", "-")
                    if symbol != self.cfg.symbol:
                        continue
                    status = (order.get("status") or "").lower()
                    if status not in {"open", "new"}:
                        continue
                    side = (order.get("side") or "").lower()
                    view = self._order_view(order)
                    if not view:
                        continue
                    if side == "buy":
                        bid_orders.append(view)
                    elif side == "sell":
                        ask_orders.append(view)
                except Exception:
                    continue

            bid_orders.sort(key=lambda x: x["price"], reverse=True)
            ask_orders.sort(key=lambda x: x["price"])

            self.open_orders["bid"] = bid_orders
            self.open_orders["ask"] = ask_orders
            
            logger.info(f"同步订单：{self.open_orders}")

    async def _check_status_loop(self):
        """每 30 秒通过 REST 全量同步订单，防止 WS 漏单。"""
        # 首次启动延迟 30s 再拉取
        try:
            await asyncio.sleep(20)
        except asyncio.CancelledError:
            return

        while self._running:
            try:
                # 同步订单
                orders = await self.adapter.get_orders_by_rest()
                await self._refresh_open_orders(orders)
                
                # 检查价格距离
                for o in self.open_orders["bid"]:
                    delta_bps = abs(o["price"] - self.mark_price) / self.mark_price * 10000
                    logger.info(f"当前买单, 价格：{o["price"]}, bps：{delta_bps}")
                for o in self.open_orders["ask"]:
                    delta_bps = abs(o["price"] - self.mark_price) / self.mark_price * 10000
                    logger.info(f"当前卖单, 价格：{o["price"]}, bps：{delta_bps}")
                logger.info(f"当前价格：{self.mark_price}, 持仓：{self.position_qty}, ATR：{self.current_atr}")
                
                # 仓位修复检查
                if self.cfg.fix_order_enable:
                    positions = await self.adapter.get_positions()
                    pos = positions.get(self.cfg.symbol) or positions.get(self.cfg.symbol.replace("-", "/"))
                    if not pos:
                        return
                    qty = pos.get("qty")
                    if qty is not None:
                        self.position_qty = float(qty)
                        
                    if self.position_qty != 0:
                        if self.fix_order is not None:
                            if self.fix_order['amount'] == abs(self.position_qty):
                                break
                            cancel_order_ids = [self.fix_order['id']]
                            await self.adapter.cancel_grid_orders(cancel_order_ids)
                            logger.info(f"原修复订单撤单: {self.fix_order}")
                        
                        await self._place_fix_order(pos)
                        
                    if self.position_qty == 0 and self.fix_order is not None:
                        # 无仓位时撤掉修复单
                        cancel_order_ids = [self.fix_order['id']]
                        await self.adapter.cancel_grid_orders(cancel_order_ids)
                        logger.info(f"无仓位，修复订单撤单: {self.fix_order}")
                        self.fix_order = None
                    
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception(f"订单同步任务异常: {exc}")
            await asyncio.sleep(20)
            
    async def _place_fix_order(self, pos: Dict[str, Any]):
        entry_price = float(pos.get("entry_price"))
        # 挂一个回本单，如果当前价格已过，则挂到当前价格附近，只挂maker单
        is_ask = self.position_qty > 0
        if is_ask:
            # 卖单
            if self.mark_price > entry_price:
                entry_price = self.mark_price
            target_price = entry_price * (1 + 0.0002)
        else:
            # 买单
            if self.mark_price < entry_price:
                entry_price = self.mark_price
            target_price = entry_price * (1 - 0.0002)
            
        price = round(target_price, 2)
        client_order_id = f"fix_{int(time.time() * 1000) % 1000000}"
        success, order_id = await self.adapter.place_single_order(is_ask, price, self.position_qty, client_order_id)
        if success:
            order_info = {"id": order_id, "price": price, "amount": self.position_qty}
            self.fix_order = order_info
            logger.info(f"修复订单挂单成功: {order_info}, size={self.position_qty}, 当前价格: {self.mark_price}")
        else:
            logger.warning(f"修复订单挂单失败: {order_info}, size={self.position_qty}")

    # ------------------------------------------------------------------
    # 核心逻辑
    # ------------------------------------------------------------------
    async def _reconcile_orders(self):
        """根据当前行情、ATR、仓位，决定挂单/撤单/重挂。"""
        async with self._lock:
            if self.mark_price is None:
                return

            # 风控：ATR、仓位
            if self.current_atr is not None and self.current_atr > self.cfg.max_atr:
                logger.info(f"ATR 超阈值，撤单并暂停挂单, current atr: {self.current_atr}")
                await self.cancel_all()
                return
            if abs(self.position_qty) >= self.cfg.max_position_btc:
                logger.info("持仓超限，撤单并暂停挂单")
                await self.cancel_all()
                return

            target_bids = self._target_prices(is_ask=False)
            target_asks = self._target_prices(is_ask=True)

            # # 先检查是否需要撤单/裁剪
            await self._maybe_cancel_side("bid")
            await self._maybe_cancel_side("ask")

            # 决定是否补挂
            await self._ensure_side_orders("bid", is_ask=False, target_prices=target_bids)
            await self._ensure_side_orders("ask", is_ask=True, target_prices=target_asks)

    async def _maybe_cancel_side(self, side: str):
        orders = list(self.open_orders.get(side) or [])
        if not orders:
            return

        kept: List[Dict[str, Any]] = []
        for order in orders:
            delta_bps = abs(order["price"] - self.mark_price) / self.mark_price * 10000
            if delta_bps <= self.cfg.cancel_distance_bps or delta_bps >= self.cfg.rebalance_distance_bps:
                logger.info(f"{side} 撤单，delta_bps={delta_bps:.2f} price={order['price']}, 当前价格: {self.mark_price}")
                await self.cancel_order(order["id"])
            else:
                kept.append(order)

        # 超过最大允许数量时，保留距离较远的订单，取消距离近的风险单
        max_allowed = self.cfg.max_orders_per_side
        if len(kept) > max_allowed:
            kept_sorted = sorted(kept, key=lambda o: abs(o["price"] - self.mark_price), reverse=True)
            to_keep = kept_sorted[:max_allowed]
            to_cancel = kept_sorted[max_allowed:]
            for order in to_cancel:
                logger.info(f"{side} 撤单（超额裁剪） price={order['price']}")
                await self.cancel_order(order["id"])
            kept = to_keep

        if side == "bid":
            kept.sort(key=lambda x: x["price"], reverse=True)
        else:
            kept.sort(key=lambda x: x["price"])
        self.open_orders[side] = kept

    async def _ensure_side_orders(self, side: str, is_ask: bool, target_prices: List[float]):
        if not target_prices:
            return

        existing = list(self.open_orders.get(side) or [])
        side_sign = -self.cfg.order_size_btc if is_ask else self.cfg.order_size_btc

        # logger.info(f"下单前存在订单: {self.open_orders}")
        for target_price in target_prices:
            if any(round(o["price"], 2) == round(target_price, 2) for o in existing):
                continue
            if len(existing) >= self.cfg.max_orders_per_side:
                break

            # expected_pos = self.position_qty + side_sign * (len(existing) + 1)
            # if abs(expected_pos) > self.cfg.max_position_btc:
            #     logger.info(f"{side} 不挂单，预期持仓超限: expected_pos={expected_pos}")
            #     break

            price = round(target_price, 2)
            client_order_id = f"maker_{int(time.time() * 1000) % 1000000}"
            success, order_id = await self.adapter.place_single_order(is_ask, price, self.cfg.order_size_btc, client_order_id)
            if success:
                order_info = {"id": order_id, "price": price}
                existing.append(order_info)
                logger.info(f"{side} 挂单成功 price={price} size={self.cfg.order_size_btc}, 当前价格: {self.mark_price}")
            else:
                logger.warning(f"{side} 挂单失败 price={price} size={self.cfg.order_size_btc}")

        if side == "bid":
            existing.sort(key=lambda x: x["price"], reverse=True)
        else:
            existing.sort(key=lambda x: x["price"])
        self.open_orders[side] = existing
        # logger.info(f"下单后存在订单: {self.open_orders}")

    def _target_prices(self, is_ask: bool) -> List[float]:
        """生成当前侧需要挂单的目标价列表（含间距约束）。"""
        if self.mark_price is None:
            return []
        targets: List[float] = []
        base_bps = self.cfg.order_distance_bps
        gap_bps = max(self.cfg.side_order_gap_bps, 0)

        for idx in range(self.cfg.max_orders_per_side):
            offset_bps = base_bps + idx * gap_bps
            price = self.mark_price * (1 + offset_bps / 10000) if is_ask else self.mark_price * (1 - offset_bps / 10000)
            targets.append(round(price, 2))
        return targets

    # ------------------------------------------------------------------
    # 市场数据监控
    # ------------------------------------------------------------------
    async def _market_stats_monitor_loop(self):
        """监控市场数据更新，超过30秒未更新则重新连接WS。"""
        while self._running:
            try:
                await asyncio.sleep(10)  # 每10秒检查一次
                
                if self.last_market_stats_time is None:
                    continue
                    
                current_time = time.time()
                time_since_last_update = current_time - self.last_market_stats_time
                
                if time_since_last_update > 30:
                    logger.warning(f"市场数据超过30秒未更新，最后更新时间: {self.last_market_stats_time}, 当前时间: {current_time}")
                    await self._reconnect_ws_and_resubscribe()
                    
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception(f"市场数据监控任务异常: {exc}")

    async def _reconnect_ws_and_resubscribe(self):
        """重新连接WS并重新订阅。"""
        try:
            logger.info("开始重新连接WS并重新订阅...")
            
            # 关闭当前WS连接
            await self.adapter.close()
            
            # 重新初始化客户端
            await self.adapter.initialize_client()
            
            # 重新订阅
            callbacks = {
                "market_stats": self.on_market_stats,
                "orders": self.on_orders,
                "positions": self.on_positions,
            }
            await self.adapter.subscribe(callbacks, proxy=self.cfg.proxy)
            
            logger.info("WS重新连接并订阅成功")
            
        except Exception as exc:
            logger.exception(f"重新连接WS并重新订阅失败: {exc}")

    # ------------------------------------------------------------------
    # ATR 计算
    # ------------------------------------------------------------------
    async def _atr_loop(self):
        while self._running:
            try:
                df = await self.adapter.candle_stick(
                    market_id=self.adapter.market_id,
                    resolution=self.cfg.atr_resolution,
                    count_back=self.cfg.atr_count_back,
                )
                if isinstance(df, pd.DataFrame) and not df.empty:
                    atr_series = quota.compute_atr(df, period=self.cfg.atr_period)
                    if not atr_series.empty:
                        self.current_atr = float(atr_series.iloc[-1])
                        logger.info(f"ATR 更新: {self.current_atr:.4f}")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception(f"ATR loop error: {exc}")
            await asyncio.sleep(self.cfg.atr_refresh_sec)

    # ------------------------------------------------------------------
    # 撤单辅助
    # ------------------------------------------------------------------
    async def cancel_order(self, order_id: str):
        try:
            await self.adapter.cancel_grid_orders([order_id])
        except Exception as exc:
            logger.exception(f"cancel_order error: {exc}")

    async def cancel_all(self):
        order_ids = [o["id"] for side_orders in self.open_orders.values() for o in side_orders if o]
        if not order_ids:
            return
        try:
            await self.adapter.cancel_grid_orders(order_ids)
        finally:
            self.open_orders["bid"] = []
            self.open_orders["ask"] = []

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    @staticmethod
    def _order_view(order: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not order:
            return None
        try:
            return {
                "id": order.get("clientOrderId") or order.get("cl_ord_id") or order.get("id"),
                "price": float(order.get("price", 0)),
            }
        except Exception:
            return None


async def main():
    from common.logging_config import setup_logging

    setup_logging("only_maker")

    adapter = StandXAdapter(
        market_id=1,
        wallet_address=None,
        private_key=None,
        chain="bsc",
    )

    config = MakerConfig.from_env()
    strategy = OnlyMakerStrategy(adapter, config)

    await strategy.start()

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt, stopping...")
    finally:
        await strategy.stop()


if __name__ == "__main__":
    asyncio.run(main())
