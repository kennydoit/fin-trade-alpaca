from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from fin_trade_alpaca.optimize_and_buy import resolve_credentials
from fin_trade_alpaca.env_loader import load_environment_for_mode
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

load_environment_for_mode('paper')
creds = resolve_credentials('paper')
client = TradingClient(api_key=creds.api_key, secret_key=creds.api_secret, oauth_token=creds.oauth_token, paper=creds.paper)

positions = client.get_all_positions()
if not positions:
    print('No paper positions to force-sell.')
    sys.exit(0)

for p in positions:
    sym = str(p.symbol).strip().upper()
    qty = float(p.qty)
    print(f'Submitting MARKET SELL qty={qty} for {sym}')
    req = MarketOrderRequest(symbol=sym, qty=qty, side=OrderSide.SELL, time_in_force=TimeInForce.DAY)
    try:
        order = client.submit_order(req)
        print('  submitted', getattr(order,'id',None))
    except Exception as e:
        print('  failed to submit sell for', sym, e)
