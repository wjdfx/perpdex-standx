#!/usr/bin/env python3
"""
Test script to verify keystore integration with StandXAdapter
"""
import os
import asyncio
from quant.exchanges.standx_adapter import StandXAdapter

async def test_keystore_integration():
    """Test the keystore integration functionality"""
    print("Testing StandXAdapter keystore integration...")
    
    # Test 1: Test direct private key (backward compatibility)
    print("\n=== Test 1: Direct private key (backward compatibility) ===")
    try:
        test_private_key = "0x" + "1" * 64  # Fake private key for testing
        adapter = StandXAdapter(
            market_id=1,
            private_key=test_private_key,
            chain="bsc"
        )
        print(f"✓ Adapter created with direct private key")
        print(f"  Private key matches: {adapter.private_key == test_private_key}")
        print(f"  Wallet address extracted: {adapter.wallet_address}")
        await adapter.close()
    except Exception as e:
        print(f"✗ Error with direct private key: {e}")
    
    # Test 2: Test keystore loading with explicit path
    print("\n=== Test 2: Keystore loading with explicit path ===")
    try:
        # Check if keystore directory exists and has files
        if os.path.exists("keystore") and os.listdir("keystore"):
            keystore_files = [f for f in os.listdir("keystore") if f.startswith('UTC--') and f.endswith('.json')]
            if keystore_files:
                keystore_path = os.path.join("keystore", keystore_files[0])
                print(f"Found keystore file: {keystore_path}")
                
                # Test with keystore path parameter
                adapter = StandXAdapter(
                    market_id=1,
                    chain="bsc",
                    keystore_path=keystore_path
                )
                print(f"✓ Adapter created with keystore")
                print(f"  Private key loaded: {'Yes' if adapter.private_key else 'No'}")
                print(f"  Private key starts with: {adapter.private_key[:10] if adapter.private_key else 'None'}")
                print(f"  Wallet address extracted: {adapter.wallet_address}")
                await adapter.close()
            else:
                print("⚠ No keystore files found in keystore/ directory")
        else:
            print("⚠ No keystore directory or files found")
    except Exception as e:
        print(f"✗ Error with keystore loading: {e}")
    
    # Test 3: Test keystore loading via environment variable
    print("\n=== Test 3: Keystore loading via environment variable ===")
    try:
        # Set environment variable for keystore path
        if os.path.exists("keystore") and os.listdir("keystore"):
            keystore_files = [f for f in os.listdir("keystore") if f.startswith('UTC--') and f.endswith('.json')]
            if keystore_files:
                keystore_path = os.path.join("keystore", keystore_files[0])
                os.environ['STANDX_KEYSTORE_PATH'] = keystore_path
                
                # Also set a dummy password for testing (in real usage, this would be prompted)
                os.environ['STANDX_KEYSTORE_PASSWORD'] = "testpassword123"
                
                adapter = StandXAdapter(
                    market_id=1,
                    chain="bsc"
                )
                print(f"✓ Adapter created with keystore from env variable")
                print(f"  Private key loaded: {'Yes' if adapter.private_key else 'No'}")
                print(f"  Wallet address extracted: {adapter.wallet_address}")
                await adapter.close()
                
                # Clean up env variables
                del os.environ['STANDX_KEYSTORE_PATH']
                del os.environ['STANDX_KEYSTORE_PASSWORD']
            else:
                print("⚠ No keystore files found")
        else:
            print("⚠ No keystore directory or files found")
    except Exception as e:
        print(f"✗ Error with keystore env variable: {e}")
    
    # Test 4: Test wallet address extraction from private key
    print("\n=== Test 4: Wallet address extraction from private key ===")
    try:
        test_private_key = "0x" + "1" * 64  # Fake private key for testing
        adapter = StandXAdapter(
            market_id=1,
            private_key=test_private_key,
            chain="bsc"
            # Note: No wallet_address provided, should be extracted from private key
        )
        print(f"✓ Wallet address automatically extracted: {adapter.wallet_address}")
        print(f"  Expected address format: {len(adapter.wallet_address)} chars, starts with 0x")
        await adapter.close()
    except Exception as e:
        print(f"✗ Error with wallet address extraction: {e}")
    
    print("\n=== All tests completed ===")

if __name__ == "__main__":
    asyncio.run(test_keystore_integration())