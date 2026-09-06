"""Scoring of the latest trading day for a set of candidate symbols."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .data_loading import download_price_history
from .enhanced_features import enhance_features
from .feature_engineering import make_technical_features, prepare_features

_CANDIDATE_METADATA_COLUMNS = [
    "pct_1w",
    "pct_1m",
    "rel_volume",
    "revenueGrowth",
    "earningsQuarterlyGrowth",
    "pegRatio",
    "trailingPE",
    "sector",
    "industry",
]


def score_latest(  # noqa: PLR0915, PLR0917 - sequential scoring pipeline; each positional arg mirrors train_and_evaluate's outputs
    model,
    feat_cols: list[str],
    cand_df: pd.DataFrame,
    symbols: list[str],
    return_days: int,
    scaler=None,
    metrics: dict = None,
    use_enhanced_features: bool = True,
):
    """Score the latest trading day for ``symbols`` using a trained model.

    Args:
        model: Trained model for predictions.
        feat_cols: List of feature column names.
        cand_df: Candidate dataframe with symbol metadata.
        symbols: List of symbols to score.
        return_days: Forward return horizon (unused for scoring itself, kept
            for signature/metrics symmetry with training).
        scaler: Fitted StandardScaler from training (CRITICAL: must be from
            training data only).
        metrics: Optional model performance metrics to attach to output rows.
        use_enhanced_features: Whether to apply enhanced feature engineering.

    To properly compute enhanced features (lags, ranks, sector-relative), we
    need historical context, so a mini-dataset covering the last 30 days is
    built before extracting the latest date's predictions.
    """
    adj = download_price_history(symbols, 90)

    if adj.empty or len(adj) < 30:
        print("Insufficient historical data for enhanced features")
        return pd.DataFrame()

    latest_date = adj.index[-1]
    rows = []

    for sym in symbols:
        if sym not in adj.columns:
            continue
        series = adj[sym].dropna()
        if len(series) < 30:
            continue
        tech = make_technical_features(series)

        row_meta = cand_df[cand_df["symbol"].astype(str) == sym]
        for t_idx in range(max(30, len(tech) - 30), len(tech)):
            date = tech.index[t_idx]
            feat_row = tech.iloc[t_idx].to_dict()
            meta = {}
            if not row_meta.empty:
                for col in _CANDIDATE_METADATA_COLUMNS:
                    if col in row_meta.columns:
                        meta[col] = row_meta.iloc[0].get(col)
            rec = {"symbol": sym, "date": date, "fwd_ret": np.nan}  # fwd_ret dummy for enhance_features
            rec.update({k: (v if v is not None else np.nan) for k, v in feat_row.items()})
            rec.update(meta)
            rows.append(rec)

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    if use_enhanced_features:
        df = enhance_features(df, add_sector_features=("sector" in df.columns))

    df = df[df["date"] == latest_date].copy()

    # Prepare features with same scaler used in training (NO fitting here).
    use_std = scaler is not None
    X, _, _ = prepare_features(df, scaler=scaler, fit_scaler=False, use_standardization=use_std)
    preds = model.predict(X)
    df["pred_ret"] = preds
    df = df.sort_values("pred_ret", ascending=False)

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
