from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import r2_score
from xgboost import XGBRegressor


DEFAULT_INPUT = Path("user_dev/prediction_training_data_20261027.csv")
DEFAULT_COMPARISON_OUTPUT = Path("user_dev/vertical_target_comparison_xgboost.csv")
DEFAULT_HOLDOUT_OUTPUT = Path("user_dev/vertical_target_comparison_holdout_fit_xgboost.csv")


TARGETS = ("ret_5d", "fwd_ret")


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


def daily_spearman_ic(df: pd.DataFrame, actual_col: str, pred_col: str) -> float:
    daily_ic: list[float] = []
    for _date, g in df.groupby("date"):
        if len(g) < 3:
            continue
        if g[actual_col].nunique() < 2 or g[pred_col].nunique() < 2:
            continue
        ic = g[actual_col].corr(g[pred_col], method="spearman")
        if pd.notna(ic):
            daily_ic.append(float(ic))
    return float(np.mean(daily_ic)) if daily_ic else np.nan


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
        description=(
            "Compare vertical XGBoost performance for ret_5d vs fwd_ret using identical "
            "feature set and last-X-date holdout."
        )
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Vertical training dataset CSV")
    parser.add_argument("--holdout-days", type=int, default=5, help="Number of latest dates to keep as holdout")
    parser.add_argument(
        "--comparison-out",
        type=Path,
        default=DEFAULT_COMPARISON_OUTPUT,
        help="Side-by-side metrics CSV",
    )
    parser.add_argument(
        "--holdout-fit-out",
        type=Path,
        default=DEFAULT_HOLDOUT_OUTPUT,
        help="Holdout actual/predicted rows for both targets",
    )
    args = parser.parse_args()

    df = pd.read_csv(args.input)
    required = {"date", "symbol", *TARGETS}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Input is missing required columns: {sorted(missing)}")

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    unique_dates = np.array(sorted(df["date"].dropna().unique()))
    if len(unique_dates) <= args.holdout_days:
        raise ValueError(
            f"Not enough unique dates ({len(unique_dates)}) for holdout_days={args.holdout_days}."
        )
    holdout_dates = set(unique_dates[-args.holdout_days :])

    # Shared leakage-safe feature pool for apples-to-apples target comparison.
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
    shared_feature_cols = [
        c for c in df.columns if c not in excluded and pd.api.types.is_numeric_dtype(df[c])
    ]
    if not shared_feature_cols:
        raise ValueError("No shared numeric feature columns after exclusions")

    metric_rows: list[dict] = []
    holdout_rows: list[dict] = []

    for target in TARGETS:
        target_df = df.dropna(subset=[target]).copy()

        train_df = target_df[~target_df["date"].isin(holdout_dates)].copy()
        holdout_df = target_df[target_df["date"].isin(holdout_dates)].copy()
        if train_df.empty or holdout_df.empty:
            continue

        feature_cols = [c for c in shared_feature_cols if train_df[c].notna().any()]
        if not feature_cols:
            continue

        X_train = train_df[feature_cols]
        y_train = train_df[target].to_numpy(dtype=float)

        X_holdout = holdout_df[feature_cols]
        y_holdout = holdout_df[target].to_numpy(dtype=float)

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

        train_ic = daily_spearman_ic(
            pd.DataFrame({"date": train_df["date"], "actual": y_train, "pred": train_pred}),
            actual_col="actual",
            pred_col="pred",
        )
        holdout_ic = daily_spearman_ic(
            pd.DataFrame({"date": holdout_df["date"], "actual": y_holdout, "pred": holdout_pred}),
            actual_col="actual",
            pred_col="pred",
        )

        metric_rows.append(
            {
                "target": target,
                "holdout_days": args.holdout_days,
                "n_train": len(train_df),
                "n_holdout": len(holdout_df),
                "n_features": len(feature_cols),
                "train_r2": train_r2,
                "train_mape": train_mape,
                "train_smape": train_smape,
                "train_daily_spearman_ic": train_ic,
                "holdout_r2": holdout_r2,
                "holdout_mape": holdout_mape,
                "holdout_smape": holdout_smape,
                "holdout_daily_spearman_ic": holdout_ic,
            }
        )

        target_holdout = holdout_df[["date", "symbol"]].copy()
        target_holdout["target"] = target
        target_holdout["actual"] = y_holdout
        target_holdout["predicted"] = holdout_pred
        target_holdout["error"] = target_holdout["actual"] - target_holdout["predicted"]
        target_holdout["abs_pct_error"] = (
            np.abs(target_holdout["error"]) / np.maximum(np.abs(target_holdout["actual"]), 1e-8)
        )
        holdout_rows.append(target_holdout)

    if not metric_rows:
        raise SystemExit("No models were trained. Check data and holdout settings.")

    comparison_df = pd.DataFrame(metric_rows)
    holdout_fit_df = pd.concat(holdout_rows, ignore_index=True).sort_values(["target", "date", "symbol"])

    args.comparison_out.parent.mkdir(parents=True, exist_ok=True)
    args.holdout_fit_out.parent.mkdir(parents=True, exist_ok=True)

    comparison_df.to_csv(args.comparison_out, index=False)
    holdout_fit_df.to_csv(args.holdout_fit_out, index=False)

    print(f"Comparison CSV: {args.comparison_out}")
    print(f"Holdout fit CSV: {args.holdout_fit_out}")
    print("\nSide-by-side metrics:")
    print(
        comparison_df[
            [
                "target",
                "n_train",
                "n_holdout",
                "n_features",
                "holdout_r2",
                "holdout_mape",
                "holdout_smape",
                "holdout_daily_spearman_ic",
            ]
        ].to_string(index=False)
    )


if __name__ == "__main__":
    main()
