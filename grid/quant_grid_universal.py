"""
通用网格交易策略模块

支持做多和做空两种方向的网格交易策略。
"""

from common.config import (
    BASE_URL,
    API_KEY_PRIVATE_KEY,
    ACCOUNT_INDEX,
    API_KEY_INDEX,
    PROXY_URL,
)

import logging
from common.logging_config import setup_logging

# 配置日志
logger = logging.getLogger(__name__)

import asyncio
import time
from typing import Optional

from .grid_trading import GridTrading
from exchanges import create_exchange_adapter
from exchanges.order_converter import normalize_order_to_ccxt

# 导入状态管理模块
from .grid_state import (
    trading_state,
    GRID_CONFIG,
    OPEN_SIDE_IS_ASK,
    CLOSE_SIDE_IS_ASK,
    replenish_grid_lock,
    configure_direction,
    set_grid_config,
    seconds_formatter,
)

# 导入仓位管理模块
from .grid_position import check_position_limits

# 导入订单管理模块
from .grid_order import (
    check_order_fills,
    check_current_orders,
)

# 导入风控管理模块
from .grid_risk import (
    _risk_check,
    is_rapid_market_move,
)

# 导入网格补单模块
from .grid_replenish import (
    replenish_grid,
    calculate_grid_prices,
)


async def on_market_stats_update(market_id: str, market_stats: dict):
    """
    处理市场统计数据更新
    
    Args:
        market_id: 市场ID
        market_stats: 市场统计数据
    """
    from .grid_state import trading_state, GRID_CONFIG

    mark_price = float(market_stats.get("mark_price"))
    if mark_price:
        trading_state.current_price = mark_price

        cs_1m = trading_state.candle_stick_1m
        if trading_state.grid_trading is not None and cs_1m is not None:
            try:
                # 急跌/暴涨检测
                is_rapid_move, details = await is_rapid_market_move(cs_1m, close=mark_price)

                if is_rapid_move:
                    min_step = trading_state.base_grid_single_price
                    max_step = trading_state.base_grid_single_price * 30

                    raw_step = 0.8 * round(details.get("atr"), 2)
                    trading_state.active_grid_signle_price = max(
                        min_step, min(raw_step, max_step)
                    )
            except Exception as e:
                logger.exception(f"Error checking rapid move in market stats update: {e}")


async def on_account_all_orders_update(account_id: str, orders: dict):
    """
    处理账户所有订单更新
    
    Args:
        account_id: 账户ID
        orders: 订单列表
    """
    # 检查是否有订单成交
    await check_order_fills(orders)


async def on_account_all_positions_update(account_id: str, positions: dict):
    """
    处理账户所有仓位更新
    
    Args:
        account_id: 账户ID
        positions: 仓位数据
    """
    from .grid_state import trading_state, GRID_CONFIG
    
    if len(trading_state.original_open_prices) == 0:
        logger.info("等待初始化完成...")
        return
    for market_id, position in positions.items():
        # 处理不同字段名的仓位
        position_size = position.get(
            "position", position.get("size", position.get("amount", 0))
        )
        position_size = round(abs(float(position_size)), 2)
        await check_position_limits(position_size)


