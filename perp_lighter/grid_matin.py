import json
import logging
import time
import lighter
import pandas as pd
from typing import Any, Dict, List, Tuple, Optional
from . import quota
from .ws_client import UnifiedWebSocketClient
from common.config import BASE_URL
from lighter.signer_client import CODE_OK


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

    def generate_grid_orders(self, side: int, base_price: float, grid_count: int,
                            grid_amount: float, grid_spread: float) -> List[Tuple[bool, float, float]]:
        """
        生成双向网格订单列表（中性策略）

        Args:
            side: -1 做空策略，0 中性策略，1 做多策略
            base_price: 基准价格
            grid_count: 网格数量（每侧）
            grid_amount: 单网格挂单量
            grid_spread: 单网格价差（百分比）

        Returns:
            订单列表，每个元素为 (is_ask, price, amount) 元组
        """
        orders = []

        # 生成买单（ask=False）：基准价格下方
        if side != -1:
            for i in range(1, grid_count + 1):
                buy_price = base_price * (1 - grid_spread * i / 100)
                orders.append((False, round(buy_price, 2), grid_amount))

        # 生成卖单（ask=True）：基准价格上方
        if side != 1:
            for i in range(1, grid_count + 1):
                sell_price = base_price * (1 + grid_spread * i / 100)
                orders.append((True, round(sell_price, 2), grid_amount))

        return orders
    
    async def place_grid_orders(self, side: int, base_price: float, grid_count: int,
                               grid_amount: float, grid_spread: float) -> bool:
        """
        放置双向网格订单（中性策略）

        Args:
            side: -1 做空策略，0 中性策略，1 做多策略
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
            orders = self.generate_grid_orders(side, base_price, grid_count,
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
                tx_type, tx_info, error = self.signer_client.sign_create_order(
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
                tx_types.append(tx_type)
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
            tx_type, tx_info, error = self.signer_client.sign_create_order(
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
                tx_type, tx_info
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
                tx_type, tx_info, error = self.signer_client.sign_cancel_order(
                    market_index=self.market_id,
                    order_index=order_id,
                    nonce=nonce_value,
                )
                
                if error is not None:
                    logger.error(f"签名取消订单失败 (订单ID={order_id}): {error}")
                    return False

                # 累积交易类型和信息到列表中
                tx_types.append(tx_type)
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
            expiry = int(time.time()) + 10 * lighter.SignerClient.MINUTE
            auth, err = self.signer_client.create_auth_token_with_expiry(
                deadline=expiry
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

    @staticmethod
    def _resolution_to_seconds(resolution: str) -> int:
        mapping = {
            "1m": 60,
            "3m": 180,
            "5m": 300,
            "15m": 900,
            "30m": 1800,
            "1h": 3600,
            "4h": 14400,
            "1d": 86400,
        }
        if resolution not in mapping:
            raise ValueError(f"Unsupported resolution: {resolution}")
        return mapping[resolution]
    
    async def candle_stick(
        self,
        market_id: int,
        resolution: str = "1m",
        count_back: int = 200,
    ) -> pd.DataFrame:
        """
        通过REST API获取K线数据

        Returns:
            订单列表或None（如果获取失败）
        """
        
        resolution_seconds = self._resolution_to_seconds(resolution)
        end_time = int(time.time())
        start_time = end_time - resolution_seconds * count_back
        
        configuration = lighter.Configuration(BASE_URL)
        api_client = lighter.ApiClient(configuration)
        candle_api = lighter.CandlestickApi(api_client)
        
        try:
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
        except Exception as e:
            logger.error(f"通过REST请求K线数据时发生错误: {e}")
            return None
        finally:
            await api_client.close()

        return df

    async def is_yindie(
        self,
        df: pd.DataFrame,
        ema_period: int = 20,
        adx_period: int = 14,
        rsi_period: int = 14,
    ) -> Tuple[bool, Dict[str, Any]]:
        if df is None or len(df) < max(ema_period, adx_period, rsi_period):
            logger.warning("阴跌检测数据不足")
            return False, {"reason": "insufficient_data"}

        ema_series = quota.compute_ema(df, period=ema_period)
        rsi_series = quota.compute_rsi(df, period=rsi_period)
        adx_series, pdi_series, mdi_series = quota.compute_adx(df, period=adx_period)

        ema_value = float(ema_series.iloc[-1])
        rsi_value = float(rsi_series.iloc[-1])
        adx_value = float(adx_series.iloc[-1])
        pdi_value = float(pdi_series.iloc[-1])
        mdi_value = float(mdi_series.iloc[-1])
        close_value = float(df["close"].iloc[-1])

        is_downtrend = close_value < ema_value
        has_trend = adx_value > 25 and pdi_value < mdi_value
        weak_rsi = rsi_value < 50

        result = is_downtrend and has_trend and weak_rsi
        details = {
            "close": close_value,
            "ema": ema_value,
            "adx": adx_value,
            "pdi": pdi_value,
            "mdi": mdi_value,
            "rsi": rsi_value,
        }
        # logger.info("阴跌检测: %s", details | {"result": result})
        return result, details

    async def is_jidie(
        self,
        df: pd.DataFrame,
        close: Optional[float] = None,
        atr_period: int = 7,
        atr_ma_period: int = 60,
        fall_threshold: float = 15.0,
        atr_multiplier_threshold: float = 3.0,
    ) -> Tuple[bool, Dict[str, Any]]:
        if df is None or len(df) < max(atr_period, atr_ma_period):
            logger.warning("急跌检测数据不足")
            return False, {"reason": "insufficient_data"}

        atr_series = quota.compute_atr(df, period=atr_period)
        atr_ma_series = atr_series.rolling(window=atr_ma_period).mean()

        atr_value = float(atr_series.iloc[-1])
        atr_ma_value = float(atr_ma_series.iloc[-1]) if not pd.isna(atr_ma_series.iloc[-1]) else 0.0
        atr_multiplier = atr_value / atr_ma_value if atr_ma_value else float("inf")

        latest_open = float(df["open"].iloc[-1])
        latest_close = float(df["close"].iloc[-1])
        if close is not None:
            latest_close = close
        fall_amount = latest_open - latest_close

        fall_condition = fall_amount > fall_threshold
        atr_condition = (
            atr_ma_value > 0
            and atr_value > atr_multiplier_threshold * atr_ma_value
        )
        result = fall_condition or atr_condition

        details = {
            "open": latest_open,
            "close": latest_close,
            "fall_amount": fall_amount,
            "atr": atr_value,
            "atr_ma": atr_ma_value,
            "atr_multiplier": atr_multiplier,
        }
        # logger.info("急跌检测: %s", details | {"result": result})
        return result, details
    
    async def current_atr(
        self,
        df: pd.DataFrame,
        atr_period: int = 7,
    ) -> Optional[float]:
        if df is None or len(df) < atr_period:
            logger.warning("ATR计算数据不足")
            return None

        atr_series = quota.compute_atr(df, period=atr_period)
        atr_value = float(atr_series.iloc[-1])
        return atr_value
    
    async def ema_mean_reversion_filter(
        self,
        df: pd.DataFrame,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        EMA乖离率熔断过滤器
        
        计算乖离率：Distance = (Price - 15m_EMA60) / 15m_EMA60
        如果 Distance > 0.02 (偏离2%)：返回True，表示触发熔断
        
        Args:
            df: K线数据DataFrame，包含close列
            
        Returns:
            Tuple[bool, Dict[str, Any]]: (是否触发熔断, 指标数据)
        """
        if df is None or len(df) < 60:
            logger.warning("EMA乖离率熔断检测数据不足")
            return False, {"reason": "insufficient_data"}
        
        try:
            # 计算15分钟EMA60
            ema_60 = quota.compute_ema(df, period=60, column="close")
            ema_value = float(ema_60.iloc[-1])
            
            # 获取当前价格（最新收盘价）
            current_price = float(df["close"].iloc[-1])
            
            # 计算乖离率
            distance = (current_price - ema_value) / ema_value
            
            # 判断是否触发熔断（偏离超过2%）
            is_triggered = distance > 0.02
            
            # 构建指标数据
            metrics = {
                "current_price": current_price,
                "ema_60": ema_value,
                "distance": distance,
                "threshold": 0.02,
                "is_triggered": is_triggered
            }
            
            logger.info(f"EMA乖离率熔断检测: 价格={current_price:.2f}, EMA60={ema_value:.2f}, "
                       f"乖离率={distance:.4f}, 阈值=0.02, 触发={is_triggered}")
            
            return is_triggered, metrics
            
        except Exception as e:
            logger.error(f"EMA乖离率熔断检测时发生错误: {e}")
            return False, {"reason": f"calculation_error: {str(e)}"}