import json
import asyncio
import lighter
from ws_client import create_unified_client
from grid_matin import GridTrading
from common.config import (
    BASE_URL,
    API_KEY_PRIVATE_KEY,
    ACCOUNT_INDEX,
    API_KEY_INDEX,
)

# 全局变量存储最新的订单簿数据
latest_order_books = {}

def detailed_market_stats_handler(market_id, market_stats):
    """
    处理市场统计数据更新的回调函数，打印详细信息
    """
    print(f"\n📈 市场 {market_id} 统计数据更新:")
    print(f"   标记价格: ${market_stats.get('mark_price', 'N/A')}")
    print(f"   指数价格: ${market_stats.get('index_price', 'N/A')}")

def on_order_book_update(market_id, order_book):
    # 更新全局订单簿数据
    if market_id not in latest_order_books:
        latest_order_books[market_id] = {'asks': [], 'bids': []}

    # 合并新数据：只更新有数据的侧
    if 'asks' in order_book and order_book['asks']:
        latest_order_books[market_id]['asks'] = order_book['asks']
    if 'bids' in order_book and order_book['bids']:
        latest_order_books[market_id]['bids'] = order_book['bids']

    # 使用合并后的最新数据计算价差
    current_book = latest_order_books[market_id]
    asks = current_book.get('asks', [])
    bids = current_book.get('bids', [])

    # 计算当前最优ask和bid差价比例
    if asks and bids:
        best_ask = float(asks[0]['price']) if asks else None
        best_bid = float(bids[0]['price']) if bids else None

        if best_ask and best_bid and best_ask > 0:
            spread_ratio = ((best_ask - best_bid) / best_ask) * 100
            print(f"📊 市场 {market_id} 订单簿更新:")
            print(f"   最优卖价 (Ask): ${best_ask}")
            print(f"   最优买价 (Bid): ${best_bid}")
            print(f"   价差比例: {spread_ratio:.4f}%")
            print("-" * 40)
        else:
            print(f"⚠️ 市场 {market_id} 数据异常 - Ask: {best_ask}, Bid: {best_bid}")
    else:
        print(f"⚠️ 市场 {market_id} 订单簿数据不完整 - Asks: {len(asks)}, Bids: {len(bids)}")


def on_account_all_orders_update(account_id, orders):
    """
    处理账户所有订单数据更新的回调函数
    """
    print(f"\n📋 账户 {account_id} 所有订单更新:")
    total_orders = 0
    for market_index, order_list in orders.items():
        order_count = len(order_list)
        total_orders += order_count
        print(f"   市场 {market_index}: {order_count} 个订单")

        # 显示前几个订单的详细信息
        for i, order in enumerate(order_list[:3]):
            order_id = order.get('order_id', 'N/A')
            status = order.get('status', 'N/A')
            is_ask = order.get('is_ask', 'N/A')
            type = order.get('type', 'N/A')
            price = order.get('price', 'N/A')
            size = order.get('initial_base_amount', 'N/A')
            print(f"     订单 {i+1}: ID={order_id}, 状态={status}, 方向={is_ask}, type={type}, 价格={price}, 数量={size}")

    print(f"   总计: {total_orders} 个订单")
    print("-" * 40)


def on_account_all_positions_update(account_id, positions):
    """
    处理账户所有仓位数据更新的回调函数
    """
    print(f"\n📊 账户 {account_id} 所有仓位更新:")
    total_positions = len(positions)
    total_unrealized_pnl = 0.0

    for market_index, position in positions.items():
        position_size = float(position.get('position', '0'))
        unrealized_pnl = float(position.get('unrealized_pnl', '0'))
        realized_pnl = float(position.get('realized_pnl', '0'))
        avg_entry_price = position.get('avg_entry_price', 'N/A')
        liquidation_price = position.get('liquidation_price', 'N/A')
        sign = int(position.get('sign', '0'))

        total_unrealized_pnl += unrealized_pnl

        print(f"   市场 {market_index}:")
        print(f"     仓位大小: {position_size}")
        print(f"     仓位方向: {'多头' if sign > 0 else '空头' if sign < 0 else '无仓位'}")
        print(f"     平均开仓价: ${avg_entry_price}")
        print(f"     未实现盈亏: ${unrealized_pnl}")
        print(f"     已实现盈亏: ${realized_pnl}")
        print(f"     强平价格: ${liquidation_price}")

    print(f"   总计: {total_positions} 个仓位")
    print(f"   总未实现盈亏: ${total_unrealized_pnl:.2f}")
    print("-" * 40)
    
