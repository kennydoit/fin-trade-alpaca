"""
Add stop loss and take profit orders to existing positions.

Since Alpaca doesn't support bracket orders with fractional shares,
we need to submit simple buy orders first, then add protective orders
after the position is established.
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from runners.optimize_and_buy import resolve_credentials
from fin_trade_alpaca.env_loader import load_environment_for_mode
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import StopLossRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest

# Configuration (from strategy.json)
STOP_LOSS_PCT = -1.0   # -1% stop loss
TAKE_PROFIT_PCT = 2.0  # +2% take profit


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Add stop loss and take profit orders to existing positions")
    parser.add_argument("--mode", choices=["paper", "live"], default="paper", help="Trading mode")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, don't submit orders")
    parser.add_argument("--symbols", nargs="+", help="Specific symbols to protect (all if omitted)")
    parser.add_argument("--stop-pct", type=float, default=STOP_LOSS_PCT, help="Stop loss percentage")
    parser.add_argument("--take-pct", type=float, default=TAKE_PROFIT_PCT, help="Take profit percentage")
    args = parser.parse_args()
    
    print("=" * 80)
    print(f"ADD POSITION PROTECTIONS ({args.mode.upper()} MODE)")
    print("=" * 80)
    print(f"Stop loss: {args.stop_pct}%")
    print(f"Take profit: {args.take_pct}%")
    if args.dry_run:
        print("DRY RUN MODE - No orders will be submitted")
    print()
    
    # Load credentials
    load_environment_for_mode(args.mode)
    creds = resolve_credentials(args.mode)
    
    # Create clients
    trading_client = TradingClient(
        api_key=creds.api_key,
        secret_key=creds.api_secret,
        oauth_token=creds.oauth_token,
        paper=creds.paper
    )
    
    data_client = StockHistoricalDataClient(
        api_key=creds.api_key,
        secret_key=creds.api_secret
    )
    
    # Get all positions
    print("Fetching positions...")
    positions = trading_client.get_all_positions()
    
    if not positions:
        print("No positions found.")
        return
    
    # Filter by symbols if specified
    if args.symbols:
        symbols_set = set(s.upper() for s in args.symbols)
        positions = [p for p in positions if p.symbol.upper() in symbols_set]
    
    print(f"Found {len(positions)} position(s) to protect\n")
    
    # Get existing orders to avoid duplicates
    existing_orders = trading_client.get_orders()
    existing_order_symbols = set()
    for order in existing_orders:
        if order.status.lower() in ['open', 'pending_new']:
            existing_order_symbols.add(order.symbol)
    
    if existing_order_symbols:
        print(f"Note: {len(existing_order_symbols)} symbols have existing open orders")
    
    orders_to_submit = []
    
    # For each position, create stop loss and take profit orders
    for pos in positions:
        symbol = pos.symbol
        qty = float(pos.qty)
        current_value = float(pos.market_value)
        
        print(f"\n{symbol}:")
        print(f"  Quantity: {qty:.6f}")
        print(f"  Market value: ${current_value:.2f}")
        
        if symbol in existing_order_symbols:
            print(f"  ⚠️  Skipping - has existing open orders")
            continue
        
        # Fetch current price
        try:
            request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            quote = data_client.get_stock_latest_quote(request)
            current_price = float(quote[symbol].bid_price)  # Use bid for sell orders
            print(f"  Current bid: ${current_price:.2f}")
        except Exception as e:
            print(f"  ⚠️  Could not fetch current price: {e}")
            continue
        
        # Calculate stop and take prices
        stop_price = round(current_price * (1.0 + args.stop_pct / 100.0), 2)
        take_price = round(current_price * (1.0 + args.take_pct / 100.0), 2)
        
        print(f"  Stop loss: ${stop_price:.2f} ({args.stop_pct}%)")
        print(f"  Take profit: ${take_price:.2f} ({args.take_pct}%)")
        
        orders_to_submit.append({
            'symbol': symbol,
            'qty': qty,
            'stop_price': stop_price,
            'take_price': take_price,
        })
    
    if not orders_to_submit:
        print("\nNo orders to submit.")
        return
    
    print(f"\n{'=' * 80}")
    print(f"Ready to submit {len(orders_to_submit) * 2} orders ({len(orders_to_submit)} stop loss + {len(orders_to_submit)} take profit)")
    
    if args.dry_run:
        print("\nDRY RUN - No orders submitted")
        return
    
    # Confirm before submitting
    if args.mode == "live":
        response = input("\n⚠️  LIVE MODE - Submit these orders? (type 'YES' to confirm): ")
        if response != 'YES':
            print("Cancelled.")
            return
    else:
        response = input("\nSubmit these orders to paper account? (yes/no): ")
        if response.lower() != 'yes':
            print("Cancelled.")
            return
    
    print("\nSubmitting orders...")
    success_count = 0
    fail_count = 0
    
    for order_info in orders_to_submit:
        symbol = order_info['symbol']
        qty = order_info['qty']
        stop_price = order_info['stop_price']
        take_price = order_info['take_price']
        
        # Submit stop loss order (stop market)
        try:
            stop_req = StopLossRequest(stop_price=stop_price)
            # Use stop order request
            from alpaca.trading.requests import StopOrderRequest
            stop_order_req = StopOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,  # Good till cancelled
                stop_price=stop_price,
            )
            stop_order = trading_client.submit_order(stop_order_req)
            print(f"  ✓ {symbol} stop loss order: {stop_order.id}")
            success_count += 1
        except Exception as e:
            print(f"  ❌ {symbol} stop loss failed: {e}")
            fail_count += 1
        
        # Submit take profit order (limit order)
        try:
            take_order_req = LimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
                limit_price=take_price,
            )
            take_order = trading_client.submit_order(take_order_req)
            print(f"  ✓ {symbol} take profit order: {take_order.id}")
            success_count += 1
        except Exception as e:
            print(f"  ❌ {symbol} take profit failed: {e}")
            fail_count += 1
    
    print(f"\n{'=' * 80}")
    print(f"Complete: {success_count} orders submitted, {fail_count} failed")
    print("=" * 80)


if __name__ == '__main__':
    main()
