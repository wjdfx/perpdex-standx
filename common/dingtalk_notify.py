"""
钉钉通知模块
"""

import logging
import aiohttp
from typing import Optional

logger = logging.getLogger(__name__)


class DingTalkNotifier:
    """钉钉机器人消息推送"""

    WEBHOOK_BASE = "https://oapi.dingtalk.com/robot/send?access_token="

    def __init__(self, access_token: str, proxy: Optional[str] = None):
        self.access_token = access_token
        self.webhook = f"{self.WEBHOOK_BASE}{access_token}" if access_token else ""
        self.proxy = proxy
        self.enabled = bool(access_token and access_token.strip())
        
        if self.enabled:
            logger.info("钉钉通知已启用")
        else:
            logger.info("钉钉通知已关闭 (ACCESS_TOKEN 未配置)")

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
                            logger.debug("钉钉消息发送成功")
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

    async def notify_order_filled(
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
        Standx 订单成交通知
        
        Args:
            address: 钱包地址
            symbol: 交易对
            side: 买卖方向
            price: 成交价格
            qty: 成交数量
            position_qty: 当前仓位
            current_price: 当前市场价格
        """
        side_emoji = "🟢" if side.lower() == "buy" else "🔴"
        side_text = "买入" if side.lower() == "buy" else "卖出"
        
        # 截取地址显示
        short_addr = f"{address[:6]}...{address[-4:]}" if len(address) > 10 else address
        
        title = f"Standx {side_text} {symbol}"
        
        text = f"""### 📢 Standx 订单成交通知

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
