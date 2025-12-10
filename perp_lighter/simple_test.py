import logging
# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

import lighter
import asyncio

from common.config import (
    BASE_URL,
    API_KEY_PRIVATE_KEY,
    ACCOUNT_INDEX,
    API_KEY_INDEX,
)
from .grid_matin import GridTrading


async def main():
    
    signer_client = lighter.SignerClient(
        url=BASE_URL,
        private_key=API_KEY_PRIVATE_KEY,
        account_index=ACCOUNT_INDEX,
        api_key_index=API_KEY_INDEX,
    )
    logger.info(f"{BASE_URL}")
    logger.info(f"Signer Client created with account index {ACCOUNT_INDEX} and API key index {API_KEY_INDEX}")
    logger.info(f"{API_KEY_PRIVATE_KEY}")

    # 创建认证令牌
    auth, err = signer_client.create_auth_token_with_expiry(
        lighter.SignerClient.DEFAULT_10_MIN_AUTH_EXPIRY
    )
    if err is not None:
        logger.exception(f"创建认证令牌失败: {auth}")
        return

    # 创建网格交易实例
    grid_trading = GridTrading(
        ws_client=None,  # 稍后设置
        signer_client=signer_client,
        account_index=ACCOUNT_INDEX,
        api_key_index=API_KEY_INDEX,
        market_id=0,
    )
    
    # orders = await grid_trading.get_orders_by_rest()
    # for order in orders:
    #     logger.info(f"Order: {order}")
    
    counter = 0
    while True:
        df = await grid_trading.candle_stick(market_id=0, resolution="1m")
        # 每10秒执行一次
        await grid_trading.is_jidie(df)
        # 每60秒执行一次（10秒 * 6 = 60秒）
        if counter % 6 == 0:
            df = await grid_trading.candle_stick(market_id=0, resolution="5m")
            await grid_trading.is_yindie(df)
        counter += 1
        await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())