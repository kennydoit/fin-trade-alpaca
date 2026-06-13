"""Quick test to see what fields are available on Alpaca Position objects."""
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / 'src'))

from runners.optimize_and_buy import resolve_credentials
from fin_trade_alpaca.env_loader import load_environment_for_mode
from alpaca.trading.client import TradingClient

# Load paper account
load_environment_for_mode('paper')
creds = resolve_credentials('paper')
client = TradingClient(
    api_key=creds.api_key,
    secret_key=creds.api_secret,
    oauth_token=creds.oauth_token,
    paper=creds.paper
)

positions = client.get_all_positions()
if positions:
    pos = positions[0]
    print(f"Position for {pos.symbol}:")
    print(f"Available attributes:")
    for attr in dir(pos):
        if not attr.startswith('_'):
            val = getattr(pos, attr, None)
            if not callable(val):
                print(f"  {attr}: {val}")
