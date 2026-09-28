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
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import RandomizedSearchCV

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


def train_and_evaluate(
    df: pd.DataFrame, return_days: int, use_enhanced_features: bool = True, model_config: dict | None = None
):
    """Split, engineer features for, train, and evaluate a short-term return model.

    Returns:
        Tuple of (model, feat_cols, metrics, feature_importance_df, scaler).
    """
    print(f"Building dataset with {len(df)} rows...")

    # Convert the target into a cross-sectional excess-return label for better alpha learning
    df = compute_excess_return_target(df.sort_values("date"))

    # CRITICAL FIX: Split train/test BEFORE feature engineering to prevent data leakage
    df = df.sort_values("date")
    unique_dates = sorted(df["date"].unique())
    split_date = unique_dates[int(len(unique_dates) * 0.7)] if len(unique_dates) < 60 else unique_dates[-30]

    train = df[df["date"] <= split_date].copy()
    test = df[df["date"] > split_date].copy()
    train = train.dropna(subset=["fwd_ret_target"])
    test = test.dropna(subset=["fwd_ret_target"])

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

    ic, _ = spearmanr(pred, y_test)
    r2 = r2_score(y_test, pred)
    mae = mean_absolute_error(y_test, pred)

    print(f"Eval (return_days={return_days}): Spearman IC={ic:.4f}, R2={r2:.4f}, MAE={mae:.6f}")
    print("Target is cross-sectional excess return vs sector/date median")
    print(f"Number of features used: {len(feat_cols)}")

    eval_df = pd.DataFrame({"actual_ret": y_test, "pred_ret": pred})
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
        "r2_score": r2,
        "spearman_ic": ic,
        "mae": mae,
        "return_days": return_days,
        "n_features": len(feat_cols),
    }

    return model, feat_cols, metrics, fi, scaler
