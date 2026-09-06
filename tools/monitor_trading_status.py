"""
Trading Status Monitor - Check orders, positions, and market status.

This tool helps monitor:
1. Market hours (is trading open?)
2. Pending orders (waiting to fill)
3. Current positions
4. Whether it's safe to run optimize & buy

Usage:
    python tools/monitor_trading_status.py --mode paper
    python tools/monitor_trading_status.py --mode paper --watch  # Continuous monitoring
"""
import argparse
import sys
import time
from pathlib import Path
from datetime import datetime, timezone
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus

# Add source to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from runners.optimize_and_buy import resolve_credentials
from fin_trade_alpaca.env_loader import load_environment_for_mode


def check_market_status(trading_client):
    """Check if market is currently open."""
    clock = trading_client.get_clock()
    return {
        'is_open': clock.is_open,
        'next_open': clock.next_open,
        'next_close': clock.next_close,
        'timestamp': clock.timestamp
    }


def check_orders(trading_client, status_filter=None):
    """Get current orders, optionally filtered by status."""
    from alpaca.trading.requests import GetOrdersRequest
    
    request = GetOrdersRequest(
        status=status_filter,
        limit=100
    )
    orders = trading_client.get_orders(filter=request)
    return orders


def check_positions(trading_client):
    """Get current positions."""
    return trading_client.get_all_positions()


def format_order_summary(orders):
    """Format orders into a readable summary."""
    if not orders:
        return "No orders found"
    
    summary = []
    for order in orders:
        filled_price = f"${float(order.filled_avg_price):.2f}" if order.filled_avg_price else "pending"
        filled_qty = f"{order.filled_qty}/{order.qty}" if order.filled_qty else f"0/{order.qty}"
        summary.append(
            f"  {order.symbol}: {order.side.value} {filled_qty} @ {filled_price} "
            f"- {order.status.value} ({order.created_at.strftime('%H:%M:%S')})"
        )
    return "\n".join(summary)


def is_ready_for_new_trades(pending_orders, target_position_count=15):
    """Determine if it's safe to run optimize & buy."""
    pending_count = len(pending_orders)
    
    # Safe to trade if:
    # 1. No pending orders, OR
    # 2. Only have a few pending orders and they're old (>5 minutes)
    
    if pending_count == 0:
        return True, "No pending orders - ready to trade"
    
    # Check if pending orders are old
    now = datetime.now(timezone.utc)
    old_orders = [o for o in pending_orders 
                  if (now - o.created_at).total_seconds() > 300]
    
    if len(old_orders) == pending_count and pending_count <= 3:
        return True, f"{pending_count} old pending orders (>5 min) - likely stuck, may proceed cautiously"
    
    return False, f"{pending_count} pending orders - wait for them to fill before new trades"


def main():
    parser = argparse.ArgumentParser(description="Monitor trading status")
    parser.add_argument("--mode", choices=["paper", "live"], default="paper")
    parser.add_argument("--watch", action="store_true", help="Continuous monitoring (every 30 seconds)")
    parser.add_argument("--interval", type=int, default=30, help="Watch interval in seconds (default: 30)")
    args = parser.parse_args()
    
    # Load environment and credentials
    load_environment_for_mode(args.mode)
    creds = resolve_credentials(mode=args.mode)
    
    trading_client = TradingClient(
        api_key=creds.api_key,
        secret_key=creds.api_secret,
        paper=(args.mode == 'paper')
    )
    
    def print_status():
        print("\n" + "="*80)
        print(f"TRADING STATUS MONITOR - {args.mode.upper()} MODE")
        print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*80)
        
        # Market status
        market = check_market_status(trading_client)
        status_emoji = "🟢" if market['is_open'] else "🔴"
        print(f"\n{status_emoji} Market Status: {'OPEN' if market['is_open'] else 'CLOSED'}")
        if not market['is_open']:
            print(f"   Next open: {market['next_open'].strftime('%Y-%m-%d %H:%M:%S %Z')}")
        else:
            print(f"   Closes at: {market['next_close'].strftime('%H:%M:%S %Z')}")
        
        # Pending orders
        print("\n📋 Pending Orders:")
        pending_orders = check_orders(trading_client, status_filter=QueryOrderStatus.OPEN)
        if pending_orders:
            print(format_order_summary(pending_orders))
        else:
            print("  ✓ No pending orders")
        
        # Recent filled orders (last 24 hours)
        print("\n✅ Recent Filled Orders (last 10):")
        filled_orders = check_orders(trading_client, status_filter=QueryOrderStatus.CLOSED)
        recent_filled = [o for o in filled_orders[:10]]
        if recent_filled:
            print(format_order_summary(recent_filled))
        else:
            print("  No recent filled orders")
        
        # Current positions
        print("\n💼 Current Positions:")
        positions = check_positions(trading_client)
        print(f"  Total: {len(positions)}")
        if positions:
            for pos in sorted(positions, key=lambda p: float(p.unrealized_plpc), reverse=True):
                pnl = float(pos.unrealized_pl)
                pnl_pct = float(pos.unrealized_plpc) * 100
                pnl_emoji = "🟢" if pnl >= 0 else "🔴"
                print(f"  {pnl_emoji} {pos.symbol}: {float(pos.qty):.2f} @ ${float(pos.current_price):.2f} "
                      f"(P&L: ${pnl:+.2f} / {pnl_pct:+.2f}%)")
        
        # Trading readiness
        print("\n🎯 Trading Readiness:")
        ready, reason = is_ready_for_new_trades(pending_orders)
        readiness_emoji = "✅" if ready else "⏳"
        print(f"  {readiness_emoji} {reason}")
        
        if ready and market['is_open']:
            available_slots = 15 - len(positions)
            print(f"  💡 {available_slots} position slots available - Ready to run optimize & buy!")
        elif ready and not market['is_open']:
            print(f"  ⏰ Market is closed - wait for market open to trade")
        
        print("\n" + "="*80)
    
    if args.watch:
        print(f"Starting continuous monitoring (checking every {args.interval} seconds)")
        print("Press Ctrl+C to stop")
        try:
            while True:
                print_status()
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n\nMonitoring stopped by user")
    else:
        print_status()


if __name__ == "__main__":
    main()
