"""
Test bracket order with quantity-based order
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from runners.optimize_and_buy import resolve_credentials
from fin_trade_alpaca.env_loader import load_environment_for_mode
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, TakeProfitRequest, StopLossRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest

# Test parameters
TEST_SYMBOL = "SPY"
TEST_NOTIONAL = 10.00
STOP_PCT = -1.0  # Tighter stop loss
TAKE_PCT = 2.0   # Tighter take profit

def main():
    print("=" * 80)
    print("BRACKET ORDER TEST (QUANTITY-BASED WITH LIVE PRICE)")
    print("=" * 80)
    
    load_environment_for_mode('paper')
    creds = resolve_credentials('paper')
    client = TradingClient(
        api_key=creds.api_key,
        secret_key=creds.api_secret,
        oauth_token=creds.oauth_token,
        paper=creds.paper
    )
    
    # Create data client to fetch current price
    data_client = StockHistoricalDataClient(
        api_key=creds.api_key,
        secret_key=creds.api_secret
    )
    
    print(f"Fetching current price for {TEST_SYMBOL}...")
    try:
        request = StockLatestQuoteRequest(symbol_or_symbols=TEST_SYMBOL)
        quote = data_client.get_stock_latest_quote(request)
        current_price = float(quote[TEST_SYMBOL].ask_price)
        print(f"Current ask price: ${current_price:.2f}")
    except Exception as e:
        print(f"Error fetching price: {e}")
        return
    
    # Calculate qty and prices
    qty = TEST_NOTIONAL / current_price
    # Round prices to penny increments (no sub-penny pricing allowed)
    stop_price = round(current_price * (1.0 + STOP_PCT / 100.0), 2)
    take_price = round(current_price * (1.0 + TAKE_PCT / 100.0), 2)
    
    print(f"Symbol: {TEST_SYMBOL}")
    print(f"Notional: ${TEST_NOTIONAL}")
    print(f"Current price: ${current_price:.2f}")
    print(f"Quantity: {qty:.6f}")
    print(f"Stop loss: {STOP_PCT}% = ${stop_price:.2f}")
    print(f"Take profit: {TAKE_PCT}% = ${take_price:.2f}")
    print()
    
    tp = TakeProfitRequest(limit_price=take_price)
    sl = StopLossRequest(stop_price=stop_price)
    
    req = MarketOrderRequest(
        symbol=TEST_SYMBOL,
        qty=qty,  # Use qty instead of notional
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
        order_class="bracket",  # Explicitly set bracket order
        take_profit=tp,
        stop_loss=sl,
    )
    
    response = input("\n⚠️  Submit this bracket order to paper account? (yes/no): ")
    if response.lower() != 'yes':
        print("Test cancelled.")
        return
    
    print("\nSubmitting bracket order...")
    try:
        order = client.submit_order(req)
        print(f"\n✓ Order submitted successfully!")
        print(f"  Order ID: {order.id}")
        print(f"  Symbol: {order.symbol}")
        print(f"  Status: {order.status}")
        print(f"  Type: {order.type}")
        print(f"  Order Class: {getattr(order, 'order_class', 'N/A')}")
        print(f"  Qty: {getattr(order, 'qty', 'N/A')}")
        
        # Check for bracket legs
        if hasattr(order, 'legs') and order.legs:
            print(f"\n✓ Bracket order created with {len(order.legs)} leg(s):")
            for i, leg in enumerate(order.legs):
                print(f"  Leg {i+1}: {getattr(leg, 'type', 'N/A')} - {getattr(leg, 'side', 'N/A')}")
                if hasattr(leg, 'stop_price'):
                    print(f"    Stop price: ${leg.stop_price}")
                if hasattr(leg, 'limit_price'):
                    print(f"    Limit price: ${leg.limit_price}")
        else:
            print(f"\n❌ No legs found - not a bracket order!")
            
        # Wait and check order status
        import time
        time.sleep(2)
        retrieved = client.get_order_by_id(order.id)
        print(f"\nRetrieved order status: {retrieved.status}")
        if hasattr(retrieved, 'legs') and retrieved.legs:
            print(f"✓ Retrieved order has {len(retrieved.legs)} leg(s)")
            for i, leg in enumerate(retrieved.legs):
                leg_order = client.get_order_by_id(leg.id)
                print(f"  Leg {i+1} status: {leg_order.status}")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
