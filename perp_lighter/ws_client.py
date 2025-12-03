import json
import logging
import asyncio
import time
from websockets.sync.client import connect
from websockets.asyncio.client import connect as connect_async

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class UnifiedWebSocketClient:
    """
    统一的 WebSocket 客户端，支持在同一个连接中订阅多种类型的消息
    包括市场统计数据和订单簿数据
    """

    def __init__(
        self,
        host="mainnet.zklighter.elliot.ai",
        path="/stream",
        market_stats_ids=None,
        order_book_ids=None,
        account_all_orders_ids=None,
        account_all_positions_ids=None,
        auth_token=None,
        on_market_stats_update=None,
        on_order_book_update=None,
        on_account_all_orders_update=None,
        on_account_all_positions_update=None,
        on_generic_message_update=None,
        auto_reconnect=True,
        max_reconnect_attempts=5,
        initial_reconnect_delay=1,
        max_reconnect_delay=30,
    ):
        """
        初始化统一的 WebSocket 客户端

        Args:
            host: WebSocket 服务器主机地址
            path: WebSocket 路径
            market_stats_ids: 要订阅市场统计数据的市场ID列表，如果为None则不订阅
            order_book_ids: 要订阅订单簿数据的市场ID列表，如果为None则不订阅
            account_all_orders_ids: 要订阅账户所有订单数据的账户ID列表，如果为None则不订阅
            account_all_positions_ids: 要订阅账户所有仓位数据的账户ID列表，如果为None则不订阅
            auth_token: 认证令牌，用于需要认证的频道
            on_market_stats_update: 市场统计数据更新回调函数
            on_order_book_update: 订单簿数据更新回调函数
            on_account_all_orders_update: 账户所有订单数据更新回调函数
            on_account_all_positions_update: 账户所有仓位数据更新回调函数
            on_generic_message_update: 通用消息更新回调函数
            auto_reconnect: 是否启用自动重连，默认为True
            max_reconnect_attempts: 最大重连尝试次数，默认为5
            initial_reconnect_delay: 初始重连延迟（秒），默认为1
            max_reconnect_delay: 最大重连延迟（秒），默认为30
        """
        self.base_url = f"wss://{host}{path}"
        self.market_stats_ids = market_stats_ids
        self.order_book_ids = order_book_ids
        self.account_all_orders_ids = account_all_orders_ids
        self.account_all_positions_ids = account_all_positions_ids
        self.auth_token = auth_token
        
        # 回调函数
        self.on_market_stats_update = on_market_stats_update or self.default_market_stats_handler
        self.on_order_book_update = on_order_book_update or self.default_order_book_handler
        self.on_account_all_orders_update = on_account_all_orders_update or self.default_account_all_orders_handler
        self.on_account_all_positions_update = on_account_all_positions_update or self.default_account_all_positions_handler
        self.on_generic_message_update = on_generic_message_update or self.default_generic_message_handler

        # 存储数据
        self.market_stats_states = {}
        self.order_book_states = {}
        self.account_all_orders_states = {}
        self.account_all_positions_states = {}

        # WebSocket 连接
        self.ws = None
        self.running = False

        # 订阅状态
        self.subscribed_channels = set()
        
        # 重连相关参数
        self.auto_reconnect = auto_reconnect
        self.max_reconnect_attempts = max_reconnect_attempts
        self.initial_reconnect_delay = initial_reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay
        
        # 重连状态
        self.reconnect_attempts = 0
        self.is_reconnecting = False

    def default_market_stats_handler(self, market_id, market_stats):
        """
        默认的市场统计数据处理函数
        """
        logger.info(f"Market {market_id} stats updated:")
        logger.info(f"  Index Price: {market_stats.get('index_price', 'N/A')}")
        logger.info(f"  Mark Price: {market_stats.get('mark_price', 'N/A')}")
        logger.info(f"  Open Interest: {market_stats.get('open_interest', 'N/A')}")
        logger.info(f"  Last Trade Price: {market_stats.get('last_trade_price', 'N/A')}")
        logger.info(f"  Current Funding Rate: {market_stats.get('current_funding_rate', 'N/A')}")
        logger.info(f"  Daily Volume: {market_stats.get('daily_base_token_volume', 'N/A')}")
        logger.info(f"  Daily Price Change: {market_stats.get('daily_price_change', 'N/A')}")
        logger.info("-" * 50)

    def default_order_book_handler(self, market_id, order_book):
        """
        默认的订单簿处理函数
        """
        logger.info(f"Order book for market {market_id} updated:")
        asks = order_book.get('asks', [])
        bids = order_book.get('bids', [])
        logger.info(f"  Asks ({len(asks)}): {asks[:5] if asks else 'None'}")  # 显示前5个
        logger.info(f"  Bids ({len(bids)}): {bids[:5] if bids else 'None'}")  # 显示前5个
        logger.info(f"  Offset: {order_book.get('offset', 'N/A')}")
        logger.info("-" * 50)

    def default_account_all_orders_handler(self, account_id, orders):
        """
        默认的账户所有订单处理函数
        """
        logger.info(f"Account {account_id} all orders updated:")
        for market_index, order_list in orders.items():
            logger.info(f"   Market {market_index}: {len(order_list)} orders")
            for order in order_list[:3]:  # 显示前3个订单
                logger.info(f"     Order ID: {order.get('order_id', 'N/A')}, Status: {order.get('status', 'N/A')}")
        logger.info("-" * 50)

    def default_account_all_positions_handler(self, account_id, positions):
        """
        默认的账户所有仓位处理函数
        """
        logger.info(f"Account {account_id} all positions updated:")
        for market_index, position in positions.items():
            logger.info(f"   Market {market_index}: Position={position.get('position', 'N/A')}, "
                        f"PNL={position.get('unrealized_pnl', 'N/A')}")
        logger.info("-" * 50)

    def default_generic_message_handler(self, message):
        """
        默认的通用消息处理函数
        """
        # logger.info(f"Generic message received:")
        # logger.info(f"  Type: {message.get('type', 'N/A')}")
        # logger.info(f"  Channel: {message.get('channel', 'N/A')}")
        # logger.info(f"  Data: {message}")
        # logger.info("-" * 50)

    def on_message(self, message):
        """
        处理接收到的 WebSocket 消息
        """
        try:
            if isinstance(message, str):
                message = json.loads(message)

            message_type = message.get("type")

            if message_type == "connected":
                self.handle_connected()
            elif message_type == "subscribed/market_stats":
                self.handle_subscribed_market_stats(message)
            elif message_type == "subscribed/order_book":
                self.handle_subscribed_order_book(message)
            elif message_type == "update/market_stats":
                self.handle_update_market_stats(message)
            elif message_type == "update/order_book":
                self.handle_update_order_book(message)
            elif message_type == "update/account_all_orders":
                self.handle_update_account_all_orders(message)
            elif message_type == "update/account_all_positions":
                self.handle_update_account_all_positions(message)
            # elif message_type == "subscribed/account_all_orders":
            #     self.handle_update_account_all_orders(message)
            elif message_type == "subscribed/account_all_positions":
                self.handle_update_account_all_positions(message)
            elif message_type == "ping":
                self.ws.send(json.dumps({"type": "pong"}))
            else:
                # 处理通用消息
                self.handle_generic_message(message)

        except Exception as e:
            logger.error(f"Error processing message: {e}")
            logger.error(f"Message: {message}")

    async def on_message_async(self, message):
        """
        异步处理接收到的 WebSocket 消息
        """
        try:
            if isinstance(message, str):
                message = json.loads(message)

            message_type = message.get("type")

            if message_type == "connected":
                await self.handle_connected_async()
            elif message_type == "subscribed/market_stats":
                await self.handle_subscribed_market_stats_async(message)
            elif message_type == "subscribed/order_book":
                await self.handle_subscribed_order_book_async(message)
            elif message_type == "update/market_stats":
                await self.handle_update_market_stats_async(message)
            elif message_type == "update/order_book":
                await self.handle_update_order_book_async(message)
            elif message_type == "update/account_all_orders":
                await self.handle_update_account_all_orders_async(message)
            elif message_type == "update/account_all_positions":
                await self.handle_update_account_all_positions_async(message)
            # elif message_type == "subscribed/account_all_orders":
            #     await self.handle_update_account_all_orders_async(message)
            elif message_type == "subscribed/account_all_positions":
                await self.handle_update_account_all_positions_async(message)
            elif message_type == "ping":
                await self.ws.send(json.dumps({"type": "pong"}))
                logger.info("发送心跳响应 pong...")
            else:
                # 处理通用消息
                await self.handle_generic_message_async(message)

        except Exception as e:
            logger.error(f"Error processing message: {e}")
            logger.error(f"Message: {message}")

    def handle_connected(self):
        """
        处理连接成功消息
        """
        logger.info("Connected to WebSocket server")

        # 只有传入参数才会订阅市场统计数据
        if self.market_stats_ids is not None:
            if not self.market_stats_ids:
                # 订阅所有市场统计数据
                self.subscribe_all_market_stats()
            else:
                # 订阅指定市场的统计数据
                for market_id in self.market_stats_ids:
                    self.subscribe_market_stats(market_id)

        # 只有传入参数才会订阅订单簿数据
        if self.order_book_ids is not None:
            for market_id in self.order_book_ids:
                self.subscribe_order_book(market_id)

        # 只有传入参数才会订阅账户所有订单数据
        if self.account_all_orders_ids is not None:
            for account_id in self.account_all_orders_ids:
                self.subscribe_account_all_orders(account_id)

        # 只有传入参数才会订阅账户所有仓位数据
        if self.account_all_positions_ids is not None:
            for account_id in self.account_all_positions_ids:
                self.subscribe_account_all_positions(account_id)

    async def handle_connected_async(self):
        """
        异步处理连接成功消息
        """
        logger.info("Connected to WebSocket server")

        # 只有传入参数才会订阅市场统计数据
        if self.market_stats_ids is not None:
            if not self.market_stats_ids:
                # 订阅所有市场统计数据
                await self.subscribe_all_market_stats_async()
            else:
                # 订阅指定市场的统计数据
                for market_id in self.market_stats_ids:
                    await self.subscribe_market_stats_async(market_id)

        # 只有传入参数才会订阅订单簿数据
        if self.order_book_ids is not None:
            for market_id in self.order_book_ids:
                await self.subscribe_order_book_async(market_id)

        # 只有传入参数才会订阅账户所有订单数据
        if self.account_all_orders_ids is not None:
            for account_id in self.account_all_orders_ids:
                await self.subscribe_account_all_orders_async(account_id)

        # 只有传入参数才会订阅账户所有仓位数据
        if self.account_all_positions_ids is not None:
            for account_id in self.account_all_positions_ids:
                await self.subscribe_account_all_positions_async(account_id)

    def subscribe_all_market_stats(self):
        """
        订阅所有市场的统计数据
        """
        subscription_msg = {
            "type": "subscribe",
            "channel": "market_stats/all"
        }
        self.send_message(subscription_msg)
        logger.info("Subscribed to all market stats")

    async def subscribe_all_market_stats_async(self):
        """
        异步订阅所有市场的统计数据
        """
        subscription_msg = {
            "type": "subscribe",
            "channel": "market_stats/all"
        }
        await self.send_message_async(subscription_msg)
        logger.info("Subscribed to all market stats")

    def subscribe_market_stats(self, market_id):
        """
        订阅指定市场的统计数据

        Args:
            market_id: 市场ID
        """
        subscription_msg = {
            "type": "subscribe",
            "channel": f"market_stats/{market_id}"
        }
        self.send_message(subscription_msg)
        logger.info(f"Subscribed to market {market_id} stats")

    async def subscribe_market_stats_async(self, market_id):
        """
        异步订阅指定市场的统计数据

        Args:
            market_id: 市场ID
        """
        subscription_msg = {
            "type": "subscribe",
            "channel": f"market_stats/{market_id}"
        }
        await self.send_message_async(subscription_msg)
        logger.info(f"Subscribed to market {market_id} stats")

    def subscribe_order_book(self, market_id):
        """
        订阅指定市场的订单簿数据

        Args:
            market_id: 市场ID
        """
        subscription_msg = {
            "type": "subscribe",
            "channel": f"order_book/{market_id}"
        }
        self.send_message(subscription_msg)
        logger.info(f"Subscribed to order book for market {market_id}")

    async def subscribe_order_book_async(self, market_id):
        """
        异步订阅指定市场的订单簿数据

        Args:
            market_id: 市场ID
        """
        subscription_msg = {
            "type": "subscribe",
            "channel": f"order_book/{market_id}"
        }
        await self.send_message_async(subscription_msg)
        logger.info(f"Subscribed to order book for market {market_id}")

    def subscribe_account_all_orders(self, account_id):
        """
        订阅指定账户的所有订单数据

        Args:
            account_id: 账户ID
        """
        subscription_msg = {
            "type": "subscribe",
            "channel": f"account_all_orders/{account_id}",
            "auth": self.auth_token
        }
        self.send_message(subscription_msg)
        logger.info(f"Subscribed to account {account_id} all orders")

    async def subscribe_account_all_orders_async(self, account_id):
        """
        异步订阅指定账户的所有订单数据

        Args:
            account_id: 账户ID
        """
        subscription_msg = {
            "type": "subscribe",
            "channel": f"account_all_orders/{account_id}",
            "auth": self.auth_token
        }
        await self.send_message_async(subscription_msg)
        logger.info(f"Subscribed to account {account_id} all orders")

    def subscribe_account_all_positions(self, account_id):
        """
        订阅指定账户的所有仓位数据

        Args:
            account_id: 账户ID
        """
        subscription_msg = {
            "type": "subscribe",
            "channel": f"account_all_positions/{account_id}",
            "auth": self.auth_token
        }
        self.send_message(subscription_msg)
        logger.info(f"Subscribed to account {account_id} all positions")
        
    async def subscribe_account_all_positions_async(self, account_id):
        """
        订阅指定账户的所有仓位数据

        Args:
            account_id: 账户ID
        """
        subscription_msg = {
            "type": "subscribe",
            "channel": f"account_all_positions/{account_id}",
            "auth": self.auth_token
        }
        await self.send_message_async(subscription_msg)
        logger.info(f"Subscribed to account {account_id} all positions")

    def handle_subscribed_market_stats(self, message):
        """
        处理市场统计数据订阅确认消息
        """
        channel = message.get("channel", "")
        logger.info(f"Successfully subscribed to market stats: {channel}")
        self.subscribed_channels.add(channel)

    async def handle_subscribed_market_stats_async(self, message):
        """
        异步处理市场统计数据订阅确认消息
        """
        channel = message.get("channel", "")
        logger.info(f"Successfully subscribed to market stats: {channel}")
        self.subscribed_channels.add(channel)

    def handle_subscribed_order_book(self, message):
        """
        处理订单簿数据订阅确认消息
        """
        channel = message.get("channel", "")
        logger.info(f"Successfully subscribed to order book: {channel}")
        self.subscribed_channels.add(channel)

    async def handle_subscribed_order_book_async(self, message):
        """
        异步处理订单簿数据订阅确认消息
        """
        channel = message.get("channel", "")
        logger.info(f"Successfully subscribed to order book: {channel}")
        self.subscribed_channels.add(channel)

    def handle_update_market_stats(self, message):
        """
        处理市场统计数据更新
        """
        try:
            channel = message.get("channel", "")
            market_stats = message.get("market_stats", {})

            # 从频道名称中提取市场ID
            # 格式: "market_stats:MARKET_ID" 或 "market_stats:all"
            if ":" in channel:
                parts = channel.split(":")
                market_id = parts[1] if len(parts) > 1 else "all"
            else:
                market_id = "unknown"

            # 如果是特定市场的数据，使用 market_id 字段
            if "market_id" in market_stats:
                market_id = str(market_stats["market_id"])

            # 存储市场状态
            self.market_stats_states[market_id] = market_stats

            # 调用回调函数
            self.on_market_stats_update(market_id, market_stats)

        except Exception as e:
            logger.error(f"Error handling market stats update: {e}")

    async def handle_update_market_stats_async(self, message):
        """
        异步处理市场统计数据更新
        """
        try:
            channel = message.get("channel", "")
            market_stats = message.get("market_stats", {})

            # 从频道名称中提取市场ID
            # 格式: "market_stats:MARKET_ID" 或 "market_stats:all"
            if ":" in channel:
                parts = channel.split(":")
                market_id = parts[1] if len(parts) > 1 else "all"
            else:
                market_id = "unknown"

            # 如果是特定市场的数据，使用 market_id 字段
            if "market_id" in market_stats:
                market_id = str(market_stats["market_id"])

            # 存储市场状态
            self.market_stats_states[market_id] = market_stats

            # 调用回调函数
            if asyncio.iscoroutinefunction(self.on_market_stats_update):
                # 如果回调函数是异步的，直接等待它
                await self.on_market_stats_update(market_id, market_stats)
            else:
                # 如果回调函数是同步的，直接调用
                self.on_market_stats_update(market_id, market_stats)

        except Exception as e:
            logger.error(f"Error handling market stats update: {e}")

    def handle_update_order_book(self, message):
        """
        处理订单簿数据更新
        """
        try:
            channel = message.get("channel", "")
            order_book_data = message.get("order_book", {})

            # 从频道名称中提取市场ID
            # 格式: "order_book:MARKET_ID"
            if ":" in channel:
                parts = channel.split(":")
                market_id = parts[1] if len(parts) > 1 else "unknown"
            else:
                market_id = "unknown"

            # 存储订单簿状态
            self.order_book_states[market_id] = order_book_data

            # 调用回调函数
            self.on_order_book_update(market_id, order_book_data)

        except Exception as e:
            logger.error(f"Error handling order book update: {e}")

    async def handle_update_order_book_async(self, message):
        """
        异步处理订单簿数据更新
        """
        try:
            channel = message.get("channel", "")
            order_book_data = message.get("order_book", {})

            # 从频道名称中提取市场ID
            # 格式: "order_book:MARKET_ID"
            if ":" in channel:
                parts = channel.split(":")
                market_id = parts[1] if len(parts) > 1 else "unknown"
            else:
                market_id = "unknown"

            # 存储订单簿状态
            self.order_book_states[market_id] = order_book_data

            # 调用回调函数
            self.on_order_book_update(market_id, order_book_data)

        except Exception as e:
            logger.error(f"Error handling order book update: {e}")

    def handle_update_account_all_orders(self, message):
        """
        处理账户所有订单数据更新
        """
        try:
            channel = message.get("channel", "")
            orders = message.get("orders", {})

            # 从频道名称中提取账户ID
            # 格式: "account_all_orders:ACCOUNT_ID"
            if ":" in channel:
                parts = channel.split(":")
                account_id = parts[1] if len(parts) > 1 else "unknown"
            else:
                account_id = "unknown"

            # 存储账户订单状态
            self.account_all_orders_states[account_id] = orders

            # 调用回调函数
            self.on_account_all_orders_update(account_id, orders)

        except Exception as e:
            logger.error(f"Error handling account all orders update: {e}")

    async def handle_update_account_all_orders_async(self, message):
        """
        异步处理账户所有订单数据更新
        """
        try:
            channel = message.get("channel", "")
            orders = message.get("orders", {})

            # 从频道名称中提取账户ID
            # 格式: "account_all_orders:ACCOUNT_ID"
            if ":" in channel:
                parts = channel.split(":")
                account_id = parts[1] if len(parts) > 1 else "unknown"
            else:
                account_id = "unknown"

            # 存储账户订单状态
            self.account_all_orders_states[account_id] = orders

            # 调用回调函数
            if asyncio.iscoroutinefunction(self.on_account_all_orders_update):
                # 如果回调函数是异步的，直接等待它
                await self.on_account_all_orders_update(account_id, orders)
            else:
                # 如果回调函数是同步的，直接调用
                self.on_account_all_orders_update(account_id, orders)

        except Exception as e:
            logger.error(f"Error handling account all orders update: {e}")

    def handle_update_account_all_positions(self, message):
        """
        处理账户所有仓位数据更新
        """
        try:
            channel = message.get("channel", "")
            positions = message.get("positions", {})

            # 从频道名称中提取账户ID
            # 格式: "account_all_positions:ACCOUNT_ID"
            if ":" in channel:
                parts = channel.split(":")
                account_id = parts[1] if len(parts) > 1 else "unknown"
            else:
                account_id = "unknown"

            # 存储账户仓位状态
            self.account_all_positions_states[account_id] = positions

            # 调用回调函数
            self.on_account_all_positions_update(account_id, positions)

        except Exception:
            logger.exception("Error handling account all positions update")

    async def handle_update_account_all_positions_async(self, message):
        """
        异步处理账户所有仓位数据更新
        """
        try:
            channel = message.get("channel", "")
            positions = message.get("positions", {})

            # 从频道名称中提取账户ID
            # 格式: "account_all_positions:ACCOUNT_ID"
            if ":" in channel:
                parts = channel.split(":")
                account_id = parts[1] if len(parts) > 1 else "unknown"
            else:
                account_id = "unknown"

            # 存储账户仓位状态
            self.account_all_positions_states[account_id] = positions

            # 调用回调函数
            if asyncio.iscoroutinefunction(self.on_account_all_positions_update):
                # 如果回调函数是异步的，直接等待它
                await self.on_account_all_positions_update(account_id, positions)
            else:
                # 如果回调函数是同步的，直接调用
                self.on_account_all_positions_update(account_id, positions)

        except Exception:
            logger.exception("Error handling account all positions update")

    def handle_generic_message(self, message):
        """
        处理通用消息
        """
        try:
            # 调用回调函数
            self.on_generic_message_update(message)
        except Exception as e:
            logger.error(f"Error handling generic message: {e}")

    async def handle_generic_message_async(self, message):
        """
        异步处理通用消息
        """
        try:
            # 调用回调函数
            self.on_generic_message_update(message)
        except Exception as e:
            logger.error(f"Error handling generic message: {e}")

    def send_message(self, message):
        """
        发送消息到 WebSocket 服务器
        """
        if self.ws and self.running:
            try:
                self.ws.send(json.dumps(message))
            except Exception as e:
                logger.error(f"Error sending message: {e}")
        else:
            logger.warning("WebSocket is not connected")

    async def send_message_async(self, message):
        """
        异步发送消息到 WebSocket 服务器
        """
        if self.ws and self.running:
            try:
                await self.ws.send(json.dumps(message))
            except Exception as e:
                logger.error(f"Error sending message: {e}")
        else:
            logger.warning("WebSocket is not connected")

    def send_batch_tx(self, tx_types, tx_infos):
        """
        发送批量交易到 WebSocket 服务器

        Args:
            tx_types: 交易类型列表，格式为 [INTEGER, INTEGER, ...]
            tx_infos: 交易信息列表，格式为 [tx_info, tx_info, ...]

        Returns:
            bool: 发送是否成功
        """
        if not self.ws or not self.running:
            logger.warning("WebSocket is not connected")
            return False

        try:
            message = {
                "type": "jsonapi/sendtxbatch",
                "data": {
                    "tx_types": json.dumps(tx_types),
                    "tx_infos": json.dumps(tx_infos)
                }
            }
            self.ws.send(json.dumps(message))
            logger.info(f"Sent batch transaction with {len(tx_types)} transactions")
            return True
        except Exception as e:
            logger.error(f"Error sending batch transaction: {e}")
            return False

    async def send_batch_tx_async(self, tx_types, tx_infos):
        """
        异步发送批量交易到 WebSocket 服务器

        Args:
            tx_types: 交易类型列表，格式为 [INTEGER, INTEGER, ...]
            tx_infos: 交易信息列表，格式为 [tx_info, tx_info, ...]

        Returns:
            bool: 发送是否成功
        """
        if not self.ws or not self.running:
            logger.warning("WebSocket is not connected")
            return False

        try:
            message = {
                "type": "jsonapi/sendtxbatch",
                "data": {
                    "tx_types": json.dumps(tx_types),
                    "tx_infos": json.dumps(tx_infos)
                }
            }
            await self.ws.send(json.dumps(message))
            logger.info(f"Sent batch transaction with {len(tx_types)} transactions")
            return True
        except Exception as e:
            logger.error(f"Error sending batch transaction: {e}")
            return False

    def send_single_tx(self, tx_type, tx_info):
        """
        发送单个交易到 WebSocket 服务器

        Args:
            tx_type: 交易类型，格式为 INTEGER
            tx_info: 交易信息对象

        Returns:
            bool: 发送是否成功
        """
        if not self.ws or not self.running:
            logger.warning("WebSocket is not connected")
            return False

        try:
            message = {
                "type": "jsonapi/sendtx",
                "data": {
                    "tx_type": tx_type,
                    "tx_info": json.loads(tx_info)
                }
            }
            self.ws.send(json.dumps(message))
            # logger.info(f"Sent single transaction of type {tx_type}")
            return True
        except Exception as e:
            logger.error(f"Error sending single transaction: {e}")
            return False

    async def send_single_tx_async(self, tx_type, tx_info):
        """
        异步发送单个交易到 WebSocket 服务器

        Args:
            tx_type: 交易类型，格式为 INTEGER
            tx_info: 交易信息对象

        Returns:
            bool: 发送是否成功
        """
        if not self.ws or not self.running:
            logger.warning("WebSocket is not connected")
            return False

        try:
            message = {
                "type": "jsonapi/sendtx",
                "data": {
                    "tx_type": tx_type,
                    "tx_info": json.loads(tx_info)
                }
            }
            await self.ws.send(json.dumps(message))
            # logger.info(f"Sent single transaction of type {tx_type}")
            return True
        except Exception as e:
            logger.error(f"Error sending single transaction: {e}")
            return False

    def get_market_stats(self, market_id=None):
        """
        获取存储的市场统计数据

        Args:
            market_id: 市场ID，如果为None则返回所有市场数据

        Returns:
            市场统计数据字典
        """
        if market_id is None:
            return self.market_stats_states
        return self.market_stats_states.get(str(market_id))

    def get_order_book(self, market_id=None):
        """
        获取存储的订单簿数据

        Args:
            market_id: 市场ID，如果为None则返回所有市场数据

        Returns:
            订单簿数据字典
        """
        if market_id is None:
            return self.order_book_states
        return self.order_book_states.get(str(market_id))

    def get_account_all_orders(self, account_id=None):
        """
        获取存储的账户所有订单数据

        Args:
            account_id: 账户ID，如果为None则返回所有账户数据

        Returns:
            账户订单数据字典
        """
        if account_id is None:
            return self.account_all_orders_states
        return self.account_all_orders_states.get(str(account_id))

    def get_account_all_positions(self, account_id=None):
        """
        获取存储的账户所有仓位数据

        Args:
            account_id: 账户ID，如果为None则返回所有账户数据

        Returns:
            账户仓位数据字典
        """
        if account_id is None:
            return self.account_all_positions_states
        return self.account_all_positions_states.get(str(account_id))

    def run(self):
        """
        运行同步 WebSocket 客户端
        """
        try:
            logger.info(f"Connecting to {self.base_url}")
            self.ws = connect(self.base_url)
            self.running = True

            for message in self.ws:
                if not self.running:
                    break
                self.on_message(message)

        except Exception as e:
            logger.error(f"WebSocket error: {e}")
        finally:
            self.running = False
            if self.ws:
                self.ws.close()

    def _calculate_reconnect_delay(self):
        """
        计算重连延迟，使用指数退避算法
        
        Returns:
            float: 重连延迟时间（秒）
        """
        delay = self.initial_reconnect_delay * (2 ** self.reconnect_attempts)
        return min(delay, self.max_reconnect_delay)

    async def run_async(self):
        """
        运行异步 WebSocket 客户端，支持自动重连
        """
        # 设置运行状态为 True，确保循环能够开始
        self.running = True
        
        while self.running and (self.auto_reconnect or self.reconnect_attempts == 0):
            try:
                if self.reconnect_attempts > 0:
                    delay = self._calculate_reconnect_delay()
                    logger.info(f"尝试重连 ({self.reconnect_attempts}/{self.max_reconnect_attempts})，{delay}秒后开始...")
                    await asyncio.sleep(delay)
                
                logger.info(f"Connecting to {self.base_url}")
                self.ws = await connect_async(self.base_url)
                
                from lighter import SignerClient
                from common.config import (
                    BASE_URL,
                    API_KEY_PRIVATE_KEY,
                    ACCOUNT_INDEX,
                    API_KEY_INDEX,
                )
                signer_client = SignerClient(
                    url=BASE_URL,
                    private_key=API_KEY_PRIVATE_KEY,
                    account_index=ACCOUNT_INDEX,
                    api_key_index=API_KEY_INDEX,
                )

                # 创建认证令牌
                expiry = int(time.time()) + 10 * SignerClient.MINUTE
                auth, err = signer_client.create_auth_token_with_expiry(
                    deadline=expiry
                )
                if err is not None:
                    logger.error(f"创建认证令牌失败: {auth}")
                    return
                self.auth_token = auth
                
                # 重连成功，重置重连计数器
                if self.reconnect_attempts > 0:
                    logger.info("重连成功！")
                    self.reconnect_attempts = 0
                    self.is_reconnecting = False

                async for message in self.ws:
                    if not self.running:
                        break
                    await self.on_message_async(message)

            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                
                # 如果启用了自动重连且未达到最大重连次数
                if self.auto_reconnect and self.reconnect_attempts < self.max_reconnect_attempts:
                    self.reconnect_attempts += 1
                    self.is_reconnecting = True
                    logger.warning(f"连接断开，准备第 {self.reconnect_attempts} 次重连...")
                    continue
                else:
                    # 达到最大重连次数或未启用自动重连
                    if self.auto_reconnect and self.reconnect_attempts >= self.max_reconnect_attempts:
                        logger.error(f"已达到最大重连次数 ({self.max_reconnect_attempts})，停止重连")
                    break
            finally:
                if self.ws:
                    await self.ws.close()
                    logger.info("WebSocket connection closed")
        
        # 循环结束，重置运行状态
        self.running = False

    def stop(self):
        """
        停止 WebSocket 客户端
        """
        logger.info("Stopping WebSocket client...")
        self.running = False
        # 重置重连状态
        self.reconnect_attempts = 0
        self.is_reconnecting = False

    def add_market_stats_subscription(self, market_id):
        """
        添加市场统计数据订阅

        Args:
            market_id: 市场ID
        """
        if market_id not in self.market_stats_ids:
            self.market_stats_ids.append(market_id)
            if self.running:
                self.subscribe_market_stats(market_id)

    def add_order_book_subscription(self, market_id):
        """
        添加订单簿数据订阅

        Args:
            market_id: 市场ID
        """
        if market_id not in self.order_book_ids:
            self.order_book_ids.append(market_id)
            if self.running:
                self.subscribe_order_book(market_id)

    def add_account_all_orders_subscription(self, account_id):
        """
        添加账户所有订单数据订阅

        Args:
            account_id: 账户ID
        """
        if account_id not in self.account_all_orders_ids:
            self.account_all_orders_ids.append(account_id)
            if self.running:
                self.subscribe_account_all_orders(account_id)

    def add_account_all_positions_subscription(self, account_id):
        """
        添加账户所有仓位数据订阅

        Args:
            account_id: 账户ID
        """
        if account_id not in self.account_all_positions_ids:
            self.account_all_positions_ids.append(account_id)
            if self.running:
                self.subscribe_account_all_positions(account_id)

    def remove_market_stats_subscription(self, market_id):
        """
        移除市场统计数据订阅（注意：WebSocket API 可能不支持取消订阅）

        Args:
            market_id: 市场ID
        """
        if market_id in self.market_stats_ids:
            self.market_stats_ids.remove(market_id)
            # 注意：Lighter WebSocket API 可能不支持取消订阅
            logger.warning(f"Removed {market_id} from market stats subscription list, but WebSocket API may not support unsubscribe")

    def remove_order_book_subscription(self, market_id):
        """
        移除订单簿数据订阅（注意：WebSocket API 可能不支持取消订阅）

        Args:
            market_id: 市场ID
        """
        if market_id in self.order_book_ids:
            self.order_book_ids.remove(market_id)
            # 注意：Lighter WebSocket API 可能不支持取消订阅
            logger.warning(f"Removed {market_id} from order book subscription list, but WebSocket API may not support unsubscribe")

    def remove_account_all_orders_subscription(self, account_id):
        """
        移除账户所有订单数据订阅（注意：WebSocket API 可能不支持取消订阅）

        Args:
            account_id: 账户ID
        """
        if account_id in self.account_all_orders_ids:
            self.account_all_orders_ids.remove(account_id)
            # 注意：Lighter WebSocket API 可能不支持取消订阅
            logger.warning(f"Removed {account_id} from account all orders subscription list, but WebSocket API may not support unsubscribe")

    def remove_account_all_positions_subscription(self, account_id):
        """
        移除账户所有仓位数据订阅（注意：WebSocket API 可能不支持取消订阅）

        Args:
            account_id: 账户ID
        """
        if account_id in self.account_all_positions_ids:
            self.account_all_positions_ids.remove(account_id)
            # 注意：Lighter WebSocket API 可能不支持取消订阅
            logger.warning(f"Removed {account_id} from account all positions subscription list, but WebSocket API may not support unsubscribe")


