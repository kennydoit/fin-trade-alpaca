"""Packaged CLI wrapper for the prediction screener.

This mirrors the existing `tools/predict_screener_cli.py` but lives inside
the installed package so console-scripts can import it reliably.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, List

import pandas as pd
import numpy as np

# Ensure the repo root is accessible for imports
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Import from our local src/yfinance/screener module
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from yfinance.screener.predict_short_term import (
    find_latest_screener_file,
    build_dataset,
    train_and_evaluate,
    download_price_history,
    make_technical_features,
)

DEFAULT_PREDICTION_CONFIG: dict[str, Any] = {
    "candidates_file": None,
    "sector": None,
    "industry": None,
    "limit": 200,
    "return_days": 5,
    "lookback": 180,
    "out": None,
    "versioned": False,
    "model": {
        "type": "lightgbm",
        "n_estimators": 200,
        "learning_rate": 0.05,
        "random_state": 42,
        "tune": False,
        "tune_n_iter": 6,
        "tune_cv": 3,
    },
    "target": {"horizon_days": 5, "label_column": "fwd_ret"},
}


def load_prediction_config(config_path: str | Path | None) -> dict[str, Any]:
    """Load the prediction screener JSON config, if present."""
    if not config_path:
        return {}

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Prediction config not found: {path}")

    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    if not isinstance(data, dict):
        raise ValueError("Prediction config must be a JSON object")

    return data


def resolve_runtime_config(args: argparse.Namespace) -> dict[str, Any]:
    """Merge config-file defaults with CLI overrides."""
    config = load_prediction_config(getattr(args, "config", None))
    merged = dict(DEFAULT_PREDICTION_CONFIG)
    merged.update(config)

    for key, value in vars(args).items():
        if key == "config":
            continue
        if value is not None:
            merged[key] = value

    # normalize keys so CLI option names and config keys line up
    merged["candidates_file"] = merged.get("candidates_file")
    merged["sector"] = merged.get("sector")
    merged["industry"] = merged.get("industry")
    merged["limit"] = int(merged.get("limit", DEFAULT_PREDICTION_CONFIG["limit"]))
    merged["return_days"] = int(merged.get("return_days", DEFAULT_PREDICTION_CONFIG["return_days"]))
    merged["lookback"] = int(merged.get("lookback", DEFAULT_PREDICTION_CONFIG["lookback"]))
    merged["out"] = merged.get("out")
    merged["versioned"] = bool(merged.get("versioned", False))
    return merged


def filter_candidates(df: pd.DataFrame, sector: str | None, industry: str | None) -> pd.DataFrame:
    if sector:
        if 'sector' in df.columns:
            df = df[df['sector'].astype(str).str.contains(sector, case=False, na=False)]
        else:
            print('Warning: candidates file has no `sector` column; ignoring --sector')
    if industry:
        if 'industry' in df.columns:
            df = df[df['industry'].astype(str).str.contains(industry, case=False, na=False)]
        else:
            print('Warning: candidates file has no `industry` column; ignoring --industry')
    return df


def score_latest_local(model, feat_cols: list, cand_df: pd.DataFrame, symbols: List[str], return_days: int, out_path: Path, metrics: dict = None):
    """Score latest prices using enhanced features from predict_short_term module."""
    from yfinance.screener.predict_short_term import score_latest
    
    # Use the updated score_latest that handles enhanced features
    df = score_latest(model, feat_cols, cand_df, symbols, return_days, metrics, use_enhanced_features=True)
    
    if df.empty:
        raise SystemExit('No rows to score')
    
    # Add avg_ret column if not present
    if 'avg_ret' not in df.columns and all(c in df.columns for c in ['ret_1d', 'ret_3d', 'ret_5d']):
        df['avg_ret'] = df[['ret_1d','ret_3d','ret_5d']].mean(axis=1)
    
    # Sort and add strategy metadata
    if 'pred_ret' in df.columns:
        df = df.sort_values('pred_ret', ascending=False)
    elif 'avg_ret' in df.columns:
        df = df.sort_values('avg_ret', ascending=False)
    
    # Add strategy attribution metadata
    df['screener_rank'] = range(1, len(df) + 1)
    df['strategy_source'] = 'predictive_model'
    if metrics:
        df['model_type'] = metrics.get('model_type', 'unknown')
        df['model_r2_score'] = metrics.get('r2_score')
        df['model_spearman_ic'] = metrics.get('spearman_ic')
        df['model_mae'] = metrics.get('mae')
    
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f'Wrote predictions to {out_path}')



def main():
    p = argparse.ArgumentParser(description='Run prediction screener with filtering options')
    p.add_argument('--config', help='Path to JSON config file for prediction screener settings')
    p.add_argument('--candidates-file', default=None, help='Input screener CSV (if omitted use latest)')
    p.add_argument('--sector', default=None, help='Filter symbols by sector (case-insensitive substring)')
    p.add_argument('--industry', default=None, help='Filter symbols by industry (case-insensitive substring)')
    p.add_argument('--limit', type=int, default=None, help='Max number of symbols to analyze')
    p.add_argument('--return-days', type=int, default=None, choices=[1, 3, 5], help='Forward return horizon to predict')
    p.add_argument('--lookback', type=int, default=None, help='Price history lookback (days)')
    p.add_argument('--out', default=None, help='Optional output path for predictions CSV')
    p.add_argument('--versioned', action='store_const', const=True, default=None, help='Also save a timestamped copy')
    args = p.parse_args()

    runtime = resolve_runtime_config(args)

    repo = Path(__file__).resolve().parents[2]
    reports = repo / 'reports' / 'screener_results'

    if runtime['candidates_file']:
        cand_file = Path(runtime['candidates_file'])
    else:
        cand_file = find_latest_screener_file(reports)

    print(f'Using candidates file: {cand_file}')
    cand_df = pd.read_csv(cand_file)
    cand_df = filter_candidates(cand_df, runtime['sector'], runtime['industry'])
    symbols = list(cand_df['symbol'].astype(str).unique())[: runtime['limit']]
    print(f'Analyzing {len(symbols)} symbols (limit={runtime["limit"]})')

    df = build_dataset(cand_df, symbols, runtime['lookback'], runtime['return_days'])
    if df.empty:
        raise SystemExit('No training data constructed; try increasing lookback or limit')

    print(f'Constructed dataset with {len(df)} rows')
    model, feat_cols, metrics, feature_importance = train_and_evaluate(
        df,
        runtime['return_days'],
        model_config=runtime.get('model', {}),
    )

    # determine output path
    ts = datetime.utcnow().strftime('%Y%m%d')
    if runtime['out']:
        out_path = Path(runtime['out'])
    else:
        out_path = reports / f'predictions_{ts}.csv'
    if not feature_importance.empty:
        fi_path = reports / f'feature_importance_{ts}.csv'
        feature_importance.to_csv(fi_path, index=False)
        print(f'Wrote feature importances to {fi_path}')

    score_latest_local(model, feat_cols, cand_df, symbols, runtime['return_days'], out_path, metrics)

    if runtime['versioned']:
        ver = datetime.utcnow().strftime('%Y%m%dT%H%M%S')
        ver_path = out_path.with_name(out_path.stem + f'.v{ver}' + out_path.suffix)
        ver_path.parent.mkdir(parents=True, exist_ok=True)
        ver_path.write_text(out_path.read_text())
        print(f'Wrote versioned copy: {ver_path}')


if __name__ == '__main__':
    main()
