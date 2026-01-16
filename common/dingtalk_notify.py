"""
钉钉通知模块
"""

import logging
import aiohttp
from typing import Optional

logger = logging.getLogger(__name__)


class DingTalkNotifier:
    """钉钉机器人消息推送"""

    def __init__(self, webhook: str, keyword: str = "Standx", proxy: Optional[str] = None):
        self.webhook = webhook.strip() if webhook else ""
        self.keyword = keyword
        self.proxy = proxy
        self.enabled = bool(self.webhook)
        
        if self.enabled:
            logger.info(f"钉钉通知已启用, 关键词: {keyword}")
        else:
            logger.info("钉钉通知已关闭 (WEBHOOK 未配置)")

    async def send_message(self, title: str, text: str) -> bool:
        """
        发送钉钉 Markdown 消息
        
        Args:
            title: 消息标题
            text: Markdown 格式的消息内容
            
        Returns:
            是否发送成功
        """
        if not self.enabled:
            return False

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": text,
            }
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.webhook, json=payload, proxy=self.proxy, timeout=10) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        if result.get("errcode") == 0:
                            logger.info("钉钉推送成功")
                            return True
                        else:
                            logger.warning(f"钉钉消息发送失败: {result}")
                            return False
                    else:
                        response_text = await resp.text()
                        logger.warning(f"钉钉消息发送失败: {resp.status}, {response_text}")
                        return False
        except Exception as e:
            logger.error(f"钉钉消息发送异常: {e}")
            return False

    async def notify_position_open(
        self,
        address: str,
        symbol: str,
        side: str,
        price: float,
        qty: float,
        position_qty: float,
        current_price: Optional[float] = None,
    ) -> bool:
        """
        开仓通知（仓位从0变为非0，或仓位增加）
        """
        side_emoji = "🟢" if side.lower() == "buy" else "🔴"
        side_text = "买入开仓" if side.lower() == "buy" else "卖出开仓"
        short_addr = f"{address[:6]}...{address[-4:]}" if len(address) > 10 else address
        
        title = f"{self.keyword} ⚠️ 开仓警报"
        
        logger.info(f"钉钉推送[开仓]: {side_text} {symbol}, 数量={qty}, 价格={price:.2f}, 仓位={position_qty}")
        
        text = f"""### ⚠️ {self.keyword} 开仓警报

{side_emoji} **{side_text}** {symbol}

---

- 💰 成交价格: `{price:.2f}`
- 📊 成交数量: `{qty}`
- 📈 当前仓位: `{position_qty}`
{f'- 📉 市场价格: `{current_price:.2f}`' if current_price else ''}

---

🔑 地址: `{short_addr}`
"""
        return await self.send_message(title, text)

    async def notify_position_reduce(
        self,
        address: str,
        symbol: str,
        side: str,
        price: float,
        qty: float,
        position_qty: float,
        current_price: Optional[float] = None,
    ) -> bool:
        """
        减仓通知（部分平仓，仓位减少但不为零）
        """
        side_emoji = "🟢" if side.lower() == "buy" else "🔴"
        side_text = "买入减仓" if side.lower() == "buy" else "卖出减仓"
        short_addr = f"{address[:6]}...{address[-4:]}" if len(address) > 10 else address
        
        title = f"{self.keyword} 📉 减仓通知"
        
        logger.info(f"钉钉推送[减仓]: {side_text} {symbol}, 数量={qty}, 价格={price:.2f}, 剩余仓位={position_qty}")
        
        text = f"""### 📉 {self.keyword} 减仓通知

{side_emoji} **{side_text}** {symbol}

---

- 💰 减仓价格: `{price:.2f}`
- 📊 减仓数量: `{qty}`
- 📈 剩余仓位: `{position_qty}`
{f'- 📉 市场价格: `{current_price:.2f}`' if current_price else ''}

---

🔑 地址: `{short_addr}`
"""
        return await self.send_message(title, text)

    async def notify_position_cleared(
        self,
        address: str,
        symbol: str,
        price: float,
        qty: float,
        current_price: Optional[float] = None,
    ) -> bool:
        """
        清仓通知（所有仓位已平掉）
        """
        short_addr = f"{address[:6]}...{address[-4:]}" if len(address) > 10 else address
        
        title = f"{self.keyword} 🎉 清仓完成"
        
        logger.info(f"钉钉推送[清仓]: {symbol}, 最后平仓数量={qty}, 价格={price:.2f}")
        
        text = f"""### 🎉 {self.keyword} 清仓完成

✅ **所有仓位已平掉** {symbol}

---

- 💰 最后平仓价格: `{price:.2f}`
- 📊 最后平仓数量: `{qty}`
- 📈 当前仓位: `0`
{f'- 📉 市场价格: `{current_price:.2f}`' if current_price else ''}

---

🔑 地址: `{short_addr}`
"""
        return await self.send_message(title, text)
