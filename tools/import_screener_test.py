import importlib.util, traceback
files = [
    r'C:\Users\Kenrm\repositories\fin-trade-alpaca\src\yfinance\screener\yfinance_equity_screener.py',
    r'C:\Users\Kenrm\repositories\fin-trade-alpaca\src\yfinance\screener\yfinance_growth_screener.py',
    r'C:\Users\Kenrm\repositories\fin-trade-alpaca\src\yfinance\screener\rank_candidates.py',
    r'C:\Users\Kenrm\repositories\fin-trade-alpaca\src\yfinance\screener\dedup_by_correlation.py',
    r'C:\Users\Kenrm\repositories\fin-trade-alpaca\src\yfinance\screener\inspect_screener_module.py',
]
for f in files:
    try:
        spec = importlib.util.spec_from_file_location('m', f)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        print(f + ' loaded')
    except Exception as e:
        print(f + ' FAILED ->', type(e).__name__, e)
        traceback.print_exc()
