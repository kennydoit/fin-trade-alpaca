# yfinance.screener — Run instructions

Quick instructions for running the yfinance screener and related tooling.

## Quick start (Windows PowerShell)

Activate the project's virtualenv and (re)install the package in editable mode:

```powershell
& .venv\Scripts\Activate.ps1
pip install -e .
```

Run the growth screener (console script):

```powershell
yfinance-screener --limit 100 --workers 4
```

Run the screener module directly (no install required):

```powershell
python -m yfinance.screener.yfinance_growth_screener --limit 100 --workers 4
```

Or run the sandbox copy (useful for quick experiments):

```powershell
python sandbox/yfinance_growth_screener.py --limit 100 --workers 4
```

## Enriching saved results

If the screener CSV is missing `sector`, `industry`, or `eodprice`, run the enrichment helper:

```powershell
python tools/enrich_csv.py reports/screener_results/yfinance_screener_results.csv
```

## Re-ranking and de-duplication

After producing an enriched CSV, run ranking and dedup:

```powershell
yfinance-rank --input reports/screener_results/yfinance_screener_results_with_metrics.csv --output reports/screener_results/ranked.csv
yfinance-dedup --input reports/screener_results/ranked.csv --output reports/screener_results/ranked_deduped.csv
```

Or run the modules directly:

```powershell
python -m yfinance.screener.rank_candidates --input reports/screener_results/yfinance_screener_results_with_metrics.csv
python -m yfinance.screener.dedup_by_correlation --input reports/screener_results/ranked.csv
```

## Troubleshooting

- If the screener returns zero candidates, ensure `yfinance` provides `EquityQuery`.
  - Install the latest upstream version if needed:

```powershell
pip install --upgrade git+https://github.com/ranaroussi/yfinance.git@main
```

- If you edit code under `src/`, re-run `pip install -e .` so console scripts and imports reflect changes.

- The repository contains a safe local shim at `src/yfinance/__init__.py` that prefers an installed `yfinance` but falls back to the local `screener` package. If you need to force using the installed `yfinance`, install the upstream package and then restart your Python session/terminal.

## Notes

- Prefer running the sandbox script for quick iterative debugging; switch to console scripts for reproducible CI runs.
- Commands above assume PowerShell on Windows; replace activation command and path separators as needed for other shells/OSes.
