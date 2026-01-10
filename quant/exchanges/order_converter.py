#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Order format converter module for unifying Lighter, GRVT, and StandX order formats to CCXT standard.
"""

import logging
from typing import Dict, Any, Union, List
from datetime import datetime

logger = logging.getLogger(__name__)


def normalize_order_to_ccxt(order: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert exchange-specific order format to CCXT standard format.
    
    Args:
        order: Exchange-specific order dictionary
        
    Returns:
        CCXT standardized order dictionary
    """
    # Determine if this is a Lighter, GRVT, or StandX order based on field structure
    if is_lighter_order(order):
        return convert_lighter_to_ccxt(order)
    elif is_grvt_order(order):
        return convert_grvt_to_ccxt(order)
    elif is_standx_order(order):
        return convert_standx_to_ccxt(order)
    else:
        logger.warning(f"Unknown order format: {order}")
        return convert_unknown_to_ccxt(order)


def is_lighter_order(order: Dict[str, Any]) -> bool:
    """Check if order is in Lighter format."""
    # Lighter orders have client_order_index, is_ask, filled_base_amount fields
    return (
        'client_order_index' in order and
        'is_ask' in order and
        'filled_base_amount' in order and
        'initial_base_amount' in order
    )


def is_grvt_order(order: Dict[str, Any]) -> bool:
    """Check if order is in GRVT format."""
    # GRVT orders have order_id, legs, state, metadata fields
    return (
        'order_id' in order and
        'legs' in order and
        'state' in order and
        'metadata' in order
    )


def is_standx_order(order: Dict[str, Any]) -> bool:
    """Check if order is in StandX format."""
    # StandX orders have id, cl_ord_id, symbol, side, order_type, status fields
    return (
        'id' in order and
        'cl_ord_id' in order and
        'symbol' in order and
        'side' in order and
        'order_type' in order and
        'status' in order and
        'price' in order and
        'qty' in order
    )


def convert_lighter_to_ccxt(order: Dict[str, Any]) -> Dict[str, Any]:
    """Convert Lighter order format to CCXT format."""
    try:
        # Map status
        status_mapping = {
            'open': 'open',
            'filled': 'closed', 
            'canceled': 'canceled',
            'expired': 'expired',
            'rejected': 'rejected'
        }
        
        # Map side
        side = 'sell' if order.get('is_ask', False) else 'buy'
        
        # Calculate remaining amount
        amount = float(order.get('initial_base_amount', 0))
        filled = float(order.get('filled_base_amount', 0))
        remaining = amount - filled
        
        # Build CCXT order
        ccxt_order = {
            'id': str(order.get('order_id', order.get('client_order_index', ''))),
            'clientOrderId': str(order.get('client_order_index', '')),
            'datetime': None,  # Lighter doesn't provide timestamp
            'timestamp': None,  # Lighter doesn't provide timestamp
            'lastTradeTimestamp': None,
            'status': status_mapping.get(order.get('status', 'open'), 'open'),
            'symbol': order.get('symbol', 'ETH/USDT'),
            'type': 'limit',  # Lighter orders are typically limit orders
            'timeInForce': 'GTC',  # Good Till Cancelled
            'side': side,
            'price': float(order.get('price', 0)),
            'average': float(order.get('average_price', order.get('price', 0))),
            'amount': amount,
            'filled': filled,
            'remaining': remaining,
            'cost': filled * float(order.get('price', 0)),
            'trades': [],
            'fee': {},
            'reduceOnly': order.get('reduce_only', False),
            'postOnly': order.get('post_only', False),
            'info': order  # Store original order as info
        }
        
        return ccxt_order
        
    except Exception as e:
        logger.error(f"Error converting Lighter order to CCXT: {e}", exc_info=True)
        return {
            'id': str(order.get('client_order_index', '')),
            'clientOrderId': str(order.get('client_order_index', '')),
            'status': 'unknown',
            'symbol': 'ETH/USDT',
            'side': 'buy',
            'price': 0,
            'amount': 0,
            'filled': 0,
            'remaining': 0,
            'cost': 0,
            'info': order,
            'error': str(e)
        }


