import yfinance as yf
import pandas as pd

# Test download
symbols = ['RXT', 'SMWB', 'CIFR']
df = yf.download(symbols, period='95d', progress=False)
print(f'Downloaded shape: {df.shape}')
print(f'Columns type: {type(df.columns)}')
print(f'Has MultiIndex: {isinstance(df.columns, pd.MultiIndex)}')

# Try the logic from download_price_history
if "Adj Close" in df:
    print('Found Adj Close directly')
    adj = df["Adj Close"].copy()
else:
    print('No Adj Close, trying Close')
    adj = df["Close"].copy()

print(f'Adj shape: {adj.shape}')
print(f'Adj columns before transformation: {adj.columns.tolist()}')

# Transform columns
adj.columns = [c if isinstance(c, str) else c[1] for c in adj.columns]
print(f'Adj columns after transformation: {adj.columns.tolist()}')
print(f'Adj has data: {not adj.empty}')
print(f'First few rows:\n{adj.head(3)}')

