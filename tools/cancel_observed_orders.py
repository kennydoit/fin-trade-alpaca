from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from fin_trade_alpaca.optimize_and_buy import load_environment_for_mode, resolve_credentials
from alpaca.trading.client import TradingClient

load_environment_for_mode('paper')
creds = resolve_credentials('paper')
client = TradingClient(api_key=creds.api_key, secret_key=creds.api_secret, oauth_token=creds.oauth_token, paper=creds.paper)

# Attempt to cancel specific order IDs observed in earlier run
order_ids = ['8c17a63f-0f08-4c61-9dff-18e032664307','57cb73c5-3485-4a10-b3b7-99aabd5176cb']
for oid in order_ids:
    try:
        client.cancel_order(oid)
        print('Cancelled', oid)
    except Exception as e:
        print('Cancel failed for', oid, e)

print('\nPost-cancel open orders:')
try:
    orders = client.get_orders()
except Exception:
    try:
        orders = client.get_all_orders()
    except Exception as e:
        print('list failed', e)
        orders = []
for o in orders:
    print(o.id, getattr(o,'symbol',None), getattr(o,'status',None))

print('\nPositions:')
for p in client.get_all_positions():
    print(p.symbol, p.qty, p.market_value)
