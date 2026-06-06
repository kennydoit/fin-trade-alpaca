"""Walk-forward CV + simple hyperparameter tuning for short-term returns.

This script reuses `sandbox/predict_short_term.py` helpers to build the
dataset, then performs a rolling walk-forward CV, evaluates Spearman IC,
and selects hyperparameters by average IC. It supports cross-sectional
standardization of features via `--cs-standardize`.

Example:
  python sandbox/predict_short_term_cv.py --return-days 5 --limit 50 --lookback 180 --cs-standardize
"""
from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path
import statistics

import numpy as np
import pandas as pd

from scipy.stats import spearmanr


def load_predict_module():
    spec = importlib.util.spec_from_file_location("predict_short_term", "sandbox/predict_short_term.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    return mod


def cs_standardize(df: pd.DataFrame, feat_cols):
    # per-date z-score across cross-section
    out = []
    for d, g in df.groupby("date"):
        g2 = g.copy()
        g2[feat_cols] = (g2[feat_cols] - g2[feat_cols].mean()) / g2[feat_cols].std(ddof=0)
        g2[feat_cols] = g2[feat_cols].fillna(0.0)
        out.append(g2)
    return pd.concat(out, axis=0)


def main():
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--return-days", type=int, default=5)
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--lookback", type=int, default=180)
    p.add_argument("--candidates-file", default=None)
    p.add_argument("--cs-standardize", action="store_true")
    args = p.parse_args()

    repo = Path(__file__).resolve().parents[1]
    reports = repo / "reports" / "screener_results"

    predict = load_predict_module()

    if args.candidates_file:
        cand_file = Path(args.candidates_file)
    else:
        cand_file = predict.find_latest_screener_file(reports)

    cand_df = predict.pd.read_csv(cand_file)
    symbols = list(cand_df["symbol"].astype(str).unique())[: args.limit]

    print(f"Building dataset for {len(symbols)} symbols...")
    df = predict.build_dataset(cand_df, symbols, args.lookback, args.return_days)
    if df.empty:
        raise SystemExit("No data constructed")

    # feature columns
    drop = ["date", "symbol", "fwd_ret"]
    feat_cols = [c for c in df.columns if c not in drop]

    if args.cs_standardize:
        print("Applying cross-sectional standardization to features...")
        df = cs_standardize(df, feat_cols)

    # prepare folds by unique date
    dates = sorted(pd.to_datetime(df["date"]).unique())
    if len(dates) < 10:
        raise SystemExit("Not enough dates for walk-forward CV")

    # define walk-forward windows: initial train 50% then validate windows of 20% each
    n = len(dates)
    init = max(1, int(n * 0.5))
    val_window = max(1, int(n * 0.2))
    folds = []
    i = init
    while i + val_window <= n:
        train_dates = dates[:i]
        val_dates = dates[i : i + val_window]
        folds.append((train_dates, val_dates))
        i += val_window

    print(f"Created {len(folds)} walk-forward folds (init={init}, val_window={val_window})")

    # simple hyperparam grid
    LGB = hasattr(predict, "lgb") or predict.LGB_INSTALLED
    if LGB:
        grid = [
            {"num_leaves": 31, "learning_rate": 0.05, "n_estimators": 200},
            {"num_leaves": 63, "learning_rate": 0.01, "n_estimators": 500},
        ]
    else:
        grid = [
            {"n_estimators": 100, "max_depth": 5},
            {"n_estimators": 200, "max_depth": 10},
        ]

    results = {}
    for params in grid:
        key = tuple(sorted(params.items()))
        results[key] = []

    for fidx, (train_dates, val_dates) in enumerate(folds, start=1):
        train_df = df[df["date"].isin(train_dates)]
        val_df = df[df["date"].isin(val_dates)]
        X_train = train_df[feat_cols].fillna(0.0).values
        y_train = train_df["fwd_ret"].values
        X_val = val_df[feat_cols].fillna(0.0).values
        y_val = val_df["fwd_ret"].values

        for params in grid:
            if LGB:
                import lightgbm as lgb

                m = lgb.LGBMRegressor(**params)
            else:
                from sklearn.ensemble import RandomForestRegressor

                m = RandomForestRegressor(**params, random_state=42)
            try:
                m.fit(X_train, y_train)
                pred = m.predict(X_val)
                ic = spearmanr(y_val, pred).correlation
                if np.isnan(ic):
                    ic = 0.0
            except Exception as e:
                print("Fold training error", e)
                ic = 0.0
            results[tuple(sorted(params.items()))].append(ic)
        print(f"Fold {fidx}/{len(folds)} done")

    avg_scores = {k: statistics.mean(v) if v else 0.0 for k, v in results.items()}
    best_key = max(avg_scores, key=avg_scores.get)
    best_params = dict(best_key)
    print("Average IC per param set:")
    for k, v in avg_scores.items():
        print(k, f"IC={v:.4f}")
    print("Best params:", best_params)

    # retrain on all data up to last validation end
    last_val_end = folds[-1][1][-1]
    train_all = df[df["date"] <= last_val_end]
    X_all = train_all[feat_cols].fillna(0.0).values
    y_all = train_all["fwd_ret"].values
    if LGB:
        import lightgbm as lgb

        final_model = lgb.LGBMRegressor(**best_params)
    else:
        from sklearn.ensemble import RandomForestRegressor

        final_model = RandomForestRegressor(**best_params, random_state=42)
    final_model.fit(X_all, y_all)

    # score latest
    latest_date = max(df["date"])
    latest_df = df[df["date"] == latest_date]
    X_latest = latest_df[feat_cols].fillna(0.0).values
    preds = final_model.predict(X_latest)
    out = latest_df.copy()
    out["pred_ret"] = preds
    out = out.sort_values("pred_ret", ascending=False)
    ts = datetime.utcnow().strftime("%Y%m%d")
    out_file = reports / f"predictions_cv_{ts}.csv"
    out.to_csv(out_file, index=False)
    print(f"Wrote CV predictions to {out_file}")


if __name__ == "__main__":
    main()
