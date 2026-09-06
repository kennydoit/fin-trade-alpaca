import pandas as pd
import yfinance as yf

# Simulate what download_price_history does
df = pd.read_csv('reports/screener_results/yfinance_screener_results_with_metrics_tech_health_75cap_20260616_ranked_with_flags_20260616.csv')
symbols = df['symbol'].tolist()[:10]

print(f'Input symbols: {symbols}')
print(f'Symbol types: {[type(s) for s in symbols[:3]]}')

# Download
data = yf.download(symbols, period='190d', interval='1d', progress=False, threads=True)

print(f'\nData columns (first level): {data.columns.levels[0].tolist() if hasattr(data.columns, "levels") else "Not MultiIndex"}')

# Extract Close
if "Adj Close" in data:
    adj = data["Adj Close"].copy()
    print('Using Adj Close')
else:
    adj = data["Close"].copy()
    print('Using Close')

print(f'\nadj type: {type(adj)}')
print(f'adj columns: {adj.columns.tolist()}')
print(f'adj column types: {[type(c) for c in adj.columns[:3]]}')

# Check the column name processing
adj.columns = [c if isinstance(c, str) else c[1] for c in adj.columns]
print(f'\nAfter processing, adj columns: {adj.columns.tolist()}')

# Now check membership
print(f'\nChecking symbol membership:')
for sym in symbols[:5]:
    print(f'  {sym!r} in adj.columns? {sym in adj.columns}')
    print(f'    adj.columns contains: {[c for c in adj.columns if sym.lower() in c.lower()]}')
