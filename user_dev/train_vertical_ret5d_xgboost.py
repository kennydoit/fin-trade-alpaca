from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import r2_score
from xgboost import XGBRegressor


DEFAULT_INPUT = Path("user_dev/prediction_training_data_20261027.csv")
DEFAULT_METRICS_OUTPUT = Path("user_dev/vertical_ret5d_metrics_xgboost.csv")
DEFAULT_HOLDOUT_OUTPUT = Path("user_dev/vertical_ret5d_holdout_fit_xgboost.csv")


def safe_mape(y_true: np.ndarray, y_pred: np.ndarray, eps: float = 1e-8) -> float:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    denom = np.maximum(np.abs(y_true), eps)
    return float(np.mean(np.abs((y_true - y_pred) / denom)))


def safe_smape(y_true: np.ndarray, y_pred: np.ndarray, eps: float = 1e-8) -> float:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    denom = np.maximum(np.abs(y_true) + np.abs(y_pred), eps)
    return float(np.mean(2.0 * np.abs(y_pred - y_true) / denom))


def make_model(random_state: int = 42) -> XGBRegressor:
    return XGBRegressor(
        objective="reg:squarederror",
        n_estimators=500,
        learning_rate=0.03,
        max_depth=4,
        subsample=0.9,
        colsample_bytree=0.9,
        reg_alpha=0.0,
        reg_lambda=1.0,
        min_child_weight=2,
        random_state=random_state,
        n_jobs=-1,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train vertical XGBoost model to predict ret_5d using last-X-dates holdout"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Vertical training dataset CSV")
    parser.add_argument("--holdout-days", type=int, default=5, help="Number of latest dates to keep as holdout")
    parser.add_argument("--metrics-out", type=Path, default=DEFAULT_METRICS_OUTPUT, help="Summary metrics output CSV")
    parser.add_argument(
        "--holdout-fit-out",
        type=Path,
        default=DEFAULT_HOLDOUT_OUTPUT,
        help="Row-level holdout actual vs predicted CSV",
    )
    args = parser.parse_args()

    df = pd.read_csv(args.input)
    required = {"date", "ret_5d"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Input is missing required columns: {sorted(missing)}")

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "ret_5d"]).sort_values("date").reset_index(drop=True)

    unique_dates = np.array(sorted(df["date"].dropna().unique()))
    if len(unique_dates) <= args.holdout_days:
        raise ValueError(
            f"Not enough unique dates ({len(unique_dates)}) for holdout_days={args.holdout_days}."
        )
    holdout_dates = set(unique_dates[-args.holdout_days:])

    train_df = df[~df["date"].isin(holdout_dates)].copy()
    holdout_df = df[df["date"].isin(holdout_dates)].copy()
    if train_df.empty or holdout_df.empty:
        raise ValueError("Train or holdout split is empty")

    # Leakage controls:
    # - Target is ret_5d, so ret_5d itself is not in features.
    # - Exclude all forward-looking target-like columns and identifiers.
    excluded = {
        "date",
        "symbol",
        "ret_5d",
        "fwd_ret",
        "close",
        "sma_10",
        "sector",
        "industry",
    }

    feature_cols = [
        c for c in df.columns if c not in excluded and pd.api.types.is_numeric_dtype(df[c])
    ]
    if not feature_cols:
        raise ValueError("No numeric feature columns available after exclusions")

    # Keep only features with at least one non-null in training.
    feature_cols = [c for c in feature_cols if train_df[c].notna().any()]
    if not feature_cols:
        raise ValueError("No usable feature columns with non-null training values")

    X_train = train_df[feature_cols]
    y_train = train_df["ret_5d"].to_numpy(dtype=float)

    X_holdout = holdout_df[feature_cols]
    y_holdout = holdout_df["ret_5d"].to_numpy(dtype=float)

    model = make_model()
    model.fit(X_train, y_train)

    train_pred = model.predict(X_train)
    holdout_pred = model.predict(X_holdout)

    train_r2 = float(r2_score(y_train, train_pred)) if len(y_train) > 1 else np.nan
    holdout_r2 = float(r2_score(y_holdout, holdout_pred)) if len(y_holdout) > 1 else np.nan

    train_mape = safe_mape(y_train, train_pred)
    holdout_mape = safe_mape(y_holdout, holdout_pred)

    train_smape = safe_smape(y_train, train_pred)
    holdout_smape = safe_smape(y_holdout, holdout_pred)

    metrics_df = pd.DataFrame(
        [
            {
                "target": "ret_5d",
                "holdout_days": args.holdout_days,
                "n_train": len(train_df),
                "n_holdout": len(holdout_df),
                "n_features": len(feature_cols),
                "train_r2": train_r2,
                "train_mape": train_mape,
                "train_smape": train_smape,
                "holdout_r2": holdout_r2,
                "holdout_mape": holdout_mape,
                "holdout_smape": holdout_smape,
            }
        ]
    )

    holdout_fit_df = holdout_df[["date", "symbol", "ret_5d"]].copy()
    holdout_fit_df = holdout_fit_df.rename(columns={"ret_5d": "actual"})
    holdout_fit_df["predicted"] = holdout_pred
    holdout_fit_df["error"] = holdout_fit_df["actual"] - holdout_fit_df["predicted"]
    holdout_fit_df["abs_pct_error"] = (
        np.abs(holdout_fit_df["error"]) / np.maximum(np.abs(holdout_fit_df["actual"]), 1e-8)
    )

    args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
    args.holdout_fit_out.parent.mkdir(parents=True, exist_ok=True)

    metrics_df.to_csv(args.metrics_out, index=False)
    holdout_fit_df.to_csv(args.holdout_fit_out, index=False)

    print(f"Train rows: {len(train_df)}")
    print(f"Holdout rows: {len(holdout_df)}")
    print(f"Features used: {len(feature_cols)}")
    print(f"Metrics CSV: {args.metrics_out}")
    print(f"Holdout fit CSV: {args.holdout_fit_out}")
    print("\nMetrics:")
    print(metrics_df.to_string(index=False))


if __name__ == "__main__":
    main()
