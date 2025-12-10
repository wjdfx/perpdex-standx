#!/usr/bin/env python3
"""
测试 WebSocket 客户端的自动重连功能
"""

import asyncio
import logging
import signal
import sys
from ws_client import create_unified_client

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 全局变量用于控制测试
running = True

def signal_handler(sig, frame):
    """处理 Ctrl+C 信号"""
    global running
    logger.info("收到停止信号，正在关闭...")
    running = False
    sys.exit(0)

async def test_reconnect():
    """测试自动重连功能"""
    # 注册信号处理器
    signal.signal(signal.SIGINT, signal_handler)
    
    # 创建客户端，启用自动重连
    client = create_unified_client(
        market_stats_ids=[1],  # 订阅市场1的统计数据
        auto_reconnect=True,
        max_reconnect_attempts=3,  # 最多重连3次
        initial_reconnect_delay=2,  # 初始延迟2秒
        max_reconnect_delay=10,     # 最大延迟10秒
    )
    
    logger.info("开始测试 WebSocket 自动重连功能...")
    logger.info("提示：您可以尝试断开网络连接来测试重连功能")
    logger.info("按 Ctrl+C 停止测试")
    
    try:
        # 运行客户端
        await client.run_async()
    except KeyboardInterrupt:
        logger.info("用户中断测试")
    except Exception as e:
        logger.exception(f"测试过程中发生错误: {e}")
    finally:
        client.stop()
        logger.info("测试结束")

if __name__ == "__main__":
    asyncio.run(test_reconnect())