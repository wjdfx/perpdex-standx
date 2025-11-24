"""
配置文件
现在优先从 .env 文件加载配置，未设置时使用默认值
"""

import os
from dotenv import load_dotenv

load_dotenv()

# API配置
BASE_URL = os.getenv('BASE_URL', "https://mainnet.zklighter.elliot.ai")  # 测试网地址

# 账户配置 - 使用示例账户信息
L1_ADDRESS = os.getenv('L1_ADDRESS', '')
ACCOUNT_INDEX = int(os.getenv('ACCOUNT_INDEX', ''))
API_KEY_INDEX = int(os.getenv('API_KEY_INDEX', ''))

# API密钥私钥（示例账户）
API_KEY_PRIVATE_KEY = os.getenv('API_KEY_PRIVATE_KEY', '')

# 日志配置
LOG_LEVEL = os.getenv('LOG_LEVEL', "INFO")
LOG_FORMAT = os.getenv('LOG_FORMAT', "")

# 订单查询配置
DEFAULT_ORDER_LIMIT = int(os.getenv('DEFAULT_ORDER_LIMIT', '50'))  # 默认查询订单数量限制