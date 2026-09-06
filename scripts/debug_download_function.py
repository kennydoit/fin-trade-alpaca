import pandas as pd
import sys
from pathlib import Path

# Add src to path like the screener does
sys.path.insert(0, str(Path(__file__).resolve().parents[0] / 'src'))

from yfinance.screener.predict_short_term import download_price_history

# Load symbols
df = pd.read_csv('reports/screener_results/yfinance_screener_results_with_metrics_tech_health_75cap_20260616_ranked_with_flags_20260616.csv')
symbols = df['symbol'].tolist()

print(f'Loading {len(symbols)} symbols')
print(f'First 10: {symbols[:10]}')

# Call download_price_history with the same parameters the screener uses
# lookback=180, return_days=5, so: 180 + 5 + 5 = 190
lookback_total = 180 + 5 + 5
print(f'\nCalling download_price_history with lookback={lookback_total}')

adj = download_price_history(symbols, lookback_total)

print(f'\nResult:')
print(f'  Type: {type(adj)}')
print(f'  Shape: {adj.shape if not adj.empty else "EMPTY"}')
print(f'  Columns: {len(adj.columns) if not adj.empty else 0}')

if not adj.empty:
    print(f'  First 10 columns: {adj.columns.tolist()[:10]}')
    print(f'  Sample data:\n{adj.head()}')
else:
    print('  *** DATAFRAME IS EMPTY ***')
