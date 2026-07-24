import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from yfinance.screener.predict_short_term import build_dataset, train_and_evaluate, find_latest_screener_file

screener_folder = Path(__file__).resolve().parent.parent / 'reports' / 'screener_results'
cand_file = find_latest_screener_file(screener_folder)
cand_df = pd.read_csv(cand_file)
symbols = cand_df['symbol'].dropna().astype(str).tolist()[:40]

print('cand_file:', cand_file)
print('symbols:', len(symbols))

df = build_dataset(cand_df, symbols, lookback=180, return_days=5)
print('rows:', len(df))
print(df[['fwd_ret']].describe().to_string())

cols = ['ret_1d','ret_3d','ret_5d','vol_10d','mom_20d','sma_10','price_sma10_z','pct_1w','pct_1m','rel_volume','revenueGrowth','earningsQuarterlyGrowth','pegRatio','trailingPE']
for col in cols:
    if col in df.columns:
        v = df[['fwd_ret', col]].dropna()
        if len(v) > 50:
            corr = v[col].corr(v['fwd_ret'])
            print(f'{col}: corr={corr:.4f}, n={len(v)}')

for model_type in ['lightgbm', 'random_forest', 'ridge']:
    try:
        model, feat_cols, metrics, fi, scaler = train_and_evaluate(
            df,
            return_days=5,
            use_enhanced_features=True,
            model_config={'type': model_type},
        )
        print(model_type, 'metrics=', metrics)
    except Exception as e:
        print(model_type, 'failed:', repr(e))
