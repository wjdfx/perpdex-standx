"""
网格交易订单管理模块

包含订单检查、取消、同步和成交处理。
"""

import logging
import time
from typing import List

from . import grid_state
from exchanges.order_converter import normalize_order_to_ccxt

logger = logging.getLogger(__name__)


async def check_order_fills(orders: dict):
    """
    检查订单成交情况
    
    Args:
        orders: 订单列表
    """
    trading_state = grid_state.trading_state
    GRID_CONFIG = grid_state.GRID_CONFIG
    OPEN_SIDE_IS_ASK = grid_state.OPEN_SIDE_IS_ASK
    replenish_grid_lock = grid_state.replenish_grid_lock
    
    for order in orders:
        # 从 CCXT 格式提取字段
        client_order_index = str(order.get("clientOrderId") or order.get("id", ""))
        status = order.get("status")
        side = order.get("side", "buy")  # 'buy' or 'sell'
        price = order.get("price", 0)
        filled_amount = float(order.get("filled", 0))
        initial_base_amount = float(order.get("amount", 0))

        is_ask = side == "sell"

        # 判断是开仓侧还是平仓侧订单
        if OPEN_SIDE_IS_ASK:  # 做空策略
            is_open_side_order = is_ask
            is_close_side_order = not is_ask
        else:  # 做多策略
            is_open_side_order = not is_ask
            is_close_side_order = is_ask

        # 过滤非网格订单 (占位订单等)
        if initial_base_amount > GRID_CONFIG["GRID_AMOUNT"]:
            continue
        
        # 如果是已知的占位订单，也忽略 (防止update消息中amount为0导致的误判)
        if client_order_index in trading_state.pause_orders:
            continue

        # 记录是否需要补单，如果不在列表中，有可能是直接成交，则不补单
        replenish = False

        logger.info(
            f"检查订单: ID={client_order_index}, 方向={side}, "
            f"价格={price}, 状态={status}, 成交量={filled_amount}"
        )

        async with replenish_grid_lock:
            if status in ["open"]:
                if is_ask:
                    trading_state.sell_orders[client_order_index] = float(price)
                else:
                    trading_state.buy_orders[client_order_index] = float(price)

            # 如果订单已成交
            if status in ["closed", "filled"] and filled_amount > 0:
                trading_state.filled_count += 1
                trading_state.last_trade_price = float(price)
                
                trading_state.last_filled_order_is_close_side = is_close_side_order
                
                if is_ask:
                    if client_order_index in trading_state.sell_orders:
                        del trading_state.sell_orders[client_order_index]
                        logger.info(
                            f"从活跃卖单订单列表删除订单ID={client_order_index}, 价格={price}"
                        )
                        replenish = True
                else:
                    if client_order_index in trading_state.buy_orders:
                        del trading_state.buy_orders[client_order_index]
                        logger.info(
                            f"从活跃买单订单列表删除订单ID={client_order_index}, 价格={price}"
                        )
                        replenish = True

                # 如果是平仓单（Close Side）成交
                if is_close_side_order and replenish:
                    # 吃掉平仓单时，由于仓位更新推送较慢，先将记录仓位提前降低
                    trading_state.available_position_size = round(
                        trading_state.available_position_size
                        - GRID_CONFIG["GRID_AMOUNT"],
                        2,
                    )

                    # 收到平仓单成交时，证明完成了一次网格套利，记录套利收益
                    once_profit = (
                        trading_state.base_grid_single_price
                        * GRID_CONFIG["GRID_AMOUNT"]
                    )
                    trading_state.active_profit += once_profit
                    trading_state.total_profit += once_profit
                    trading_state.available_reduce_profit += once_profit

        # 在锁范围外补充网格订单
        if replenish:
            from .grid_replenish import replenish_grid
            async with replenish_grid_lock:
                await replenish_grid(True, float(price))
                trading_state.last_replenish_time = time.time()