async def initialize_grid_trading(grid_trading: GridTrading) -> bool:
    """
    初始化网格交易
    
    Args:
        grid_trading: GridTrading 实例
        
    Returns:
        是否初始化成功
    """
    from .grid_state import (
        trading_state,
        GRID_CONFIG,
        OPEN_SIDE_IS_ASK,
    )
    from .grid_order import _sync_current_orders

    try:
        # 记录初始账户情况
        account_info = await grid_trading.exchange.get_account_info()
        if not account_info:
            logger.info("获取账户信息失败")
            return False
        trading_state.start_collateral = float(
            account_info.get("total_equity") or account_info.get("collateral", 0)
        )

        positions = account_info.get("positions", {})
        if isinstance(positions, dict):
            position = next(iter(positions.values())) if positions else None
        else:
            position = positions[0] if positions else None

        # 仓位数据
        position_size = 0
        position_sign = 0

        if position:
            position_size = position.get(
                "position", position.get("size", position.get("amount", 0))
            )
            sign_raw = position.get("sign", position.get("side", 0))
            if isinstance(sign_raw, str):
                position_sign = (
                    1
                    if sign_raw.lower() == "buy"
                    else -1 if sign_raw.lower() == "sell" else 0
                )
            else:
                position_sign = int(sign_raw)

        trading_state.current_position_size = abs(float(position_size))
        trading_state.current_position_sign = position_sign
        await check_position_limits(trading_state.current_position_size)

        # 记录最后一单成交价格
        trades = await grid_trading.get_trades_by_rest(0, 1)
        if len(trades) > 0:
            last_trade = trades[0]
            trading_state.last_trade_price = float(last_trade.get("price", 0))

        # 等待获取当前价格
        max_wait = 10
        wait_count = 0
        while trading_state.current_price is None and wait_count < max_wait:
            await asyncio.sleep(1)
            wait_count += 1

        if trading_state.current_price is None:
            return False

        # 放置初始网格订单
        base_price = trading_state.current_price
        grid_count = GRID_CONFIG["GRID_COUNT"]
        grid_amount = GRID_CONFIG["GRID_AMOUNT"]
        grid_spread = GRID_CONFIG["GRID_SPREAD"]

        logger.info(f"🚀 初始化网格交易: 基准价格=${base_price}, 网格数量={grid_count}")
        trading_state.open_price = base_price

        # 同步订单状态
        await _sync_current_orders()

        success = True
        if trading_state.open_orders_count > 0 or trading_state.close_orders_count > 0:
            # 已有订单
            logger.info("当前账户已有未结订单，跳过初始化")
        else:
            if not trading_state.grid_pause:
                place_spread = grid_spread
                if trading_state.grid_open_spread_alert:
                    place_spread *= 2

                # 使用 GridTrading.place_grid_orders 辅助函数
                # side: 1=Long, -1=Short
                side_param = 1 if not OPEN_SIDE_IS_ASK else -1
                success = await grid_trading.place_grid_orders(
                    side_param, base_price, grid_count, grid_amount, place_spread
                )

        if success:
            # 初始化价格列表
            trading_state.open_prices = calculate_grid_prices(
                base_price, grid_count, grid_spread
            )

            # 设置基础价差
            if len(trading_state.open_prices) > 1:
                trading_state.base_grid_single_price = abs(
                    trading_state.open_prices[1] - trading_state.open_prices[0]
                )
            else:
                trading_state.base_grid_single_price = base_price * (grid_spread / 100)

            trading_state.active_grid_signle_price = trading_state.base_grid_single_price
            trading_state.original_open_prices = trading_state.open_prices.copy()

            trading_state.is_running = True
            return True
        else:
            return False

    except Exception as e:
        logger.exception(f"初始化网格交易时发生错误: {e}")
        return False