def convert_grvt_to_ccxt(order: Dict[str, Any]) -> Dict[str, Any]:
    """Convert GRVT order format to CCXT format."""
    try:
        # Map status
        status_mapping = {
            'OPEN': 'open',
            'FILLED': 'closed',
            'CANCELED': 'canceled',
            'EXPIRED': 'expired',
            'REJECTED': 'rejected',
            'UNSPECIFIED': 'unknown'
        }
        
        # Get leg information (GRVT orders can have multiple legs, we take the first)
        leg = order.get('legs', [{}])[0] if order.get('legs') else {}
        
        # Map side based on is_buying_asset
        side = 'buy' if leg.get('is_buying_asset', True) else 'sell'
        
        # Get state information
        state = order.get('state', {})
        book_size = float(state.get('book_size', ['0'])[0]) if state.get('book_size') else 0
        traded_size = float(state.get('traded_size', ['0'])[0]) if state.get('traded_size') else 0
        
        # Convert timestamp from nanoseconds to milliseconds
        create_time_ns = int(order.get('metadata', {}).get('create_time', '0'))
        timestamp_ms = create_time_ns // 1_000_000  # Convert nanoseconds to milliseconds
        
        # Convert to ISO8601 datetime
        datetime_str = datetime.fromtimestamp(timestamp_ms / 1000).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        
        # Build CCXT order
        ccxt_order = {
            'id': order.get('order_id', ''),
            'clientOrderId': str(order.get('metadata', {}).get('client_order_id', '')),
            'datetime': datetime_str,
            'timestamp': timestamp_ms,
            'lastTradeTimestamp': None,
            'status': status_mapping.get(state.get('status', 'OPEN'), 'open'),
            'symbol': leg.get('instrument', 'ETH/USDT').replace('_', '/'),
            'type': 'market' if order.get('is_market', False) else 'limit',
            'timeInForce': map_time_in_force(order.get('time_in_force', 'GOOD_TILL_TIME')),
            'side': side,
            'price': float(leg.get('limit_price', 0)),
            'average': float(state.get('avg_fill_price', ['0'])[0]) if state.get('avg_fill_price') else 0,
            'amount': book_size,
            'filled': traded_size,
            'remaining': book_size - traded_size,
            'cost': traded_size * float(leg.get('limit_price', 0)),
            'trades': [],
            'fee': {},
            'reduceOnly': order.get('reduce_only', False),
            'postOnly': order.get('post_only', False),
            'info': order  # Store original order as info
        }
        
        return ccxt_order
        
    except Exception as e:
        logger.error(f"Error converting GRVT order to CCXT: {e}", exc_info=True)
        return {
            'id': order.get('order_id', ''),
            'clientOrderId': str(order.get('metadata', {}).get('client_order_id', '')),
            'status': 'unknown',
            'symbol': 'ETH/USDT',
            'side': 'buy',
            'price': 0,
            'amount': 0,
            'filled': 0,
            'remaining': 0,
            'cost': 0,
            'info': order,
            'error': str(e)
        }


def map_time_in_force(time_in_force: str) -> str:
    """Map exchange time_in_force to CCXT format."""
    mapping = {
        # GRVT time_in_force values
        'GOOD_TILL_TIME': 'GTC',
        'IMMEDIATE_OR_CANCEL': 'IOC',
        'FILL_OR_KILL': 'FOK',
        'GOOD_TILL_CANCEL': 'GTC',
        # StandX time_in_force values
        'gtc': 'GTC',
        'ioc': 'IOC',
        'fok': 'FOK',
        'gtx': 'GTC'
    }
    return mapping.get(time_in_force, 'GTC')


