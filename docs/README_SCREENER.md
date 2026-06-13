Running the Screener CLI
========================

This project exposes a small set of CLI entry points for the local screener
utilities. There are two recommended ways to make the CLIs available:

1) Editable install (recommended)

From the repository root (activate your virtualenv first):

```powershell
pip install -e .
```

This registers the `yfinance-screener`, `yfinance-rank`, and `yfinance-dedup`
console scripts so you can run them directly from the shell:

```powershell
# fetch candidates and compute metrics (small smoke run)
yfinance-screener --limit 5

# rank a previously written candidates CSV
yfinance-rank --in reports/screener_results/yfinance_screener_results_with_metrics.csv --out reports/screener_results/ranked.csv

# deduplicate a ranked file by correlation
yfinance-dedup --in reports/screener_results/ranked.csv --threshold 0.85
```

2) Run without installing (use PYTHONPATH)

If you prefer not to install, prepend `src` to `PYTHONPATH` when running via
`python -m` so the local `yfinance.screener` package is used:

```powershell
$env:PYTHONPATH='src'
python -m yfinance.screener.yfinance_growth_screener --limit 5
```

Notes and troubleshooting
- The scripts import a local `yfinance.screener` package in `src/`. There is
  an existing `yfinance` package on PyPI; to avoid import conflicts prefer the
  editable install or the `PYTHONPATH` approach shown above.
- The screener uses features of `yfinance` that vary by version (e.g. `EquityQuery`).
  If the screener reports `EquityQuery not found in yfinance`, upgrade `yfinance`:

```powershell
pip install --upgrade yfinance
```

- Network calls to Yahoo Finance occur during screener runs. If you need to
  run offline tests, pass a `--candidates-file` to the growth screener to
  process a pre-written CSV instead of querying the screener.

Files of interest
- `src/yfinance/screener/yfinance_equity_screener.py` — core screener implementation
- `src/yfinance/screener/yfinance_growth_screener.py` — growth metrics CLI
- `src/yfinance/screener/rank_candidates.py` — ranking CLI
- `src/yfinance/screener/dedup_by_correlation.py` — dedup CLI

If you want, I can run `pip install -e .` in your current venv and execute a
smoke run of the CLI to verify everything works end-to-end.
