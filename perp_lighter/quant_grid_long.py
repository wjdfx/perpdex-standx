from common.config import (
    BASE_URL,
    API_KEY_PRIVATE_KEY,
    ACCOUNT_INDEX,
    API_KEY_INDEX,
)

import logging
from common.logging_config import setup_logging

# 配置日志
setup_logging()
logger = logging.getLogger(__name__)

import json
import asyncio
import time
import pandas as pd
from typing import Dict, List, Optional, Set, Tuple
import lighter
from lighter.signer_client import CODE_OK
from .ws_client import create_unified_client
from .grid_matin import GridTrading
from collections import deque
from typing import Deque
from common.sqlite import init_db, insert


# 网格交易参数配置
GRID_CONFIG = {
    "GRID_COUNT": 3,  # 每侧网格数量
    "GRID_AMOUNT": 0.01,  # 单网格挂单量
    "GRID_SPREAD": 0.05,  # 单网格价差（百分比）
    "MAX_TOTAL_ORDERS": 10,  # 最大活跃订单数量
    "MAX_POSITION": 0.8,  # 最大仓位限制
    "DECREASE_POSITION": 0.4,  # 降低仓位触发点
    "ALER_POSITION": 0.2,  # 警告仓位限制
    "MARKET_ID": 0,  # 市场ID
    "ATR_THRESHOLD": 7,  # ATR波动阈值
    "DB_PATH": "data/quant_grid_long.db",  # SQLite数据库路径
    "DB_TABLE_NAME": "grid_pause_records",  # 熔断记录表
}


# 全局状态
class GridTradingState:
    def __init__(self):
        self.current_price: Optional[float] = None
        self.is_running: bool = False
        self.grid_trading: Optional[GridTrading] = None  # 网格交易实例
        self.buy_prices: List[float] = []  # 买单价格列表（升序）
        self.sell_prices: List[float] = []  # 卖单价格列表（升序）
        self.buy_orders: dict[str, float] = {}  # 买单订单ID到价格映射
        self.sell_orders: dict[str, float] = {}  # 卖单订单ID到价格映射
        self.original_buy_prices: List[float] = []  # 原始买单价格序列
        self.original_sell_prices: List[float] = []  # 原始卖单价格序列
        self.base_grid_single_price: float = 0  # 单网格价差值
        self.active_grid_signle_price: float = 0  # 动态单网格价差值
        self.start_collateral: float = 0  # 初始保证金
        self.current_collateral: float = 0  # 当前保证金
        self.start_time: float = time.time()  # 启动时间
        self.open_price: Optional[float] = None  # 启动时基准价格
        self.last_filled_order_is_ask: bool = True  # 上次成交订单方向
        self.last_replenish_time: float = 0  # 上次补单时间
        self.last_trade_price: float= 0  # 上次成交价格
        self.grid_pause: bool = False  # 网格交易暂停标志
        self.grid_sell_spread_alert: bool = False  # 卖单警告价差状态
        self.grid_buy_spread_alert: bool = False  # 买单警告价差状态
        self.grid_decrease_status: bool = False  # 降低仓位状态
        self.current_position_size: float = 0  # 当前仓位大小
        self.current_position_sign: int = 0  # 当前仓位方向
        self.filled_count: int = 0  # 成交订单计数
        self.candle_stick_1m: pd.DataFrame = None  # 1分钟K线数据
        self.current_atr: float = 0.0  # 当前ATR值
        self.grid_profit: float = 0.0  # 网格净收益(部分收益会用来减仓)
        self.pause_positions: dict[float, float] = {}  # 熔断时的仓位映射
        self.pause_position_exist: bool = False  # 记录本次是否已经进行了熔断占位仓位下单
        self.available_position_size: float = 0.0  # 可用仓位
        self.active_profit: float = 0.0  # 动态网格收益
        self.total_profit: float = 0.0  # 本次运行总收益


# 全局状态实例
trading_state = GridTradingState()

# 全局异步锁，用于保护 replenish_grid() 方法
replenish_grid_lock = asyncio.Lock()


async def on_market_stats_update(market_id: str, market_stats: dict):
    """
    处理市场统计数据更新
    """
    global trading_state

    mark_price = float(market_stats.get("mark_price"))
    if mark_price:
        trading_state.current_price = mark_price

        cs_1m = trading_state.candle_stick_1m
        if trading_state.grid_trading is not None and cs_1m is not None:
            try:
                is_jidie, jidie_details = await trading_state.grid_trading.is_jidie(
                    cs_1m, close=mark_price
                )
                if is_jidie:
                    min_step = trading_state.base_grid_single_price
                    max_step = (
                        trading_state.base_grid_single_price * 30
                    )  # 即使天塌下来，间距也不能超过（防止ATR计算出错导致不挂单）

                    raw_step = 0.8 * round(jidie_details.get("atr"), 2)
                    trading_state.active_grid_signle_price = max(
                        min_step, min(raw_step, max_step)
                    )
            except Exception as e:
                logger.error(f"Error checking jidie in market stats update: {e}")


async def on_account_all_orders_update(account_id: str, orders: dict):
    """
    处理账户所有订单更新
    注意：这是订单状态变化的更新，不是获取所有当前订单
    """
    global trading_state

    if account_id != str(ACCOUNT_INDEX):
        return

    # logger.info(
    #     f"🔄 收到订单更新通知，订单数量: {sum(len(market_orders) for market_orders in orders.values())}"
    # )

    # 检查是否有订单成交
    await check_order_fills(orders)


async def on_account_all_positions_update(account_id: str, positions: dict):
    """
    处理账户所有仓位更新
    """
    if account_id != str(ACCOUNT_INDEX):
        return

    # 检查仓位是否超出限制
    await check_position_limits(positions)


#######################################################
# 仓位管理部分
#######################################################
async def _cal_position_highest_order_price() -> float:
    """
    计算当前仓位最高一单的价格: 当前价格+(基础间距x仓位)
    """
    global trading_state

    highest_price = (
        trading_state.current_price
        + trading_state.base_grid_single_price * trading_state.available_position_size
    )
    return highest_price


