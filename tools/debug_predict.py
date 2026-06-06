import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('predict', 'sandbox/predict_short_term.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

cf = Path('reports/screener_results/yfinance_screener_results_with_metrics_20260606.csv')
df = mod.pd.read_csv(cf)
symbols = list(df['symbol'].astype(str).unique())[:50]
print('symbols len', len(symbols))
adj = mod.download_price_history(symbols, 180+5)
print('adj shape', None if adj is None else adj.shape)

built = mod.build_dataset(df, symbols, 180, 5)
print('built rows', len(built))
print('columns:', built.columns.tolist() if len(built)>0 else '')
