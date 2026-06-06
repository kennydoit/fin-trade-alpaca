import pandas as pd
import yfinance as yf

cf='reports/screener_results/yfinance_screener_results_with_metrics_20260606.csv'
df=pd.read_csv(cf)
print('Total symbols in CSV:', len(df))
symbols=list(df['symbol'].astype(str).unique())[:10]
print('Sample symbols:', symbols)
for s in symbols:
    try:
        h = yf.Ticker(s).history(period='200d', interval='1d', actions=False)
        print(s, 'rows:', 0 if h is None else len(h))
    except Exception as e:
        print(s, 'error', e)
