#!/usr/bin/env python3
"""
Lighter Market Stats WebSocket 客户端示例

这个示例展示了如何使用 MarketStatsWebSocketClient 来订阅和处理市场统计数据
"""

import asyncio
import time
import signal
import sys
from ws_client import MarketStatsWebSocketClient, MultiMarketStatsClient, create_all_markets_client


def simple_market_stats_handler(market_id, market_stats):
    """
    简单的市场统计数据处理函数
    
    Args:
        market_id: 市场ID
        market_stats: 市场统计数据
    """
    print(f"\n📊 市场 {market_id} 数据更新:")
    print(f"   指数价格: ${market_stats.get('index_price', 'N/A')}")
    print(f"   标记价格: ${market_stats.get('mark_price', 'N/A')}")
    print(f"   最新成交价: ${market_stats.get('last_trade_price', 'N/A')}")
    print(f"   未平仓合约: {market_stats.get('open_interest', 'N/A')}")
    print(f"   当前资金费率: {market_stats.get('current_funding_rate', 'N/A')}")
    print(f"   24小时成交量: {market_stats.get('daily_base_token_volume', 'N/A')}")
    print(f"   24小时价格变化: {market_stats.get('daily_price_change', 'N/A')}%")
    print(f"   24小时最高价: ${market_stats.get('daily_price_high', 'N/A')}")
    print(f"   24小时最低价: ${market_stats.get('daily_price_low', 'N/A')}")
    print("-" * 60)


