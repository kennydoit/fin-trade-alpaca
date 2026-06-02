from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from fin_trade_alpaca.optimize_and_buy import load_environment_for_mode, resolve_credentials
from alpaca.trading.client import TradingClient

load_environment_for_mode('paper')
creds = resolve_credentials('paper')
client = TradingClient(api_key=creds.api_key, secret_key=creds.api_secret, oauth_token=creds.oauth_token, paper=creds.paper)

print('Positions:')
for p in client.get_all_positions():
    print(p.symbol, p.qty, p.market_value)

print('\nOpen orders:')
orders = []
try:
    orders = client.get_orders()
except Exception as e:
    try:
        orders = client.get_all_orders()
    except Exception as e2:
        print('Unable to list open orders:', e, e2)

open_orders = [o for o in orders if getattr(o, 'status', '').lower() == 'open']
for o in open_orders:
    try:
        print(o.id, getattr(o,'symbol',None), getattr(o,'qty',None), getattr(o,'notional',None), o.status)
    except Exception as e:
        print('order print error', e)