async def _highest_order_lost() -> float:
    """
    计算当前仓位最高一单距离当前价格浮亏多少
    """
    higest_price = _cal_position_highest_order_price()
    return (higest_price - trading_state.current_price) * GRID_CONFIG["GRID_AMOUNT"]


async def _reduce_position():
    """
    降低仓位的逻辑
    """
    global trading_state

    if not trading_state.grid_decrease_status:
        return

    highest_lost = _highest_order_lost()
    if trading_state.grid_profit <= highest_lost:
        # 当前总收益不够降仓
        return

    success, order_id = await trading_state.grid_trading.place_reduce_order(
        True, GRID_CONFIG["GRID_AMOUNT"]
    )
    if success:
        trading_state.grid_profit = trading_state.grid_profit - highest_lost
        logger.info(
            f"降低仓位成功，当前价格：{trading_state.current_price}, 当前总收益：{trading_state.grid_profit}"
        )


#######################################################


async def check_order_fills(orders: dict):
    """
    检查订单成交情况
    """
    global trading_state

    for market_orders in orders.values():
        for order in market_orders:
            # order_id = order.get("order_id")
            client_order_index = int(order.get("client_order_index"))
            status = order.get("status")
            is_ask = order.get("is_ask", "N/A")
            price = order.get("price", "N/A")
            filled_amount = float(order.get("filled_base_amount", 0))
            initial_base_amount = float(order.get("initial_base_amount", 0))
            
            if initial_base_amount > GRID_CONFIG["GRID_AMOUNT"]:
                # 过滤非网格订单
                continue

            logger.info(
                f"检查订单: ID={client_order_index}, 方向={is_ask}, 价格={price}, 状态={status}, 成交量={filled_amount}"
            )

            async with replenish_grid_lock:
                if status in ["open"]:
                    if is_ask:
                        trading_state.sell_orders[client_order_index] = float(price)
                    else:
                        trading_state.buy_orders[client_order_index] = float(price)

                # 如果订单已成交
                if status in ["filled"] and filled_amount > 0:
                    trading_state.filled_count += 1
                    
                    trading_state.last_trade_price = float(price)

                    # 记录是否需要补单，如果不在列表中，有可能是直接成交，则不补单
                    replenish = False
                    trading_state.last_filled_order_is_ask = is_ask

                    if is_ask:
                        if client_order_index in trading_state.sell_orders:
                            del trading_state.sell_orders[client_order_index]
                            logger.info(
                                f"从活跃卖单订单列表删除订单ID={client_order_index}, 价格={price}"
                            )
                            replenish = True

                            # 吃掉卖单时，由于仓位更新推送较慢，先将记录仓位提前降低，等待仓位更新后再调整可用仓位
                            trading_state.available_position_size = round(
                                trading_state.available_position_size - GRID_CONFIG["GRID_AMOUNT"], 2
                            )
                            
                            # 收到卖单成交时，证明完成了一次网格套利，记录套利收益
                            trading_state.active_profit += trading_state.base_grid_single_price * GRID_CONFIG["GRID_AMOUNT"]
                            trading_state.total_profit += trading_state.base_grid_single_price * GRID_CONFIG["GRID_AMOUNT"]
                            
                    else:
                        if client_order_index in trading_state.buy_orders:
                            del trading_state.buy_orders[client_order_index]
                            logger.info(
                                f"从活跃买单订单列表删除订单ID={client_order_index}, 价格={price}"
                            )
                            replenish = True

                    # 补充网格订单
                    if replenish:
                        await replenish_grid(True)
                        trading_state.last_replenish_time = time.time()


def calculate_grid_prices(
    current_price: float, grid_count: int, grid_spread: float
) -> List[float]:
    """
    计算网格价格列表
    订单以GRID_SPREAD的价差比例，均匀分布在当前价格上下两侧，
    最贴近当前价格的买单和卖单之间的距离是两倍价差。
    """
    buy_prices = []

    # 价差比例（百分比转换为小数）
    spread_decimal = grid_spread / 100

    # 计算网格价格
    # 最贴近当前价格的买单和卖单之间的距离是2倍价差
    # 所以每个订单距离当前价格是1倍价差（对称分布）
    for i in range(grid_count):
        # 买单价格：当前价格下方
        # 第一个买单距离 = 1 * spread，第二个 = 2 * spread，以此类推
        distance = (i + 1) * spread_decimal
        buy_price = current_price * (1 - distance)
        buy_prices.append(round(buy_price, 2))

    return buy_prices


async def check_position_limits(positions: dict):
    """
    检查仓位是否超出限制
    """
    global trading_state

    if len(trading_state.original_buy_prices) == 0:
        logger.info("等待初始化完成...")
        return

    for market_id, position in positions.items():
        position_size = round(abs(float(position.get("position", 0))), 2)
        trading_state.current_position_size = position_size
        current_pause_position = await _get_current_pause_position()
        trading_state.available_position_size = round(trading_state.current_position_size - current_pause_position, 2)
        
        sign = int(position.get("sign", "0"))
        alert_pos = GRID_CONFIG["ALER_POSITION"]
        decrease_position = GRID_CONFIG["DECREASE_POSITION"]
        # direction = "多头" if sign > 0 else "空头"
        # logger.info(f"📊 当前仓位: {position_size}, 方向: {direction}")
        if position_size == 0:
            return
        # 当仓位到了警戒线时，触发挂单倾斜，将单边挂单网格距离增大
        if position_size >= alert_pos and position_size < decrease_position:
            # logger.warning(
            #     f"⚠️ 警告：仓位接近限制，已触发挂单倾斜: 市场={market_id}, 当前={position_size}, 警告={alert_pos}"
            # )
            if sign > 0:
                # 多头仓位
                trading_state.grid_buy_spread_alert = True
            else:
                # 空头仓位
                trading_state.grid_sell_spread_alert = True

            # logger.info("当前处于警告价差状态，补单间距加倍")
            # trading_state.base_grid_single_price = (
            #     trading_state.original_buy_prices[1]
            #     - trading_state.original_buy_prices[0]
            # ) * 2
            trading_state.grid_decrease_status = False
        elif position_size >= decrease_position:
            # logger.warning(
            #     f"⚠️ 警告：仓位超出降低点，开始降低仓位: 市场={market_id}, 当前={position_size}, 降低点={decrease_position}"
            # )
            trading_state.grid_decrease_status = True
        else:
            trading_state.grid_buy_spread_alert = False
            trading_state.grid_sell_spread_alert = False
            trading_state.base_grid_single_price = (
                trading_state.original_buy_prices[1]
                - trading_state.original_buy_prices[0]
            )
            trading_state.grid_decrease_status = False

        max_pos = GRID_CONFIG["MAX_POSITION"]
        if position_size > max_pos:
            logger.warning(
                f"⚠️ 仓位超出限制: 市场={market_id}, 当前={position_size}, 限制={max_pos}"
            )
            # 网格交易暂停
            trading_state.grid_pause = True


