"""Baseline short-term return prediction pipeline (sandbox).

Creates a dataset by sliding over price history for symbols from a screener
CSV, attaches static screener metrics, computes past technical features,
labels forward `N`-day returns, trains a baseline model, evaluates IC and
prints top candidates for the latest date. Saves `reports/screener_results/predictions_{YYYYMMDD}.csv`.

This is a sandbox prototyping script — adapt feature set and CV to taste.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd

try:
    import lightgbm as lgb  # type: ignore
    LGB_INSTALLED = True
except Exception:
    LGB_INSTALLED = False

from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import r2_score, mean_absolute_error
from scipy.stats import spearmanr

import yfinance as yf


def find_latest_screener_file(folder: Path) -> Path:
    files = sorted(folder.glob("yfinance_screener_results_with_metrics*.csv"))
    if not files:
        raise SystemExit(f"No screener CSVs found in {folder}")
    return files[-1]


def download_price_history(symbols: List[str], lookback_days: int) -> pd.DataFrame:
    period = f"{lookback_days}d"
    # prefer yf.download when available; fall back to per-symbol history
    if hasattr(yf, "download"):
        df = yf.download(symbols, period=period, interval="1d", progress=False, threads=True)
        if isinstance(df, tuple):
            df = df[0]
        # prefer Adjusted Close when present
        if "Adj Close" in df:
            adj = df["Adj Close"].copy()
        else:
            adj = df["Close"].copy()
        if isinstance(adj, pd.Series):
            adj = adj.to_frame()
        adj.columns = [c if isinstance(c, str) else c[1] for c in adj.columns]
        return adj
    # fallback: per-symbol history
    cols = {}
    for s in symbols:
        try:
            t = yf.Ticker(s)
            h = t.history(period=period, interval="1d", actions=False)
            if h is None or h.empty:
                continue
            if "Adj Close" in h:
                cols[s] = h["Adj Close"].rename(s)
            else:
                cols[s] = h["Close"].rename(s)
        except Exception:
            continue
    if not cols:
        return pd.DataFrame()
    adj = pd.concat(cols.values(), axis=1)
    return adj


def make_technical_features(adj: pd.Series) -> pd.DataFrame:
    # adj is a Series of prices indexed by date for one symbol
    df = pd.DataFrame({"close": adj})
    df["ret_1d"] = df["close"].pct_change()
    df["ret_3d"] = df["close"].pct_change(3)
    df["ret_5d"] = df["close"].pct_change(5)
    df["vol_10d"] = df["ret_1d"].rolling(10).std()
    df["mom_20d"] = df["close"].pct_change(20)
    df["sma_10"] = df["close"].rolling(10).mean()
    df["price_sma10_z"] = (df["close"] - df["sma_10"]) / df["close"].rolling(60).std()
    return df


def build_dataset(cand_df: pd.DataFrame, symbols: List[str], lookback: int, return_days: int) -> pd.DataFrame:
    # download price history
    adj = download_price_history(symbols, lookback + return_days + 5)
    rows = []
    for sym in symbols:
        if sym not in adj.columns:
            continue
        series = adj[sym].dropna()
        if len(series) < 30:
            continue
        tech = make_technical_features(series)
        for t_idx in range(30, len(tech) - return_days):
            date = tech.index[t_idx]
            feat_row = tech.iloc[t_idx].to_dict()
            # label: forward return over return_days
            future_price = series.iloc[t_idx + return_days]
            price = series.iloc[t_idx]
            fwd_ret = (future_price / price) - 1.0
            # attach static screener metrics if available (from candidate dataframe)
            meta = {}
            row_meta = cand_df[cand_df["symbol"].astype(str) == sym]
            if not row_meta.empty:
                # take first row
                for col in ["pct_1w", "pct_1m", "rel_volume", "revenueGrowth", "earningsQuarterlyGrowth", "pegRatio", "trailingPE"]:
                    if col in row_meta.columns:
                        meta[col] = row_meta.iloc[0].get(col)
            rec = {"date": date, "symbol": sym, "fwd_ret": fwd_ret}
            rec.update({k: (v if v is not None else np.nan) for k, v in feat_row.items()})
            rec.update(meta)
            rows.append(rec)
    df = pd.DataFrame(rows)
    # drop rows with NaN target
    df = df.dropna(subset=["fwd_ret"]) if not df.empty else df
    return df


def prepare_features(df: pd.DataFrame) -> (pd.DataFrame, List[str]):
    drop_cols = ["date", "symbol", "fwd_ret"]
    feat_cols = [c for c in df.columns if c not in drop_cols]
    X = df[feat_cols].fillna(0.0)
    return X, feat_cols


def train_and_evaluate(df: pd.DataFrame, return_days: int):
    df = df.sort_values("date")
    # split by date: last 30 days as test
    unique_dates = sorted(df["date"].unique())
    if len(unique_dates) < 60:
        split_date = unique_dates[int(len(unique_dates) * 0.7)]
    else:
        split_date = unique_dates[-30]
    train = df[df["date"] <= split_date]
    test = df[df["date"] > split_date]
    X_train, feat_cols = prepare_features(train)
    y_train = train["fwd_ret"].values
    X_test, _ = prepare_features(test)
    y_test = test["fwd_ret"].values

    if LGB_INSTALLED:
        model = lgb.LGBMRegressor(n_estimators=200, learning_rate=0.05)
    else:
        model = RandomForestRegressor(n_estimators=100, random_state=42)

    model.fit(X_train, y_train)
    pred = model.predict(X_test)

    ic, _ = spearmanr(pred, y_test)
    r2 = r2_score(y_test, pred)
    mae = mean_absolute_error(y_test, pred)

    print(f"Eval (return_days={return_days}): Spearman IC={ic:.4f}, R2={r2:.4f}, MAE={mae:.6f}")

    return model, feat_cols


def score_latest(model, feat_cols: List[str], cand_df: pd.DataFrame, symbols: List[str], return_days: int):
    # prepare features for the most recent date available in price history
    adj = download_price_history(symbols, 200)
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
        row_meta = cand_df[cand_df["symbol"].astype(str) == sym]
        if not row_meta.empty:
            for col in ["pct_1w", "pct_1m", "rel_volume", "revenueGrowth", "earningsQuarterlyGrowth", "pegRatio", "trailingPE"]:
                if col in row_meta.columns:
                    meta[col] = row_meta.iloc[0].get(col)
        rec = {"symbol": sym, "date": latest_date}
        rec.update({k: (v if v is not None else np.nan) for k, v in feat_row.items()})
        rec.update(meta)
        rows.append(rec)
    df = pd.DataFrame(rows)
    X = df[feat_cols].fillna(0.0)
    preds = model.predict(X)
    df["pred_ret"] = preds
    df = df.sort_values("pred_ret", ascending=False)
    out = Path("reports/screener_results")
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d")
    out_file = out / f"predictions_{ts}.csv"
    df.to_csv(out_file, index=False)
    print(f"Wrote predictions to {out_file}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--return-days", type=int, default=5)
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--lookback", type=int, default=180, help="lookback days for price history per symbol")
    p.add_argument("--candidates-file", default=None)
    args = p.parse_args()

    repo = Path(__file__).resolve().parents[1]
    reports = repo / "reports" / "screener_results"

    if args.candidates_file:
        cand_file = Path(args.candidates_file)
    else:
        cand_file = find_latest_screener_file(reports)

    print(f"Using candidates file: {cand_file}")
    cand_df = pd.read_csv(cand_file)
    symbols = list(cand_df["symbol"].astype(str).unique())[: args.limit]

    print(f"Building dataset for {len(symbols)} symbols (lookback={args.lookback})...")
    df = build_dataset(cand_df, symbols, args.lookback, args.return_days)
    if df.empty:
        raise SystemExit("No training data constructed; increase lookback or limit")

    print(f"Constructed dataset with {len(df)} rows")
    model, feat_cols = train_and_evaluate(df, args.return_days)
    score_latest(model, feat_cols, cand_df, symbols, args.return_days)


if __name__ == "__main__":
    main()
