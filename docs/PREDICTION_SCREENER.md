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

Output CSV Columns

The predictions CSV includes:

**Symbol & Date**
- `symbol`: Stock ticker
- `date`: Latest date for prediction

**Technical Features**
- `close`, `ret_1d`, `ret_3d`, `ret_5d`: Price and returns
- `vol_10d`: 10-day volatility
- `mom_20d`: 20-day momentum
- `sma_10`: 10-day simple moving average
- `price_sma10_z`: Z-score relative to SMA

**Fundamental Metrics** (from screener input)
- `pct_1w`, `pct_1m`: Recent price changes
- `rel_volume`: Relative volume
- `revenueGrowth`, `earningsQuarterlyGrowth`: Growth metrics
- `pegRatio`, `trailingPE`: Valuation ratios
- `sector`, `industry`: Classification

**Predictions**
- `pred_ret`: Predicted return (model output)
- `avg_ret`: Average of ret_1d, ret_3d, ret_5d

**Strategy Attribution** (NEW - for database integration)
- `screener_rank`: Ranking from 1 (best) to N (sorted by avg_ret descending)
- `strategy_source`: Always "predictive_model" for this screener
- `model_type`: Model used ("lightgbm" if installed, else "random_forest")
- `model_r2_score`: R² score from validation set
- `model_spearman_ic`: Spearman IC from validation set
- `model_mae`: Mean absolute error from validation set

These attribution columns can be used to populate database metadata when positions are opened.
See `tools/set_metadata_from_predictions.py` for an example integration.