async def replenish_grid(filled_signal: bool):
    """
    补充网格订单逻辑
    基于原始订单价格分布和当前价格，计算补充订单的价格和方向
    """

    global trading_state

    logger.info("🔄 检查并补充网格订单中...")
    
    if trading_state.grid_pause:
        logger.info("网格交易处于暂停状态，跳过补单")
        return

    if len(trading_state.buy_orders) == 0 and len(trading_state.sell_orders) == 0:
        # 初始化网格交易
        if not await initialize_grid_trading(trading_state.grid_trading):
            logger.error("网格交易初始化失败，退出")
            return

    try:
        if filled_signal:
            # 买单侧被吃单
            await _buy_side_filled_order()
            # 卖单侧被吃单
            await _sell_side_filled_order()

        # 大间距补单
        await _over_range_replenish_order()

        # 卖单侧补充不少于配置单的数量，补充卖单不能触及到熔断前的仓位
        if trading_state.available_position_size > 0:
            await _sell_side_replenish_config_orders()

    except Exception:
        logger.exception(f"补充网格订单时发生错误")


async def _buy_side_filled_order():
    """
    买单侧被吃单到需要补单时
    """
    global trading_state

    if trading_state.last_filled_order_is_ask:
        return

    logger.info("买单侧被吃单补单")
    # 买单侧被吃单补充买单
    if (
        not trading_state.grid_pause
        and len(trading_state.buy_orders) < GRID_CONFIG["GRID_COUNT"]
    ):
        await _buy_side_replenish_buy_order()

    # 买单侧被吃单补充卖单
    await _buy_side_replenish_sell_order()


async def _buy_side_replenish_buy_order():
    """
    买单侧被吃单到补充买单
    """
    global trading_state

    low_buy_price = (
        trading_state.current_price - trading_state.active_grid_signle_price
    )
    if len(trading_state.buy_orders) > 0:
        low_buy_price = min(trading_state.buy_orders.values())

    new_buy_price = round(low_buy_price - trading_state.active_grid_signle_price, 2)
    while new_buy_price >= trading_state.current_price:
        new_buy_price = round(
            new_buy_price - trading_state.active_grid_signle_price, 2
        )
    # 执行订单补充
    success, order_id = await trading_state.grid_trading.place_single_order(
        is_ask=False,
        price=new_buy_price,
        amount=GRID_CONFIG["GRID_AMOUNT"],
    )
    if success:
        trading_state.buy_orders[order_id] = new_buy_price
        logger.info(
            f"买单侧被吃单补充买单订单成功: 价格={new_buy_price}, 订单ID={order_id}"
        )


async def _buy_side_replenish_sell_order():
    """
    买单侧被吃单到补充卖单
    """
    global trading_state

    buy_orders_prices = sorted(list(trading_state.buy_orders.values()))

    high_buy_price = (
        trading_state.current_price - trading_state.active_grid_signle_price
    )
    if len(buy_orders_prices) > 0:
        high_buy_price = buy_orders_prices[-1]

    new_sell_price = round(
        high_buy_price + trading_state.active_grid_signle_price * 2, 2
    )
    if len(trading_state.sell_orders) > 0:
        new_sell_price = (
            min(trading_state.sell_orders.values())
            - trading_state.active_grid_signle_price
        )

    # 补单价格离当前价格过远，调整为最高买单价格上方2倍单网格价差
    if (
        new_sell_price - trading_state.current_price
        > trading_state.active_grid_signle_price * 2
    ):
        new_sell_price = round(
            high_buy_price + trading_state.active_grid_signle_price * 2, 2
        )

    # 当前价格超过新补单价格时，不补单
    if trading_state.current_price < new_sell_price:
        # 执行订单补充
        success, order_id = await trading_state.grid_trading.place_single_order(
            is_ask=True,
            price=new_sell_price,
            amount=GRID_CONFIG["GRID_AMOUNT"],
        )
        if success:
            # 更新sell_orders_prices而不是trading_state.sell_prices
            trading_state.sell_orders[order_id] = new_sell_price
            logger.info(
                f"买单侧被吃单补充卖单订单成功: 价格={new_sell_price}, 订单ID={order_id}"
            )


async def _sell_side_filled_order():
    """
    卖单侧被吃单到需要补单时
    """
    global trading_state

    if not trading_state.last_filled_order_is_ask:
        return

    logger.info("卖单侧被吃单补单")
    # 卖单侧被吃单到补充买单
    if not trading_state.grid_pause:
        await _sell_side_replenish_buy_order()
        
    # 卖单侧被吃单到补充卖单
    if (
        trading_state.available_position_size > (len(trading_state.sell_orders) + 1) * GRID_CONFIG["GRID_AMOUNT"]
        and len(trading_state.sell_orders) > 0
        and trading_state.current_position_sign > 0
    ):
        await _sell_side_replenish_sell_order()


