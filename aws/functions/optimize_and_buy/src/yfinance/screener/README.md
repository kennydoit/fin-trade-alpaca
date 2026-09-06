# yfinance.screener — Run instructions

Quick instructions for running the yfinance screener and related tooling.

## Quick start (Windows PowerShell)

Activate the project's virtualenv and (re)install the package in editable mode:

```powershell
& .venv\Scripts\Activate.ps1
pip install -e .
```

Run the growth screener directly from the repo root:

```powershell
python src/yfinance/screener/yfinance_growth_screener.py --config configs/equity_screener.json
```

This is the best sample command to copy for a real run because the JSON file already controls the sector filters, price range, volume/market-cap floors, worker count, and output path.

If you prefer an editable install for local imports, you can still run:

```powershell
pip install -e .
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

After producing an enriched CSV, run ranking and dedup directly from the repo root:

```powershell
python src/yfinance/screener/rank_candidates.py --input reports/screener_results/yfinance_screener_results_with_metrics.csv --output reports/screener_results/ranked.csv
python src/yfinance/screener/dedup_by_correlation.py --input reports/screener_results/ranked.csv --output reports/screener_results/ranked_deduped.csv
```

## Troubleshooting

- If the screener returns zero candidates, ensure `yfinance` provides `EquityQuery`.
  - Install the latest upstream version if needed:

```powershell
pip install --upgrade git+https://github.com/ranaroussi/yfinance.git@main
```

- If you edit code under `src/`, re-run `pip install -e .` so local imports reflect changes.

- The repository contains a safe local shim at `src/yfinance/__init__.py` that prefers an installed `yfinance` but falls back to the local `screener` package. If you need to force using the installed `yfinance`, install the upstream package and then restart your Python session/terminal.

## Notes

- Prefer running the sandbox script for quick iterative debugging; use the module entry points for reproducible runs.
- Commands above assume PowerShell on Windows; replace activation command and path separators as needed for other shells/OSes.
