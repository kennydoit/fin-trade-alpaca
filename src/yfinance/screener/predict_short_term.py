"""Short-term return prediction pipeline moved into the screener package.

This keeps the prediction runner on the package-side implementation instead of
relying on sandbox copies.
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
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import RandomizedSearchCV, train_test_split
from scipy.stats import spearmanr

import yfinance as yf

from .enhanced_features import enhance_features


def find_latest_screener_file(folder: Path) -> Path:
    files = sorted(folder.glob("yfinance_screener_results_with_metrics*.csv"))
    if not files:
        raise SystemExit(f"No screener CSVs found in {folder}")
    return files[-1]


def download_price_history(symbols: List[str], lookback_days: int) -> pd.DataFrame:
    period = f"{lookback_days}d"
    if hasattr(yf, "download"):
        df = yf.download(symbols, period=period, interval="1d", progress=False, threads=True)
        if isinstance(df, tuple):
            df = df[0]
        if "Adj Close" in df:
            adj = df["Adj Close"].copy()
        else:
            adj = df["Close"].copy()
        if isinstance(adj, pd.Series):
            adj = adj.to_frame()
        adj.columns = [c if isinstance(c, str) else c[1] for c in adj.columns]
        return adj

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
    return pd.concat(cols.values(), axis=1)


def make_technical_features(adj: pd.Series) -> pd.DataFrame:
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
            future_price = series.iloc[t_idx + return_days]
            price = series.iloc[t_idx]
            fwd_ret = (future_price / price) - 1.0
            meta = {}
            row_meta = cand_df[cand_df["symbol"].astype(str) == sym]
            if not row_meta.empty:
                for col in ["pct_1w", "pct_1m", "rel_volume", "revenueGrowth", "earningsQuarterlyGrowth", "pegRatio", "trailingPE", "sector", "industry"]:
                    if col in row_meta.columns:
                        meta[col] = row_meta.iloc[0].get(col)
            rec = {"date": date, "symbol": sym, "fwd_ret": fwd_ret}
            rec.update({k: (v if v is not None else np.nan) for k, v in feat_row.items()})
            rec.update(meta)
            rows.append(rec)
    df = pd.DataFrame(rows)
    return df.dropna(subset=["fwd_ret"]) if not df.empty else df


def prepare_features(df: pd.DataFrame) -> tuple[pd.DataFrame, List[str]]:
    drop_cols = ["date", "symbol", "fwd_ret", "sector", "industry"]
    feat_cols = [c for c in df.columns if c not in drop_cols]
    # Note: fillna handled by enhance_features, but add safety check
    X = df[feat_cols].fillna(0.0)
    return X, feat_cols


def build_model(model_config: dict | None = None):
    """Create the base estimator from config, optionally using LightGBM if available."""
    config = model_config or {}
    model_type = str(config.get("type", "lightgbm" if LGB_INSTALLED else "random_forest")).lower()

    if model_type == "lightgbm" and LGB_INSTALLED:
        model = lgb.LGBMRegressor(
            n_estimators=int(config.get("n_estimators", 200)),
            learning_rate=float(config.get("learning_rate", 0.05)),
            random_state=int(config.get("random_state", 42)),
            n_jobs=int(config.get("n_jobs", -1)),
        )
        return model, "lightgbm"

    model = RandomForestRegressor(
        n_estimators=int(config.get("n_estimators", 100)),
        random_state=int(config.get("random_state", 42)),
        n_jobs=int(config.get("n_jobs", -1)),
    )
    return model, "random_forest"


def tune_model(model, X_train: pd.DataFrame, y_train: np.ndarray, model_config: dict | None = None):
    """Simple hyperparameter tuning for LightGBM / RandomForest when enabled in config."""
    config = model_config or {}
    if not bool(config.get("tune", False)):
        return model

    if isinstance(model, lgb.LGBMRegressor) if LGB_INSTALLED else False:
        param_distributions = {
            "n_estimators": [100, 200, 300],
            "learning_rate": [0.03, 0.05, 0.1],
        }
    else:
        param_distributions = {
            "n_estimators": [100, 200, 300],
        }

    search = RandomizedSearchCV(
        estimator=model,
        param_distributions=param_distributions,
        n_iter=int(config.get("tune_n_iter", 6)),
        cv=int(config.get("tune_cv", 3)),
        scoring="neg_mean_squared_error",
        random_state=int(config.get("random_state", 42)),
        n_jobs=int(config.get("n_jobs", -1)),
    )
    search.fit(X_train, y_train)
    print("Best tuning params:", search.best_params_)
    return search.best_estimator_


def compute_feature_importance(model, feat_cols: List[str]) -> pd.DataFrame:
    """Return a sorted feature-importance table for the fitted model."""
    if hasattr(model, "feature_importances_"):
        importances = model.feature_importances_
    elif hasattr(model, "booster_") and hasattr(model.booster_, "feature_importance"):
        importances = model.booster_.feature_importance(importance_type="gain")
    else:
        return pd.DataFrame(columns=["feature", "importance"])

    fi = pd.DataFrame({"feature": feat_cols, "importance": importances}).sort_values("importance", ascending=False)
    return fi


def train_and_evaluate(df: pd.DataFrame, return_days: int, use_enhanced_features: bool = True, model_config: dict | None = None):
    print(f"Building dataset with {len(df)} rows...")
    
    # Apply enhanced feature engineering
    if use_enhanced_features:
        df = enhance_features(df, add_sector_features=("sector" in df.columns))
    
    df = df.sort_values("date")
    unique_dates = sorted(df["date"].unique())
    if len(unique_dates) < 60:
        split_date = unique_dates[int(len(unique_dates) * 0.7)]
    else:
        split_date = unique_dates[-30]
    train = df[df["date"] <= split_date]
    test = df[df["date"] > split_date]
    
    print(f"Train: {len(train)} rows, Test: {len(test)} rows")
    
    X_train, feat_cols = prepare_features(train)
    y_train = train["fwd_ret"].values
    X_test, _ = prepare_features(test)
    y_test = test["fwd_ret"].values
    
    print(f"Training with {len(feat_cols)} features...")

    model, model_type = build_model(model_config)
    model = tune_model(model, X_train, y_train, model_config)

    model.fit(X_train, y_train)
    pred = model.predict(X_test)

    ic, _ = spearmanr(pred, y_test)
    r2 = r2_score(y_test, pred)
    mae = mean_absolute_error(y_test, pred)

    print(f"Eval (return_days={return_days}): Spearman IC={ic:.4f}, R2={r2:.4f}, MAE={mae:.6f}")
    print(f"Number of features used: {len(feat_cols)}")

    fi = compute_feature_importance(model, feat_cols)
    if not fi.empty:
        print("Top feature importances:")
        print(fi.head(10).to_string(index=False))
    metrics = {
        "model_type": model_type,
        "r2_score": r2,
        "spearman_ic": ic,
        "mae": mae,
        "return_days": return_days,
        "n_features": len(feat_cols)
    }

    return model, feat_cols, metrics, fi


def score_latest(model, feat_cols: List[str], cand_df: pd.DataFrame, symbols: List[str], return_days: int, metrics: dict = None, use_enhanced_features: bool = True):
    """
    Score latest prices for given symbols.
    
    To properly compute enhanced features (lags, ranks, sector-relative), we need historical context.
    Build a mini-dataset with last 30 days, then extract latest predictions.
    """
    # Download more history to compute lagged and cross-sectional features
    adj = download_price_history(symbols, 90)
    
    if adj.empty or len(adj) < 30:
        print("Insufficient historical data for enhanced features")
        return pd.DataFrame()
    
    latest_date = adj.index[-1]
    rows = []
    
    # Build dataset with historical context (last 30 trading days)
    for sym in symbols:
        if sym not in adj.columns:
            continue
        series = adj[sym].dropna()
        if len(series) < 30:
            continue
        tech = make_technical_features(series)
        
        # Take last 30 days to compute features
        for t_idx in range(max(30, len(tech) - 30), len(tech)):
            date = tech.index[t_idx]
            feat_row = tech.iloc[t_idx].to_dict()
            meta = {}
            row_meta = cand_df[cand_df["symbol"].astype(str) == sym]
            if not row_meta.empty:
                for col in ["pct_1w", "pct_1m", "rel_volume", "revenueGrowth", "earningsQuarterlyGrowth", "pegRatio", "trailingPE", "sector", "industry"]:
                    if col in row_meta.columns:
                        meta[col] = row_meta.iloc[0].get(col)
            rec = {"symbol": sym, "date": date, "fwd_ret": np.nan}  # fwd_ret dummy for enhance_features
            rec.update({k: (v if v is not None else np.nan) for k, v in feat_row.items()})
            rec.update(meta)
            rows.append(rec)
    
    df = pd.DataFrame(rows)
    
    if df.empty:
        return df
    
    # Apply enhanced features (needs historical context)
    if use_enhanced_features:
        df = enhance_features(df, add_sector_features=("sector" in df.columns))
    
    # Filter to only latest date
    df = df[df["date"] == latest_date].copy()
    
    # Prepare features and predict
    X = df[feat_cols].fillna(0.0)
    preds = model.predict(X)
    df["pred_ret"] = preds
    df = df.sort_values("pred_ret", ascending=False)
    
    # Add strategy attribution metadata
    df["screener_rank"] = range(1, len(df) + 1)
    df["strategy_source"] = "predictive_model"
    if metrics:
        df["model_type"] = metrics.get("model_type", "unknown")
        df["model_r2_score"] = metrics.get("r2_score")
        df["model_spearman_ic"] = metrics.get("spearman_ic")
        df["model_mae"] = metrics.get("mae")
    
    return df


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--return-days", type=int, default=5)
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--lookback", type=int, default=180, help="lookback days for price history per symbol")
    p.add_argument("--candidates-file", default=None)
    args = p.parse_args()

    repo = Path(__file__).resolve().parents[2]
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
    model, feat_cols, metrics = train_and_evaluate(df, args.return_days)
    score_latest(model, feat_cols, cand_df, symbols, args.return_days, metrics)


if __name__ == "__main__":
    main()