async def _sell_side_replenish_buy_order():
    """
    卖单侧被吃单到补充买单
    """
    global trading_state

    high_buy_price = (
        trading_state.current_price - trading_state.active_grid_signle_price
    )
    if len(trading_state.buy_orders) > 0:
        high_buy_price = max(trading_state.buy_orders.values())

    new_buy_price = round(high_buy_price + trading_state.active_grid_signle_price, 2)
    # 执行订单补充
    success, order_id = await trading_state.grid_trading.place_single_order(
        is_ask=False,
        price=new_buy_price,
        amount=GRID_CONFIG["GRID_AMOUNT"],
    )
    if success:
        trading_state.buy_orders[order_id] = new_buy_price
        logger.info(
            f"卖单侧被吃单补充买单订单成功: 价格={new_buy_price}, 订单ID={order_id}"
        )


async def _sell_side_replenish_sell_order():
    """
    卖单侧被吃单到补充卖单
    """
    global trading_state

    high_buy_price = (
        trading_state.current_price - trading_state.active_grid_signle_price
    )
    if len(trading_state.buy_orders) > 0:
        high_buy_price = max(trading_state.buy_orders.values())
    new_sell_price = round(
        high_buy_price + trading_state.active_grid_signle_price * 2, 2
    )
    if len(trading_state.sell_orders) > 0:
        new_sell_price = (
            max(trading_state.sell_orders.values())
            + trading_state.active_grid_signle_price
        )

    # 执行订单补充
    success, order_id = await trading_state.grid_trading.place_single_order(
        is_ask=True,
        price=new_sell_price,
        amount=GRID_CONFIG["GRID_AMOUNT"],
    )
    if success:
        trading_state.sell_orders[order_id] = new_sell_price
        logger.info(
            f"卖单侧被吃单补充卖单订单成功: 价格={new_sell_price}, 订单ID={order_id}"
        )


async def _over_range_replenish_order():
    """
    大间距补单逻辑
    """
    if trading_state.grid_pause:
        return
    low_sell_price = (
        trading_state.current_price + trading_state.active_grid_signle_price * 2
    )
    if len(trading_state.sell_orders) > 0:
        low_sell_price = min(trading_state.sell_orders.values())

    high_buy_price = (
        trading_state.current_price - trading_state.active_grid_signle_price * 2
    )
    if len(trading_state.buy_orders) > 0:
        high_buy_price = max(trading_state.buy_orders.values())

    if low_sell_price - high_buy_price > 2.5 * trading_state.active_grid_signle_price:
        # 补充买单
        if trading_state.current_price - high_buy_price > trading_state.active_grid_signle_price * 1.5:
            await _over_range_replenish_buy_order(high_buy_price)
        # 补充卖单，补充卖单不能触及到熔断前的仓位
        if low_sell_price - trading_state.current_price > trading_state.active_grid_signle_price * 1.5:
            if trading_state.available_position_size > 0:
                await _over_range_replenish_sell_order(high_buy_price)


async def _over_range_replenish_buy_order(high_buy_price: float):
    """
    大间距补充买单
    """
    if len(trading_state.buy_prices) < GRID_CONFIG["MAX_TOTAL_ORDERS"]:
        if (
            not trading_state.last_filled_order_is_ask
            and len(trading_state.sell_orders) > 0
        ):
            # 如果上次成交订单是买单，则不补充买单
            logger.info("当前成交订单为买单，不补充买单")
            return

        new_buy_price = round(
            high_buy_price + trading_state.active_grid_signle_price, 2
        )
        # 如果新补买单价格已经高于当前价格，则不补单
        if new_buy_price >= trading_state.current_price:
            logger.info("新补买单价格高于当前价格，暂不补单")
            return
        success, order_id = await trading_state.grid_trading.place_single_order(
            is_ask=False,
            price=new_buy_price,
            amount=GRID_CONFIG["GRID_AMOUNT"],
        )
        if success:
            trading_state.buy_orders[order_id] = new_buy_price
            logger.info(
                f"大间距补充买单订单成功: 价格={new_buy_price}, 订单ID={order_id}"
            )


async def _over_range_replenish_sell_order(high_buy_price: float):
    """
    大间距补充卖单
    """
    global trading_state
    
    # 如果订单数量已经达到上限，删除最远订单
    if trading_state.available_position_size <= len(trading_state.sell_orders) * GRID_CONFIG["GRID_AMOUNT"]:
        cancel_orders = []
        # 卖单侧删除从最高价开始删除
        sell_orders = dict(
            sorted(
                trading_state.sell_orders.items(),
                key=lambda item: item[1],
                reverse=True,
            )
        )
        cancel_count = len(trading_state.sell_orders) - int(trading_state.available_position_size / GRID_CONFIG["GRID_AMOUNT"]) + 1
        for order_id, price in sell_orders.items():
            if len(cancel_orders) < cancel_count:
                cancel_orders.append(order_id)
                logger.info(f"取消最远卖单订单，价格={price}, 订单ID={order_id}")
            else:
                break

        await _cancel_orders(cancel_orders)
        logger.info(f"大间距补充卖单需要取消最远卖单，给出空间")
    
    if (
        trading_state.available_position_size
        > len(trading_state.sell_orders) * GRID_CONFIG["GRID_AMOUNT"]
        and trading_state.current_position_sign > 0
    ):
        if (
            trading_state.last_filled_order_is_ask
            and len(trading_state.sell_orders) > 0
        ):
            # 如果上次成交订单是卖单，则不补充卖单
            logger.info("当前成交订单为卖单，不补充卖单")
            return

        new_sell_price = round(
            high_buy_price + trading_state.active_grid_signle_price * 2,
            2,
        )
        # 如果新补卖单价格已经低于当前价格，则不补单
        if new_sell_price <= trading_state.current_price:
            logger.info("新补卖单价格低于当前价格，暂不补单")
            return
        success, order_id = await trading_state.grid_trading.place_single_order(
            is_ask=True,
            price=new_sell_price,
            amount=GRID_CONFIG["GRID_AMOUNT"],
        )
        if success:
            trading_state.sell_orders[order_id] = new_sell_price
            logger.info(
                f"大间距补充卖单订单成功: 价格={new_sell_price}, 订单ID={order_id}"
            )