def detailed_market_stats_handler(market_id, market_stats):
    """
    详细的市场统计数据处理函数，包含更多分析
    
    Args:
        market_id: 市场ID
        market_stats: 市场统计数据
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    
    print(f"\n⚡ [{timestamp}] 市场 {market_id} 详细分析:")
    print("=" * 60)
    
    # 价格信息
    index_price = float(market_stats.get('index_price', 0))
    mark_price = float(market_stats.get('mark_price', 0))
    last_price = float(market_stats.get('last_trade_price', 0))
    
    print(f"💰 价格信息:")
    print(f"   指数价格: ${index_price:.2f}")
    print(f"   标记价格: ${mark_price:.2f}")
    print(f"   最新成交: ${last_price:.2f}")
    
    # 价格偏差分析
    if index_price > 0:
        mark_deviation = ((mark_price - index_price) / index_price) * 100
        print(f"   标记价格偏差: {mark_deviation:+.4f}%")
    
    if mark_price > 0:
        last_deviation = ((last_price - mark_price) / mark_price) * 100
        print(f"   最新价偏差: {last_deviation:+.4f}%")
    
    # 市场活动
    print(f"\n📈 市场活动:")
    oi = float(market_stats.get('open_interest', 0))
    volume = float(market_stats.get('daily_base_token_volume', 0))
    
    print(f"   未平仓合约: {oi:.4f}")
    print(f"   24小时成交量: {volume:.4f}")
    
    # 价格变化
    daily_change = float(market_stats.get('daily_price_change', 0))
    daily_high = float(market_stats.get('daily_price_high', 0))
    daily_low = float(market_stats.get('daily_price_low', 0))
    
    change_emoji = "📈" if daily_change > 0 else "📉" if daily_change < 0 else "➡️"
    print(f"\n{change_emoji} 24小时表现:")
    print(f"   价格变化: {daily_change:+.4f}%")
    print(f"   最高价: ${daily_high:.2f}")
    print(f"   最低价: ${daily_low:.2f}")
    
    # 资金费率
    funding_rate = float(market_stats.get('current_funding_rate', 0))
    funding_emoji = "🔥" if funding_rate > 0.01 else "❄️" if funding_rate < -0.01 else "📍"
    print(f"\n{funding_emoji} 资金费率: {funding_rate:.6f}")
    
    # 警告信息
    warnings = []
    if abs(mark_deviation) > 0.5:
        warnings.append("标记价格偏差较大")
    if abs(daily_change) > 10:
        warnings.append("价格波动剧烈")
    if funding_rate > 0.05:
        warnings.append("资金费率过高")
    
    if warnings:
        print(f"\n⚠️  风险提醒:")
        for warning in warnings:
            print(f"   • {warning}")
    
    print("=" * 60)


def example_single_market():
    """
    示例1: 订阅单个市场统计数据
    """
    print("🚀 启动单市场订阅示例...")
    print("订阅市场 0 的统计数据")
    
    # 创建客户端，订阅市场 0
    client = MarketStatsWebSocketClient(
        market_ids=[0],
        on_market_stats_update=detailed_market_stats_handler
    )
    
    try:
        client.run()
    except KeyboardInterrupt:
        print("\n👋 用户中断，停止客户端...")
        client.stop()


def example_all_markets():
    """
    示例2: 订阅所有市场统计数据
    """
    print("🚀 启动全市场订阅示例...")
    print("订阅所有市场的统计数据")
    
    # 创建多市场客户端
    multi_client = MultiMarketStatsClient()
    
    # 创建客户端订阅所有市场
    client = create_all_markets_client(on_update=multi_client.on_market_stats_update)
    
    try:
        client.run()
    except KeyboardInterrupt:
        print("\n👋 用户中断，停止客户端...")
        client.stop()
        
        # 打印市场摘要
        print("\n📊 市场摘要:")
        summary = multi_client.get_market_summary()
        for market_id, data in summary.items():
            print(f"市场 {market_id}: "
                  f"价格=${data['price']} "
                  f"变化={data['change_24h']:.2f}% "
                  f"成交量={data['volume']}")


def example_multiple_markets():
    """
    示例3: 订阅多个指定市场
    """
    print("🚀 启动多市场订阅示例...")
    print("订阅市场 0, 1, 2 的统计数据")
    
    # 创建客户端，订阅多个市场
    client = MarketStatsWebSocketClient(
        market_ids=[0, 1, 2],
        on_market_stats_update=simple_market_stats_handler
    )
    
    try:
        client.run()
    except KeyboardInterrupt:
        print("\n👋 用户中断，停止客户端...")
        client.stop()


async def example_async():
    """
    示例4: 异步 WebSocket 客户端
    """
    print("🚀 启动异步客户端示例...")
    
    client = MarketStatsWebSocketClient(
        market_ids=[0],
        on_market_stats_update=simple_market_stats_handler
    )
    
    try:
        await client.run_async()
    except KeyboardInterrupt:
        print("\n👋 用户中断，停止客户端...")
        client.stop()


def example_dynamic_subscription():
    """
    示例5: 动态添加/移除订阅
    """
    print("🚀 启动动态订阅示例...")
    
    # 初始只订阅市场 0
    client = MarketStatsWebSocketClient(
        market_ids=[0],
        on_market_stats_update=simple_market_stats_handler
    )
    
    def signal_handler(sig, frame):
        """处理键盘中断"""
        print(f"\n📝 收到信号 {sig}")
        if hasattr(client, 'running') and client.running:
            # 添加订阅市场 1
            if len(client.market_ids) == 1:
                print("➕ 添加市场 1 订阅")
                client.add_subscription(1)
            # 添加订阅市场 2
            elif len(client.market_ids) == 2:
                print("➕ 添加市场 2 订阅")
                client.add_subscription(2)
            # 停止客户端
            else:
                print("🛑 停止客户端")
                client.stop()
                sys.exit(0)
    
    # 设置信号处理
    signal.signal(signal.SIGINT, signal_handler)
    
    print("💡 提示: 按 Ctrl+C 动态添加订阅或停止客户端")
    
    try:
        client.run()
    except Exception as e:
        print(f"\n👋 客户端停止: {e}")


def main():
    """
    主函数，展示不同的使用示例
    """
    print("=" * 80)
    print("🎯 Lighter Market Stats WebSocket 客户端示例")
    print("=" * 80)
    print("\n请选择要运行的示例:")
    print("1. 单市场订阅 (市场 0)")
    print("2. 全市场订阅")
    print("3. 多市场订阅 (市场 0, 1, 2)")
    print("4. 异步客户端")
    print("5. 动态订阅管理")
    
    try:
        choice = input("\n请输入选择 (1-5): ").strip()
        
        if choice == "1":
            example_single_market()
        elif choice == "2":
            example_all_markets()
        elif choice == "3":
            example_multiple_markets()
        elif choice == "4":
            asyncio.run(example_async())
        elif choice == "5":
            example_dynamic_subscription()
        else:
            print("❌ 无效选择，运行默认示例...")
            example_single_market()
            
    except KeyboardInterrupt:
        print("\n👋 用户中断，退出程序...")
    except Exception as e:
        print(f"❌ 程序错误: {e}")


if __name__ == "__main__":
    main()