#!/usr/bin/env python3
"""
Test script for GRVT WebSocket subscription functionality.
This script properly manages async resources and event loop.

Usage:
    python test_grvt_ws.py

Press Ctrl+C to stop the test at any time.
"""

import asyncio
import logging
import os
import signal
import sys
from typing import Dict, Any, Callable

# Add project root to Python path
sys.path.insert(0, '/Users/liguoyang/Documents/code/python-projects/perpdex')

from perp_lighter.exchanges.grvt_adapter import GrvtAdapter
from common.logging_config import setup_logging
from dotenv import load_dotenv

# Configure logging
setup_logging()
logger = logging.getLogger(__name__)

class GracefulShutdown:
    """Helper class for graceful shutdown handling."""
    
    def __init__(self):
        self.shutdown_requested = False
        self.loop = None
        
    def request_shutdown(self, signame):
        """Request shutdown on signal."""
        print(f"\n{'='*50}")
        print(f"Received {signame} signal - shutting down gracefully...")
        print(f"{'='*50}")
        logger.info(f"Received {signame}, requesting shutdown...")
        self.shutdown_requested = True
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)

def create_callback(name: str) -> Callable[[str, Any], None]:
    """Create a callback function with proper logging."""
    def callback(account_id: str, data: Any):
        # logger.info(f"{name} Callback: account_id={account_id}")
        if isinstance(data, (list, dict)):
            logger.info(f"{name}, account_id={account_id}, Data: {data}")
        else:
            logger.info(f"{name}, account_id={account_id}, Data (raw): {data}")
    return callback

async def test_ws_subscription():
    """Main test function for WebSocket subscription."""
    shutdown = GracefulShutdown()
    
    try:
        print("\n" + "="*60)
        print("GRVT WebSocket Subscription Test")
        print("="*60)
        print("Initializing GRVT adapter...")
        print("Press Ctrl+C to stop the test at any time.")
        print("-"*60)
        
        # Initialize adapter
        logger.info("Initializing GRVT adapter...")
        adapter = GrvtAdapter(market_id=0)
        
        # Initialize REST client
        print("Initializing REST client...")
        logger.info("Initializing REST client...")
        await adapter.initialize_client()
        
        # Initialize WebSocket
        print("Initializing WebSocket...")
        logger.info("Initializing WebSocket...")
        await adapter._initialize_ws()
        
        # Define callbacks
        callbacks: Dict[str, Callable] = {
            # 'market_stats': create_callback('Market Stats'),
            'orders': create_callback('Orders'),
            'positions': create_callback('Positions')
        }
        
        # Subscribe to WebSocket channels
        print("Subscribing to WebSocket channels...")
        logger.info("Subscribing to WebSocket channels...")
        await adapter.subscribe(callbacks)
        
        print("\n" + "="*60)
        print("✓ WebSocket subscription active!")
        print("="*60)
        print("Waiting for real-time market data...")
        print("Press Ctrl+C to stop the test.")
        print("-"*60)
        
        # Set up graceful shutdown
        shutdown.loop = asyncio.get_running_loop()
        
        # Run for a limited time (e.g., 60 seconds) or until shutdown
        start_time = asyncio.get_running_loop().time()
        timeout = 60  # seconds
        
        while not shutdown.shutdown_requested:
            elapsed = asyncio.get_running_loop().time() - start_time
            if elapsed >= timeout:
                print(f"\nTest completed after {timeout} seconds")
                logger.info(f"Test completed after {timeout} seconds")
                break
            
            # Show progress every 10 seconds
            if int(elapsed) % 10 == 0 and elapsed > 0:
                print(f"Running... {int(elapsed)}/{timeout} seconds")
            
            await asyncio.sleep(1)
        
        # Cleanup
        print("\nCleaning up WebSocket connections...")
        logger.info("Cleaning up...")
        await adapter.close()
        
        print("✓ Test completed successfully")
        logger.info("Test completed successfully")
        
    except KeyboardInterrupt:
        print("\n" + "="*60)
        print("User interrupted the test")
        print("="*60)
        logger.info("Test interrupted by user")
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        logger.error(f"Test failed with error: {e}", exc_info=True)
        return False
    
    return True

def main():
    """Entry point for the test script."""
    # Load environment variables
    load_dotenv()
    
    # Set up signal handlers for graceful shutdown
    shutdown = GracefulShutdown()
    
    # Register signal handlers
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    for signame in ['SIGINT', 'SIGTERM']:
        loop.add_signal_handler(
            getattr(signal, signame),
            lambda s=signame: shutdown.request_shutdown(s)
        )
    
    try:
        # Run the async test
        result = loop.run_until_complete(test_ws_subscription())
        return 0 if result else 1
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1
    finally:
        # Ensure proper cleanup
        try:
            # Close all asyncio tasks
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            if tasks:
                logger.info(f"Cancelling {len(tasks)} pending tasks...")
                for task in tasks:
                    task.cancel()
                
                # Wait for tasks to cancel
                loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
        except Exception as e:
            logger.warning(f"Error during cleanup: {e}")
        
        # Close the event loop
        if not loop.is_closed():
            loop.close()
            logger.info("Event loop closed")

if __name__ == "__main__":
    sys.exit(main())