"""
配置文件
现在优先从 .env 文件加载配置，未设置时使用默认值
"""

import os
from dotenv import load_dotenv

load_dotenv()

# API配置
BASE_URL = os.getenv('BASE_URL', "https://mainnet.zklighter.elliot.ai")  # 测试网地址

# 交易所配置
EXCHANGE_TYPE = os.getenv('EXCHANGE_TYPE', 'lighter')  # 交易所类型，默认 lighter

# 账户配置 - 使用示例账户信息
L1_ADDRESS = os.getenv('L1_ADDRESS', '')
ACCOUNT_INDEX = int(os.getenv('ACCOUNT_INDEX', ''))
API_KEY_INDEX = int(os.getenv('API_KEY_INDEX', ''))

# API密钥私钥（示例账户）
API_KEY_PRIVATE_KEY = os.getenv('API_KEY_PRIVATE_KEY', '')

# 日志配置
LOG_LEVEL = os.getenv('LOG_LEVEL', "INFO")
LOG_FORMAT = os.getenv('LOG_FORMAT', "")

# PostgreSQL 配置
POSTGRES_HOST = os.getenv('POSTGRES_HOST', 'localhost')
POSTGRES_PORT = int(os.getenv('POSTGRES_PORT', 5432))
POSTGRES_USER = os.getenv('POSTGRES_USER', 'postgres')
POSTGRES_PASSWORD = os.getenv('POSTGRES_PASSWORD', 'yourpassword')
POSTGRES_DB = os.getenv('POSTGRES_DB', 'mydb')

# StandX Maker 策略配置
STANDX_MAKER_SYMBOL = os.getenv('STANDX_MAKER_SYMBOL', 'BTC-USD')
STANDX_MAKER_ORDER_DISTANCE_BPS = float(os.getenv('STANDX_MAKER_ORDER_DISTANCE_BPS', 8))
STANDX_MAKER_CANCEL_DISTANCE_BPS = float(os.getenv('STANDX_MAKER_CANCEL_DISTANCE_BPS', 6))
STANDX_MAKER_REBALANCE_DISTANCE_BPS = float(os.getenv('STANDX_MAKER_REBALANCE_DISTANCE_BPS', 10))
STANDX_MAKER_ORDER_SIZE_BTC = float(os.getenv('STANDX_MAKER_ORDER_SIZE_BTC', 0.01))
STANDX_MAKER_MAX_POSITION_BTC = float(os.getenv('STANDX_MAKER_MAX_POSITION_BTC', 0.02))
STANDX_MAKER_MAX_ATR = float(os.getenv('STANDX_MAKER_MAX_ATR', 60))
STANDX_MAKER_MAX_ORDERS_PER_SIDE = int(os.getenv('STANDX_MAKER_MAX_ORDERS_PER_SIDE', 2))
STANDX_MAKER_SIDE_ORDER_GAP_BPS = float(os.getenv('STANDX_MAKER_SIDE_ORDER_GAP_BPS', 1))