async def run_grid_trading(_exchange_type: str = "lighter", grid_config: dict = None):
    """
    运行网格交易系统
    
    Args:
        _exchange_type: 交易所类型
        grid_config: 网格配置参数
    """
    from .grid_state import trading_state, GRID_CONFIG

    setup_logging(_exchange_type)

    if grid_config is None:
        raise ValueError("Grid configuration must be provided")
    
    # 设置全局配置
    set_grid_config(grid_config)
    
    # 配置交易方向
    direction = grid_config.get("DIRECTION", "LONG").upper()
    configure_direction(direction)
    
    if direction == "SHORT":
        logger.info("Configuration set to SHORT Strategy")
    else:
        logger.info("Configuration set to LONG Strategy")

    logger.info("🎯 启动通用网格交易系统")
    logger.info(f"配置参数: {grid_config}")
    logger.info(f"交易所类型: {_exchange_type}")

    # 重新导入配置后的变量
    from .grid_state import (
        GRID_CONFIG as CONFIG,
        OPEN_SIDE_IS_ASK as OPEN_ASK,
    )

    lighter_adapter = create_exchange_adapter(
        exchange_type=_exchange_type, market_id=CONFIG["MARKET_ID"]
    )
    if lighter_adapter is None:
        logger.exception("不支持的交易所类型")
        return
    exchange = lighter_adapter

    await exchange.initialize_client()
    auth, err = await exchange.create_auth_token()
    if err is not None:
        logger.exception(f"创建认证令牌失败: {auth}")
        return

    grid_trading = GridTrading(exchange=exchange, market_id=CONFIG["MARKET_ID"])

    proxy_config = PROXY_URL if PROXY_URL else None
    await exchange.subscribe(
        {
            "market_stats": on_market_stats_update,
            "orders": on_account_all_orders_update,
            "positions": on_account_all_positions_update,
        },
        proxy=proxy_config,
    )

    trading_state.grid_trading = grid_trading

    try:
        await asyncio.sleep(2)
        await _risk_check(start=True)
        if not await initialize_grid_trading(grid_trading):
            logger.exception("网格交易初始化失败，退出")
            return

        counter = 0
        while trading_state.is_running:
            try:
                # 每10秒打印一次网格状态
                await asyncio.sleep(10)

                # 检查仓位状态
                account_info = await exchange.get_account_info()
                if not account_info:
                    logger.info("获取账户信息失败")
                    continue
                positions = account_info.get("positions", {})

                if isinstance(positions, dict):
                    position = next(iter(positions.values())) if positions else None
                else:
                    position = positions[0] if positions else None

                # 处理仓位为空的情况
                if position is None:
                    position_size = 0
                    position_sign = 0
                else:
                    position_size = position.get(
                        "position", position.get("size", position.get("amount", 0))
                    )
                    sign_raw = position.get("sign", position.get("side", 0))
                    if isinstance(sign_raw, str):
                        position_sign = (
                            1
                            if sign_raw.lower() == "buy"
                            else -1 if sign_raw.lower() == "sell" else 0
                        )
                    else:
                        position_sign = int(sign_raw)

                trading_state.current_position_size = round(abs(float(position_size)), 2)
                trading_state.current_position_sign = position_sign
                if position_size is not None:
                    await check_position_limits(trading_state.current_position_size)

                unrealized_pnl = (
                    float(position.get("unrealized_pnl", position.get("pnl", 0)))
                    if position
                    else 0.0
                )

                # 检查当前账户保证金
                trading_state.current_collateral = float(
                    account_info.get("total_equity") or account_info.get("collateral", 0)
                )

                unrealized_collateral = trading_state.current_collateral + unrealized_pnl
                pnl = unrealized_collateral - trading_state.start_collateral

                from .grid_risk import _get_current_pause_position
                current_pause_position = await _get_current_pause_position()
                time_formatted = await seconds_formatter(
                    time.time() - trading_state.start_time
                )
                # 美化日志输出
                log_pnl = round(pnl, 6)
                log_total_profit = round(trading_state.total_profit, 2)
                log_active_profit = round(trading_state.active_profit, 2)
                log_reduce_profit = round(trading_state.available_reduce_profit, 2)
                log_grid_step = round(trading_state.active_grid_signle_price, 2)
                
                logger.info(
                    f"\n"
                    f"════════════════════ 策略运行报告 ════════════════════\n"
                    f"[资产情况] 初始: {round(trading_state.start_collateral, 6)} | 当前: {round(unrealized_collateral, 6)} | 盈亏: {log_pnl}\n"
                    f"[收益统计] 套利: {log_total_profit:<8} | 动态: {log_active_profit:<8} | 减仓: {log_reduce_profit:<8}\n"
                    f"[仓位管理] 当前: {position_size:<8} | 冻结: {current_pause_position:<8} | 可用: {trading_state.available_position_size:<8}\n"
                    f"[运行状态] 耗时: {time_formatted:<8} | 成交: {trading_state.filled_count:<8} | 间距: {log_grid_step:<8}\n"
                    f"[市场行情] 开仓: {trading_state.open_price:<8} | 当前: {trading_state.current_price:<8}\n"
                    f"[活跃订单] 买单: {trading_state.buy_orders} | 卖单: {trading_state.sell_orders}\n"
                    f"════════════════════════════════════════════════════"
                )

                # 获取K线数据
                cs_1m = await grid_trading.candle_stick(market_id=0, resolution="1m")
                trading_state.candle_stick_1m = cs_1m

                # 急跌/急涨 判断 (Rapid Market Move)
                if trading_state.current_price:
                    is_rapid, details = await is_rapid_market_move(
                        cs_1m, trading_state.current_price
                    )
                    if is_rapid:
                        logger.info(f"⚠️ 警告：当前市场剧烈波动中, {details}")

                    # 波动检测 (Dynamic Step Adjustment)
                    atr_value = details.get("atr", 0)
                    trading_state.current_atr = atr_value

                    if atr_value > CONFIG["ATR_THRESHOLD"]:
                        min_step = trading_state.base_grid_single_price
                        max_step = trading_state.base_grid_single_price * 30

                        raw_step = 0.7 * round(atr_value, 2)
                        trading_state.active_grid_signle_price = max(
                            min_step, min(raw_step, max_step)
                        )
                    else:
                        trading_state.active_grid_signle_price = (
                            trading_state.base_grid_single_price
                        )

                        if trading_state.grid_open_spread_alert:
                            # 开仓侧警告时增加价差
                            trading_state.active_grid_signle_price = (
                                trading_state.base_grid_single_price * 2
                            )

                # 定期风控检查 (每60秒)
                if counter % 6 == 0:
                    if trading_state.current_price and "details" in locals():
                        logger.info("波动检测: %s", details | {"result": is_rapid})
                    await _risk_check()

                # 补单
                async with replenish_grid_lock:
                    if time.time() - trading_state.last_replenish_time > 5:
                        await check_current_orders()
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
        await exchange.close()
        logger.info("🔚 网格交易系统已停止")


if __name__ == "__main__":
    # 示例配置
    TEST_CONFIG = {
        "MARKET_ID": 1,
        "GRID_COUNT": 10,
        "GRID_AMOUNT": 0.01,
        "GRID_SPREAD": 0.1,
        "MAX_TOTAL_ORDERS": 20,
        "ALER_POSITION": 1.0,
        "DECREASE_POSITION": 2.0,
        "MAX_POSITION": 5.0,
        "ATR_THRESHOLD": 5.0,
        "DIRECTION": "LONG",  # 或 "SHORT"
    }
    # 运行时导入此函数并传入配置
    pass
