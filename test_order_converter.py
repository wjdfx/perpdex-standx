#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Test script for order converter module.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from perp_lighter.exchanges.order_converter import (
    normalize_order_to_ccxt,
    is_lighter_order,
    is_grvt_order,
    convert_lighter_to_ccxt,
    convert_grvt_to_ccxt
)

# Test Lighter order format
lighter_order = {
    "client_order_index": "12345",
    "order_id": "67890",
    "status": "filled",
    "is_ask": True,
    "price": "3100.5",
    "filled_base_amount": "0.01",
    "initial_base_amount": "0.01",
    "symbol": "ETH/USDT"
}

# Test GRVT order format (simplified version of the user's example)
grvt_order = {
    "order_id": "0x0101010403e0117e000000007e62c4cf",
    "sub_account_id": "397248316538241",
    "is_market": False,
    "time_in_force": "GOOD_TILL_TIME",
    "post_only": False,
    "reduce_only": False,
    "legs": [
        {
            "instrument": "ETH_USDT_Perp",
            "size": "0.01",
            "limit_price": "3100.0",
            "is_buying_asset": True
        }
    ],
    "metadata": {
        "client_order_id": "1313015872",
        "create_time": "1767757128940810199",
        "trigger": {
            "trigger_type": "UNSPECIFIED",
            "tpsl": {
                "trigger_by": "UNSPECIFIED",
                "trigger_price": "0.0",
                "close_position": False
            }
        }
    },
    "state": {
        "status": "OPEN",
        "reject_reason": "UNSPECIFIED",
        "book_size": ["0.01"],
        "traded_size": ["0.0"],
        "update_time": "1767757128940810199",
        "avg_fill_price": ["0.0"]
    }
}

def test_order_conversion():
    print("Testing Order Converter...")
    print("=" * 50)
    
    # Test Lighter order detection and conversion
    print("\n1. Testing Lighter Order:")
    print(f"Is Lighter order: {is_lighter_order(lighter_order)}")
    print(f"Is GRVT order: {is_grvt_order(lighter_order)}")
    
    lighter_ccxt = convert_lighter_to_ccxt(lighter_order)
    print(f"Converted Lighter order to CCXT: {lighter_ccxt}")
    
    # Test GRVT order detection and conversion
    print("\n2. Testing GRVT Order:")
    print(f"Is Lighter order: {is_lighter_order(grvt_order)}")
    print(f"Is GRVT order: {is_grvt_order(grvt_order)}")
    
    grvt_ccxt = convert_grvt_to_ccxt(grvt_order)
    print(f"Converted GRVT order to CCXT: {grvt_ccxt}")
    
    # Test automatic normalization
    print("\n3. Testing Automatic Normalization:")
    
    lighter_normalized = normalize_order_to_ccxt(lighter_order)
    print(f"Lighter normalized: {lighter_normalized}")
    
    grvt_normalized = normalize_order_to_ccxt(grvt_order)
    print(f"GRVT normalized: {grvt_normalized}")
    
    # Verify key fields
    print("\n4. Verifying Key Fields:")
    
    # Check Lighter conversion
    assert lighter_normalized['clientOrderId'] == "12345"
    assert lighter_normalized['side'] == "sell"  # is_ask=True -> sell
    assert lighter_normalized['status'] == "closed"  # filled -> closed
    assert abs(lighter_normalized['price'] - 3100.5) < 0.01
    assert abs(lighter_normalized['amount'] - 0.01) < 0.001
    print("✓ Lighter order conversion verified")
    
    # Check GRVT conversion
    assert grvt_normalized['id'] == "0x0101010403e0117e000000007e62c4cf"
    assert grvt_normalized['clientOrderId'] == "1313015872"
    assert grvt_normalized['side'] == "buy"  # is_buying_asset=True -> buy
    assert grvt_normalized['status'] == "open"
    assert abs(grvt_normalized['price'] - 3100.0) < 0.01
    assert abs(grvt_normalized['amount'] - 0.01) < 0.001
    print("✓ GRVT order conversion verified")
    
    print("\n✅ All tests passed!")

if __name__ == "__main__":
    test_order_conversion()