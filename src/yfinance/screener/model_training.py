"""Model construction, tuning, and train/evaluate loop for the short-term predictor."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import lightgbm as lgb  # type: ignore

    LGB_INSTALLED = True
except Exception:
    LGB_INSTALLED = False

from scipy.stats import spearmanr
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import RandomizedSearchCV


def make_time_split(df: pd.DataFrame, return_days: int = 5, train_fraction: float = 0.7, embargo_days: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Create a chronological train/test split with a purge buffer for multi-day horizons.

    The purge/embargo prevents overlapping forward-return windows across the split,
    which is important when the target uses multi-day future returns.
    """
    if df.empty or "date" not in df.columns:
        return df.copy(), df.copy()

    df = df.sort_values("date").copy()
    if embargo_days is None:
        embargo_days = max(0, int(return_days))

    unique_dates = pd.DatetimeIndex(pd.Index(df["date"]).drop_duplicates().sort_values())
    if len(unique_dates) < 3:
        return df.iloc[:1].copy(), df.iloc[1:].copy()

    split_idx = max(1, min(len(unique_dates) - 1, int(len(unique_dates) * train_fraction)))
    train_end = unique_dates[split_idx - 1]
    purge_end = train_end + pd.Timedelta(days=embargo_days)

    train = df[df["date"] <= train_end].copy()
    test = df[df["date"] > purge_end].copy()

    if test.empty:
        test_start = unique_dates[split_idx]
        test = df[df["date"] >= test_start].copy()

    return train, test

from .charts import save_actual_vs_predicted_chart
from .enhanced_features import enhance_features_test, enhance_features_train
from .feature_engineering import compute_excess_return_target, prepare_features


