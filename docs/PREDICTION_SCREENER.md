Prediction Screener CLI

Purpose
- Run the short-term prediction pipeline from the command line with filters and options.
- Produces a predictions CSV in `reports/screener_results/` (timestamped by date).

Usage

Basic run using latest screener CSV:

```bash
python tools/predict_screener_cli.py --limit 200 --return-days 5
```

Specify a candidates file (produced by the screener):

```bash
python tools/predict_screener_cli.py --candidates-file reports/screener_results/yfinance_screener_results_with_metrics_20260606.csv --limit 100
```

Filter by sector or industry (case-insensitive substring):

```bash
python tools/predict_screener_cli.py --sector Technology --limit 100
python tools/predict_screener_cli.py --industry "Software" --limit 200
```

Save a versioned copy:

```bash
python tools/predict_screener_cli.py --versioned
```

Options
- `--candidates-file`: path to an existing screener CSV. If omitted, the latest screener CSV in `reports/screener_results/` is used.
- `--sector`: case-insensitive substring filter on `sector` column.
- `--industry`: case-insensitive substring filter on `industry` column.
- `--limit`: maximum number of symbols to analyze.
- `--return-days`: forward return horizon to predict (1/3/5).
- `--lookback`: price history lookback in days.
- `--out`: output path for predictions CSV.
- `--versioned`: save an additional timestamped copy.

Notes
- This CLI reuses the sandbox prediction pipeline. It's intended as a reproducible and extensible runner. For production use, factor shared code into a package and add unit tests.
- If you edit files under `src/`, reinstall the package in editable mode: `pip install -e .` to refresh console scripts.
