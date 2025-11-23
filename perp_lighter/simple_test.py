#!/usr/bin/env python3
"""
简单的测试脚本，验证自动重连功能的基本实现
"""

import sys
import os

# 添加当前目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ws_client import UnifiedWebSocketClient

def test_initialization():
    """测试客户端初始化和重连参数"""
    print("测试 1: 客户端初始化和重连参数")
    
    # 测试默认参数
    client1 = UnifiedWebSocketClient()
    assert client1.auto_reconnect == True
    assert client1.max_reconnect_attempts == 5
    assert client1.initial_reconnect_delay == 1
    assert client1.max_reconnect_delay == 30
    assert client1.reconnect_attempts == 0
    assert client1.is_reconnecting == False
    print("✓ 默认参数测试通过")
    
    # 测试自定义参数
    client2 = UnifiedWebSocketClient(
        auto_reconnect=False,
        max_reconnect_attempts=10,
        initial_reconnect_delay=2,
        max_reconnect_delay=60
    )
    assert client2.auto_reconnect == False
    assert client2.max_reconnect_attempts == 10
    assert client2.initial_reconnect_delay == 2
    assert client2.max_reconnect_delay == 60
    print("✓ 自定义参数测试通过")

def test_reconnect_delay_calculation():
    """测试重连延迟计算"""
    print("\n测试 2: 重连延迟计算")
    
    client = UnifiedWebSocketClient(
        initial_reconnect_delay=1,
        max_reconnect_delay=16
    )
    
    # 测试指数退避算法
    client.reconnect_attempts = 0
    delay = client._calculate_reconnect_delay()
    assert delay == 1, f"期望 1，实际 {delay}"
    
    client.reconnect_attempts = 1
    delay = client._calculate_reconnect_delay()
    assert delay == 2, f"期望 2，实际 {delay}"
    
    client.reconnect_attempts = 2
    delay = client._calculate_reconnect_delay()
    assert delay == 4, f"期望 4，实际 {delay}"
    
    client.reconnect_attempts = 3
    delay = client._calculate_reconnect_delay()
    assert delay == 8, f"期望 8，实际 {delay}"
    
    client.reconnect_attempts = 4
    delay = client._calculate_reconnect_delay()
    assert delay == 16, f"期望 16，实际 {delay}"
    
    # 测试最大延迟限制
    client.reconnect_attempts = 10
    delay = client._calculate_reconnect_delay()
    assert delay == 16, f"期望 16（最大值），实际 {delay}"
    
    print("✓ 重连延迟计算测试通过")

def test_stop_method():
    """测试停止方法是否正确重置重连状态"""
    print("\n测试 3: 停止方法")
    
    client = UnifiedWebSocketClient()
    client.reconnect_attempts = 5
    client.is_reconnecting = True
    
    client.stop()
    
    assert client.reconnect_attempts == 0
    assert client.is_reconnecting == False
    assert client.running == False
    
    print("✓ 停止方法测试通过")

def main():
    """运行所有测试"""
    print("开始测试 WebSocket 客户端自动重连功能...\n")
    
    try:
        test_initialization()
        test_reconnect_delay_calculation()
        test_stop_method()
        
        print("\n🎉 所有测试通过！自动重连功能实现正确。")
        return True
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)