def convert_standx_to_ccxt(order: Dict[str, Any]) -> Dict[str, Any]:
    """Convert StandX order format to CCXT format."""
    try:
        # Map status
        status_mapping = {
            'new': 'open',
            'open': 'open',
            'filled': 'closed',
            'canceled': 'canceled',
            'expired': 'expired',
            'rejected': 'rejected'
        }
        
        # Map side
        side = order.get('side', 'buy')
        
        # Convert string numbers to floats
        price = float(order.get('price', '0'))
        amount = float(order.get('qty', '0'))
        filled = float(order.get('fill_qty', '0'))
        average_price = float(order.get('fill_avg_price', '0'))
        
        # Calculate remaining amount
        remaining = amount - filled
        
        # Convert symbol format from BTC-USD to BTC/USDT
        symbol = order.get('symbol', 'BTC-USD').replace('-', '/')
        
        # Convert timestamp from ISO8601 to Unix timestamp in milliseconds
        created_at = order.get('created_at', '')
        if created_at:
            # Parse ISO8601 format like "2025-08-11T03:35:25.559151Z"
            try:
                dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                timestamp_ms = int(dt.timestamp() * 1000)
                datetime_str = dt.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
            except:
                timestamp_ms = 0
                datetime_str = created_at
        else:
            timestamp_ms = 0
            datetime_str = ''
        
        # Build CCXT order
        ccxt_order = {
            'id': str(order.get('id', '')),
            'clientOrderId': str(order.get('cl_ord_id', '')),
            'datetime': datetime_str,
            'timestamp': timestamp_ms,
            'lastTradeTimestamp': None,
            'status': status_mapping.get(order.get('status', 'open'), 'open'),
            'symbol': symbol,
            'type': order.get('order_type', 'limit'),
            'timeInForce': map_time_in_force(order.get('time_in_force', 'gtc')),
            'side': side,
            'price': price,
            'average': average_price,
            'amount': amount,
            'filled': filled,
            'remaining': remaining,
            'cost': filled * average_price,
            'trades': [],
            'fee': {},
            'reduceOnly': order.get('reduce_only', False),
            'postOnly': False,  # StandX doesn't have post_only field
            'info': order  # Store original order as info
        }
        
        return ccxt_order
        
    except Exception as e:
        logger.error(f"Error converting StandX order to CCXT: {e}", exc_info=True)
        return {
            'id': str(order.get('id', '')),
            'clientOrderId': str(order.get('cl_ord_id', '')),
            'status': 'unknown',
            'symbol': 'BTC/USDT',
            'side': 'buy',
            'price': 0,
            'amount': 0,
            'filled': 0,
            'remaining': 0,
            'cost': 0,
            'info': order,
            'error': str(e)
        }


def convert_unknown_to_ccxt(order: Dict[str, Any]) -> Dict[str, Any]:
    """Convert unknown order format to basic CCXT format."""
    logger.warning(f"Converting unknown order format: {order}")
    
    return {
        'id': str(order.get('id', order.get('order_id', order.get('client_order_id', '')))),
        'clientOrderId': str(order.get('client_order_id', order.get('clientOrderId', ''))),
        'status': 'unknown',
        'symbol': 'ETH/USDT',
        'side': 'buy',
        'price': float(order.get('price', 0)),
        'amount': float(order.get('amount', order.get('size', 0))),
        'filled': float(order.get('filled', order.get('traded_size', 0))),
        'remaining': 0,
        'cost': 0,
        'info': order
    }


def normalize_orders_list(orders: Union[List[Dict], Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Normalize a list of orders or a dictionary of orders to CCXT format.
    
    Args:
        orders: Either a list of orders or a dict with market_id -> orders mapping
        
    Returns:
        List of CCXT standardized orders
    """
    if isinstance(orders, dict):
        # Handle dict format (market_id -> orders list)
        normalized = []
        for market_orders in orders.values():
            if isinstance(market_orders, list):
                for order in market_orders:
                    normalized.append(normalize_order_to_ccxt(order))
        return normalized
    elif isinstance(orders, list):
        # Handle list format
        return [normalize_order_to_ccxt(order) for order in orders]
    else:
        logger.warning(f"Unknown orders format: {type(orders)}")
        return []