def my_generic_handler(message):
    print(f"Received generic message: {message}")


async def quant():
    """
    快速开始示例 - 订阅单个市场并显示基本统计信息
    """
    print("🚀 Lighter Market Stats WebSocket 客户端快速开始")
    print("=" * 60)
    print("正在连接到 Lighter WebSocket 服务器...")
    print("按 Ctrl+C 停止程序")
    print("=" * 60)
    
    market_id = 41
    
    signer_client = lighter.SignerClient(
        url=BASE_URL,
        private_key=API_KEY_PRIVATE_KEY,
        account_index=ACCOUNT_INDEX,
        api_key_index=API_KEY_INDEX,
    )
    
    auth, err = signer_client.create_auth_token_with_expiry(lighter.SignerClient.DEFAULT_10_MIN_AUTH_EXPIRY)
    # print(f"{auth=}")
    if err is not None:
        print(f"auth token error: {auth}")

    # 创建统一的客户端，同时订阅市场统计和订单簿数据
    client = create_unified_client(
        auth_token=auth,  # 使用认证令牌
        # market_stats_ids=[market_id],  # 订阅市场统计数据
        # on_market_stats_update=detailed_market_stats_handler,
        # order_book_ids=[market_id],    # 订阅订单簿数据
        # on_order_book_update=on_order_book_update,
        account_all_orders_ids=[ACCOUNT_INDEX],  # 订阅账户所有订单数据
        on_account_all_orders_update=on_account_all_orders_update,
        account_all_positions_ids=[ACCOUNT_INDEX],  # 订阅账户所有仓位数据
        on_account_all_positions_update=on_account_all_positions_update,
        on_generic_message_update=my_generic_handler,
    )
    
    try:
        # 运行客户端
        client.run()
    except KeyboardInterrupt:
        print("\n👋 程序已停止")

        # 显示最后接收到的数据
        last_market_stats = client.get_market_stats("0")
        last_order_book = client.get_order_book("0")
        last_account_orders = client.get_account_all_orders(str(ACCOUNT_INDEX))
        last_account_positions = client.get_account_all_positions(str(ACCOUNT_INDEX))

        print("\n📊 最后接收到的数据:")

        if last_market_stats:
            print("市场统计数据:")
            print(f"   标记价格: ${last_market_stats.get('mark_price', 'N/A')}")
            print(f"   指数价格: ${last_market_stats.get('index_price', 'N/A')}")
            print(f"   未平仓合约: {last_market_stats.get('open_interest', 'N/A')}")
            print(f"   24小时变化: {last_market_stats.get('daily_price_change', 'N/A')}%")
        else:
            print("❌ 没有接收到市场统计数据")

        if last_order_book:
            asks = last_order_book.get('asks', [])
            bids = last_order_book.get('bids', [])
            if asks and bids:
                best_ask = float(asks[0]['price']) if asks else None
                best_bid = float(bids[0]['price']) if bids else None
                print("订单簿数据:")
                print(f"   最优卖价 (Ask): ${best_ask}")
                print(f"   最优买价 (Bid): ${best_bid}")
        else:
            print("❌ 没有接收到订单簿数据")

        if last_account_orders:
            print("账户订单数据:")
            total_orders = 0
            for market_index, orders in last_account_orders.items():
                order_count = len(orders)
                total_orders += order_count
                print(f"   市场 {market_index}: {order_count} 个订单")
            print(f"   总计: {total_orders} 个订单")
        else:
            print("❌ 没有接收到账户订单数据")

        if last_account_positions:
            print("账户仓位数据:")
            total_positions = len(last_account_positions)
            total_unrealized_pnl = 0.0
            for market_index, position in last_account_positions.items():
                unrealized_pnl = float(position.get('unrealized_pnl', '0'))
                total_unrealized_pnl += unrealized_pnl
                print(f"   市场 {market_index}: 仓位={position.get('position', 'N/A')}, 盈亏=${unrealized_pnl}")
            print(f"   总计: {total_positions} 个仓位, 总盈亏=${total_unrealized_pnl:.2f}")
        else:
            print("❌ 没有接收到账户仓位数据")
            
if __name__ == "__main__":
    asyncio.run(quant())