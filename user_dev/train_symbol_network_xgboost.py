from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import r2_score
from xgboost import XGBRegressor


DEFAULT_INPUT = Path("user_dev/prediction_training_data_20261027_wide.csv")
DEFAULT_METRICS_OUTPUT = Path("user_dev/symbol_model_metrics_xgboost.csv")
DEFAULT_HOLDOUT_OUTPUT = Path("user_dev/symbol_holdout_fit_xgboost.csv")


def safe_mape(y_true: np.ndarray, y_pred: np.ndarray, eps: float = 1e-8) -> float:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    denom = np.maximum(np.abs(y_true), eps)
    return float(np.mean(np.abs((y_true - y_pred) / denom)))


def make_model(
    random_state: int = 42,
    n_estimators: int = 400,
    learning_rate: float = 0.03,
    max_depth: int = 4,
    subsample: float = 0.9,
    colsample_bytree: float = 0.9,
    reg_alpha: float = 0.0,
    reg_lambda: float = 1.0,
    min_child_weight: float = 2.0,
) -> XGBRegressor:
    return XGBRegressor(
        objective="reg:squarederror",
        n_estimators=n_estimators,
        learning_rate=learning_rate,
        max_depth=max_depth,
        subsample=subsample,
        colsample_bytree=colsample_bytree,
        reg_alpha=reg_alpha,
        reg_lambda=reg_lambda,
        min_child_weight=min_child_weight,
        random_state=random_state,
        n_jobs=-1,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Train one XGBoost model per symbol target (*_RET_5D) using all other available "
            "features with a last-X-days holdout."
        )
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Wide date-level feature CSV")
    parser.add_argument("--holdout-days", type=int, default=5, help="Number of latest dates to keep as holdout")
    parser.add_argument(
        "--target-suffix",
        type=str,
        default="_RET_5D",
        help="Target column suffix to model per symbol (e.g. _RET_5D or _FWD_RET)",
    )
    parser.add_argument(
        "--max-targets",
        type=int,
        default=0,
        help="Optional cap on number of target symbols to train (0 means all)",
    )
    parser.add_argument("--metrics-out", type=Path, default=DEFAULT_METRICS_OUTPUT, help="Per-symbol metrics CSV")
    parser.add_argument("--holdout-fit-out", type=Path, default=DEFAULT_HOLDOUT_OUTPUT, help="Holdout fit CSV")
    parser.add_argument("--n-estimators", type=int, default=400)
    parser.add_argument("--learning-rate", type=float, default=0.03)
    parser.add_argument("--max-depth", type=int, default=4)
    parser.add_argument("--subsample", type=float, default=0.9)
    parser.add_argument("--colsample-bytree", type=float, default=0.9)
    parser.add_argument("--reg-alpha", type=float, default=0.0)
    parser.add_argument("--reg-lambda", type=float, default=1.0)
    parser.add_argument("--min-child-weight", type=float, default=2.0)
    args = parser.parse_args()

    df = pd.read_csv(args.input)
    if "date" not in df.columns:
        raise ValueError("Input must contain a 'date' column")

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    numeric_cols = [c for c in df.columns if c != "date" and pd.api.types.is_numeric_dtype(df[c])]
    target_cols = sorted([c for c in numeric_cols if c.endswith(args.target_suffix)])
    if args.max_targets > 0:
        target_cols = target_cols[: args.max_targets]

    if not target_cols:
        raise ValueError(f"No target columns found ending with '{args.target_suffix}'")

    # Global holdout split based on latest unique dates.
    unique_dates = np.array(sorted(df["date"].dropna().unique()))
    if len(unique_dates) <= args.holdout_days:
        raise ValueError(
            f"Not enough unique dates ({len(unique_dates)}) for holdout_days={args.holdout_days}."
        )
    holdout_dates = set(unique_dates[-args.holdout_days :])

    # Leakage control:
    # 1) Exclude all forward-return columns (*_FWD_RET) for every model.
    # 2) Exclude the target column itself for each model.
    base_feature_cols = [c for c in numeric_cols if not c.endswith("_FWD_RET")]

    metric_rows: list[dict] = []
    holdout_rows: list[dict] = []

    for target_col in target_cols:
        symbol = target_col[: -len(args.target_suffix)] if len(args.target_suffix) else target_col
        feature_cols = [c for c in base_feature_cols if c != target_col]

        model_df = df[["date", target_col] + feature_cols].copy()
        model_df = model_df.dropna(subset=[target_col])
        if model_df.empty:
            continue

        train_mask = ~model_df["date"].isin(holdout_dates)
        test_mask = model_df["date"].isin(holdout_dates)

        train_df = model_df[train_mask].copy()
        test_df = model_df[test_mask].copy()

        if len(train_df) < 20 or len(test_df) == 0:
            continue

        # Remove columns that are entirely NaN in training for this symbol.
        feature_cols = [c for c in feature_cols if train_df[c].notna().any()]
        if not feature_cols:
            continue

        X_train = train_df[feature_cols]
        y_train = train_df[target_col].to_numpy(dtype=float)

        X_test = test_df[feature_cols]
        y_test = test_df[target_col].to_numpy(dtype=float)

        model = make_model(
            n_estimators=args.n_estimators,
            learning_rate=args.learning_rate,
            max_depth=args.max_depth,
            subsample=args.subsample,
            colsample_bytree=args.colsample_bytree,
            reg_alpha=args.reg_alpha,
            reg_lambda=args.reg_lambda,
            min_child_weight=args.min_child_weight,
        )
        model.fit(X_train, y_train)

        train_pred = model.predict(X_train)
        test_pred = model.predict(X_test)

        train_r2 = float(r2_score(y_train, train_pred)) if len(y_train) > 1 else np.nan
        test_r2 = float(r2_score(y_test, test_pred)) if len(y_test) > 1 else np.nan

        train_mape = safe_mape(y_train, train_pred)
        test_mape = safe_mape(y_test, test_pred)

        metric_rows.append(
            {
                "symbol": symbol,
                "target": target_col,
                "n_train": len(train_df),
                "n_holdout": len(test_df),
                "n_features": len(feature_cols),
                "train_r2": train_r2,
                "train_mape": train_mape,
                "holdout_r2": test_r2,
                "holdout_mape": test_mape,
            }
        )

        for date_val, actual_val, pred_val in zip(test_df["date"].to_numpy(), y_test, test_pred):
            abs_pct_err = abs(actual_val - pred_val) / float(np.maximum(abs(actual_val), 1e-8))
            holdout_rows.append(
                {
                    "date": pd.Timestamp(date_val),
                    "symbol": symbol,
                    "target": target_col,
                    "actual": float(actual_val),
                    "predicted": float(pred_val),
                    "error": float(actual_val - pred_val),
                    "abs_pct_error": float(abs_pct_err),
                }
            )

    if not metric_rows:
        raise SystemExit("No models were trained. Check data coverage and holdout settings.")

    metrics_df = pd.DataFrame(metric_rows).sort_values("holdout_r2", ascending=False)
    holdout_df = pd.DataFrame(holdout_rows).sort_values(["symbol", "date"])

    args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
    args.holdout_fit_out.parent.mkdir(parents=True, exist_ok=True)

    metrics_df.to_csv(args.metrics_out, index=False)
    holdout_df.to_csv(args.holdout_fit_out, index=False)

    print(f"Trained models: {len(metrics_df)}")
    print(f"Holdout dates: {args.holdout_days}")
    print(f"Metrics CSV: {args.metrics_out}")
    print(f"Holdout fit CSV: {args.holdout_fit_out}")
    print("\nPer-symbol holdout metrics preview:")
    print(metrics_df[["symbol", "holdout_r2", "holdout_mape"]].to_string(index=False))


if __name__ == "__main__":
    main()
