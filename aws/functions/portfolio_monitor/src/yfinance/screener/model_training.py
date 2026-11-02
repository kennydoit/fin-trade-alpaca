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
from sklearn.base import clone
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import average_precision_score, mean_absolute_error, r2_score, roc_auc_score
from sklearn.model_selection import ParameterGrid, TimeSeriesSplit

from .charts import save_actual_vs_predicted_chart
from .enhanced_features import enhance_features_test, enhance_features_train
from .feature_engineering import compute_breakout_target, compute_excess_return_target, prepare_features


def make_time_split(
    df: pd.DataFrame, return_days: int = 5, train_fraction: float = 0.7, embargo_days: int | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
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
    """Tune hyperparameters with a time-aware validation split.

    The screener uses a chronological target, so the validation procedure must
    respect time order. We avoid plain RandomizedSearchCV with a non-temporal
    K-fold split on this data.
    """
    config = model_config or {}
    if not bool(config.get("tune", False)):
        return model

    if len(X_train) < 10:
        return model

    if isinstance(model, lgb.LGBMRegressor) if LGB_INSTALLED else False:
        param_grid = {
            "n_estimators": [100, 200, 300],
            "learning_rate": [0.03, 0.05, 0.1],
        }
    else:
        param_grid = {
            "n_estimators": [100, 200, 300],
            "max_depth": [None, 5, 10],
        }

    n_splits = min(3, max(2, len(X_train) // 10))
    cv = TimeSeriesSplit(n_splits=n_splits)
    best_model = model
    best_score = np.inf
    best_params = None

    for params in ParameterGrid(param_grid):
        fold_errors = []
        for train_idx, valid_idx in cv.split(X_train):
            candidate = clone(model).set_params(**params)
            candidate.fit(X_train.iloc[train_idx], y_train[train_idx])
            pred = candidate.predict(X_train.iloc[valid_idx])
            fold_errors.append(np.mean((pred - y_train[valid_idx]) ** 2))
        mean_error = float(np.mean(fold_errors))
        if mean_error < best_score:
            best_score = mean_error
            best_params = params
            best_model = clone(model).set_params(**params)

    if best_params is not None:
        print("Best tuning params:", best_params)
        best_model.fit(X_train, y_train)
        return best_model

    return model


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
            daily_rows.append(
                {"date": date, "daily_ic": float(ic), "bull_regime": bool(group["actual_ret"].median() > 0)}
            )
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
    for _date, group in test_df.groupby("date"):
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
    screener_rank_ic = (
        _safe_rank_ic(test_df.get("screener_rank", pd.Series(np.nan, index=test_df.index)).rank(pct=True), y_test)
        if "screener_rank" in test_df.columns
        else np.nan
    )
    momentum_ic = (
        _safe_rank_ic(test_df.get("mom_20d", pd.Series(np.nan, index=test_df.index)), y_test)
        if "mom_20d" in test_df.columns
        else np.nan
    )

    linear_model = LinearRegression()
    linear_model.fit(X_train, y_train)
    linear_pred = linear_model.predict(X_test)
    linear_ic = _safe_rank_ic(linear_pred, y_test)

    if not daily_df.empty:
        rolling_series = daily_df["daily_ic"].rolling(window=min(5, len(daily_df)), min_periods=1).mean()
        rolling_5d_mean_daily_ic = float(rolling_series.iloc[-1])
        expanding_mean_daily_ic = float(daily_df["daily_ic"].expanding(min_periods=1).mean().iloc[-1])
        bull = daily_df[daily_df["bull_regime"]]
        bear = daily_df[~daily_df["bull_regime"]]
    else:
        rolling_5d_mean_daily_ic = np.nan
        expanding_mean_daily_ic = np.nan
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

    print(
        f"Eval (return_days={return_days}): Spearman IC={ic:.4f}, mean_daily_IC={mean_daily_ic:.4f}, top_decile_minus_bottom_decile={mean_top_bottom_spread:.4f}, R2={r2:.4f}, MAE={mae:.6f}"
    )
    print("Target is cross-sectional excess return vs sector/date median")
    print(f"Number of features used: {len(feat_cols)}")

    eval_df = pd.DataFrame(
        {
            "actual_ret": y_test,
            "pred_ret": pred,
            "target_name": "fwd_ret_target",
            "target_semantics": "cross-sectional rank target percentile within date",
        }
    )
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
        "daily_ic_std": ic_std,
        "ic_std": ic_std,
        "rolling_5d_mean_daily_ic": rolling_5d_mean_daily_ic,
        "expanding_mean_daily_ic": expanding_mean_daily_ic,
        "top_decile_minus_bottom_decile_spread": mean_top_bottom_spread,
        "baseline_constant_predictor_ic": constant_ic,
        "baseline_screener_rank_ic": screener_rank_ic,
        "baseline_raw_screener_rank_ic": screener_rank_ic,
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


def build_classifier(model_config: dict | None = None):
    """Create the breakout classifier from config.

    Supports ``lightgbm`` and ``random_forest``; any other type (including the
    linear regression types) falls back to a random forest. Breakouts are rare, so
    class weights are balanced.

    Returns:
        Tuple of (model, model_type).
    """
    config = model_config or {}
    model_type = str(config.get("type", "lightgbm" if LGB_INSTALLED else "random_forest")).lower()

    if model_type == "lightgbm" and LGB_INSTALLED:
        model = lgb.LGBMClassifier(
            n_estimators=int(config.get("n_estimators", 300)),
            learning_rate=float(config.get("learning_rate", 0.05)),
            max_depth=int(config.get("max_depth", 6)),
            num_leaves=int(config.get("num_leaves", 31)),
            min_child_samples=int(config.get("min_child_samples", 20)),
            subsample=float(config.get("subsample", 0.8)),
            colsample_bytree=float(config.get("colsample_bytree", 0.8)),
            reg_alpha=float(config.get("reg_alpha", 0.1)),
            reg_lambda=float(config.get("reg_lambda", 0.1)),
            class_weight="balanced",
            random_state=int(config.get("random_state", 42)),
            n_jobs=int(config.get("n_jobs", -1)),
            verbose=-1,
        )
        return model, "lightgbm"

    if model_type != "random_forest":
        print(f"Breakout classifier does not support model type '{model_type}'; using random_forest.")
    model = RandomForestClassifier(
        n_estimators=int(config.get("n_estimators", 200)),
        max_depth=int(config.get("max_depth", 10)),
        min_samples_split=int(config.get("min_samples_split", 10)),
        min_samples_leaf=int(config.get("min_samples_leaf", 5)),
        max_features=config.get("max_features", "sqrt"),
        class_weight="balanced_subsample",
        random_state=int(config.get("random_state", 42)),
        n_jobs=int(config.get("n_jobs", -1)),
    )
    return model, "random_forest"


def _safe_auc(y_true, score) -> float:
    y_true = np.asarray(y_true)
    score = pd.Series(score).astype(float)
    mask = score.notna().to_numpy()
    y_true, score = y_true[mask], score.to_numpy()[mask]
    if len(y_true) < 2 or len(np.unique(y_true)) < 2 or len(np.unique(score)) < 2:
        return np.nan
    return float(roc_auc_score(y_true, score))


def _daily_top_decile_hit_rates(test_df: pd.DataFrame) -> tuple[float, float, pd.Series]:
    """Mean daily hit rate of the top-decile names (by probability) and the matching base rate."""
    hit_rates, base_rates = [], []
    sizes = test_df.groupby("date").size()
    for _date, group in test_df.groupby("date"):
        if len(group) < 2:
            continue
        top_k = max(1, int(np.ceil(0.1 * len(group))))
        top = group.nlargest(top_k, "breakout_prob")
        hit_rates.append(float(top["label"].mean()))
        base_rates.append(float(group["label"].mean()))
    mean_hit = float(np.mean(hit_rates)) if hit_rates else np.nan
    mean_base = float(np.mean(base_rates)) if base_rates else np.nan
    return mean_hit, mean_base, sizes


def train_and_evaluate_breakout(
    df: pd.DataFrame,
    return_days: int,
    model_config: dict | None = None,
    train_fraction: float = 0.7,
    embargo_days: int | None = None,
    breakout_k: float = 1.5,
):
    """Train and evaluate a breakout classifier on a purged chronological split.

    ``df`` must come from ``build_dataset(..., include_breakout=True)``. The model uses
    plain technical + breakout features only (no cross-sectional/imputed enhancements,
    no raw price level) so training and live inference see identical inputs.

    Returns:
        Tuple of (model, feat_cols, metrics, feature_importance_df, scaler=None).
    """
    df = compute_breakout_target(df.sort_values("date"), return_days, breakout_k)
    if embargo_days is None:
        embargo_days = max(0, int(return_days))
    train, test = make_time_split(df, return_days=return_days, train_fraction=train_fraction, embargo_days=embargo_days)
    train = train.drop(columns=["close"], errors="ignore")
    test = test.drop(columns=["close"], errors="ignore")

    y_train = train["breakout_label"].astype(int).to_numpy()
    y_test = test["breakout_label"].astype(int).to_numpy()
    print(f"Train: {len(train)} rows, Test: {len(test)} rows")
    if len(train) == 0 or len(test) == 0:
        raise ValueError("Not enough dates for a purged train/test split; increase lookback or symbol count")
    if len(np.unique(y_train)) < 2:
        raise ValueError(
            "Training window contains a single breakout class; increase lookback/symbols or lower target.breakout_k"
        )

    X_train, feat_cols, _ = prepare_features(train)
    X_test, _, _ = prepare_features(test)
    X_test = X_test.reindex(columns=feat_cols)

    model, model_type = build_classifier(model_config)
    print(f"Training {model_type} breakout classifier with {len(feat_cols)} features (k={breakout_k})...")
    model.fit(X_train, y_train)
    prob = model.predict_proba(X_test)[:, list(model.classes_).index(1)]

    test_df = pd.DataFrame(
        {
            "date": test["date"].to_numpy(),
            "label": y_test,
            "breakout_prob": prob,
            "actual_ret": test["fwd_ret"].to_numpy(),
            "pred_ret": prob,
        }
    )

    base_rate_train = float(np.mean(y_train))
    base_rate_test = float(np.mean(y_test))
    roc_auc = _safe_auc(y_test, prob)
    avg_precision = float(average_precision_score(y_test, prob)) if y_test.sum() > 0 else np.nan

    top_k = max(1, int(np.ceil(0.1 * len(test_df))))
    top_overall = test_df.nlargest(top_k, "breakout_prob")
    precision_top_decile = float(top_overall["label"].mean())
    lift_top_decile = precision_top_decile / base_rate_test if base_rate_test > 0 else np.nan

    mean_daily_hit, mean_daily_base, sizes = _daily_top_decile_hit_rates(test_df)
    daily_ic, _daily_df = _daily_ic_by_date(test_df)
    mean_daily_ic = float(np.mean(daily_ic)) if daily_ic else np.nan
    daily_ic_std = float(np.std(daily_ic, ddof=0)) if len(daily_ic) > 1 else np.nan

    momentum_auc = _safe_auc(y_test, test["mom_20d"]) if "mom_20d" in test.columns else np.nan
    proximity_auc = _safe_auc(y_test, test["dist_high_20d"]) if "dist_high_20d" in test.columns else np.nan

    print(
        f"Eval (breakout, return_days={return_days}): AUC={roc_auc:.4f}, AP={avg_precision:.4f}, "
        f"base_rate_test={base_rate_test:.4f}, top_decile_precision={precision_top_decile:.4f} "
        f"(lift {lift_top_decile:.2f}x), mean_daily_IC_vs_fwd_ret={mean_daily_ic:.4f}"
    )

    fi = compute_feature_importance(model, feat_cols)
    if not fi.empty:
        print("Top feature importances:")
        print(fi.head(10).to_string(index=False))

    metrics = {
        "model_type": model_type,
        "target_name": "breakout_label",
        "target_semantics": (
            f"1 if max close over the next {return_days} days >= {breakout_k} * vol_10d * sqrt({return_days}) "
            "above today's close, else 0"
        ),
        "breakout_k": float(breakout_k),
        "roc_auc": roc_auc,
        "average_precision": avg_precision,
        "base_rate_train": base_rate_train,
        "base_rate_test": base_rate_test,
        "precision_top_decile": precision_top_decile,
        "lift_top_decile": lift_top_decile,
        "mean_daily_top_decile_hit_rate": mean_daily_hit,
        "mean_daily_base_rate": mean_daily_base,
        "mean_daily_ic_vs_fwd_ret": mean_daily_ic,
        "daily_ic_std": daily_ic_std,
        "baseline_base_rate": base_rate_test,
        "baseline_momentum_auc": momentum_auc,
        "baseline_proximity_to_high_auc": proximity_auc,
        "return_days": return_days,
        "n_features": len(feat_cols),
        "n_train": len(train),
        "n_test": len(test),
        "n_test_positives": int(y_test.sum()),
        "n_test_dates": int(sizes.shape[0]),
        "min_symbols_per_test_date": int(sizes.min()) if not sizes.empty else 0,
        "median_symbols_per_test_date": float(sizes.median()) if not sizes.empty else 0.0,
    }

    return model, feat_cols, metrics, fi, None
