import pandas as pd
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parents[0] / 'src'))

from yfinance.screener.predict_short_term import download_price_history

# Load symbols
df = pd.read_csv('reports/screener_results/yfinance_screener_results_with_metrics_tech_health_75cap_20260616_ranked_with_flags_20260616.csv')

# Mimic what the screener does
candidate_symbols = list(df['symbol'].astype(str).str.upper().unique())

print(f'Input symbols (after .upper()): {candidate_symbols[:10]}')

# Download
adj = download_price_history(candidate_symbols[:10], 190)

print(f'\nDownloaded columns: {adj.columns.tolist()}')

# Check membership
print(f'\nChecking membership:')
for sym in candidate_symbols[:5]:
    print(f'  {sym!r} in adj.columns? {sym in adj.columns}')
    # Try lowercase too
    print(f'  {sym.lower()!r} in adj.columns? {sym.lower() in adj.columns}')
