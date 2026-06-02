from pathlib import Path
import os
import sys
import time
import subprocess
import requests
from decimal import Decimal

# Ensure src is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from fin_trade_alpaca.optimize_and_buy import load_environment_for_mode, resolve_credentials
from alpaca.trading.client import TradingClient

# Configure
ORDER_IDS = [
    '8c17a63f-0f08-4c61-9dff-18e032664307',
    '57cb73c5-3485-4a10-b3b7-99aabd5176cb'
]

# Load paper env and creds
load_environment_for_mode('paper')
creds = resolve_credentials('paper')
api_endpoint = os.getenv('ALPACA_PAPER_API_ENDPOINT') or 'https://paper-api.alpaca.markets'

headers = {}
if creds.oauth_token:
    headers['Authorization'] = f'Bearer {creds.oauth_token}'
else:
    headers['APCA-API-KEY-ID'] = creds.api_key
    headers['APCA-API-SECRET-KEY'] = creds.api_secret

print('Canceling observed order IDs:')
for oid in ORDER_IDS:
    url = f"{api_endpoint.rstrip('/')}/orders/{oid}"
    try:
        r = requests.delete(url, headers=headers, timeout=15)
        if r.status_code in (200, 204):
            print(f"  Cancelled {oid} -> {r.status_code}")
        else:
            print(f"  Failed to cancel {oid} -> {r.status_code} {r.text}")
    except Exception as e:
        print(f"  Exception canceling {oid}: {e}")

# Short wait to let API process cancellations
print('Waiting 3s for cancellations to settle...')
time.sleep(3)

# Attempt a blanket cancel-all-orders via REST to clear any new close_all_positions orders
print('Attempting blanket cancel-all-orders via DELETE /v2/orders')
try:
    r = requests.delete(f"{api_endpoint.rstrip('/')}/orders", headers=headers, timeout=15)
    print('  cancel-all response:', r.status_code, r.text[:200])
except Exception as e:
    print('  cancel-all exception:', e)

time.sleep(1)

# Run the clone script with auto-execute
cmd = [sys.executable, 'src/fin_trade_alpaca/clone_live_to_paper.py', '--auto-execute-paper', '--wait-timeout-sec', '60', '--poll-interval-sec', '2']
env = os.environ.copy()
env['PYTHONPATH'] = str(Path('src').resolve())
print('Running:', ' '.join(cmd), 'with PYTHONPATH=', env['PYTHONPATH'])
proc = subprocess.run(cmd, check=False, env=env)
print('Clone script exited with', proc.returncode)

# Produce side-by-side report
print('\nProducing side-by-side report:')
# init clients
load_environment_for_mode('paper')
paper_creds = resolve_credentials('paper')
paper_client = TradingClient(api_key=paper_creds.api_key, secret_key=paper_creds.api_secret, oauth_token=paper_creds.oauth_token, paper=paper_creds.paper)

load_environment_for_mode('live')
live_creds = resolve_credentials('live')
live_client = TradingClient(api_key=live_creds.api_key, secret_key=live_creds.api_secret, oauth_token=live_creds.oauth_token, paper=live_creds.paper)

live_positions = {p.symbol.strip().upper(): p for p in live_client.get_all_positions()}
paper_positions = {p.symbol.strip().upper(): p for p in paper_client.get_all_positions()}

symbols = sorted(set(list(live_positions.keys()) + list(paper_positions.keys())))
print('symbol, live_qty, live_value, paper_qty, paper_value')
for s in symbols:
    lp = live_positions.get(s)
    pp = paper_positions.get(s)
    lq = getattr(lp, 'qty', 0) if lp else 0
    lv = getattr(lp, 'market_value', 0) if lp else 0
    pq = getattr(pp, 'qty', 0) if pp else 0
    pv = getattr(pp, 'market_value', 0) if pp else 0
    print(f"{s}, {lq}, {lv}, {pq}, {pv}")

print('\nDone.')