async def _sell_side_replenish_config_orders():
    """
    卖单侧补充不少于配置单的数量,只向远距离补单
    """
    global trading_state
    
    available_sell_orders_count = trading_state.available_position_size / GRID_CONFIG["GRID_AMOUNT"]
    while (
        len(trading_state.sell_orders) < GRID_CONFIG["GRID_COUNT"]
        and trading_state.available_position_size
        > (len(trading_state.sell_orders)) * GRID_CONFIG["GRID_AMOUNT"]
        and trading_state.current_position_sign > 0
        and len(trading_state.sell_orders) < available_sell_orders_count
    ):
        high_sell_price = (
            max(trading_state.buy_orders.values())
            + trading_state.active_grid_signle_price * 2
        )
        if len(trading_state.sell_orders) > 0:
            high_sell_price = max(trading_state.sell_orders.values())
        new_sell_price = round(
            high_sell_price + trading_state.active_grid_signle_price,
            2,
        )
        # 如果新补卖单价格已经低于当前价格，则不补单
        while new_sell_price <= trading_state.current_price:
            new_sell_price = round(
                new_sell_price + trading_state.active_grid_signle_price,
                2,
            )
            
        success, order_id = await trading_state.grid_trading.place_single_order(
            is_ask=True,
            price=new_sell_price,
            amount=GRID_CONFIG["GRID_AMOUNT"],
        )
        if success:
            trading_state.sell_orders[order_id] = new_sell_price
            logger.info(
                f"卖单数量不足补充卖单订单成功: 价格={new_sell_price}, 订单ID={order_id}"
            )

#######################################################
# 订单管理部分
#######################################################
async def check_current_orders():
    """
    检查当前订单是否合理：
    如果有一侧订单过多，取消最远的订单
    """

    global trading_state

    # 如果有一侧订单过多，取消最远的订单
    if len(trading_state.buy_orders) > GRID_CONFIG["GRID_COUNT"] + 1:
        cancel_orders = []
        # 买单侧删除从最低价开始删除
        buy_orders = dict(
            sorted(trading_state.buy_orders.items(), key=lambda item: item[1])
        )
        cancel_count = len(trading_state.buy_orders) - (GRID_CONFIG["GRID_COUNT"] + 1)
        for order_id, price in buy_orders.items():
            if len(cancel_orders) < cancel_count:
                cancel_orders.append(order_id)
                logger.info(f"取消最远买单订单，价格={price}, 订单ID={order_id}")
            else:
                break

        await _cancel_orders(cancel_orders)

    if len(trading_state.sell_orders) > GRID_CONFIG["MAX_TOTAL_ORDERS"]:
        cancel_orders = []
        # 卖单侧删除从最高价开始删除
        sell_orders = dict(
            sorted(
                trading_state.sell_orders.items(),
                key=lambda item: item[1],
                reverse=True,
            )
        )
        cancel_count = (
            len(trading_state.sell_orders) - GRID_CONFIG["MAX_TOTAL_ORDERS"] / 2
        )
        for order_id, price in sell_orders.items():
            if len(cancel_orders) < cancel_count:
                cancel_orders.append(order_id)
                logger.info(f"取消最远卖单订单，价格={price}, 订单ID={order_id}")
            else:
                break

        await _cancel_orders(cancel_orders)

    # 卖单侧订单不能超过买单持仓量
    if (
        len(trading_state.sell_orders) * GRID_CONFIG["GRID_AMOUNT"]
        > trading_state.available_position_size
        and (time.time() - trading_state.start_time) > 60
    ):
        logger.info(f"卖单订单超过买单持仓数量，删除多余订单")
        cancel_orders = []
        # 卖单侧删除从最高价开始删除
        sell_orders = dict(
            sorted(
                trading_state.sell_orders.items(),
                key=lambda item: item[1],
                reverse=True,
            )
        )
        cancel_count = (
            len(trading_state.sell_orders)
            - trading_state.available_position_size / GRID_CONFIG["GRID_AMOUNT"]
        )
        if cancel_count > 0:
            for order_id, price in sell_orders.items():
                if len(cancel_orders) < cancel_count:
                    cancel_orders.append(order_id)
                    logger.info(f"取消最远卖单订单，价格={price}, 订单ID={order_id}")
                else:
                    break

            await _cancel_orders(cancel_orders)

    # 如果交易暂停，则取消所有订单
    if trading_state.grid_pause:
        if len(trading_state.buy_orders) > 0:
            cancel_orders = list(trading_state.buy_orders.keys())
            logger.info("交易暂停，取消所有买单")
            await _cancel_orders(cancel_orders)
                
        if len(trading_state.sell_orders) > 0:
            cancel_orders = list(trading_state.sell_orders.keys())
            logger.info("交易暂停，取消所有卖单")
            await _cancel_orders(cancel_orders)

    # 检查重复订单
    if len(trading_state.sell_orders) > 0:
        cancel_orders = []
        # 卖单侧删除从最高价开始删除
        sell_orders = dict(
            sorted(
                trading_state.sell_orders.copy().items(),
                key=lambda item: item[1],
                reverse=True,
            )
        )
        prev_price = None
        for order_id, price in sell_orders.items():
            if prev_price is not None and round(price, 0) == round(prev_price, 0):
                cancel_orders.append(order_id)
                logger.info(f"检测到重复价格订单，删除订单ID={order_id}, 价格={price}")
            prev_price = price

        if len(cancel_orders) > 0:
            await _cancel_orders(cancel_orders)

    # 如果订单中间距离过大，取消最远订单
    if len(trading_state.sell_orders) > 0:
        cancel_orders = []
        # 正序排列
        sell_orders = dict(
            sorted(
                trading_state.sell_orders.items(),
                key=lambda item: item[1],
            )
        )
        prev_price = None
        faraway = False
        for order_id, price in sell_orders.items():
            if prev_price is not None and not faraway:
                if price - prev_price > trading_state.active_grid_signle_price * 1.5:
                    # 价格间距过大，取消所有大于此价格的订单
                    cancel_orders.append(order_id)
                    logger.info(
                        f"检测到价格间距过大，删除订单ID={order_id}, 价格={price}"
                    )
                    faraway = True
            if faraway:
                cancel_orders.append(order_id)
            prev_price = price

        if len(cancel_orders) > 0:
            await _cancel_orders(cancel_orders)

    # 当前仓位 + 同方向订单，需要小于最大仓位限制
    if trading_state.available_position_size > GRID_CONFIG["ALER_POSITION"] / 2:
        if trading_state.current_position_sign > 0:
            # 多头仓位
            if len(trading_state.buy_orders) > GRID_CONFIG["GRID_COUNT"]:
                logger.info("当前多头仓位较大，取消部分买单订单以降低仓位")
                cancel_orders = []
                # 取消最远的买单订单
                buy_orders = dict(
                    sorted(trading_state.buy_orders.items(), key=lambda item: item[1])
                )
                cancel_count = len(trading_state.buy_orders) - GRID_CONFIG["GRID_COUNT"]
                for order_id, price in buy_orders.items():
                    if len(cancel_orders) < cancel_count:
                        cancel_orders.append(order_id)
                        logger.info(
                            f"取消最远买单订单，价格={price}, 订单ID={order_id}"
                        )
                    else:
                        break

                await _cancel_orders(cancel_orders)
        elif trading_state.current_position_sign < 0:
            # 空头仓位
            if len(trading_state.sell_orders) > GRID_CONFIG["GRID_COUNT"]:
                logger.info("当前空头仓位较大，取消部分卖单订单以降低仓位")
                cancel_orders = []
                # 取消最远的卖单订单
                sell_orders = dict(
                    sorted(
                        trading_state.sell_orders.items(),
                        key=lambda item: item[1],
                        reverse=True,
                    )
                )
                cancel_count = (
                    len(trading_state.sell_orders) - GRID_CONFIG["GRID_COUNT"]
                )
                for order_id, price in sell_orders.items():
                    if len(cancel_orders) < cancel_count:
                        cancel_orders.append(order_id)
                        logger.info(
                            f"取消最远卖单订单，价格={price}, 订单ID={order_id}"
                        )
                    else:
                        break

                await _cancel_orders(cancel_orders)

    # 同步订单状态
    await _sync_current_orders()

