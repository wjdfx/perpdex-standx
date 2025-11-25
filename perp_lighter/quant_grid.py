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
from typing import Dict, List, Optional, Set, Tuple
import lighter
from lighter.signer_client import CODE_OK
from .ws_client import create_unified_client
from .grid_matin import GridTrading


# 网格交易参数配置
GRID_CONFIG = {
    "GRID_COUNT": 3,  # 每侧网格数量
    "GRID_AMOUNT": 0.01,  # 单网格挂单量
    "GRID_SPREAD": 0.05,  # 单网格价差（百分比）
    "MAX_TOTAL_ORDERS": 10,  # 最大活跃订单数量
    "MAX_POSITION": 0.3,  # 最大仓位限制
    "DECREASE_POSITION": 0.2,  # 降低仓位触发点
    "ALER_POSITION": 0.1,  # 警告仓位限制
    "MARKET_ID": 0,  # 市场ID
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
        self.grid_single_price: float = 0  # 单网格价差值
        self.start_collateral: float = 0  # 初始保证金
        self.current_collateral: float = 0  # 当前保证金
        self.start_time: float = time.time()  # 启动时间
        self.open_price: Optional[float] = None  # 启动时基准价格
        self.last_filled_order_is_ask: bool = False  # 上次成交订单方向
        self.last_replenish_time: float = 0  # 上次补单时间
        self.grid_pause: bool = False  # 网格交易暂停标志
        self.grid_sell_spread_alert: bool = False  # 卖单警告价差状态
        self.grid_buy_spread_alert: bool = False  # 买单警告价差状态
        self.grid_decrease_status: bool = False  # 降低仓位状态


# 全局状态实例
trading_state = GridTradingState()

# 全局异步锁，用于保护 replenish_grid() 方法
replenish_grid_lock = asyncio.Lock()


def on_market_stats_update(market_id: str, market_stats: dict):
    """
    处理市场统计数据更新
    """
    global trading_state

    mark_price = market_stats.get("mark_price")
    if mark_price:
        trading_state.current_price = float(mark_price)
        # logger.info(f"📊 市场 {market_id} 标记价格更新: ${trading_state.current_price}")


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


def on_account_all_positions_update(account_id: str, positions: dict):
    """
    处理账户所有仓位更新
    """
    if account_id != str(ACCOUNT_INDEX):
        return

    # 检查仓位是否超出限制
    check_position_limits(positions)


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

                    # logger.info(
                    #     f"🎯 订单成交: ID={client_order_index}, 方向={is_ask}, 价格={price}, 状态={status}, 成交量={filled_amount}"
                    # )

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
                        # 移除低于或等于成交价格的所有卖单订单
                        sell_orders_to_remove = [
                            client_order_index
                            for client_order_index, order_price in trading_state.sell_orders.items()
                            if order_price <= float(price)
                        ]
                        for client_order_index in sell_orders_to_remove:
                            del trading_state.sell_orders[client_order_index]
                            logger.info(
                                f"从活跃卖单订单列表删除订单ID={client_order_index}, 价格={trading_state.sell_orders.get(client_order_index, 'N/A')}"
                            )
                    else:
                        if client_order_index in trading_state.buy_orders:
                            del trading_state.buy_orders[client_order_index]
                            logger.info(
                                f"从活跃买单订单列表删除订单ID={client_order_index}, 价格={price}"
                            )
                            replenish = True
                        # 移除高于或等于成交价格的所有买单订单
                        buy_orders_to_remove = [
                            client_order_index
                            for client_order_index, order_price in trading_state.buy_orders.items()
                            if order_price >= float(price)
                        ]
                        for client_order_index in buy_orders_to_remove:
                            del trading_state.buy_orders[client_order_index]
                            logger.info(
                                f"从活跃买单订单列表删除订单ID={client_order_index}, 价格={trading_state.buy_orders.get(client_order_index, 'N/A')}"
                            )

                    # 补充网格（异步方式）
                    if replenish:
                        # 检查当前订单是否合理
                        # await check_current_orders()
                        # 补充网格订单
                        await replenish_grid()
                        trading_state.last_replenish_time = time.time()


def calculate_grid_prices(
    current_price: float, grid_count: int, grid_spread: float
) -> tuple[List[float], List[float]]:
    """
    计算网格价格列表
    订单以GRID_SPREAD的价差比例，均匀分布在当前价格上下两侧，
    最贴近当前价格的买单和卖单之间的距离是两倍价差。
    """
    buy_prices = []
    sell_prices = []

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

    for i in range(grid_count):
        # 卖单价格：当前价格上方
        # 第一个卖单距离 = 1 * spread，第二个 = 2 * spread，以此类推
        distance = (i + 1) * spread_decimal
        sell_price = current_price * (1 + distance)
        sell_prices.append(round(sell_price, 2))

    return buy_prices, sell_prices


def check_position_limits(positions: dict):
    """
    检查仓位是否超出限制
    """
    for market_id, position in positions.items():
        position_size = abs(float(position.get("position", 0)))
        if position_size == 0:
            return
        sign = int(position.get("sign", "0"))
        alert_pos = GRID_CONFIG["ALER_POSITION"]
        decrease_position = GRID_CONFIG["DECREASE_POSITION"]
        # 当仓位到了警戒线时，触发挂单倾斜，将单边挂单网格距离增大
        if position_size >= alert_pos and position_size < decrease_position:
            logger.warning(
                f"⚠️ 警告：仓位接近限制，已触发挂单倾斜: 市场={market_id}, 当前={position_size}, 警告={alert_pos}"
            )
            if sign > 0:
                # 多头仓位
                trading_state.grid_buy_spread_alert = True
            else:
                # 空头仓位
                trading_state.grid_sell_spread_alert = True
            
            # logger.info("当前处于警告价差状态，补单间距加倍")
            trading_state.grid_single_price = (
                trading_state.original_buy_prices[1]
                - trading_state.original_buy_prices[0]
            ) * 2
            trading_state.grid_decrease_status = False
        elif position_size >= decrease_position:
            logger.warning(
                f"⚠️ 警告：仓位超出降低点，开始降低仓位: 市场={market_id}, 当前={position_size}, 降低点={decrease_position}"
            )
            trading_state.grid_decrease_status = True
        else:
            trading_state.grid_buy_spread_alert = False
            trading_state.grid_sell_spread_alert = False
            trading_state.grid_single_price = (
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
            
async def decrease_position():
    """
    TODO 降低仓位的逻辑
    """


# def check_orders_count():
#     """
#     在订单倾斜期间，将单边方向的订单保持在最小数量，只保留最远的几个订单
#     """
#     global trading_state

#     buy_count = len(trading_state.buy_prices)
#     sell_count = len(trading_state.sell_prices)

#     if buy_count >= GRID_CONFIG["MAX_TOTAL_ORDERS"]:
#         # 买单过多，取消最远的买单
#         farthest_buy_price = trading_state.buy_prices[0]
#         asyncio.run(
#             trading_state.grid_trading.cancel_orders_by_price(
#                 is_ask=False, price=farthest_buy_price
#             )
#         )
#         trading_state.buy_prices.remove(farthest_buy_price)
#         logger.info(f"取消最远买单订单，价格={farthest_buy_price}")

#     logger.info(f"📋 当前活跃订单数量: 买单={buy_count}, 卖单={sell_count}")


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

        # 设置全局网格交易实例
        trading_state.grid_trading = grid_trading

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

        success = await grid_trading.place_grid_orders(
            base_price, grid_count, grid_amount, grid_spread
        )

        if success:
            # 设置初始网格价格列表
            trading_state.buy_prices, trading_state.sell_prices = calculate_grid_prices(
                base_price, grid_count, grid_spread
            )

            trading_state.buy_prices.sort()
            trading_state.sell_prices.sort()

            # 单网格价差值
            trading_state.grid_single_price = (
                trading_state.buy_prices[1] - trading_state.buy_prices[0]
            )

            # 保存原始价格序列
            trading_state.original_buy_prices = trading_state.buy_prices.copy()
            trading_state.original_sell_prices = trading_state.sell_prices.copy()

            logger.info(
                f"初始网格价格: 买单={trading_state.buy_prices}, 卖单={trading_state.sell_prices}"
            )

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


async def replenish_grid():
    """
    补充网格订单逻辑
    基于原始订单价格分布和当前价格，计算补充订单的价格和方向

    补充规则：
    1. 保持原始网格的价格分布相对关系（等差序列）
    2. 当订单成交后，补充缺失的数量，根据当前价格决定在哪侧补充
    3. 保证每侧都有 >= GRID_COUNT 个活跃订单
    4. 卖单最低价必须比买单最高价高 2 * GRID_SPREAD
    5. 补充订单的价格基于当前市场价格和原始价差计算
    """

    global trading_state

    logger.info("🔄 检查并补充网格订单中...")

    # 使用带订单ID的价格列表
    buy_orders_prices = sorted(list(trading_state.buy_orders.values()))
    sell_orders_prices = sorted(list(trading_state.sell_orders.values()))

    if len(buy_orders_prices) == 0 and len(sell_orders_prices) == 0:
        return

    try:
        low_sell_price = trading_state.current_price + trading_state.grid_single_price
        if len(sell_orders_prices) > 0:
            low_sell_price = sell_orders_prices[0]
        high_buy_price = trading_state.current_price - trading_state.grid_single_price
        if len(buy_orders_prices) > 0:
            high_buy_price = buy_orders_prices[-1]
            
        # 买单侧被吃单到需要补单时
        while len(buy_orders_prices) < GRID_CONFIG["GRID_COUNT"]:
            # 买单侧补单
            if trading_state.grid_buy_spread_alert and trading_state.grid_decrease_status:
                logger.info("当前处于买单警告价差和降低仓位状态，只做减仓单")
                break
            logger.info("买单侧需要补单")
            # 价格下降，补低价单
            low_buy_price = buy_orders_prices[0]
            low_sell_price = sell_orders_prices[0]
            # 计算新买单价格
            grid_single_price = trading_state.grid_single_price
            new_buy_price = round(low_buy_price - grid_single_price, 2)
            # 如果新补买单价格已经高于当前价格，则不补单
            if new_buy_price >= trading_state.current_price:
                logger.info("新补买单价格高于当前价格，暂不补单")
                break
            # 执行订单补充
            success, order_id = await trading_state.grid_trading.place_single_order(
                is_ask=False,
                price=new_buy_price,
                amount=GRID_CONFIG["GRID_AMOUNT"],
            )
            if success:
                # 更新buy_orders_prices而不是trading_state.buy_prices
                buy_orders_prices.append(new_buy_price)
                buy_orders_prices.sort()
                trading_state.buy_orders[order_id] = new_buy_price
                logger.info(
                    f"买单侧被吃单补充买单订单成功: 价格={new_buy_price}, 订单ID={order_id}"
                )

            # 卖单侧需补充低价单
            if low_sell_price - high_buy_price <= 2.5 * trading_state.grid_single_price:
                logger.info("买单侧和卖单侧价格差距过小，暂不补单")
                break
            # 计算新卖单价格
            new_sell_price = round(low_sell_price - trading_state.grid_single_price, 2)
            # 执行订单补充
            success, order_id = await trading_state.grid_trading.place_single_order(
                is_ask=True,
                price=new_sell_price,
                amount=GRID_CONFIG["GRID_AMOUNT"],
            )
            if success:
                # 更新sell_orders_prices而不是trading_state.sell_prices
                sell_orders_prices.append(new_sell_price)
                sell_orders_prices.sort()
                trading_state.sell_orders[order_id] = new_sell_price
                logger.info(
                    f"买单侧被吃单补充卖单订单成功: 价格={new_sell_price}, 订单ID={order_id}"
                )

        # 卖单侧被吃单到需要补单时
        while len(sell_orders_prices) < GRID_CONFIG["GRID_COUNT"]:
            # 卖单侧补单
            if trading_state.grid_sell_spread_alert and trading_state.grid_decrease_status:
                logger.info("当前处于卖单警告价差和降低仓位状态，只做减仓单")
                break
            logger.info("卖单侧需要补单")
            # 价格上升，补高价单
            high_sell_price = sell_orders_prices[-1]
            high_buy_price = buy_orders_prices[-1]
            # 计算新卖单价格
            grid_single_price = trading_state.grid_single_price
            new_sell_price = round(high_sell_price + grid_single_price, 2)
            # 如果新补卖单价格已经低于当前价格，则不补单
            if new_sell_price <= trading_state.current_price:
                logger.info("新补卖单价格低于当前价格，暂不补单")
                return
            # 执行订单补充
            success, order_id = await trading_state.grid_trading.place_single_order(
                is_ask=True,
                price=new_sell_price,
                amount=GRID_CONFIG["GRID_AMOUNT"],
            )
            if success:
                # 更新sell_orders_prices而不是trading_state.sell_prices
                sell_orders_prices.append(new_sell_price)
                sell_orders_prices.sort()
                trading_state.sell_orders[order_id] = new_sell_price
                logger.info(
                    f"卖单侧被吃单补充卖单订单成功: 价格={new_sell_price}, 订单ID={order_id}"
                )

            # 买单侧需补充高价单
            if low_sell_price - high_buy_price <= 2.5 * trading_state.grid_single_price:
                logger.info("买单侧和卖单侧价格差距过小，暂不补单")
                return
            # 计算新买单价格
            new_buy_price = round(high_buy_price + trading_state.grid_single_price, 2)
            # 执行订单补充
            success, order_id = await trading_state.grid_trading.place_single_order(
                is_ask=False,
                price=new_buy_price,
                amount=GRID_CONFIG["GRID_AMOUNT"],
            )
            if success:
                # 更新buy_orders_prices而不是trading_state.buy_prices
                buy_orders_prices.append(new_buy_price)
                buy_orders_prices.sort()
                trading_state.buy_orders[order_id] = new_buy_price
                logger.info(
                    f"卖单侧被吃单补充买单订单成功: 价格={new_buy_price}, 订单ID={order_id}"
                )

        # 大间距补单，如果卖单最低价和买单最高价差距大于 2 * GRID_SPREAD，则补充中间价单
        buy_orders_prices = sorted(list(trading_state.buy_orders.values()))
        sell_orders_prices = sorted(list(trading_state.sell_orders.values()))
        low_sell_price = trading_state.current_price + trading_state.grid_single_price
        if len(sell_orders_prices) > 0:
            low_sell_price = sell_orders_prices[0]
        high_buy_price = trading_state.current_price - trading_state.grid_single_price
        if len(buy_orders_prices) > 0:
            high_buy_price = buy_orders_prices[-1]
        if low_sell_price - high_buy_price > 2.5 * trading_state.grid_single_price:
            if trading_state.current_price - high_buy_price > trading_state.grid_single_price * 1.2:
                # 补充买单
                if trading_state.grid_buy_spread_alert:
                    logger.info("当前处于买单警告价差状态，大间距暂不补单")
                else:
                    if not trading_state.last_filled_order_is_ask and not trading_state.grid_sell_spread_alert:
                        # 如果上次成交订单是买单，且当前没有卖单警告价差状态，则不补充买单，卖单警告状态时，允许补充买单以平衡仓位
                        logger.info("当前成交订单为买单，不补充买单")
                    else:
                        new_buy_price = round(
                            high_buy_price + trading_state.grid_single_price, 2
                        )
                        # 如果新补买单价格已经高于当前价格，则不补单
                        if new_buy_price >= trading_state.current_price:
                            logger.info("新补买单价格高于当前价格，暂不补单")
                            return
                        success, order_id = (
                            await trading_state.grid_trading.place_single_order(
                                is_ask=False,
                                price=new_buy_price,
                                amount=GRID_CONFIG["GRID_AMOUNT"],
                            )
                        )
                        if success:
                            # 更新buy_orders_prices而不是trading_state.buy_prices
                            buy_orders_prices.append(new_buy_price)
                            buy_orders_prices.sort()
                            trading_state.buy_orders[order_id] = new_buy_price
                            logger.info(
                                f"大间距补充买单订单成功: 价格={new_buy_price}, 订单ID={order_id}"
                            )

            buy_orders_prices = sorted(list(trading_state.buy_orders.values()))
            sell_orders_prices = sorted(list(trading_state.sell_orders.values()))
            low_sell_price = trading_state.current_price + trading_state.grid_single_price
            if len(sell_orders_prices) > 0:
                low_sell_price = sell_orders_prices[0]
            high_buy_price = trading_state.current_price - trading_state.grid_single_price
            if len(buy_orders_prices) > 0:
                high_buy_price = buy_orders_prices[-1]
            if low_sell_price - trading_state.current_price > trading_state.grid_single_price * 1.2:
                # 补充卖单
                if trading_state.grid_sell_spread_alert:
                    logger.info("当前处于卖单警告价差状态，大间距暂不补单")
                else:
                    if trading_state.last_filled_order_is_ask and not trading_state.grid_buy_spread_alert:
                        # 如果上次成交订单是卖单，且当前没有买单警告价差状态，则不补充卖单，买单警告状态时，允许补充卖单以平衡仓位
                        logger.info("当前成交订单为卖单，不补充卖单")
                    else:
                        new_sell_price = round(
                            low_sell_price - trading_state.grid_single_price, 2
                        )
                        # 如果新补卖单价格已经低于当前价格，则不补单
                        if new_sell_price <= trading_state.current_price:
                            logger.info("新补卖单价格低于当前价格，暂不补单")
                            return
                        success, order_id = (
                            await trading_state.grid_trading.place_single_order(
                                is_ask=True,
                                price=new_sell_price,
                                amount=GRID_CONFIG["GRID_AMOUNT"],
                            )
                        )
                        if success:
                            # 更新sell_orders_prices而不是trading_state.sell_prices
                            sell_orders_prices.append(new_sell_price)
                            sell_orders_prices.sort()
                            trading_state.sell_orders[order_id] = new_sell_price
                            logger.info(
                                f"大间距补充卖单订单成功: 价格={new_sell_price}, 订单ID={order_id}"
                            )

            # # 重新获取最新的价格列表
            # high_buy_price = buy_orders_prices[-1]
            # low_sell_price = sell_orders_prices[0]

    except Exception as e:
        logger.error(f"补充网格订单时发生错误: {e}")


async def check_current_orders():
    """
    检查当前订单是否合理：
    如果有一侧订单过多，取消最远的订单
    """

    global trading_state

    cancel_orders = []
    # 如果有一侧订单过多，取消最远的订单
    if len(trading_state.buy_orders) > GRID_CONFIG["MAX_TOTAL_ORDERS"]:
        # 买单侧删除从最低价开始删除
        buy_orders = dict(
            sorted(trading_state.buy_orders.items(), key=lambda item: item[1])
        )
        cancel_count = (
            len(trading_state.buy_orders) - GRID_CONFIG["MAX_TOTAL_ORDERS"] / 2
        )
        for order_id, price in buy_orders.items():
            if len(cancel_orders) < cancel_count:
                cancel_orders.append(order_id)
                logger.info(f"取消最远买单订单，价格={price}, 订单ID={order_id}")
            else:
                break

        success = await trading_state.grid_trading.cancel_grid_orders(cancel_orders)
        if success:
            for order_id in cancel_orders:
                if order_id in trading_state.buy_orders:
                    del trading_state.buy_orders[order_id]
            logger.info(f"批量取消买单订单成功: 订单ID列表={cancel_orders}")
    
    cancel_orders = []
    if len(trading_state.sell_orders) > GRID_CONFIG["MAX_TOTAL_ORDERS"]:
        # 卖单侧删除从最高价开始删除
        sell_orders = dict(
            sorted(trading_state.sell_orders.items(), key=lambda item: item[1], reverse=True)
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

        success = await trading_state.grid_trading.cancel_grid_orders(cancel_orders)
        if success:
            for order_id in cancel_orders:
                if order_id in trading_state.sell_orders:
                    del trading_state.sell_orders[order_id]
            logger.info(f"批量取消卖单订单成功: 订单ID列表={cancel_orders}")
            
    # 通过rest api核对当前订单列表
    orders = await trading_state.grid_trading.get_orders_by_rest()
    if orders is None:
        logger.error("通过REST API获取当前订单失败")
        return
    # 以orders为准，更新buy_orders和sell_orders
    buy_orders = {}
    sell_orders = {}
    for order in orders:
        order_id = order.client_order_index
        is_ask = order.is_ask
        price = float(order.price)
        status = order.status
        if status != "open":
            continue
        if is_ask:
            sell_orders[order_id] = price
        else:
            buy_orders[order_id] = price
    buy_orders = dict(sorted(buy_orders.items(), key=lambda item: item[1]))
    sell_orders = dict(sorted(sell_orders.items(), key=lambda item: item[1]))
    trading_state.buy_orders = buy_orders
    trading_state.sell_orders = sell_orders
    
    buy_orders_prices = sorted(
        list(trading_state.buy_orders.copy().values())
    )
    sell_orders_prices = sorted(
        list(trading_state.sell_orders.copy().values())
    )
    logger.info(
        f"活跃订单: 总数: {(len(buy_orders_prices)+len(sell_orders_prices))}, 买单: {buy_orders_prices}, 卖单: {sell_orders_prices}"
    )


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
    auth, err = signer_client.create_auth_token_with_expiry(
        deadline=expiry
    )
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
    # 设置网格交易的REST客户端
    configuration = lighter.Configuration(BASE_URL)
    api_client = lighter.ApiClient(configuration)
    account_api = lighter.AccountApi(api_client)

    try:
        # 启动WebSocket客户端（异步方式）
        ws_task = asyncio.create_task(client.run_async())

        # 等待连接建立
        await asyncio.sleep(2)

        # 初始化网格交易
        if not await initialize_grid_trading(grid_trading):
            logger.error("网格交易初始化失败，退出")
            return

        # 保持运行并监控
        while trading_state.is_running:

            # 每10秒打印一次网格状态
            await asyncio.sleep(10)
            # 额外检查是否需要补单
            async with replenish_grid_lock:
                # 订阅消息补单时间大于一定时间后，才进行常规检查补单
                if time.time() - trading_state.last_replenish_time > 5:
                    # 检查当前订单是否合理
                    await check_current_orders()
                    # 补充网格订单
                    await replenish_grid()

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
            if position_size is not None:
                direction = "多头" if position.sign > 0 else "空头"
                logger.info(f"📊 当前仓位: {position_size}, 方向: {direction}")

            unrealized_pnl = float(position.unrealized_pnl)

            # 检查当前账户保证金
            trading_state.current_collateral = float(account_info.collateral)

            unrealized_collateral = (
                trading_state.current_collateral + unrealized_pnl
            )
            pnl = (
                unrealized_collateral - trading_state.start_collateral
            )
            logger.info(
                f"💰盈亏情况: 初始: {trading_state.start_collateral}, 当前: {unrealized_collateral}, 盈亏: {round(pnl,6)}"
            )
            logger.info(
                f"⏱️ 运行时间: {round(time.time() - trading_state.start_time)} 秒, 开仓价格: {trading_state.open_price}, 当前价格: {trading_state.current_price}"
            )

            # get_current_grid_status()

    except KeyboardInterrupt:
        logger.info("👋 收到停止信号")
    except Exception as e:
        logger.error(f"网格交易运行时发生错误: {e}")
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


if __name__ == "__main__":
    asyncio.run(run_grid_trading())
