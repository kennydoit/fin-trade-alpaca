import csv
from pathlib import Path
import importlib.util
import sys

# load sandbox/yfinance_equity_screener.py by path
spec = importlib.util.spec_from_file_location("yfs", "sandbox/yfinance_equity_screener.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["yfs"] = mod
spec.loader.exec_module(mod)
enrich_results_with_info = getattr(mod, "enrich_results_with_info")

p = Path('reports/screener_results/yfinance_screener_results.csv')
if not p.exists():
    print('CSV not found:', p)
    raise SystemExit(1)

rows = []
with p.open('r', encoding='utf-8') as f:
    rdr = csv.DictReader(f)
    for r in rdr:
        rows.append(r)
print('loaded', len(rows), 'rows')

enriched = enrich_results_with_info(rows)
print('enriched len', len(enriched))

with p.open('w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    headers = ['symbol', 'shortName', 'exchange', 'sector', 'industry', 'eodprice', 'regularMarketPrice']
    writer.writerow(headers)
    for r in enriched:
        row = [r.get(h, '') for h in headers]
        writer.writerow(row)
print('done')