async def _cancel_orders(cancel_orders: List[int]):
    """
    批量取消订单
    """
    success = await trading_state.grid_trading.cancel_grid_orders(cancel_orders)
    if success:
        for order_id in cancel_orders:
            if order_id in trading_state.buy_orders:
                del trading_state.buy_orders[order_id]
            if order_id in trading_state.sell_orders:
                del trading_state.sell_orders[order_id]
        logger.info(f"批量取消订单成功: 订单ID列表={cancel_orders}")
    
async def _sync_current_orders():
    """
    同步订单状态
    """
    global trading_state
    # 通过rest api核对当前订单列表
    orders = await trading_state.grid_trading.get_orders_by_rest()
    if orders is None:
        logger.error("通过REST API获取当前订单失败")
        return
    # 以orders为准，更新buy_orders和sell_orders
    buy_orders = {}
    sell_orders = {}
    trading_state.pause_positions = {}
    for order in orders:
        order_id = order.client_order_index
        is_ask = order.is_ask
        price = float(order.price)
        status = order.status
        initial_base_amount = float(order.initial_base_amount)
        if status != "open":
            continue
        if initial_base_amount > GRID_CONFIG["GRID_AMOUNT"]:
            # 非网格订单，记录为熔断占位订单
            trading_state.pause_positions[price] = initial_base_amount
            continue
        
        if is_ask:
            sell_orders[order_id] = price
        else:
            buy_orders[order_id] = price
    buy_orders = dict(sorted(buy_orders.items(), key=lambda item: item[1]))
    sell_orders = dict(sorted(sell_orders.items(), key=lambda item: item[1]))
    trading_state.buy_orders = buy_orders
    trading_state.sell_orders = sell_orders

    buy_orders_prices = sorted(list(trading_state.buy_orders.copy().values()))
    sell_orders_prices = sorted(list(trading_state.sell_orders.copy().values()))
    logger.info(
        f"活跃订单: 总数: {(len(buy_orders_prices)+len(sell_orders_prices))}, 买单: {buy_orders_prices}, 卖单: {sell_orders_prices}"
    )
    
#######################################################


async def initialize_grid_trading(grid_trading: GridTrading) -> bool:
    """
    初始化网格交易
    """
    global trading_state

    rest_client = lighter.ApiClient(configuration=lighter.Configuration(host=BASE_URL))
    try:
        # 记录初始账户情况
        account_api = lighter.AccountApi(rest_client)
        account_info_resp = await account_api.account(
            by="index", value=str(ACCOUNT_INDEX)
        )
        if account_info_resp.code != CODE_OK:
            logger.info(f"获取账户信息失败: {account_info_resp.message}")
            return False, None
        account_info = account_info_resp.accounts[0]
        trading_state.start_collateral = float(account_info.collateral)

        position = account_info.positions[0]
        position_size = position.position
        trading_state.current_position_size = abs(float(position_size))
        trading_state.current_position_sign = int(position.sign)

        # 等待获取当前价格
        max_wait = 10
        wait_count = 0

        while trading_state.current_price is None and wait_count < max_wait:
            logger.info("等待获取当前价格...")
            await asyncio.sleep(1)
            wait_count += 1

        if trading_state.current_price is None:
            logger.error("无法获取当前价格，初始化失败")
            return False

        # 放置初始网格订单
        base_price = trading_state.current_price
        grid_count = GRID_CONFIG["GRID_COUNT"]
        grid_amount = GRID_CONFIG["GRID_AMOUNT"]
        grid_spread = GRID_CONFIG["GRID_SPREAD"]

        logger.info(f"🚀 初始化网格交易: 基准价格=${base_price}, 网格数量={grid_count}")
        trading_state.open_price = base_price

        success = True
        if account_info.total_order_count > 1:
            logger.info(f"当前账户已有未结订单或仓位，以原始订单为准，跳过初始化网格交易")
            await check_current_orders()
        else:
            # 同步订单状态
            await _sync_current_orders()
            success = await grid_trading.place_grid_orders(
                1, base_price, grid_count, grid_amount, grid_spread
            )


        if success:
            # 设置初始网格价格列表
            trading_state.buy_prices = calculate_grid_prices(
                base_price, grid_count, grid_spread
            )

            trading_state.buy_prices.sort()

            # 单网格价差值
            trading_state.base_grid_single_price = (
                trading_state.buy_prices[1] - trading_state.buy_prices[0]
            )
            trading_state.active_grid_signle_price = (
                trading_state.base_grid_single_price
            )

            # 保存原始价格序列
            trading_state.original_buy_prices = trading_state.buy_prices.copy()

            logger.info(f"初始网格价格: 买单={trading_state.buy_prices}")

            logger.info("✅ 网格交易初始化成功")
            trading_state.is_running = True
            return True
        else:
            logger.error("❌ 网格交易初始化失败")
            return False

    except Exception as e:
        logger.error(f"初始化网格交易时发生错误: {e}")
        return False
    finally:
        await rest_client.close()


