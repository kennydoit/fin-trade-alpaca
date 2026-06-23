"""Short-term return prediction pipeline moved into the screener package.

This keeps the prediction runner on the package-side implementation instead of
relying on sandbox copies.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
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
from sklearn.preprocessing import StandardScaler
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
                for col in ["pct_1w", "pct_1m", "rel_volume", "revenueGrowth", "earningsQuarterlyGrowth", "pegRatio", "trailingPE", "sector", "industry", "screener_rank"]:
                    if col in row_meta.columns:
                        meta[col] = row_meta.iloc[0].get(col)
            rec = {"date": date, "symbol": sym, "fwd_ret": fwd_ret}
            rec.update({k: (v if v is not None else np.nan) for k, v in feat_row.items()})
            rec.update(meta)
            rows.append(rec)
    df = pd.DataFrame(rows)
    return df.dropna(subset=["fwd_ret"]) if not df.empty else df


def prepare_features(df: pd.DataFrame, scaler=None, fit_scaler: bool = False, use_standardization: bool = False) -> tuple[pd.DataFrame, List[str], StandardScaler | None]:
    """Prepare features with optional standardization.
    
    Args:
        df: Input dataframe with features
        scaler: Fitted StandardScaler to use for transformation. If None and fit_scaler=True, creates new scaler.
        fit_scaler: If True, fits a new scaler on the data. Only set True for training data.
        use_standardization: Whether to apply standardization (only needed for linear models, not trees)
    
    Returns:
        Tuple of (transformed features, feature column names, fitted scaler or None)
    """
    drop_cols = ["date", "symbol", "fwd_ret", "sector", "industry", "screener_rank"]
    feat_cols = [c for c in df.columns if c not in drop_cols]
    # Note: fillna handled by enhance_features, but add safety check
    X = df[feat_cols].fillna(0.0)
    
    # Apply standardization only if requested (for linear models)
    if use_standardization:
        if fit_scaler:
            scaler = StandardScaler()
            X_scaled = scaler.fit_transform(X)
            X = pd.DataFrame(X_scaled, columns=feat_cols, index=X.index)
            return X, feat_cols, scaler
        elif scaler is not None:
            X_scaled = scaler.transform(X)
            X = pd.DataFrame(X_scaled, columns=feat_cols, index=X.index)
            return X, feat_cols, scaler
    
    return X, feat_cols, None


def build_model(model_config: dict | None = None):
    """Create the base estimator from config.
    
    Returns:
        Tuple of (model, model_type, needs_standardization)
    """
    config = model_config or {}
    model_type = str(config.get("type", "lightgbm" if LGB_INSTALLED else "random_forest")).lower()

    if model_type == "lightgbm" and LGB_INSTALLED:
        model = lgb.LGBMRegressor(
            n_estimators=int(config.get("n_estimators", 300)),
            learning_rate=float(config.get("learning_rate", 0.05)),
            max_depth=int(config.get("max_depth", 6)),
            num_leaves=int(config.get("num_leaves", 31)),
            min_child_samples=int(config.get("min_child_samples", 20)),
            subsample=float(config.get("subsample", 0.8)),
            colsample_bytree=float(config.get("colsample_bytree", 0.8)),
            reg_alpha=float(config.get("reg_alpha", 0.1)),
            reg_lambda=float(config.get("reg_lambda", 0.1)),
            random_state=int(config.get("random_state", 42)),
            n_jobs=int(config.get("n_jobs", -1)),
        )
        return model, "lightgbm", False

    if model_type == "random_forest":
        model = RandomForestRegressor(
            n_estimators=int(config.get("n_estimators", 200)),
            max_depth=int(config.get("max_depth", 10)),
            min_samples_split=int(config.get("min_samples_split", 10)),
            min_samples_leaf=int(config.get("min_samples_leaf", 5)),
            max_features=config.get("max_features", "sqrt"),
            random_state=int(config.get("random_state", 42)),
            n_jobs=int(config.get("n_jobs", -1)),
        )
        return model, "random_forest", False
    
    # Linear models need standardization
    from sklearn.linear_model import Ridge, Lasso, ElasticNet
    
    if model_type == "ridge":
        model = Ridge(
            alpha=float(config.get("alpha", 1.0)),
            random_state=int(config.get("random_state", 42)),
        )
        return model, "ridge", True
    
    if model_type == "lasso":
        model = Lasso(
            alpha=float(config.get("alpha", 0.1)),
            random_state=int(config.get("random_state", 42)),
            max_iter=int(config.get("max_iter", 2000)),
        )
        return model, "lasso", True
    
    if model_type == "elasticnet":
        model = ElasticNet(
            alpha=float(config.get("alpha", 0.1)),
            l1_ratio=float(config.get("l1_ratio", 0.5)),
            random_state=int(config.get("random_state", 42)),
            max_iter=int(config.get("max_iter", 2000)),
        )
        return model, "elasticnet", True
    
    # Default to random forest
    model = RandomForestRegressor(
        n_estimators=200,
        max_depth=10,
        min_samples_split=10,
        min_samples_leaf=5,
        random_state=42,
        n_jobs=-1,
    )
    return model, "random_forest", False


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


def save_actual_vs_predicted_chart(eval_df: pd.DataFrame, out_path: Path) -> Path:
    """Save an actual-vs-predicted return scatter plot to disk."""
    plot_df = pd.DataFrame(
        {
            "actual_ret": pd.to_numeric(eval_df.get("actual_ret", pd.Series(dtype=float)), errors="coerce"),
            "pred_ret": pd.to_numeric(eval_df.get("pred_ret", pd.Series(dtype=float)), errors="coerce"),
        }
    ).dropna()

    if plot_df.empty:
        raise ValueError("No actual/predicted values available to plot")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(8, 6))
    ax.scatter(plot_df["actual_ret"], plot_df["pred_ret"], alpha=0.75)

    min_val = min(plot_df["actual_ret"].min(), plot_df["pred_ret"].min())
    max_val = max(plot_df["actual_ret"].max(), plot_df["pred_ret"].max())
    span = max(max_val - min_val, 1e-6)
    ax.plot([min_val - 0.05 * span, max_val + 0.05 * span], [min_val - 0.05 * span, max_val + 0.05 * span], "r--", linewidth=1, label="Ideal fit")

    ax.set_xlabel("Actual forward return")
    ax.set_ylabel("Predicted forward return")
    ax.set_title("Actual vs Predicted Returns")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)

    return out_path


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
    
    # Build model and check if it needs standardization
    model, model_type, needs_standardization = build_model(model_config)
    
    # CRITICAL: Fit scaler on training data only to prevent data leakage
    # Only standardize for linear models (Ridge, Lasso, ElasticNet)
    X_train, feat_cols, scaler = prepare_features(train, fit_scaler=needs_standardization, use_standardization=needs_standardization)
    y_train = train["fwd_ret"].values
    
    # Transform test data using fitted scaler (no refitting)
    X_test, _, _ = prepare_features(test, scaler=scaler, fit_scaler=False, use_standardization=needs_standardization)
    y_test = test["fwd_ret"].values
    
    standardization_msg = " (standardized)" if needs_standardization else " (no standardization - tree model)"
    print(f"Training {model_type} with {len(feat_cols)} features{standardization_msg}...")
    model = tune_model(model, X_train, y_train, model_config)

    model.fit(X_train, y_train)
    pred = model.predict(X_test)

    ic, _ = spearmanr(pred, y_test)
    r2 = r2_score(y_test, pred)
    mae = mean_absolute_error(y_test, pred)

    print(f"Eval (return_days={return_days}): Spearman IC={ic:.4f}, R2={r2:.4f}, MAE={mae:.6f}")
    print(f"Number of features used: {len(feat_cols)}")

    eval_df = pd.DataFrame({"actual_ret": y_test, "pred_ret": pred})
    repo_root = Path(__file__).resolve().parents[3]
    chart_path = repo_root / "reports" / "screener_results" / f"actual_vs_predicted_{datetime.now(timezone.utc).strftime('%Y%m%d')}.png"
    save_actual_vs_predicted_chart(eval_df, chart_path)
    print(f"Wrote actual-vs-predicted chart to {chart_path}")

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

    return model, feat_cols, metrics, fi, scaler


def score_latest(model, feat_cols: List[str], cand_df: pd.DataFrame, symbols: List[str], return_days: int, scaler=None, metrics: dict = None, use_enhanced_features: bool = True):
    """
    Score latest prices for given symbols.
    
    Args:
        model: Trained model for predictions
        feat_cols: List of feature column names
        cand_df: Candidate dataframe with symbol metadata
        symbols: List of symbols to score
        return_days: Forward return horizon
        scaler: Fitted StandardScaler from training (CRITICAL: must be from training data only)
        metrics: Optional model performance metrics
        use_enhanced_features: Whether to apply enhanced feature engineering
    
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
    
    # Prepare features with same scaler used in training (NO fitting here)
    # use_standardization determined by whether scaler is provided
    use_std = scaler is not None
    X, _, _ = prepare_features(df, scaler=scaler, fit_scaler=False, use_standardization=use_std)
    preds = model.predict(X)
    df["pred_ret"] = preds
    df = df.sort_values("pred_ret", ascending=False)
    
    # Add strategy attribution metadata
    df["prediction_rank"] = range(1, len(df) + 1)
    df["strategy_source"] = "predictive_model"
    if metrics:
        df["model_type"] = metrics.get("model_type", "unknown")
        df["model_r2_score"] = metrics.get("r2_score")
        df["model_spearman_ic"] = metrics.get("spearman_ic")
        df["model_mae"] = metrics.get("mae")
    
    # Reorder columns to put prediction_rank and screener_rank at the beginning
    cols = df.columns.tolist()
    rank_cols = []
    if "prediction_rank" in cols:
        rank_cols.append("prediction_rank")
        cols.remove("prediction_rank")
    if "screener_rank" in cols:
        rank_cols.append("screener_rank")
        cols.remove("screener_rank")
    if rank_cols:
        cols = rank_cols + cols
        df = df[cols]
    
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
