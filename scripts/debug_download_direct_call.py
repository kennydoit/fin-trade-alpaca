import sys
from pathlib import Path

# Add src to path like the screener does
sys.path.insert(0, str(Path(__file__).resolve().parents[0] / 'src'))

from yfinance.screener.predict_short_term import download_price_history

symbols = ['RXT', 'TXG', 'PDFS', 'VSH', 'SMWB', 'WGS', 'HPE', 'COHU', 'CIFR', 'OUST']

print(f'Calling download_price_history with {len(symbols)} symbols, period=190d')
adj = download_price_history(symbols, 190)

print(f'\nResult shape: {adj.shape}')
print(f'Empty? {adj.empty}')
if not adj.empty:
    print(f'Columns: {adj.columns.tolist()}')
    print(f'First few rows:\n{adj.head()}')