#######################################################
# 风控管理部分
#######################################################
async def _risk_check(start: bool = False):
    """
    风控检查
    """
    global trading_state
    grid_trading = trading_state.grid_trading
    
    cs_5m = await grid_trading.candle_stick(
        market_id=GRID_CONFIG["MARKET_ID"], resolution="5m"
    )
    is_yindie_5m, yindie_details_5m = await grid_trading.is_yindie(
        cs_5m
    )
    logger.info(
        "5分钟阴跌检测: %s",
        yindie_details_5m | {"result": is_yindie_5m},
    )

    cs_15m = await grid_trading.candle_stick(
        market_id=GRID_CONFIG["MARKET_ID"], resolution="15m"
    )
    is_yindie_15m, yindie_details_15m = await grid_trading.is_yindie(
        cs_15m
    )
    logger.info(
        "15分钟阴跌检测: %s",
        yindie_details_15m | {"result": is_yindie_15m},
    )

    is_ema_filter, ema_filter_details = (
        await grid_trading.ema_mean_reversion_filter(cs_15m)
    )
    logger.info(
        "EMA均值回归检测: %s",
        ema_filter_details | {"result": is_ema_filter},
    )

    if is_yindie_5m or is_yindie_15m or is_ema_filter:
        trading_state.grid_pause = True
        # 记录熔断仓位
        # （？？似乎不需要考虑历史记录隔离，因为占位本身是等待价格到达后自动触发平仓的，只要隔离订单本身不影响网格订单就好了）
        if start:
            # 初始启动时已经触发熔断时，默认已经有占用的仓位
            trading_state.pause_position_exist = True
        else:
            if not trading_state.pause_position_exist:
                await _save_pause_position()
                trading_state.pause_position_exist = True
        # 记录熔断时仓位
        if is_yindie_5m:
            logger.info(
                f"⚠️ 警告：当前5分钟线阴跌中,暂停交易, {yindie_details_5m}"
            )
        if is_yindie_15m:
            logger.info(
                f"⚠️ 警告：当前15分钟线阴跌中,暂停交易, {yindie_details_15m}"
            )
        if is_ema_filter:
            logger.info(
                f"⚠️ 警告：当前EMA均值回归趋势不利,暂停交易, {ema_filter_details}"
            )
    else:
        if trading_state.current_position_size < GRID_CONFIG["MAX_POSITION"]:
            # 解除熔断
            trading_state.grid_pause = False
            trading_state.pause_position_exist = False
            # logger.info("✅ 当前风控检查通过，恢复网格交易")
                    
async def _save_pause_position():
    """
    熔断时创建占位仓位订单
    """
    global trading_state

    try:
        # 仓位形成距离
        position_price_range = trading_state.available_position_size / GRID_CONFIG["GRID_AMOUNT"] * trading_state.base_grid_single_price
        
        # 成本价理论上是最后价格 + 距离差价/2，占位订单价格设置在成本价上方一些，追求微盈利
        order_price = round(trading_state.last_trade_price + position_price_range / 3 * 2, 2)
        success, order_id = await trading_state.grid_trading.place_single_order(
            is_ask=True,
            price=order_price,
            amount=trading_state.available_position_size,
        )
        if success:
            logger.info(
                f"占位订单创建成功: 价格={order_price}, 订单ID={order_id}"
            )
    except Exception as e:
        logger.exception(f"创建占位订单失败: {e}")
        
        
async def _get_current_pause_position() -> float:
    """
    获取当前价格下熔断占位仓位
    """
    global trading_state
    
    if len(trading_state.pause_positions) == 0:
        return 0
    
    total_position = 0
    for price, amount in trading_state.pause_positions.items():
        if price > trading_state.current_price:
            total_position += amount
            
    return round(total_position, 6)

#######################################################
            
            