async def check_current_orders():
    """
    检查当前订单是否合理：
    如果有一侧订单过多，取消最远的订单
    """
    # 优先同步最新订单状态，确保 pause_orders 和 active orders 正确分类
    await _sync_current_orders()
    
    trading_state = grid_state.trading_state
    GRID_CONFIG = grid_state.GRID_CONFIG
    OPEN_SIDE_IS_ASK = grid_state.OPEN_SIDE_IS_ASK
    
    # 如果 Open Side 订单过多，取消最远的订单
    if trading_state.open_orders_count > GRID_CONFIG["GRID_COUNT"] + 1:
        logger.info(f"开仓侧订单过多，删除多余订单")
        cancel_orders = []

        # 排序订单
        # 做多：买单，最远的是最低价，正序排列取前N个
        # 做空：卖单，最远的是最高价，逆序排列取前N个
        reverse_sort = OPEN_SIDE_IS_ASK
        sorted_orders = sorted(
            trading_state.open_orders.items(), 
            key=lambda item: item[1], 
            reverse=reverse_sort
        )

        cancel_count = trading_state.open_orders_count - (GRID_CONFIG["GRID_COUNT"] + 1)
        orders_to_iter = dict(sorted_orders)

        for order_id, price in orders_to_iter.items():
            if len(cancel_orders) < cancel_count:
                cancel_orders.append(order_id)
                logger.info(f"取消最远开仓单，价格={price}, 订单ID={order_id}")
            else:
                break

        await _cancel_orders(cancel_orders)

    # 如果 Close Side 订单过多
    if trading_state.close_orders_count > GRID_CONFIG["MAX_TOTAL_ORDERS"]:
        cancel_orders = []
        
        # 做多：卖单，最远的是最高价，逆序排列
        # 做空：买单，最远的是最低价，正序排列
        reverse_sort = not OPEN_SIDE_IS_ASK
        sorted_orders = sorted(
            trading_state.close_orders.items(), 
            key=lambda item: item[1], 
            reverse=reverse_sort
        )

        cancel_count = trading_state.close_orders_count - GRID_CONFIG["MAX_TOTAL_ORDERS"] + 2

        for order_id, price in dict(sorted_orders).items():
            # 双重保护：如果该订单是占位订单，绝对不取消
            if order_id in trading_state.pause_orders:
                continue
                
            if len(cancel_orders) < cancel_count:
                cancel_orders.append(order_id)
                logger.info(f"取消最远平仓单，价格={price}, 订单ID={order_id}")
            else:
                break

        await _cancel_orders(cancel_orders)

    # 平仓侧订单不能超过持仓量 (Position Sizing check)
    if (
        trading_state.close_orders_count * GRID_CONFIG["GRID_AMOUNT"]
        > trading_state.available_position_size
        and (time.time() - trading_state.start_time) > 60
    ):
        logger.info("平仓单总量超过持仓，进行修剪")
        cancel_orders = []
        
        # 取消最远的订单
        reverse_sort = not OPEN_SIDE_IS_ASK
        sorted_orders = sorted(
            trading_state.close_orders.items(), 
            key=lambda item: item[1], 
            reverse=reverse_sort
        )

        cancel_count = trading_state.close_orders_count - int(
            trading_state.available_position_size / GRID_CONFIG["GRID_AMOUNT"]
        )

        if cancel_count > 0:
            for order_id, price in dict(sorted_orders).items():
                if order_id in trading_state.pause_orders:
                    continue
                    
                if len(cancel_orders) < cancel_count:
                    cancel_orders.append(order_id)
                    logger.info(f"取消最远平仓单(超出持仓)，价格={price}, 订单ID={order_id}")
                else:
                    break
            await _cancel_orders(cancel_orders)

    # 交易暂停清理
    if trading_state.grid_pause:
        if len(trading_state.buy_orders) > 0:
            await _cancel_orders(list(trading_state.buy_orders.keys()))
        if len(trading_state.sell_orders) > 0:
            await _cancel_orders(list(trading_state.sell_orders.keys()))

    # 检查重复订单
    await _check_duplicate_orders(trading_state.buy_orders)
    await _check_duplicate_orders(trading_state.sell_orders)


async def _check_duplicate_orders(orders: dict):
    """
    检查并取消重复价格的订单
    
    Args:
        orders: 订单字典
    """
    if len(orders) > 0:
        cancel_orders = []
        sorted_orders = dict(sorted(orders.copy().items(), key=lambda item: item[1]))
        prev_price = None
        for order_id, price in sorted_orders.items():
            if prev_price is not None and round(price, 4) == round(prev_price, 4):
                cancel_orders.append(order_id)
                logger.info(f"检测到重复价格订单，删除ID={order_id}, 价格={price}")
            prev_price = price
        if len(cancel_orders) > 0:
            await _cancel_orders(cancel_orders)


async def _cancel_orders(cancel_orders: List[int]):
    """
    批量取消订单
    
    Args:
        cancel_orders: 要取消的订单ID列表
    """
    trading_state = grid_state.trading_state
    
    if not cancel_orders:
        return
    success = await trading_state.grid_trading.cancel_grid_orders(cancel_orders)
    if success:
        for order_id in cancel_orders:
            if order_id in trading_state.buy_orders:
                del trading_state.buy_orders[order_id]
            if order_id in trading_state.sell_orders:
                del trading_state.sell_orders[order_id]
        logger.info(f"批量取消订单成功: {len(cancel_orders)}个")


async def _sync_current_orders():
    """
    同步订单状态（通过 REST API 核对当前订单列表）
    """
    trading_state = grid_state.trading_state
    GRID_CONFIG = grid_state.GRID_CONFIG
    CLOSE_SIDE_IS_ASK = grid_state.CLOSE_SIDE_IS_ASK
    
    # 通过 rest api 核对当前订单列表
    orders = await trading_state.grid_trading.get_orders_by_rest()
    if orders is None:
        return

    normalized_orders = (
        [normalize_order_to_ccxt(order) for order in orders]
        if isinstance(orders, list)
        else []
    )

    buy_orders = {}
    sell_orders = {}
    trading_state.pause_positions = {}
    trading_state.pause_orders = {}

    for order in normalized_orders:
        order_id = str(order.get("clientOrderId") or order.get("id", ""))
        side = order.get("side", "buy")
        is_ask = side == "sell"
        price = round(float(order.get("price", 0)), 6)
        status = order.get("status")
        initial_base_amount = float(order.get("amount", 0))

        if status != "open":
            continue

        # 判断订单是否在平仓侧
        is_close_side_order = is_ask == CLOSE_SIDE_IS_ASK

        if is_close_side_order and initial_base_amount > GRID_CONFIG["GRID_AMOUNT"]:
            # 非网格订单，记录为熔断占位订单 (仅平仓方向且数量大于网格单量)
            trading_state.pause_positions[price] = initial_base_amount
            trading_state.pause_orders[order_id] = {
                "price": price,
                "amount": initial_base_amount,
            }
            continue

        if is_ask:
            sell_orders[order_id] = price
        else:
            buy_orders[order_id] = price

    trading_state.buy_orders = buy_orders
    trading_state.sell_orders = sell_orders
