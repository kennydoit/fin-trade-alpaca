import sys
sys.path.insert(0, 'src')

import pandas as pd
from yfinance.screener.predict_short_term import download_price_history, make_technical_features

# Load candidates
cand_df = pd.read_csv('reports/screener_results/yfinance_screener_results_with_metrics_tech_health_75cap_20260616_ranked_deduped_20260616.csv')
symbols = cand_df['symbol'].head(10).tolist()
print(f'Testing with symbols: {symbols}')

# Download history
lookback = 90
return_days = 5
adj = download_price_history(symbols, lookback + return_days + 5)
print(f'\nDownloaded adj shape: {adj.shape}')
print(f'Adj columns: {adj.columns.tolist()}')
print(f'Adj not empty: {not adj.empty}')

# Process each symbol like build_dataset does
rows = []
for sym in symbols:
    if sym not in adj.columns:
        print(f'  {sym}: NOT in adj.columns')
        continue
    series = adj[sym].dropna()
    print(f'  {sym}: series length = {len(series)}')
    if len(series) < 30:
        print(f'    -> SKIP: too short')
        continue
    tech = make_technical_features(series)
    print(f'    -> tech shape: {tech.shape}')
    
    # Build training rows
    for t_idx in range(30, len(tech) - return_days):
        date = tech.index[t_idx]
        feat_row = tech.iloc[t_idx].to_dict()
        future_price = series.iloc[t_idx + return_days]
        price = series.iloc[t_idx]
        fwd_ret = (future_price / price) - 1.0
        rec = {"date": date, "symbol": sym, "fwd_ret": fwd_ret}
        rec.update({k: (v if v is not None else pd.NA) for k, v in feat_row.items()})
        rows.append(rec)
    print(f'    -> added {len([r for r in rows if r["symbol"] == sym])} training rows')

print(f'\nTotal rows collected: {len(rows)}')
if rows:
    df = pd.DataFrame(rows)
    print(f'Dataset shape: {df.shape}')
    print(f'Dataset empty after dropna: {df.dropna(subset=["fwd_ret"]).empty}')
else:
    print('No rows collected!')
