import quant.quant_grid_long as quant_grid_long
import asyncio
import sys
from enum import Enum


class ExchangeType(Enum):
    LIGHTER = "lighter"
    GRVT = "grvt"
    STANDX = "standx"


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
    
    asyncio.run(quant_grid_long.run_grid_trading(exchange_type))