def create_unified_client(
    market_stats_ids=None,
    order_book_ids=None,
    account_all_orders_ids=None,
    account_all_positions_ids=None,
    auth_token=None,
    on_market_stats_update=None,
    on_order_book_update=None,
    on_account_all_orders_update=None,
    on_account_all_positions_update=None,
    on_generic_message_update=None,
    auto_reconnect=True,
    max_reconnect_attempts=5,
    initial_reconnect_delay=1,
    max_reconnect_delay=30,
):
    """
    创建统一的 WebSocket 客户端的工厂函数，支持同时订阅多种类型的消息

    Args:
        market_stats_ids: 要订阅市场统计数据的市场ID列表，如果为None则不订阅
        order_book_ids: 要订阅订单簿数据的市场ID列表，如果为None则不订阅
        account_all_orders_ids: 要订阅账户所有订单数据的账户ID列表，如果为None则不订阅
        account_all_positions_ids: 要订阅账户所有仓位数据的账户ID列表，如果为None则不订阅
        auth_token: 认证令牌，用于需要认证的频道
        on_market_stats_update: 市场统计数据更新回调函数
        on_order_book_update: 订单簿数据更新回调函数
        on_account_all_orders_update: 账户所有订单数据更新回调函数
        on_account_all_positions_update: 账户所有仓位数据更新回调函数
        on_generic_message_update: 通用消息更新回调函数
        auto_reconnect: 是否启用自动重连，默认为True
        max_reconnect_attempts: 最大重连尝试次数，默认为5
        initial_reconnect_delay: 初始重连延迟（秒），默认为1
        max_reconnect_delay: 最大重连延迟（秒），默认为30

    Returns:
        UnifiedWebSocketClient 实例
    """
    return UnifiedWebSocketClient(
        market_stats_ids=market_stats_ids,
        order_book_ids=order_book_ids,
        account_all_orders_ids=account_all_orders_ids,
        account_all_positions_ids=account_all_positions_ids,
        auth_token=auth_token,
        on_market_stats_update=on_market_stats_update,
        on_order_book_update=on_order_book_update,
        on_account_all_orders_update=on_account_all_orders_update,
        on_account_all_positions_update=on_account_all_positions_update,
        on_generic_message_update=on_generic_message_update,
        auto_reconnect=auto_reconnect,
        max_reconnect_attempts=max_reconnect_attempts,
        initial_reconnect_delay=initial_reconnect_delay,
        max_reconnect_delay=max_reconnect_delay,
    )