def build_model(model_config: dict | None = None):
    """Create the base estimator from config.

    Returns:
        Tuple of (model, model_type, needs_standardization).
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

    # Linear models need standardization; imported lazily so lightgbm/random-forest-only
    # deployments (e.g. AWS Lambda layers) don't pay the import cost for models they never use.
    from sklearn.linear_model import ElasticNet, Lasso, Ridge  # noqa: PLC0415

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


def compute_feature_importance(model, feat_cols: list[str]) -> pd.DataFrame:
    """Return a sorted feature-importance table for the fitted model."""
    if hasattr(model, "feature_importances_"):
        importances = model.feature_importances_
    elif hasattr(model, "booster_") and hasattr(model.booster_, "feature_importance"):
        importances = model.booster_.feature_importance(importance_type="gain")
    else:
        return pd.DataFrame(columns=["feature", "importance"])

    return pd.DataFrame({"feature": feat_cols, "importance": importances}).sort_values("importance", ascending=False)


def _safe_rank_ic(pred, actual):
    pred = pd.Series(pred)
    actual = pd.Series(actual)
    if len(pred) < 2 or len(actual) < 2:
        return np.nan
    if pred.nunique() < 2 or actual.nunique() < 2:
        return np.nan
    ic, _ = spearmanr(pred, actual)
    return float(ic) if np.isfinite(ic) else np.nan


def _daily_ic_by_date(test_df: pd.DataFrame) -> tuple[list[float], pd.DataFrame]:
    daily_rows = []
    for date, group in test_df.groupby("date"):
        if group["actual_ret"].nunique() < 2 or group["pred_ret"].nunique() < 2:
            continue
        ic, _ = spearmanr(group["pred_ret"], group["actual_ret"])
        if np.isfinite(ic):
            daily_rows.append({"date": date, "daily_ic": float(ic), "bull_regime": bool(group["actual_ret"].median() > 0)})
    daily_df = pd.DataFrame(daily_rows)
    return daily_df["daily_ic"].tolist() if not daily_df.empty else [], daily_df


def train_and_evaluate(
    df: pd.DataFrame,
    return_days: int,
    use_enhanced_features: bool = True,
    model_config: dict | None = None,
    train_fraction: float = 0.7,
    embargo_days: int | None = None,
):
    """Split, engineer features for, train, and evaluate a short-term return model.

    Returns:
        Tuple of (model, feat_cols, metrics, feature_importance_df, scaler).
    """
    print(f"Building dataset with {len(df)} rows...")

    df = compute_excess_return_target(df.sort_values("date"))
    if embargo_days is None:
        embargo_days = max(0, int(return_days))
    train, test = make_time_split(df, return_days=return_days, train_fraction=train_fraction, embargo_days=embargo_days)
    train = train.dropna(subset=["fwd_ret_target"]).copy()
    test = test.dropna(subset=["fwd_ret_target"]).copy()

    print(f"Train: {len(train)} rows, Test: {len(test)} rows")

    # Apply enhanced feature engineering separately to avoid leakage:
    # train set computes statistics and applies transformations, test set
    # applies transformations using train statistics only.
    if use_enhanced_features:
        train, train_stats = enhance_features_train(train, add_sector_features=("sector" in df.columns))
        test = enhance_features_test(test, train_stats, add_sector_features=("sector" in df.columns))
        print(f"After feature engineering - Train: {len(train)} rows, Test: {len(test)} rows")

    model, model_type, needs_standardization = build_model(model_config)

    # CRITICAL: Fit scaler on training data only to prevent data leakage.
    # Only standardize for linear models (Ridge, Lasso, ElasticNet).
    X_train, feat_cols, scaler = prepare_features(
        train, fit_scaler=needs_standardization, use_standardization=needs_standardization
    )
    y_train = train["fwd_ret_target"].values

    # Transform test data using the fitted scaler (no refitting)
    X_test, _, _ = prepare_features(test, scaler=scaler, fit_scaler=False, use_standardization=needs_standardization)
    y_test = test["fwd_ret_target"].values

    standardization_msg = " (standardized)" if needs_standardization else " (no standardization - tree model)"
    print(f"Training {model_type} with {len(feat_cols)} features{standardization_msg} using excess-return target...")
    model = tune_model(model, X_train, y_train, model_config)

    model.fit(X_train, y_train)
    pred = model.predict(X_test)

    ic, _ = spearmanr(pred, y_test) if len(np.unique(pred)) > 1 else (np.nan, np.nan)
    r2 = r2_score(y_test, pred)
    mae = mean_absolute_error(y_test, pred)

    test_df = pd.DataFrame({"date": test["date"].to_numpy(), "actual_ret": y_test, "pred_ret": pred})
    if "screener_rank" in test.columns:
        test_df["screener_rank"] = test["screener_rank"].to_numpy()
    if "mom_20d" in test.columns:
        test_df["mom_20d"] = test["mom_20d"].to_numpy()
    if "sector" in test.columns:
        test_df["sector"] = test["sector"].to_numpy()

    daily_ic, daily_df = _daily_ic_by_date(test_df)
    daily_spreads = []
    for date, group in test_df.groupby("date"):
        if group["actual_ret"].nunique() < 2 or group["pred_ret"].nunique() < 2:
            continue
        top_cutoff = group["pred_ret"].quantile(0.9)
        bottom_cutoff = group["pred_ret"].quantile(0.1)
        top_group = group[group["pred_ret"] >= top_cutoff]
        bottom_group = group[group["pred_ret"] <= bottom_cutoff]
        if not top_group.empty and not bottom_group.empty:
            daily_spreads.append(float(top_group["actual_ret"].mean() - bottom_group["actual_ret"].mean()))

    mean_daily_ic = float(np.mean(daily_ic)) if daily_ic else np.nan
    ic_std = float(np.std(daily_ic, ddof=0)) if len(daily_ic) > 1 else 0.0
    mean_top_bottom_spread = float(np.mean(daily_spreads)) if daily_spreads else np.nan

    constant_pred = np.full_like(y_test, float(np.mean(y_train)), dtype=float)
    constant_ic = _safe_rank_ic(constant_pred, y_test)
    screener_rank_ic = _safe_rank_ic(test_df.get("screener_rank", pd.Series(np.nan, index=test_df.index)).rank(pct=True), y_test) if "screener_rank" in test_df.columns else np.nan
    momentum_ic = _safe_rank_ic(test_df.get("mom_20d", pd.Series(np.nan, index=test_df.index)), y_test) if "mom_20d" in test_df.columns else np.nan

    linear_model = LinearRegression()
    linear_model.fit(X_train, y_train)
    linear_pred = linear_model.predict(X_test)
    linear_ic = _safe_rank_ic(linear_pred, y_test)

    if not daily_df.empty:
        rolling_series = daily_df["daily_ic"].rolling(window=min(5, len(daily_df)), min_periods=1).mean()
        rolling_5d_mean_daily_ic = float(rolling_series.iloc[-1])
        bull = daily_df[daily_df["bull_regime"]]
        bear = daily_df[~daily_df["bull_regime"]]
    else:
        rolling_5d_mean_daily_ic = np.nan
        bull = pd.DataFrame()
        bear = pd.DataFrame()

    if "sector" in test_df.columns:
        sector_ic = {}
        for sector, group in test_df.groupby("sector"):
            if group["actual_ret"].nunique() < 2 or group["pred_ret"].nunique() < 2:
                continue
            sector_ic[str(sector)] = float(spearmanr(group["pred_ret"], group["actual_ret"])[0])
    else:
        sector_ic = {}

    print(f"Eval (return_days={return_days}): Spearman IC={ic:.4f}, mean_daily_IC={mean_daily_ic:.4f}, top_decile_minus_bottom_decile={mean_top_bottom_spread:.4f}, R2={r2:.4f}, MAE={mae:.6f}")
    print("Target is cross-sectional excess return vs sector/date median")
    print(f"Number of features used: {len(feat_cols)}")

    eval_df = pd.DataFrame({"actual_ret": y_test, "pred_ret": pred, "target_name": "fwd_ret_target", "target_semantics": "cross-sectional rank target percentile within date"})
    repo_root = Path(__file__).resolve().parents[3]
    # Use /tmp for Lambda environments where reports/ doesn't exist
    reports_dir = repo_root / "reports" / "screener_results"
    if not reports_dir.exists() and Path("/tmp").exists():
        chart_path = Path("/tmp") / f"actual_vs_predicted_{datetime.now(UTC).strftime('%Y%m%d')}.png"
    else:
        chart_path = reports_dir / f"actual_vs_predicted_{datetime.now(UTC).strftime('%Y%m%d')}.png"
    if save_actual_vs_predicted_chart(eval_df, chart_path):
        print(f"Wrote actual-vs-predicted chart to {chart_path}")
    else:
        print("matplotlib not installed - skipped actual-vs-predicted chart")

    fi = compute_feature_importance(model, feat_cols)
    if not fi.empty:
        print("Top feature importances:")
        print(fi.head(10).to_string(index=False))

    metrics = {
        "model_type": model_type,
        "target_name": "fwd_ret_target",
        "target_semantics": "cross-sectional rank target percentile within each date",
        "r2_score": r2,
        "spearman_ic": ic,
        "mean_daily_ic": mean_daily_ic,
        "ic_std": ic_std,
        "rolling_5d_mean_daily_ic": rolling_5d_mean_daily_ic,
        "top_decile_minus_bottom_decile_spread": mean_top_bottom_spread,
        "baseline_constant_predictor_ic": constant_ic,
        "baseline_screener_rank_ic": screener_rank_ic,
        "baseline_momentum_ic": momentum_ic,
        "baseline_linear_model_ic": linear_ic,
        "bull_regime_mean_daily_ic": float(np.mean(bull["daily_ic"])) if not bull.empty else np.nan,
        "bear_regime_mean_daily_ic": float(np.mean(bear["daily_ic"])) if not bear.empty else np.nan,
        "sector_mean_daily_ic": sector_ic,
        "mae": mae,
        "return_days": return_days,
        "n_features": len(feat_cols),
        "n_test_dates": len(test_df["date"].unique()),
    }

    return model, feat_cols, metrics, fi, scaler
