import asyncio
import json
import lighter
import logging
import time
import pandas as pd
from typing import Optional
from lighter.transactions import CreateOrder
from lighter.models import TxHash
from lighter.signer_client import CODE_OK
from strategy import (
    compute_atr,
    compute_vwap,
    compute_rsi,
    generate_signals,
    check_volatility,
    TradingSignal,
)
from config import (
    BASE_URL,
    API_KEY_PRIVATE_KEY,
    ACCOUNT_INDEX,
    API_KEY_INDEX,
)

# 配置日志输出到控制台
# 先清除已有的处理器（如果有的话）
logger = logging.getLogger()
if logger.hasHandlers():
    logger.handlers.clear()

# 创建控制台处理器
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# 创建格式化器
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
console_handler.setFormatter(formatter)

# 添加处理器到根日志记录器
logger.setLevel(logging.DEBUG)
logger.addHandler(console_handler)

# 全局变量，稍后在异步函数中初始化
client = None
transaction_api = None
base_amount_multiplier = pow(10, 4)
price_multiplier = pow(10, 2)


def trim_exception(e: Exception) -> str:
    return str(e).strip().split("\n")[-1]


# 检查开单条件
async def check_open_condition(
    account_api: lighter.AccountApi,
) -> tuple[bool, Optional[lighter.AccountPosition]]:
    account_info_resp = await account_api.account(by="index", value=str(ACCOUNT_INDEX))
    if account_info_resp.code != CODE_OK:
        print(f"获取账户信息失败: {account_info.message}")
        return False, None

    account_info = account_info_resp.accounts[0]
    # 有仓位时，不开单
    if (
        len(account_info.positions) > 0
        and float(account_info.positions[0].position) != 0
    ):
        # print("已有仓位，跳过开单")
        return False, account_info.positions[0]

    return True, None


async def candle_stick(
    candle_api: lighter.CandlestickApi,
    market_id: int,
) -> pd.DataFrame:
    start_time = int(time.time()) - 3600 * 10  # 10小时前
    end_time = int(time.time())
    count_back = 500
    resolution = "1m"

    resp = await candle_api.candlesticks(
        market_id=market_id,
        start_timestamp=start_time,
        end_timestamp=end_time,
        count_back=count_back,
        resolution=resolution,
    )
    if resp.code != CODE_OK:
        print(f"获取K线数据失败: {resp.message}")
        return None

    # 将K线数据转换为DataFrame
    candle_data = []
    for candle in resp.candlesticks:
        candle_data.append(
            {
                "time": candle.timestamp,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume0,
            }
        )

    df = pd.DataFrame(candle_data)

    # 将时间戳转换为可读格式（毫秒值）
    df["time"] = pd.to_datetime(df["time"], unit="ms")

    return df


# 开单
async def open_position(
    client: lighter.SignerClient,
    account_api: lighter.AccountApi,
    transaction_api: lighter.TransactionApi,
    market_id: int,
    order_price: float,
    order_amount: float,
    is_ask: bool,
) -> float:
    client_order_index = int(time.time() * 1000) % 1000000  # Simple unique ID

    next_nonce = await transaction_api.next_nonce(
        account_index=ACCOUNT_INDEX, api_key_index=API_KEY_INDEX
    )
    nonce_value = next_nonce.nonce
    # print(f"nonce值: {nonce_value}")
    tx, tx_hash, error = await client.create_order(
        market_id,
        client_order_index,
        int(order_amount * base_amount_multiplier),
        int(order_price * price_multiplier),
        is_ask,
        order_type=client.ORDER_TYPE_MARKET,
        time_in_force=client.ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL,
        order_expiry=client.DEFAULT_IOC_EXPIRY,
        reduce_only=False,
        nonce=nonce_value,
        api_key_index=API_KEY_INDEX,
    )
    if error is not None:
        print(f"创建市价单失败: {error}")
        return 0

    await asyncio.sleep(0.3)  # 等待订单被填充

    account_info_resp = await account_api.account(by="index", value=str(ACCOUNT_INDEX))
    if account_info_resp.code != CODE_OK:
        print(f"获取账户信息失败: {account_info_resp.message}")
        return 0

    account_info = account_info_resp.accounts[0]
    position = account_info.positions[0]

    # print(f"仓位：{position}")

    avg_entry_price = float(position.avg_entry_price)
    
    retry_count = 0
    while avg_entry_price == 0:
        if retry_count == 2:
            break
        account_info_resp = await account_api.account(by="index", value=str(ACCOUNT_INDEX))
        if account_info_resp.code != CODE_OK:
            print(f"获取账户信息失败: {account_info_resp.message}")
            return 0

        account_info = account_info_resp.accounts[0]
        position = account_info.positions[0]

        print(f"仓位：{position}")

        avg_entry_price = float(position.avg_entry_price)
        retry_count+=1
        
        await asyncio.sleep(0.3)

    return avg_entry_price


