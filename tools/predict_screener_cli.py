"""CLI wrapper to run the sandbox short-term predictor with filtering and options.

Usage examples:
  python tools/predict_screener_cli.py --candidates-file reports/screener_results/yfinance_screener_results_with_metrics_20260606.csv --sector "Technology" --limit 100 --return-days 5

If no `--candidates-file` is given, the latest screener CSV in `reports/screener_results/` is used.
The script builds a dataset, trains the baseline model, scores the latest date, and writes a versioned predictions CSV.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import List

import pandas as pd
import sys

# ensure repo root is on sys.path so we can import sandbox modules
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# import functions from sandbox pipeline
from sandbox.predict_short_term import (
    find_latest_screener_file,
    build_dataset,
    train_and_evaluate,
    download_price_history,
    make_technical_features,
)


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


def score_latest_local(model, feat_cols: list, cand_df: pd.DataFrame, symbols: List[str], return_days: int, out_path: Path):
    adj = download_price_history(symbols, 200)
    if adj.empty:
        raise SystemExit('No price history available for scoring')
    latest_date = adj.index[-1]
    rows = []
    for sym in symbols:
        if sym not in adj.columns:
            continue
        series = adj[sym].dropna()
        if len(series) < 30:
            continue
        tech = make_technical_features(series)
        feat_row = tech.iloc[-1].to_dict()
        meta = {}
        row_meta = cand_df[cand_df['symbol'].astype(str) == sym]
        if not row_meta.empty:
            for col in ['pct_1w', 'pct_1m', 'rel_volume', 'revenueGrowth', 'earningsQuarterlyGrowth', 'pegRatio', 'trailingPE', 'sector', 'industry']:
                if col in row_meta.columns:
                    meta[col] = row_meta.iloc[0].get(col)
        rec = {'symbol': sym, 'date': latest_date}
        rec.update({k: (v if v is not None else pd.NA) for k, v in feat_row.items()})
        rec.update(meta)
        rows.append(rec)
    df = pd.DataFrame(rows)
    if df.empty:
        raise SystemExit('No rows to score')
    X = df[feat_cols].fillna(0.0)
    preds = model.predict(X)
    df['pred_ret'] = preds
    df['avg_ret'] = df[['ret_1d','ret_3d','ret_5d']].mean(axis=1)
    df = df.sort_values('avg_ret', ascending=False)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f'Wrote predictions to {out_path}')


def main():
    p = argparse.ArgumentParser(description='Run prediction screener with filtering options')
    p.add_argument('--candidates-file', help='Input screener CSV (if omitted use latest)')
    p.add_argument('--sector', help='Filter symbols by sector (case-insensitive substring)')
    p.add_argument('--industry', help='Filter symbols by industry (case-insensitive substring)')
    p.add_argument('--limit', type=int, default=200, help='Max number of symbols to analyze')
    p.add_argument('--return-days', type=int, default=5, choices=[1,3,5], help='Forward return horizon to predict')
    p.add_argument('--lookback', type=int, default=180, help='Price history lookback (days)')
    p.add_argument('--out', help='Optional output path for predictions CSV')
    p.add_argument('--versioned', action='store_true', help='Also save a timestamped copy')
    args = p.parse_args()

    repo = Path(__file__).resolve().parents[1]
    reports = repo / 'reports' / 'screener_results'

    if args.candidates_file:
        cand_file = Path(args.candidates_file)
    else:
        cand_file = find_latest_screener_file(reports)

    print(f'Using candidates file: {cand_file}')
    cand_df = pd.read_csv(cand_file)
    cand_df = filter_candidates(cand_df, args.sector, args.industry)
    symbols = list(cand_df['symbol'].astype(str).unique())[: args.limit]
    print(f'Analyzing {len(symbols)} symbols (limit={args.limit})')

    df = build_dataset(cand_df, symbols, args.lookback, args.return_days)
    if df.empty:
        raise SystemExit('No training data constructed; try increasing lookback or limit')

    print(f'Constructed dataset with {len(df)} rows')
    model, feat_cols = train_and_evaluate(df, args.return_days)

    # determine output path
    ts = datetime.utcnow().strftime('%Y%m%d')
    if args.out:
        out_path = Path(args.out)
    else:
        out_path = reports / f'predictions_{ts}.csv'
    score_latest_local(model, feat_cols, cand_df, symbols, args.return_days, out_path)

    if args.versioned:
        ver = datetime.utcnow().strftime('%Y%m%dT%H%M%S')
        ver_path = out_path.with_name(out_path.stem + f'.v{ver}' + out_path.suffix)
        ver_path.parent.mkdir(parents=True, exist_ok=True)
        ver_path.write_text(out_path.read_text())
        print(f'Wrote versioned copy: {ver_path}')


if __name__ == '__main__':
    main()
