import asyncio
import json
import logging
import time
import aiohttp
import pandas as pd
from typing import List, Tuple, Dict, Callable, Any
import lighter
from lighter.signer_client import CODE_OK
from .lighter_ws_client import create_unified_client
from common.config import BASE_URL
from .interfaces import ExchangeInterface
from .order_converter import normalize_order_to_ccxt, normalize_orders_list

logger = logging.getLogger(__name__)

class LighterAdapter(ExchangeInterface):
    def __init__(
        self,
        signer_client: lighter.SignerClient,
        account_index: int,
        api_key_index: int,
        market_id: int = 0
    ):
        self.signer_client = signer_client
        self.account_index = account_index
        self.api_key_index = api_key_index
        self.market_id = market_id
        self.base_amount_multiplier = pow(10, 4)
        self.price_multiplier = pow(10, 2)
        self.api_client = lighter.ApiClient(lighter.Configuration(BASE_URL))
        self.transaction_api = lighter.TransactionApi(self.api_client)
        self.account_api = lighter.AccountApi(self.api_client)
        self.order_api = lighter.OrderApi(self.api_client)
        self.ws_client = None
        self.callbacks: Dict[str, Callable] = {}
        self.ws_task = None

    async def place_multi_orders(self, orders: List[Tuple[bool, float, float]]) -> Tuple[bool, List[str]]:
        try:
            next_nonce = await self.transaction_api.next_nonce(
                account_index=self.account_index, api_key_index=self.api_key_index
            )
            nonce_value = next_nonce.nonce

            tx_types = []
            tx_infos = []
            order_ids = []
            client_order_index = int(time.time() * 1000) % 1000000

            for is_ask, price, amount in orders:
                tx_type, tx_info, tx_hash, error = self.signer_client.sign_create_order(
                    market_index=self.market_id,
                    client_order_index=client_order_index,
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
                    return False, []
                tx_types.append(tx_type)
                tx_infos.append(tx_info)
                order_ids.append(str(client_order_index))
                client_order_index += 1
                nonce_value += 1

            await self.transaction_api.send_tx_batch(
                tx_types=json.dumps(tx_types),
                tx_infos=json.dumps(tx_infos)
            )
            return True, order_ids
        except Exception as e:
            logger.error(f"place_multi_orders error: {e}")
            return False, []

    async def place_single_order(self, is_ask: bool, price: float, amount: float) -> Tuple[bool, str]:
        try:
            next_nonce = await self.transaction_api.next_nonce(
                account_index=self.account_index, api_key_index=self.api_key_index
            )
            nonce_value = next_nonce.nonce
            order_id = int(time.time() * 1000) % 1000000
            tx_type, tx_info, tx_hash, error = self.signer_client.sign_create_order(
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
                return False, ''
            await self.transaction_api.send_tx_batch(
                tx_types=json.dumps([tx_type]),
                tx_infos=json.dumps([tx_info])
            )
            return True, str(order_id)
        except Exception as e:
            logger.error(f"place_single_order error: {e}")
            return False, ''

    async def place_single_market_order(self, is_ask: bool, price: float, amount: float) -> Tuple[bool, str]:
        try:
            next_nonce = await self.transaction_api.next_nonce(
                account_index=self.account_index, api_key_index=self.api_key_index
            )
            nonce_value = next_nonce.nonce
            order_id = int(time.time() * 1000) % 1000000
            slippage = 0.5 if not is_ask else -0.5
            order_price = round(price * (1 + slippage / 100), 2)
            tx_type, tx_info, tx_hash, error = self.signer_client.sign_create_order(
                market_index=self.market_id,
                client_order_index=order_id,
                base_amount=int(amount * self.base_amount_multiplier),
                price=int(order_price * self.price_multiplier),
                is_ask=is_ask,
                order_type=self.signer_client.ORDER_TYPE_MARKET,
                time_in_force=self.signer_client.ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL,
                trigger_price=self.signer_client.NIL_TRIGGER_PRICE,
                order_expiry=self.signer_client.DEFAULT_IOC_EXPIRY,
                nonce=nonce_value,
            )
            if error is not None:
                return False, ''
            await self.transaction_api.send_tx_batch(
                tx_types=json.dumps([tx_type]),
                tx_infos=json.dumps([tx_info])
            )
            return True, str(order_id)
        except Exception as e:
            logger.error(f"place_single_market_order error: {e}")
            return False, ''

    async def cancel_grid_orders(self, order_ids: List[str]) -> bool:
        try:
            next_nonce = await self.transaction_api.next_nonce(
                account_index=self.account_index, api_key_index=self.api_key_index
            )
            nonce_value = next_nonce.nonce
            tx_types = []
            tx_infos = []
            for order_id in order_ids:
                tx_type, tx_info, tx_hash, error = self.signer_client.sign_cancel_order(
                    market_index=self.market_id,
                    order_index=int(order_id),
                    nonce=nonce_value,
                )
                if error is not None:
                    return False
                tx_types.append(tx_type)
                tx_infos.append(tx_info)
                nonce_value += 1
            await self.transaction_api.send_tx_batch(
                tx_types=json.dumps(tx_types),
                tx_infos=json.dumps(tx_infos)
            )
            return True
        except Exception as e:
            logger.error(f"cancel_grid_orders error: {e}")
            return False

    async def modify_grid_order(self, order_id: str, new_price: float, new_amount: float) -> bool:
        try:
            next_nonce = await self.transaction_api.next_nonce(
                account_index=self.account_index, api_key_index=self.api_key_index
            )
            nonce_value = next_nonce.nonce
            tx_type, tx_info, tx_hash, error = self.signer_client.sign_modify_order(
                market_index=self.market_id,
                order_index=int(order_id),
                price=int(new_price * self.price_multiplier),
                base_amount=int(new_amount * self.base_amount_multiplier),
                nonce=nonce_value,
            )
            if error is not None:
                return False
            await self.transaction_api.send_tx_batch(
                tx_types=json.dumps([tx_type]),
                tx_infos=json.dumps([tx_info])
            )
            return True
        except Exception as e:
            logger.error(f"modify_grid_order error: {e}")
            return False

    async def get_orders(self) -> List[dict]:
        try:
            auth, err = self.signer_client.create_auth_token_with_expiry()
            if err:
                return []
            orders_resp = await self.order_api.account_active_orders(
                account_index=self.account_index,
                market_id=self.market_id,
                auth=auth
            )
            if orders_resp.code == CODE_OK:
                # Convert Lighter orders to CCXT format
                lighter_orders = [order.__dict__ for order in orders_resp.orders]
                return [normalize_order_to_ccxt(order) for order in lighter_orders]
            return []
        except Exception as e:
            logger.error(f"get_orders error: {e}")
            return []

    async def get_trades(self, limit: int = 1) -> List[dict]:
        try:
            auth, err = self.signer_client.create_auth_token_with_expiry()
            if err:
                return []
            trades_resp = await self.order_api.trades(
                account_index=self.account_index,
                market_id=self.market_id,
                auth=auth,
                sort_by='timestamp',
                ask_filter=0,
                limit=limit,
            )
            return [trade.__dict__ for trade in trades_resp.trades] if trades_resp.code == CODE_OK else []
        except Exception as e:
            logger.error(f"get_trades error: {e}")
            return []

    async def get_positions(self) -> Dict[str, dict]:
        try:
            account_resp = await self.account_api.account(by='index', value=str(self.account_index))
            if account_resp.code != CODE_OK:
                return {}
            positions = {}
            for pos in account_resp.accounts[0].positions:
                positions[str(pos.market_index)] = pos.__dict__
            return positions
        except Exception as e:
            logger.error(f"get_positions error: {e}")
            return {}

    async def candle_stick(self, market_id: int, resolution: str, count_back: int = 200) -> pd.DataFrame:
        resolution_seconds = {'1m': 60, '5m': 300, '15m': 900}.get(resolution, 60)
        end_time = int(time.time())
        start_time = end_time - resolution_seconds * count_back
        try:
            url = f'{BASE_URL}/api/v1/candles'
            params = {
                'market_id': market_id,
                'resolution': resolution,
                'start_timestamp': start_time,
                'end_timestamp': end_time,
                'count_back': count_back,
            }
            headers = {'accept': 'application/json'}
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers) as resp:
                    data = await resp.json()
                    if data.get('code') != 200:
                        logger.error(f'candle_stick error: {data}')
                        return pd.DataFrame()
                    candlesticks = data['c']
                    candle_data = [{'time': c['t'], 'open': c['o'], 'high': c['h'], 'low': c['l'], 'close': c['c'], 'volume': c['v']} for c in candlesticks]
                    df = pd.DataFrame(candle_data)
                    df['time'] = pd.to_datetime(df['time'], unit='ms')
                    return df
        except Exception as e:
            logger.error(f'candle_stick error: {e}')
            return pd.DataFrame()

    async def subscribe(self, callbacks: Dict[str, Callable[[str, Any], None]], proxy: str = None) -> None:
        self.callbacks = callbacks
        auth, err = self.signer_client.create_auth_token_with_expiry()
        if err:
            logger.error(f'subscribe auth error: {err}')
            return
        self.ws_client = create_unified_client(
            auth_token=auth,
            market_stats_ids=[self.market_id],
            on_market_stats_update=callbacks.get('market_stats'),
            account_all_orders_ids=[self.account_index],
            on_account_all_orders_update=callbacks.get('orders'),
            account_all_positions_ids=[self.account_index],
            on_account_all_positions_update=callbacks.get('positions'),
            proxy=proxy,
        )
        self.ws_task = asyncio.create_task(self.ws_client.run_async())

    async def modify_order(self, order_id: int, new_price: float, new_amount: float) -> bool:
        try:
            next_nonce = await self.transaction_api.next_nonce(
                account_index=self.account_index, api_key_index=self.api_key_index
            )
            nonce_value = next_nonce.nonce
            tx_type, tx_info, tx_hash, error = self.signer_client.sign_modify_order(
                market_index=self.market_id,
                order_index=order_id,
                price=int(new_price * self.price_multiplier),
                base_amount=int(new_amount * self.base_amount_multiplier),
                nonce=nonce_value,
            )
            if error is not None:
                return False
            await self.transaction_api.send_tx_batch(
                tx_types=json.dumps([tx_type]),
                tx_infos=json.dumps([tx_info])
            )
            return True
        except Exception as e:
            logger.error(f"modify_order error: {e}")
            return False

    async def get_orders_by_rest(self) -> List[dict]:
        try:
            auth, err = self.signer_client.create_auth_token_with_expiry()
            if err:
                return []
            orders_resp = await self.order_api.account_active_orders(
                account_index=self.account_index,
                market_id=self.market_id,
                auth=auth
            )
            return [order.__dict__ for order in orders_resp.orders] if orders_resp.code == CODE_OK else []
        except Exception as e:
            logger.error(f"get_orders_by_rest error: {e}")
            return []

    async def get_trades_by_rest(self, ask_filter: int, limit: int) -> List[dict]:
        try:
            auth, err = self.signer_client.create_auth_token_with_expiry()
            if err:
                return []
            trades_resp = await self.order_api.trades(
                account_index=self.account_index,
                market_id=self.market_id,
                auth=auth,
                sort_by='timestamp',
                ask_filter=ask_filter,
                limit=limit,
            )
            return [trade.__dict__ for trade in trades_resp.trades] if trades_resp.code == CODE_OK else []
        except Exception as e:
            logger.error(f"get_trades_by_rest error: {e}")
            return []

    async def get_account(self) -> dict:
        try:
            account_resp = await self.account_api.account(by='index', value=str(self.account_index))
            if account_resp.code != CODE_OK:
                return {}
            return account_resp.accounts[0].__dict__ if account_resp.accounts else {}
        except Exception as e:
            logger.error(f"get_account error: {e}")
            return {}

    async def close(self):
        if self.ws_client:
            self.ws_client.stop()
        if self.ws_task and not self.ws_task.done():
            await self.ws_task
        await self.api_client.close()

    async def initialize_client(self) -> None:
        # Client is already initialized in __init__
        pass

    async def create_auth_token(self) -> Tuple[str, str]:
        auth, err = self.signer_client.create_auth_token_with_expiry()
        return auth, err

    async def get_account_info(self) -> dict:
        try:
            account_resp = await self.account_api.account(by='index', value=str(self.account_index))
            if account_resp.code != CODE_OK:
                return {}
            if not account_resp.accounts:
                return {}
            account = account_resp.accounts[0]
            account_dict = account.__dict__.copy()
            # Convert positions to dict with market_index as key
            account_dict['positions'] = {str(pos.market_id): pos.__dict__ for pos in account.positions}
            return account_dict
        except Exception as e:
            logger.error(f"get_account_info error: {e}")
            return {}
        

async def test():
    from . import create_exchange_adapter
    lighter_adapter = create_exchange_adapter(
        exchange_type="lighter", market_id=0
    )
    account_info = await lighter_adapter.get_account_info()
    print(account_info)
    
if __name__ == "__main__":
    asyncio.run(test())
    