"""
钉钉推送测试脚本

用法:
    python -m tests.test_dingtalk

测试内容:
    1. 测试钉钉配置是否正确
    2. 发送测试消息验证推送功能
"""

import asyncio
import sys
import os

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from common.config import DINGTALK_WEBHOOK, DINGTALK_KEYWORD
from common.dingtalk_notify import DingTalkNotifier


def print_config():
    """打印当前钉钉配置"""
    print("=" * 50)
    print("钉钉配置检查")
    print("=" * 50)
    
    webhook_display = f"{DINGTALK_WEBHOOK[:30]}...{DINGTALK_WEBHOOK[-10:]}" if len(DINGTALK_WEBHOOK) > 40 else DINGTALK_WEBHOOK or "(未配置)"
    
    print(f"WEBHOOK: {webhook_display}")
    print(f"KEYWORD: {DINGTALK_KEYWORD or '(未配置)'}")
    print()
    
    if not DINGTALK_WEBHOOK:
        print("❌ WEBHOOK 未配置，无法发送通知")
        return False
    
    if not DINGTALK_KEYWORD:
        print("⚠️  KEYWORD 未配置，使用默认值 'Standx'")
    
    print("✅ 配置检查通过")
    return True


async def test_simple_message():
    """测试简单消息发送"""
    print("\n" + "=" * 50)
    print("测试 1: 简单消息发送")
    print("=" * 50)
    
    notifier = DingTalkNotifier(
        webhook=DINGTALK_WEBHOOK,
        keyword=DINGTALK_KEYWORD or "Standx"
    )
    
    if not notifier.enabled:
        print("❌ 通知器未启用")
        return False
    
    title = f"{DINGTALK_KEYWORD or 'Standx'} 测试消息"
    text = f"""### 🧪 {DINGTALK_KEYWORD or 'Standx'} 钉钉推送测试

这是一条测试消息，用于验证钉钉配置是否正确。

---

- 📅 时间: `{__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`
- 🔧 状态: 测试中
"""
    
    print(f"发送标题: {title}")
    print("发送中...")
    
    success = await notifier.send_message(title, text)
    
    if success:
        print("✅ 消息发送成功！请检查钉钉群是否收到消息")
    else:
        print("❌ 消息发送失败，请检查配置")
    
    return success


async def test_open_notification():
    """测试开仓通知"""
    print("\n" + "=" * 50)
    print("测试 2: 开仓通知")
    print("=" * 50)
    
    notifier = DingTalkNotifier(
        webhook=DINGTALK_WEBHOOK,
        keyword=DINGTALK_KEYWORD or "Standx"
    )
    
    if not notifier.enabled:
        print("❌ 通知器未启用")
        return False
    
    # 模拟开仓数据
    test_data = {
        "address": "0x1234567890abcdef1234567890abcdef12345678",
        "symbol": "BTC-USD",
        "side": "buy",
        "price": 95000.50,
        "qty": 0.001,
        "position_qty": 0.001,
        "current_price": 95050.25,
    }
    
    print(f"模拟开仓数据: {test_data}")
    print("发送中...")
    
    success = await notifier.notify_position_open(**test_data)
    
    if success:
        print("✅ 开仓通知发送成功！请检查钉钉群是否收到消息")
    else:
        print("❌ 开仓通知发送失败，请检查配置")
    
    return success


async def test_reduce_notification():
    """测试减仓通知"""
    print("\n" + "=" * 50)
    print("测试 3: 减仓通知")
    print("=" * 50)
    
    notifier = DingTalkNotifier(
        webhook=DINGTALK_WEBHOOK,
        keyword=DINGTALK_KEYWORD or "Standx"
    )
    
    if not notifier.enabled:
        print("❌ 通知器未启用")
        return False
    
    # 模拟减仓数据（仓位减少但不为零）
    test_data = {
        "address": "0x1234567890abcdef1234567890abcdef12345678",
        "symbol": "BTC-USD",
        "side": "sell",
        "price": 95100.00,
        "qty": 0.001,
        "position_qty": 0.001,  # 剩余仓位
        "current_price": 95100.00,
    }
    
    print(f"模拟减仓数据: {test_data}")
    print("发送中...")
    
    success = await notifier.notify_position_reduce(**test_data)
    
    if success:
        print("✅ 减仓通知发送成功！请检查钉钉群是否收到消息")
    else:
        print("❌ 减仓通知发送失败，请检查配置")
    
    return success


async def test_cleared_notification():
    """测试清仓通知"""
    print("\n" + "=" * 50)
    print("测试 4: 清仓通知")
    print("=" * 50)
    
    notifier = DingTalkNotifier(
        webhook=DINGTALK_WEBHOOK,
        keyword=DINGTALK_KEYWORD or "Standx"
    )
    
    if not notifier.enabled:
        print("❌ 通知器未启用")
        return False
    
    # 模拟清仓数据（所有仓位已平掉）
    test_data = {
        "address": "0x1234567890abcdef1234567890abcdef12345678",
        "symbol": "BTC-USD",
        "price": 95100.00,
        "qty": 0.001,
        "current_price": 95100.00,
    }
    
    print(f"模拟清仓数据: {test_data}")
    print("发送中...")
    
    success = await notifier.notify_position_cleared(**test_data)
    
    if success:
        print("✅ 清仓通知发送成功！请检查钉钉群是否收到消息")
    else:
        print("❌ 清仓通知发送失败，请检查配置")
    
    return success


async def main():
    """主测试函数"""
    print("\n" + "🔔 钉钉推送测试工具 🔔".center(50))
    print()
    
    # 检查配置
    if not print_config():
        return
    
    # 选择测试类型
    print("\n选择测试类型:")
    print("1. 简单消息测试")
    print("2. 开仓通知测试")
    print("3. 减仓通知测试")
    print("4. 清仓通知测试")
    print("5. 全部测试")
    print("q. 退出")
    
    choice = input("\n请输入选择 (1/2/3/4/5/q): ").strip().lower()
    
    if choice == "q":
        print("已退出")
        return
    
    results = []
    
    if choice in ("1", "5"):
        results.append(("简单消息", await test_simple_message()))
    
    if choice in ("2", "5"):
        results.append(("开仓通知", await test_open_notification()))
    
    if choice in ("3", "5"):
        results.append(("减仓通知", await test_reduce_notification()))
    
    if choice in ("4", "5"):
        results.append(("清仓通知", await test_cleared_notification()))
    
    # 打印结果汇总
    print("\n" + "=" * 50)
    print("测试结果汇总")
    print("=" * 50)
    
    for name, success in results:
        status = "✅ 成功" if success else "❌ 失败"
        print(f"  {name}: {status}")
    
    print()


if __name__ == "__main__":
    asyncio.run(main())
