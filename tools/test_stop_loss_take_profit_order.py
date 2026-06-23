"""
Test script to verify stop loss and take profit order submission
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from runners.optimize_and_buy import resolve_credentials
from fin_trade_alpaca.env_loader import load_environment_for_mode
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, TakeProfitRequest, StopLossRequest
from alpaca.trading.enums import OrderSide, TimeInForce

# Test parameters
TEST_SYMBOL = "SPY"  # Liquid stock for testing
TEST_NOTIONAL = 10.00  # Small test order
TEST_PRICE = 550.00  # Approximate SPY price (adjust if needed)
STOP_PCT = -3.0  # -3% stop loss
TAKE_PCT = 10.0  # 10% take profit

def main():
    print("=" * 80)
    print("STOP LOSS & TAKE PROFIT ORDER TEST")
    print("=" * 80)
    print(f"Test symbol: {TEST_SYMBOL}")
    print(f"Test notional: ${TEST_NOTIONAL}")
    print(f"Assumed price: ${TEST_PRICE}")
    print(f"Stop loss: {STOP_PCT}%")
    print(f"Take profit: {TAKE_PCT}%")
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
    
    # Calculate stop and take profit prices
    stop_price = TEST_PRICE * (1.0 + STOP_PCT / 100.0)
    take_price = TEST_PRICE * (1.0 + TAKE_PCT / 100.0)
    
    print(f"Calculated stop price: ${stop_price:.2f}")
    print(f"Calculated take profit price: ${take_price:.2f}")
    print()
    
    # Create stop loss and take profit requests
    tp = TakeProfitRequest(limit_price=take_price)
    sl = StopLossRequest(stop_price=stop_price)
    
    print("Creating TakeProfitRequest and StopLossRequest objects...")
    print(f"  TakeProfitRequest: {tp}")
    print(f"  StopLossRequest: {sl}")
    print()
    
    # Create the market order request with bracket orders
    req = MarketOrderRequest(
        symbol=TEST_SYMBOL,
        notional=TEST_NOTIONAL,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
        take_profit=tp,
        stop_loss=sl,
    )
    
    print("Market order request created:")
    print(f"  Symbol: {req.symbol}")
    print(f"  Notional: {req.notional}")
    print(f"  Side: {req.side}")
    print(f"  TIF: {req.time_in_force}")
    print(f"  Take Profit: {req.take_profit}")
    print(f"  Stop Loss: {req.stop_loss}")
    print()
    
    # Ask user for confirmation
    print("⚠️  WARNING: This will place a REAL order in your paper account!")
    response = input("Do you want to submit this test order? (yes/no): ")
    
    if response.lower() != 'yes':
        print("Test cancelled by user.")
        return
    
    print("\nSubmitting order...")
    try:
        order = client.submit_order(req)
        print(f"\n✓ Order submitted successfully!")
        print(f"  Order ID: {order.id}")
        print(f"  Symbol: {order.symbol}")
        print(f"  Status: {order.status}")
        print(f"  Type: {order.type}")
        print(f"  Side: {order.side}")
        print(f"  Notional: {getattr(order, 'notional', 'N/A')}")
        print(f"  Qty: {getattr(order, 'qty', 'N/A')}")
        
        # Check if stop_loss and take_profit are present
        if hasattr(order, 'stop_loss') and order.stop_loss:
            print(f"\n✓ Stop Loss found on order object:")
            print(f"    Stop price: {getattr(order.stop_loss, 'stop_price', 'N/A')}")
            print(f"    Limit price: {getattr(order.stop_loss, 'limit_price', 'N/A')}")
        else:
            print(f"\n❌ No stop_loss attribute found on order object")
            
        if hasattr(order, 'take_profit') and order.take_profit:
            print(f"\n✓ Take Profit found on order object:")
            print(f"    Limit price: {getattr(order.take_profit, 'limit_price', 'N/A')}")
        else:
            print(f"\n❌ No take_profit attribute found on order object")
        
        # Check for legs (bracket order)
        if hasattr(order, 'legs') and order.legs:
            print(f"\n✓ Order has {len(order.legs)} leg(s) (bracket order):")
            for i, leg in enumerate(order.legs):
                print(f"  Leg {i+1}:")
                print(f"    ID: {getattr(leg, 'id', 'N/A')}")
                print(f"    Type: {getattr(leg, 'type', 'N/A')}")
                print(f"    Side: {getattr(leg, 'side', 'N/A')}")
                if hasattr(leg, 'stop_price'):
                    print(f"    Stop price: {leg.stop_price}")
                if hasattr(leg, 'limit_price'):
                    print(f"    Limit price: {leg.limit_price}")
        else:
            print(f"\n❌ No legs found on order (not a bracket order)")
        
        print(f"\nOrder object attributes: {dir(order)}")
        
        # Wait a moment and then retrieve the order to see if protections are there
        print("\nWaiting 2 seconds, then retrieving order from API...")
        import time
        time.sleep(2)
        
        retrieved_order = client.get_order_by_id(order.id)
        print(f"\nRetrieved order status: {retrieved_order.status}")
        
        if hasattr(retrieved_order, 'stop_loss') and retrieved_order.stop_loss:
            print(f"✓ Retrieved order HAS stop_loss")
        else:
            print(f"❌ Retrieved order DOES NOT have stop_loss")
            
        if hasattr(retrieved_order, 'take_profit') and retrieved_order.take_profit:
            print(f"✓ Retrieved order HAS take_profit")
        else:
            print(f"❌ Retrieved order DOES NOT have take_profit")
            
        if hasattr(retrieved_order, 'legs') and retrieved_order.legs:
            print(f"✓ Retrieved order HAS {len(retrieved_order.legs)} leg(s)")
        else:
            print(f"❌ Retrieved order DOES NOT have legs")
        
    except Exception as e:
        print(f"\n❌ Error submitting order: {e}")
        print(f"   Error type: {type(e).__name__}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()