# 主逻辑
async def main():
    client = lighter.SignerClient(
        url=BASE_URL,
        private_key=API_KEY_PRIVATE_KEY,
        account_index=ACCOUNT_INDEX,
        api_key_index=API_KEY_INDEX,
    )
    
    configuration = lighter.Configuration(BASE_URL)
    api_client = lighter.ApiClient(configuration)
    transaction_api = lighter.TransactionApi(api_client)
    account_api = lighter.AccountApi(api_client)
    order_api = lighter.OrderApi(api_client)
    candle_api = lighter.CandlestickApi(api_client)
    
    # 市场配置
    market_id = 0
    order_amount = 0.1

    while True:
        check_open, position = await check_open_condition(account_api)
        wait_time = 3
        if not check_open:
            position.position
            print(f"已有仓位，跳过开单，等待{wait_time}秒后重新检查开单条件, 当前pnl: {position.unrealized_pnl}")
            await asyncio.sleep(wait_time)
            continue
        
        # 获得K线数据
        df = await candle_stick(candle_api, market_id)
        
        # 波动检查
        paused, info = check_volatility(df)

        if paused:
            print(f"⚠️ 波动过大，暂停开仓：{info['reason']}, 方差：{info['recent_std']}")
            await asyncio.sleep(30)
            continue
        
        # 计算技术指标
        signal = generate_signals(df)
        
        # 检查是否有信号（可能因为置信度不足而返回None）
        if signal is None:
            print(f"策略建议暂停开仓（置信度不足或市场不明确），等待{wait_time}秒后重新检查...")
            await asyncio.sleep(wait_time)
            continue
        
        # 根据置信度与建议统一执行策略（降低保守度，提升成交率）
        adjusted_strength = max(0.0, min(1.5, signal.strength))
        position_scale = 1.0

        # 统一策略层门槛：TRADE/REDUCE/WAIT 已在策略层根据 0.4 阈值与强弱分数生成
        if signal.recommendation == "TRADE":
            # 正常开仓，但随置信度与强度做细粒度缩放，最低0.4仓
            position_scale = min(1.0, max(0.3, 0.5 * signal.confidence_score + 0.5 * min(1.0, adjusted_strength)))
        elif signal.recommendation == "REDUCE":
            # 小仓试单：上限0.6，下限0.2；并考虑强度衰减
            position_scale = min(0.6, max(0.15, 0.5 * signal.confidence_score + 0.5 * min(1.0, adjusted_strength * 0.6)))
        else:  # WAIT
            # 也允许极小仓探测，进一步降低保守度；仅当极低置信度时跳过
            position_scale = min(0.30, max(0.10, 0.6 * signal.confidence_score + 0.4 * min(1.0, adjusted_strength * 0.5)))

        # 始终开单，通过仓位比例控制风险（不再因置信度/强度过低而跳过）

        # 计算实际下单手数
        effective_order_amount = max(0.01, order_amount * position_scale)
        
        # 打印关键判断数据（不跳过，统一用仓位控制）
        print(f"置信度判断: 方向={signal.direction}, 分数={signal.score}, 强度={signal.strength:.2f}, 置信度={signal.confidence_score:.3f}, 建议={signal.recommendation}, 趋势缺口={signal.trend_gap:.3f}%, 仓位比例={position_scale:.2f}")
        
        is_ask = signal.direction == "SHORT"
        # is_ask = signal.direction == "LONG"
        
        print(f"开单条件：方向：{signal.direction}, 下单手数：{effective_order_amount}, 价格：{signal.entry_price}, 止盈：{signal.take_profit}, 止损：{signal.stop_loss}")

        # # 交易配置
        # leverage = 20
        # tp_percent = 1
        # sl_percent = 1
        slippage = 0.5  # 百分比

        # 获取最后一条数据的close字段值
        last_close_price = df.iloc[-1]["close"]

        # 做空
        if is_ask:
            slippage = -slippage

        order_price = round(last_close_price * (1 + slippage / 100), 2)

        # 开单
        avg_entry_price = await open_position(
            client,
            account_api,
            transaction_api,
            market_id,
            order_price,
            effective_order_amount,
            is_ask,
        )
            
        if avg_entry_price == 0:
            print(f"开单失败, close价格: {last_close_price}, 开单价格: {order_price}")
            await asyncio.sleep(wait_time)
            break

        # print("\n最近K线数据:")
        # print(df.tail(10))

        # 计算技术指标
        signal = generate_signals(
            df, prev_direction=signal.direction, entry_price=avg_entry_price
        )
        
        print(f"止盈止损条件：方向：{signal.direction}, 价格：{signal.entry_price}, 止盈：{signal.take_profit}, 止损：{signal.stop_loss}, 置信度：{signal.confidence_score}")

        # tp_price = avg_entry_price * (1 + tp_percent / leverage / 100)
        # sl_price = avg_entry_price * (1 - sl_percent / leverage / 100)

        tp_slippage_price = round(signal.take_profit * (1 - slippage / 100), 2)
        sl_slippage_price = round(signal.stop_loss * (1 - slippage / 100), 2)

        # use next nonce for getting nonces
        next_nonce = await transaction_api.next_nonce(
            account_index=ACCOUNT_INDEX, api_key_index=API_KEY_INDEX
        )
        nonce_value = next_nonce.nonce

        client_order_index = int(time.time() * 1000) % 1000000  # Simple unique ID
        tp_tx_info, error = client.sign_create_order(
            market_index=0,
            client_order_index=client_order_index,
            base_amount=int(effective_order_amount * base_amount_multiplier),
            price=int(tp_slippage_price * price_multiplier),
            is_ask=not is_ask,
            order_type=client.ORDER_TYPE_TAKE_PROFIT,
            time_in_force=client.ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL,
            reduce_only=True,
            trigger_price=int(signal.take_profit * price_multiplier),
            nonce=nonce_value,
        )
        client_order_index += 1
        nonce_value += 1

        sl_tx_info, error = client.sign_create_order(
            market_index=0,
            client_order_index=client_order_index,
            base_amount=int(effective_order_amount * base_amount_multiplier),
            price=int(sl_slippage_price * price_multiplier),
            is_ask=not is_ask,
            order_type=client.ORDER_TYPE_STOP_LOSS,
            time_in_force=client.ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL,
            reduce_only=True,
            trigger_price=int(signal.stop_loss * price_multiplier),
            nonce=nonce_value,
        )
        # client_order_index += 1
        # nonce_value += 1

        if error is not None:
            print(f"Error signing first order (first batch): {error}")
            break

        tx_types = json.dumps(
            [
                # lighter.SignerClient.TX_TYPE_CREATE_ORDER,
                lighter.SignerClient.TX_TYPE_CREATE_ORDER,
                lighter.SignerClient.TX_TYPE_CREATE_ORDER,
            ]
        )
        tx_infos = json.dumps(
            [
                # buy_tx_info,
                tp_tx_info,
                sl_tx_info,
            ]
        )

        try:
            tx_hashes = await transaction_api.send_tx_batch(
                tx_types=tx_types, tx_infos=tx_infos
            )
            # print(f"Batch transaction successful: {tx_hashes}")
        except Exception as e:
            print(f"Error sending batch transaction 2: {trim_exception(e)}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
