import asyncio
import os
from typing import Any, Dict

from dotenv import load_dotenv

import grid.quant_grid_universal as quant_grid_universal

load_dotenv()


def load_grid_config() -> Dict[str, Any]:
    return {
        "DIRECTION": os.getenv("DIRECTION", "LONG"),
        "GRID_COUNT": int(os.getenv("GRID_COUNT", 3)),
        "GRID_AMOUNT": float(os.getenv("GRID_AMOUNT", 0.01)),
        "GRID_SPREAD": float(os.getenv("GRID_SPREAD", 0.05)),
        "MAX_TOTAL_ORDERS": int(os.getenv("MAX_TOTAL_ORDERS", 10)),
        "MAX_POSITION": float(os.getenv("MAX_POSITION", 1.0)),
        "DECREASE_POSITION": float(os.getenv("DECREASE_POSITION", 0.35)),
        "ALER_POSITION": float(os.getenv("ALER_POSITION", 0.3)),
        # StandX ETH market is fixed for this project.
        "MARKET_ID": 0,
        "ATR_THRESHOLD": int(os.getenv("ATR_THRESHOLD", 7)),
    }


if __name__ == "__main__":
    exchange_type = os.getenv("EXCHANGE_TYPE", "standx").strip().lower()
    if exchange_type != "standx":
        raise ValueError("This project only supports EXCHANGE_TYPE=standx")

    asyncio.run(quant_grid_universal.run_grid_trading("standx", load_grid_config()))
