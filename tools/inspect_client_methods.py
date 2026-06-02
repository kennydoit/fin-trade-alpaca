from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from fin_trade_alpaca.optimize_and_buy import load_environment_for_mode, resolve_credentials
from alpaca.trading.client import TradingClient
load_environment_for_mode('paper')
creds = resolve_credentials('paper')
client = TradingClient(api_key=creds.api_key, secret_key=creds.api_secret, oauth_token=creds.oauth_token, paper=creds.paper)
print([m for m in dir(client) if 'cancel' in m.lower()])
print([m for m in dir(client) if 'order' in m.lower()][:80])
