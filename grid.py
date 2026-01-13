import quant.quant_grid_long as quant_grid_long
import asyncio
import sys
from enum import Enum
from typing import Dict, Any


class ExchangeType(Enum):
    LIGHTER = "lighter"
    GRVT = "grvt"
    STANDX = "standx"


# Exchange-specific grid configurations
GRID_CONFIGS = {
    "lighter": {
        "GRID_COUNT": 3,  # 每侧网格数量
        "GRID_AMOUNT": 0.01,  # 单网格挂单量
        "GRID_SPREAD": 0.05,  # 单网格价差（百分比）
        "MAX_TOTAL_ORDERS": 10,  # 最大活跃订单数量
        "MAX_POSITION": 1.0,  # 最大仓位限制
        "DECREASE_POSITION": 0.35,  # 降低仓位触发点
        "ALER_POSITION": 0.3,  # 警告仓位限制
        "MARKET_ID": 0,  # 市场ID
        "ATR_THRESHOLD": 7,  # ATR波动阈值
    },
    "grvt": {
        "GRID_COUNT": 3,  # 每侧网格数量
        "GRID_AMOUNT": 0.01,  # 单网格挂单量
        "GRID_SPREAD": 0.05,  # 单网格价差（百分比）
        "MAX_TOTAL_ORDERS": 10,  # 最大活跃订单数量
        "MAX_POSITION": 1.0,  # 最大仓位限制
        "DECREASE_POSITION": 0.35,  # 降低仓位触发点
        "ALER_POSITION": 0.3,  # 警告仓位限制
        "MARKET_ID": 0,  # 市场ID
        "ATR_THRESHOLD": 7,  # ATR波动阈值
    },
    "standx": {
        "GRID_COUNT": 3,  # 每侧网格数量
        "GRID_AMOUNT": 0.01,  # 单网格挂单量
        "GRID_SPREAD": 0.05,  # 单网格价差（百分比）
        "MAX_TOTAL_ORDERS": 10,  # 最大活跃订单数量
        "MAX_POSITION": 1.0,  # 最大仓位限制
        "DECREASE_POSITION": 0.35,  # 降低仓位触发点
        "ALER_POSITION": 0.3,  # 警告仓位限制
        "MARKET_ID": 0,  # 市场ID
        "ATR_THRESHOLD": 7,  # ATR波动阈值
    }
}


def validate_exchange_type(exchange_type: str) -> str:
    """
    Validate that the exchange type is one of the allowed values
    """
    try:
        return ExchangeType(exchange_type).value
    except ValueError:
        allowed_values = [e.value for e in ExchangeType]
        print(f"Error: Invalid exchange type '{exchange_type}'. Allowed values are: {allowed_values}")
        sys.exit(1)


if __name__ == "__main__":
    # Access arguments via sys.argv
    # sys.argv[0] is the script name, sys.argv[1:] are the actual arguments
    args = sys.argv[1:]
    
    if len(args) == 0:
        # Use default value if no arguments provided
        exchange_type = ExchangeType.LIGHTER.value
    else:
        # Validate the provided exchange type
        exchange_type = validate_exchange_type(args[0])
    
    # Get the appropriate grid configuration for the exchange type
    grid_config = GRID_CONFIGS.get(exchange_type)
    if grid_config is None:
        print(f"Error: No grid configuration found for exchange type '{exchange_type}'")
        sys.exit(1)

    asyncio.run(quant_grid_long.run_grid_trading(exchange_type, grid_config))