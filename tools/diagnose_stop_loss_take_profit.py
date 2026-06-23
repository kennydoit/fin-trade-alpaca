"""
Diagnostic tool to check if paper orders have properly configured stop loss or take profit.
"""
from pathlib import Path
import sys
import json
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from runners.optimize_and_buy import resolve_credentials
from fin_trade_alpaca.env_loader import load_environment_for_mode
from alpaca.trading.client import TradingClient


def check_order_protections(order):
    """Extract stop loss and take profit information from an order."""
    result = {
        'id': str(getattr(order, 'id', 'N/A')),
        'symbol': getattr(order, 'symbol', 'N/A'),
        'side': str(getattr(order, 'side', 'N/A')),
        'type': str(getattr(order, 'type', 'N/A')),
        'status': str(getattr(order, 'status', 'N/A')),
        'qty': getattr(order, 'qty', None) or getattr(order, 'quantity', None),
        'notional': getattr(order, 'notional', None),
        'submitted_at': str(getattr(order, 'submitted_at', 'N/A')),
        'has_stop_loss': False,
        'stop_loss_config': None,
        'has_take_profit': False,
        'take_profit_config': None,
    }
    
    # Check for stop_loss attribute
    stop_loss = getattr(order, 'stop_loss', None)
    if stop_loss is not None:
        result['has_stop_loss'] = True
        result['stop_loss_config'] = {
            'stop_price': getattr(stop_loss, 'stop_price', None),
            'limit_price': getattr(stop_loss, 'limit_price', None),
        }
    
    # Check for take_profit attribute
    take_profit = getattr(order, 'take_profit', None)
    if take_profit is not None:
        result['has_take_profit'] = True
        result['take_profit_config'] = {
            'limit_price': getattr(take_profit, 'limit_price', None),
        }
    
    # Also check for bracket order legs
    legs = getattr(order, 'legs', None)
    if legs:
        result['has_legs'] = True
        result['legs_info'] = []
        for leg in legs:
            leg_info = {
                'id': str(getattr(leg, 'id', 'N/A')),
                'type': str(getattr(leg, 'type', 'N/A')),
                'side': str(getattr(leg, 'side', 'N/A')),
            }
            if hasattr(leg, 'stop_price'):
                leg_info['stop_price'] = getattr(leg, 'stop_price', None)
            if hasattr(leg, 'limit_price'):
                leg_info['limit_price'] = getattr(leg, 'limit_price', None)
            result['legs_info'].append(leg_info)
    
    return result


def main():
    print('=' * 80)
    print('PAPER TRADING ORDER STOP LOSS & TAKE PROFIT DIAGNOSTIC')
    print('=' * 80)
    print()
    
    # Load paper trading environment
    load_environment_for_mode('paper')
    creds = resolve_credentials('paper')
    client = TradingClient(
        api_key=creds.api_key,
        secret_key=creds.api_secret,
        oauth_token=creds.oauth_token,
        paper=creds.paper
    )
    
    # Fetch open orders
    print('Fetching open paper orders...')
    open_orders = []
    try:
        open_orders = client.get_orders()
    except Exception as e1:
        try:
            open_orders = client.get_all_orders()
        except Exception as e2:
            print(f'ERROR: Unable to fetch open orders: {e1}, {e2}')
            pass
    
    print(f'Found {len(open_orders)} open orders')
    
    # Fetch closed orders (recent history)
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus
    
    print('Fetching closed paper orders (recent history)...')
    closed_orders = []
    try:
        request = GetOrdersRequest(
            status=QueryOrderStatus.CLOSED,
            limit=100
        )
        closed_orders = client.get_orders(filter=request)
    except Exception as e:
        print(f'Note: Unable to fetch closed orders: {e}')
    
    print(f'Found {len(closed_orders)} closed orders')
    
    # Combine all orders
    orders = list(open_orders) + list(closed_orders)
    print(f'Total orders to analyze: {len(orders)}\n')
    
    # Analyze orders
    results = {
        'summary': {
            'total_orders': len(orders),
            'with_stop_loss': 0,
            'with_take_profit': 0,
            'with_both': 0,
            'with_neither': 0,
            'open_orders': 0,
            'filled_orders': 0,
        },
        'orders': []
    }
    
    for order in orders:
        order_info = check_order_protections(order)
        results['orders'].append(order_info)
        
        # Update summary
        if order_info['status'].lower() == 'open':
            results['summary']['open_orders'] += 1
        elif order_info['status'].lower() in ['filled', 'partially_filled']:
            results['summary']['filled_orders'] += 1
        
        if order_info['has_stop_loss']:
            results['summary']['with_stop_loss'] += 1
        if order_info['has_take_profit']:
            results['summary']['with_take_profit'] += 1
        if order_info['has_stop_loss'] and order_info['has_take_profit']:
            results['summary']['with_both'] += 1
        if not order_info['has_stop_loss'] and not order_info['has_take_profit']:
            results['summary']['with_neither'] += 1
    
    # Print summary
    print('SUMMARY:')
    print('-' * 80)
    print(f"  Total orders: {results['summary']['total_orders']}")
    print(f"  Open orders: {results['summary']['open_orders']}")
    print(f"  Filled orders: {results['summary']['filled_orders']}")
    print()
    print(f"  Orders with stop loss: {results['summary']['with_stop_loss']}")
    print(f"  Orders with take profit: {results['summary']['with_take_profit']}")
    print(f"  Orders with BOTH: {results['summary']['with_both']}")
    print(f"  Orders with NEITHER: {results['summary']['with_neither']}")
    print()
    
    # Print details for orders missing protections
    missing_protection = [o for o in results['orders'] if not o['has_stop_loss'] and not o['has_take_profit']]
    if missing_protection:
        print('⚠️  ORDERS WITHOUT STOP LOSS OR TAKE PROFIT:')
        print('-' * 80)
        for order in missing_protection:
            print(f"  Symbol: {order['symbol']}")
            print(f"    ID: {order['id']}")
            print(f"    Side: {order['side']}, Type: {order['type']}, Status: {order['status']}")
            print(f"    Qty: {order['qty']}, Notional: {order['notional']}")
            print(f"    Submitted: {order['submitted_at']}")
            print()
    
    # Print details for orders with protections
    with_protection = [o for o in results['orders'] if o['has_stop_loss'] or o['has_take_profit']]
    if with_protection:
        print('✓ ORDERS WITH STOP LOSS OR TAKE PROFIT:')
        print('-' * 80)
        for order in with_protection:
            print(f"  Symbol: {order['symbol']}")
            print(f"    ID: {order['id']}")
            print(f"    Side: {order['side']}, Type: {order['type']}, Status: {order['status']}")
            print(f"    Qty: {order['qty']}, Notional: {order['notional']}")
            if order['has_stop_loss']:
                print(f"    ✓ Stop Loss: {order['stop_loss_config']}")
            if order['has_take_profit']:
                print(f"    ✓ Take Profit: {order['take_profit_config']}")
            if order.get('has_legs'):
                print(f"    Bracket legs: {order.get('legs_info')}")
            print()
    
    # Save detailed results to file
    output_file = Path(__file__).parent.parent / 'reports' / 'stop_loss_take_profit_diagnostic.json'
    output_file.parent.mkdir(exist_ok=True)
    with open(output_file, 'w') as f:
        json.dump(results, indent=2, fp=f, default=str)
    
    print('-' * 80)
    print(f'Detailed results saved to: {output_file}')
    print('=' * 80)


if __name__ == '__main__':
    main()
