import json
import logging
import time
import lighter
from typing import List, Tuple, Optional
from ws_client import UnifiedWebSocketClient
from config import BASE_URL


logger = logging.getLogger(__name__)


class GridTrading:
    """
    网格交易类，用于在基准价格周围创建网格订单
    """

    def __init__(self, ws_client: UnifiedWebSocketClient, signer_client: lighter.SignerClient,
                 account_index: int, api_key_index: int, market_id: int = 0):
        """
        初始化网格交易类

        Args:
            ws_client: WebSocket客户端实例
            signer_client: 签名客户端实例
            account_index: 账户索引
            api_key_index: API密钥索引
            market_id: 市场ID，默认为0
        """
        self.ws_client = ws_client
        self.signer_client = signer_client
        self.account_index = account_index
        self.api_key_index = api_key_index
        self.market_id = market_id

        # 价格和数量乘数（与quant.py保持一致）
        self.base_amount_multiplier = pow(10, 4)
        self.price_multiplier = pow(10, 2)
        
    def check_current_orders(self) -> Optional[List[dict]]:
        """
        检查当前账户的所有订单

        Returns:
            订单列表或None（如果获取失败）
        """
        try:
            account_orders = self.ws_client.get_account_all_orders(self.account_index)
            if account_orders is None:
                logger.warning("无法获取当前账户的订单")
                return None

            logger.info(f"当前账户共有 {len(account_orders)} 个订单")
            return account_orders

        except Exception as e:
            logger.error(f"检查当前订单时发生错误: {e}")
            return None

    def generate_grid_orders(self, base_price: float, grid_count: int,
                            grid_amount: float, grid_spread: float) -> List[Tuple[bool, float, float]]:
        """
        生成双向网格订单列表（中性策略）

        Args:
            base_price: 基准价格
            grid_count: 网格数量（每侧）
            grid_amount: 单网格挂单量
            grid_spread: 单网格价差（百分比）

        Returns:
            订单列表，每个元素为 (is_ask, price, amount) 元组
        """
        orders = []

        # 生成买单（ask=False）：基准价格下方
        for i in range(1, grid_count + 1):
            buy_price = base_price * (1 - grid_spread * i / 100)
            orders.append((False, round(buy_price, 2), grid_amount))

        # 生成卖单（ask=True）：基准价格上方
        for i in range(1, grid_count + 1):
            sell_price = base_price * (1 + grid_spread * i / 100)
            orders.append((True, round(sell_price, 2), grid_amount))

        return orders

    async def place_grid_orders(self, base_price: float, grid_count: int,
                               grid_amount: float, grid_spread: float) -> bool:
        """
        放置双向网格订单（中性策略）

        Args:
            base_price: 基准价格
            grid_count: 网格数量（每侧）
            grid_amount: 单网格挂单量
            grid_spread: 单网格价差（百分比）

        Returns:
            bool: 是否成功放置所有订单
        """
        
        configuration = lighter.Configuration(BASE_URL)
        api_client = lighter.ApiClient(configuration)
        transaction_api = lighter.TransactionApi(api_client)
            
        try:
            # 生成网格订单
            orders = self.generate_grid_orders(base_price, grid_count,
                                              grid_amount, grid_spread)

            logger.info(f"生成双向网格订单: 基准价格={base_price}, "
                        f"网格数量={grid_count}, 单网格量={grid_amount}, 价差={grid_spread}%")
            logger.info(f"订单详情: {[(f'卖单' if is_ask else '买单', price, amount) for is_ask, price, amount in orders]}")

            # 获取nonce（需要使用正确的API调用方式）
            # 假设通过transaction_api获取nonce，与quant.py保持一致
            
            next_nonce = await transaction_api.next_nonce(
                account_index=self.account_index, api_key_index=self.api_key_index
            )
            nonce_value = next_nonce.nonce

            # 准备交易信息
            tx_types = []
            tx_infos = []
            client_order_index = int(time.time() * 1000) % 1000000

            for is_ask, price, amount in orders:
                # 签名订单
                tx_info, error = self.signer_client.sign_create_order(
                    market_index=self.market_id,
                    client_order_index=client_order_index,
                    base_amount=int(amount * self.base_amount_multiplier),
                    price=int(price * self.price_multiplier),
                    is_ask=is_ask,
                    order_type=self.signer_client.ORDER_TYPE_LIMIT,  # 限价单
                    time_in_force=self.signer_client.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME,  # GTC
                    reduce_only=False,
                    trigger_price=self.signer_client.NIL_TRIGGER_PRICE,
                    nonce=nonce_value,
                )

                if error is not None:
                    logger.error(f"签名订单失败 (价格={price}, 数量={amount}): {error}")
                    return False

                # 累积交易类型和信息到列表中
                tx_types.append(self.signer_client.TX_TYPE_CREATE_ORDER)
                tx_infos.append(tx_info)

                client_order_index += 1
                nonce_value += 1

            # 发送批量交易
            success = await self.ws_client.send_batch_tx_async(tx_types, tx_infos)

            if success:
                logger.info(f"成功放置 {len(orders)} 个网格订单")
                return True
            else:
                logger.error("批量发送网格订单失败")
                return False

        except Exception as e:
            logger.error(f"放置网格订单时发生错误: {e}")
            return False
        finally:
            await api_client.close()

    def check_position_size(self) -> Optional[float]:
        """
        检查当前仓位大小

        Returns:
            当前仓位大小，如果获取失败返回None
        """
        try:
            positions = self.ws_client.get_account_all_positions(self.account_index)
            if positions is None:
                logger.warning("无法获取当前仓位")
                return None

            # 假设只有一个市场，获取该市场的仓位
            for market_index, position in positions.items():
                position_size = float(position.get('position', '0'))
                return position_size

            return 0.0  # 没有仓位

        except Exception as e:
            logger.error(f"检查仓位大小时发生错误: {e}")
            return None

    async def place_single_order(self, is_ask: bool, price: float, amount: float) -> Tuple[bool, Optional[int]]:
        """
        放置单个订单

        Args:
            is_ask: 是否为卖单
            price: 价格
            amount: 数量

        Returns:
            Tuple[bool, Optional[int]]: (是否成功放置订单, 订单ID)
        """
        configuration = lighter.Configuration(BASE_URL)
        api_client = lighter.ApiClient(configuration)
        transaction_api = lighter.TransactionApi(api_client)
        
        try:
            # 获取nonce
            next_nonce = await transaction_api.next_nonce(
                account_index=self.account_index, api_key_index=self.api_key_index
            )
            nonce_value = next_nonce.nonce

            # 签名订单
            order_id = int(time.time() * 1000) % 1000000
            tx_info, error = self.signer_client.sign_create_order(
                market_index=self.market_id,
                client_order_index=order_id,
                base_amount=int(amount * self.base_amount_multiplier),
                price=int(price * self.price_multiplier),
                is_ask=is_ask,
                order_type=self.signer_client.ORDER_TYPE_LIMIT,
                time_in_force=self.signer_client.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME,
                reduce_only=False,
                trigger_price=self.signer_client.NIL_TRIGGER_PRICE,
                nonce=nonce_value,
            )

            if error is not None:
                logger.error(f"签名订单失败 (价格={price}, 数量={amount}): {error}")
                return False, None

            # 发送交易
            success = await self.ws_client.send_single_tx_async(
                self.signer_client.TX_TYPE_CREATE_ORDER, tx_info
            )

            if success:
                # logger.info(f"成功放置单个订单: {'卖单' if is_ask else '买单'} 价格={price}, 数量={amount}, 订单ID={order_id}")
                return True, order_id
            else:
                logger.error("发送单个订单失败")
                return False, None

        except Exception as e:
            logger.error(f"放置单个订单时发生错误: {e}")
            return False, None
        finally:
            await api_client.close()

    async def cancel_grid_orders(self, order_ids: List[int]) -> bool:
        """
        取消网格订单

        Args:
            order_ids: 要取消的订单ID列表

        Returns:
            bool: 是否成功取消订单
        """
        
        configuration = lighter.Configuration(BASE_URL)
        api_client = lighter.ApiClient(configuration)
        transaction_api = lighter.TransactionApi(api_client)
        
        try:
            next_nonce = await transaction_api.next_nonce(
                account_index=self.account_index, api_key_index=self.api_key_index
            )
            nonce_value = next_nonce.nonce

            # 准备交易信息
            tx_types = []
            tx_infos = []
                
            for order_id in order_ids:       
                tx_info, error = self.signer_client.sign_cancel_order(
                    market_index=self.market_id,
                    order_index=order_id,
                    nonce=nonce_value,
                )
                
                if error is not None:
                    logger.error(f"签名取消订单失败 (订单ID={order_id}): {error}")
                    return False

                # 累积交易类型和信息到列表中
                tx_types.append(self.signer_client.TX_TYPE_CANCEL_ORDER)
                tx_infos.append(tx_info)

                nonce_value += 1
                
            # 发送批量交易
            success = await self.ws_client.send_batch_tx_async(tx_types, tx_infos)

            if success:
                # logger.info(f"成功取消 {order_ids} 网格订单")
                return True
            else:
                logger.error("批量发送网格订单失败")
                return False
                
            return True
        except Exception as e:
            logger.error(f"取消网格订单时发生错误: {e}")
            return False
        finally:
            await api_client.close()
            
    async def get_orders_by_rest(self) -> List[lighter.Order]:
        """
        通过REST API获取当前账户的所有订单

        Returns:
            订单列表或None（如果获取失败）
        """
        
        configuration = lighter.Configuration(BASE_URL)
        api_client = lighter.ApiClient(configuration)
        order_api = lighter.OrderApi(api_client)
        
        try:
            auth, err = self.signer_client.create_auth_token_with_expiry(
                lighter.SignerClient.DEFAULT_10_MIN_AUTH_EXPIRY
            )
            if err is not None:
                logger.error(f"创建认证令牌失败: {auth}")
                return
            orders = await order_api.account_active_orders(
                account_index=self.account_index,
                market_id=self.market_id,
                auth=auth
            )
            return orders.orders
               
        except Exception as e:
            logger.error(f"通过REST检查当前订单时发生错误: {e}")
            return None
        finally:
            await api_client.close()