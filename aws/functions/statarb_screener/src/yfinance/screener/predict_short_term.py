"""Short-term return prediction pipeline: CLI entrypoint and backward-compatible re-exports.

The actual implementation lives in focused sibling modules:
  - data_loading.py        screener CSV discovery + yfinance price downloads
  - feature_engineering.py technical features, training dataset construction, feature prep
  - charts.py              optional actual-vs-predicted diagnostic plot
  - model_training.py      model construction, tuning, train/evaluate loop
  - scoring.py             scoring the latest trading day for candidate symbols

This module re-exports the public names from those modules so existing
callers (``from yfinance.screener.predict_short_term import build_dataset``,
etc.) keep working unchanged, and it owns the ``main()`` CLI entrypoint for
running the pipeline standalone (``python -m yfinance.screener.predict_short_term``).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .charts import MATPLOTLIB_INSTALLED, save_actual_vs_predicted_chart
from .data_loading import download_price_history, find_latest_screener_file
from .feature_engineering import (
    build_dataset,
    compute_excess_return_target,
    make_technical_features,
    prepare_features,
)
from .model_training import (
    LGB_INSTALLED,
    build_model,
    compute_feature_importance,
    train_and_evaluate,
    tune_model,
)
from .scoring import score_latest

__all__ = [
    "LGB_INSTALLED",
    "MATPLOTLIB_INSTALLED",
    "build_dataset",
    "build_model",
    "compute_excess_return_target",
    "compute_feature_importance",
    "download_price_history",
    "find_latest_screener_file",
    "make_technical_features",
    "prepare_features",
    "save_actual_vs_predicted_chart",
    "score_latest",
    "train_and_evaluate",
    "tune_model",
    "main",
]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--return-days", type=int, default=5)
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--lookback", type=int, default=180, help="lookback days for price history per symbol")
    p.add_argument("--candidates-file", default=None)
    args = p.parse_args()

    repo = Path(__file__).resolve().parents[2]
    reports = repo / "reports" / "screener_results"

    cand_file = Path(args.candidates_file) if args.candidates_file else find_latest_screener_file(reports)

    print(f"Using candidates file: {cand_file}")
    cand_df = pd.read_csv(cand_file)
    symbols = list(cand_df["symbol"].astype(str).unique())[: args.limit]

    print(f"Building dataset for {len(symbols)} symbols (lookback={args.lookback})...")
    df = build_dataset(cand_df, symbols, args.lookback, args.return_days)
    if df.empty:
        raise SystemExit("No training data constructed; increase lookback or limit")

    print(f"Constructed dataset with {len(df)} rows")
    model, feat_cols, metrics, _feature_importance, scaler = train_and_evaluate(df, args.return_days)
    score_latest(model, feat_cols, cand_df, symbols, args.return_days, scaler=scaler, metrics=metrics)


if __name__ == "__main__":
    main()
