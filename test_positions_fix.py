#!/usr/bin/env python3
"""
Test script to verify GRVT position data handling fix.
"""

import asyncio
import json
import logging
import sys
import os

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def test_position_normalization():
    """Test the position normalization functionality."""
    
    # Import after path is set
    from perp_lighter.exchanges.grvt_adapter import GrvtAdapter
    
    # Create a mock GRVT adapter instance
    adapter = GrvtAdapter(market_id=0)
    
    # Test GRVT position data (as provided in the issue)
    grvt_position = {
        'event_time': '1767855267557137566',
        'sub_account_id': '397248316538241',
        'instrument': 'ETH_USDT_Perp',
        'size': '0.01',
        'notional': '31.1591',
        'entry_price': '3140.81',
        'exit_price': '0.0',
        'mark_price': '3115.910002182',
        'unrealized_pnl': '-0.248999',
        'realized_pnl': '0.0',
        'total_pnl': '-0.248999',
        'roi': '-0.7927',
        'quote_index_price': '0.999062828',
        'est_liquidation_price': '0.0',
        'leverage': '20.0',
        'cumulative_fee': '-0.000031',
        'cumulative_realized_funding_payment': '0.0',
        'margin_type': 'CROSS'
    }
    
    print("Original GRVT position data:")
    print(json.dumps(grvt_position, indent=2))
    
    # Test normalization
    normalized_position = adapter._normalize_grvt_position(grvt_position)
    
    print("\nNormalized position data:")
    print(json.dumps(normalized_position, indent=2))
    
    # Verify key fields
    assert 'sign' in normalized_position, "Missing 'sign' field"
    assert 'size' in normalized_position, "Missing 'size' field"
    assert isinstance(normalized_position['size'], float), "size should be float"
    assert isinstance(normalized_position['sign'], int), "sign should be int"
    
    # Test with positive size
    assert normalized_position['sign'] == 1, f"Expected sign=1 for positive size, got {normalized_position['sign']}"
    
    print("\n✅ Position normalization test passed!")
    
    # Test with negative size
    grvt_position_negative = grvt_position.copy()
    grvt_position_negative['size'] = '-0.01'
    
    normalized_negative = adapter._normalize_grvt_position(grvt_position_negative)
    assert normalized_negative['sign'] == -1, f"Expected sign=-1 for negative size, got {normalized_negative['sign']}"
    
    print("✅ Negative position test passed!")
    
    # Test with zero size
    grvt_position_zero = grvt_position.copy()
    grvt_position_zero['size'] = '0.0'
    
    normalized_zero = adapter._normalize_grvt_position(grvt_position_zero)
    assert normalized_zero['sign'] == 0, f"Expected sign=0 for zero size, got {normalized_zero['sign']}"
    
    print("✅ Zero position test passed!")


async def test_positions_callback():
    """Test the positions callback with mock data."""
    
    # Import after path is set
    from perp_lighter.exchanges.grvt_adapter import GrvtAdapter
    
    # Create a mock GRVT adapter instance
    adapter = GrvtAdapter(market_id=0)
    
    # Mock callback
    callback_executed = False
    received_positions = None
    
    def mock_callback(account_id, positions):
        nonlocal callback_executed, received_positions
        callback_executed = True
        received_positions = positions
        print(f"Callback received positions for account {account_id}:")
        print(json.dumps(positions, indent=2))
    
    adapter.callbacks['positions'] = mock_callback
    
    # Test message with GRVT position data
    test_message = {
        'feed': [
            {
                'event_time': '1767855267557137566',
                'sub_account_id': '397248316538241',
                'instrument': 'ETH_USDT_Perp',
                'size': '0.01',
                'notional': '31.1591',
                'entry_price': '3140.81',
                'exit_price': '0.0',
                'mark_price': '3115.910002182',
                'unrealized_pnl': '-0.248999',
                'realized_pnl': '0.0',
                'total_pnl': '-0.248999',
                'roi': '-0.7927',
                'quote_index_price': '0.999062828',
                'est_liquidation_price': '0.0',
                'leverage': '20.0',
                'cumulative_fee': '-0.000031',
                'cumulative_realized_funding_payment': '0.0',
                'margin_type': 'CROSS'
            }
        ]
    }
    
    print("Testing positions callback with GRVT data...")
    
    # Call the positions callback
    await adapter._positions_callback(test_message)
    
    # Verify callback was executed
    assert callback_executed, "Callback was not executed"
    assert received_positions is not None, "No positions received"
    assert 'ETH_USDT_Perp' in received_positions, "ETH_USDT_Perp position not found"
    
    eth_position = received_positions['ETH_USDT_Perp']
    assert 'sign' in eth_position, "Position missing 'sign' field"
    assert 'size' in eth_position, "Position missing 'size' field"
    assert isinstance(eth_position['size'], float), "size should be float"
    
    print("✅ Positions callback test passed!")


async def test_string_position_handling():
    """Test handling of string position data (legacy format)."""
    
    # Import after path is set
    from perp_lighter.exchanges.grvt_adapter import GrvtAdapter
    
    # Create a mock GRVT adapter instance
    adapter = GrvtAdapter(market_id=0)
    
    # Mock callback
    callback_executed = False
    received_positions = None
    
    def mock_callback(account_id, positions):
        nonlocal callback_executed, received_positions
        callback_executed = True
        received_positions = positions
        print(f"Callback received positions for account {account_id}:")
        print(json.dumps(positions, indent=2))
    
    adapter.callbacks['positions'] = mock_callback
    
    # Test message with string position data (legacy format)
    test_message = {
        'feed': [
            'ETH_USDT_Perp',  # This should cause the error
            'CROSS'           # This should also cause the error
        ]
    }
    
    print("Testing positions callback with string data (should handle gracefully)...")
    
    # Call the positions callback
    await adapter._positions_callback(test_message)
    
    # Should still execute callback with empty positions
    assert callback_executed, "Callback was not executed"
    assert received_positions == {}, "Should have empty positions for invalid data"
    
    print("✅ String position handling test passed!")


async def main():
    """Run all tests."""
    print("🧪 Running GRVT position handling tests...\n")
    
    try:
        test_position_normalization()
        print()
        await test_positions_callback()
        print()
        await test_string_position_handling()
        print("\n🎉 All tests passed! The GRVT position handling fix is working correctly.")
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True


if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)