async def run_grid_trading():
    """
    运行网格交易系统
    """
    global trading_state

    logger.info("🎯 启动网格交易系统")
    logger.info(f"配置参数: {GRID_CONFIG}")

    # 创建签名客户端
    signer_client = lighter.SignerClient(
        url=BASE_URL,
        private_key=API_KEY_PRIVATE_KEY,
        account_index=ACCOUNT_INDEX,
        api_key_index=API_KEY_INDEX,
    )

    # 创建认证令牌
    expiry = int(time.time()) + 10 * lighter.SignerClient.MINUTE
    auth, err = signer_client.create_auth_token_with_expiry(deadline=expiry)
    if err is not None:
        logger.error(f"创建认证令牌失败: {auth}")
        return

    # 创建网格交易实例
    grid_trading = GridTrading(
        ws_client=None,  # 稍后设置
        signer_client=signer_client,
        account_index=ACCOUNT_INDEX,
        api_key_index=API_KEY_INDEX,
        market_id=GRID_CONFIG["MARKET_ID"],
    )

    # 创建WebSocket客户端
    client = create_unified_client(
        auth_token=auth,
        market_stats_ids=[GRID_CONFIG["MARKET_ID"]],
        on_market_stats_update=on_market_stats_update,
        account_all_orders_ids=[ACCOUNT_INDEX],
        on_account_all_orders_update=on_account_all_orders_update,
        account_all_positions_ids=[ACCOUNT_INDEX],
        on_account_all_positions_update=on_account_all_positions_update,
    )

    # 设置网格交易的WebSocket客户端
    grid_trading.ws_client = client
    # 设置全局网格交易实例
    trading_state.grid_trading = grid_trading
    # 设置网格交易的REST客户端
    configuration = lighter.Configuration(BASE_URL)
    api_client = lighter.ApiClient(configuration)
    account_api = lighter.AccountApi(api_client)

    try:
        # 启动WebSocket客户端（异步方式）
        ws_task = asyncio.create_task(client.run_async())

        # 等待连接建立
        await asyncio.sleep(2)
        
        # 风控检查
        await _risk_check(start=True)
                    
        # # 初始化网格交易
        if not await initialize_grid_trading(grid_trading):
            logger.error("网格交易初始化失败，退出")
            return

        # 保持运行并监控
        counter = 0
        while trading_state.is_running:

            try:
                # 每10秒打印一次网格状态
                await asyncio.sleep(10)

                # 检查仓位状态
                account_info_resp = await account_api.account(
                    by="index", value=str(ACCOUNT_INDEX)
                )
                if account_info_resp.code != CODE_OK:
                    logger.info(f"获取账户信息失败: {account_info_resp.message}")
                    return False, None
                account_info = account_info_resp.accounts[0]

                position = account_info.positions[0]
                position_size = position.position
                trading_state.current_position_size = round(abs(float(position_size)), 2)
                trading_state.current_position_sign = int(position.sign)
                if position_size is not None:
                    current_pause_position = await _get_current_pause_position()
                    trading_state.available_position_size = round(trading_state.current_position_size - current_pause_position, 2)
                    logger.info(
                        f"📊 当前仓位: {position_size}, 冻结仓位: {current_pause_position}, 可用仓位: {trading_state.available_position_size}, 清算价格: {position.liquidation_price}"
                    )

                unrealized_pnl = float(position.unrealized_pnl)

                # 检查当前账户保证金
                trading_state.current_collateral = float(account_info.collateral)

                unrealized_collateral = (
                    trading_state.current_collateral + unrealized_pnl
                )
                pnl = unrealized_collateral - trading_state.start_collateral
                logger.info(
                    f"💰盈亏情况: 初始: {round(trading_state.start_collateral, 6)}, 当前: {round(unrealized_collateral, 6)}, 盈亏: {round(pnl,6)}, " + 
                    f"本次套利总收益: {round(trading_state.total_profit, 2)}, 动态收益: {round(trading_state.active_profit, 2)}, " + 
                    f"网格间距: {round(trading_state.active_grid_signle_price, 2)}"
                )
                time_formatted = await seconds_formatter(
                    time.time() - trading_state.start_time
                )
                logger.info(
                    f"⏱️ 运行时间: {time_formatted}, 开仓价格: {trading_state.open_price}, 当前价格: {trading_state.current_price}, 成交次数: {trading_state.filled_count}"
                )

                cs_1m = await grid_trading.candle_stick(market_id=0, resolution="1m")
                trading_state.candle_stick_1m = cs_1m

                # 急跌判断
                is_jidie, jidie_details = await grid_trading.is_jidie(cs_1m)
                if is_jidie:
                    logger.info(f"⚠️ 警告：当前急跌中, {jidie_details}")
                #     min_step = trading_state.base_grid_single_price
                #     max_step = (
                #         trading_state.base_grid_single_price * 30
                #     )  # 即使天塌下来，间距也不能超过（防止ATR计算出错导致不挂单）

                #     raw_step = 0.7 * round(jidie_details.get("atr"), 2)
                #     trading_state.active_grid_signle_price = max(
                #         min_step, min(raw_step, max_step)
                #     )
                # else:
                #     trading_state.active_grid_signle_price = (
                #         trading_state.base_grid_single_price
                #     )

                # 波动检测
                atr_value = jidie_details.get("atr")
                trading_state.current_atr = atr_value
                if atr_value > GRID_CONFIG["ATR_THRESHOLD"]:
                    min_step = trading_state.base_grid_single_price
                    max_step = (
                        trading_state.base_grid_single_price * 30
                    )  # 即使天塌下来，间距也不能超过（防止ATR计算出错导致不挂单）

                    raw_step = 0.7 * round(atr_value, 2)
                    trading_state.active_grid_signle_price = max(
                        min_step, min(raw_step, max_step)
                    )
                else:
                    trading_state.active_grid_signle_price = (
                        trading_state.base_grid_single_price
                    )

                # 每60秒执行一次（10秒 * 6 = 60秒）
                if counter % 6 == 0:
                    logger.info("急跌检测: %s", jidie_details | {"result": is_jidie})
                    # 风控检查
                    await _risk_check()

                # 额外检查是否需要补单
                async with replenish_grid_lock:
                    # 订阅消息补单时间大于一定时间后，才进行常规检查补单
                    if time.time() - trading_state.last_replenish_time > 5:
                        # 检查当前订单是否合理
                        await check_current_orders()
                        # 补充网格订单
                        await replenish_grid(False)

                counter += 1
            except Exception:
                logger.exception("执行循环检查时出现异常")

    except KeyboardInterrupt:
        logger.info("👋 收到停止信号")
    except Exception:
        logger.exception(f"网格交易运行时发生错误")
    finally:
        trading_state.is_running = False
        await signer_client.close()
        await api_client.close()
        # 优雅地停止异步WebSocket客户端
        client.stop()
        if not ws_task.done():
            try:
                await asyncio.wait_for(ws_task, timeout=5.0)
            except asyncio.TimeoutError:
                ws_task.cancel()
                await ws_task
        logger.info("🔚 网格交易系统已停止")


async def seconds_formatter(seconds: int) -> str:
    """
    将秒数格式化为 天 时 分 秒
    """
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{round(days)}天 {round(hours)}小时 {round(minutes)}分钟 {round(seconds)}秒"


if __name__ == "__main__":
    asyncio.run(run_grid_trading())
