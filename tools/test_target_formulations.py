import sys
from pathlib import Path
import pandas as pd
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
from yfinance.screener.predict_short_term import build_dataset, find_latest_screener_file, prepare_features
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import r2_score, mean_absolute_error
from scipy.stats import spearmanr

cand_file = find_latest_screener_file(Path(__file__).resolve().parent.parent / 'reports' / 'screener_results')
cand_df = pd.read_csv(cand_file)
symbols = cand_df['symbol'].dropna().astype(str).tolist()[:40]

df = build_dataset(cand_df, symbols, lookback=180, return_days=30)

print('rows', len(df))
for label_name, label_series in {
    'raw': df['fwd_ret'],
    'excess_sector': (df['fwd_ret'] - df.groupby(['date','sector'])['fwd_ret'].transform('median')).fillna(df['fwd_ret'] - df.groupby('date')['fwd_ret'].transform('median')),
    'rank_pct': df.groupby('date')['fwd_ret'].rank(pct=True),
    'rank_pct_excess': df.groupby(['date','sector'])['fwd_ret'].rank(pct=True) if 'sector' in df.columns else df.groupby('date')['fwd_ret'].rank(pct=True),
}.items():
    dd = df.copy()
    dd['target'] = label_series
    dd = dd.sort_values('date')
    unique_dates = sorted(dd['date'].unique())
    split_date = unique_dates[-30]
    train = dd[dd['date'] <= split_date].copy()
    test = dd[dd['date'] > split_date].copy()
    X_train, feat_cols, _ = prepare_features(train, fit_scaler=False, use_standardization=False)
    X_test, _, _ = prepare_features(test, scaler=None, fit_scaler=False, use_standardization=False)
    y_train = train['target'].values
    y_test = test['target'].values
    model = RandomForestRegressor(n_estimators=200, random_state=42, n_jobs=-1)
    model.fit(X_train, y_train)
    pred = model.predict(X_test)
    print(label_name, 'R2=', round(r2_score(y_test, pred), 4), 'IC=', round(spearmanr(pred, y_test).statistic, 4), 'MAE=', round(mean_absolute_error(y_test, pred), 4))
