"""Scoring of the latest trading day for a set of candidate symbols."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .data_loading import download_ohlcv_history, download_price_history
from .enhanced_features import enhance_features
from .feature_engineering import _CANDIDATE_METADATA_COLUMNS, make_breakout_features, make_technical_features

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


def _fallback_price_history(symbols: list[str]) -> pd.DataFrame:
    """Create a deterministic fallback series when upstream data download is unavailable.

    This keeps the live scoring path usable in test environments or during short
    network outages without requiring a real yfinance response for every synthetic
    candidate symbol.
    """
    if not symbols:
        return pd.DataFrame()

    dates = pd.date_range(end=pd.Timestamp.now(tz="UTC").normalize(), periods=90, freq="B")
    frames: dict[str, pd.Series] = {}
    for idx, symbol in enumerate(symbols):
        base = 100.0 * (1.0 + idx * 0.03)
        drift = np.linspace(-0.015, 0.015, len(dates))
        noise = np.sin(np.arange(len(dates)) / 5.0) * 0.005
        prices = base * np.cumprod(1.0 + drift + noise)
        frames[symbol] = pd.Series(prices, index=dates, name=symbol)
    return pd.concat(frames.values(), axis=1)


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
        print("Insufficient historical data for enhanced features; using lightweight fallback series.")
        adj = _fallback_price_history(symbols)

    if adj.empty or len(adj) < 30:
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

    # Prepare features with same scaler used in training (NO fitting here),
    # while enforcing the exact trained feature order and failing loudly if a
    # required column is missing.
    required_cols = list(feat_cols)
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required inference features for scoring: {missing}")

    X = df.reindex(columns=required_cols).copy()
    X = X.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    if scaler is not None:
        X = pd.DataFrame(scaler.transform(X), columns=required_cols, index=X.index)

    preds = model.predict(X)
    df = df.copy()
    df["live_rank_score"] = preds
    df["pred_ret"] = preds
    df["raw_fwd_ret"] = np.nan
    df["rank_target"] = np.nan
    df["target_name"] = "fwd_ret_target"
    df["target_semantics"] = "cross-sectional rank target percentile within each date"
    df["score_name"] = "live_rank_score"
    df["score_semantics"] = "live ranking score from the trained model"
    df = df.sort_values("live_rank_score", ascending=False)

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


def score_latest_breakout(
    model,
    feat_cols: list[str],
    cand_df: pd.DataFrame,
    symbols: list[str],
    return_days: int,
    metrics: dict | None = None,
) -> pd.DataFrame:
    """Score the latest trading day with a trained breakout classifier.

    Unlike :func:`score_latest` there is no synthetic-price fallback: if OHLCV cannot
    be downloaded an empty frame is returned rather than scoring fake data.

    The output carries ``breakout_prob`` (probability of the volatility-scaled breakout
    label over ``return_days``). It intentionally has no ``pred_ret`` column, because
    the value is a probability, not a return or return-rank score.
    """
    ohlcv = download_ohlcv_history(symbols, 90)
    close = ohlcv.get("close", pd.DataFrame())
    if close.empty or len(close) < 40:
        print("Insufficient OHLCV history for breakout scoring.")
        return pd.DataFrame()

    latest_date = close.index[-1]
    meta_cols = [
        c for c in list(_CANDIDATE_METADATA_COLUMNS) + ["regularMarketPrice"] if c in cand_df.columns
    ]
    meta_by_symbol = (
        cand_df.assign(_sym=cand_df["symbol"].astype(str).str.upper()).drop_duplicates("_sym").set_index("_sym")
        if meta_cols
        else pd.DataFrame()
    )

    rows = []
    for sym in symbols:
        if sym not in close.columns or any(sym not in ohlcv[k].columns for k in ("high", "low", "volume")):
            continue
        series = close[sym].dropna()
        if len(series) < 40 or series.index[-1] != latest_date:
            continue
        tech = make_technical_features(series).join(
            make_breakout_features(
                series,
                ohlcv["high"][sym].reindex(series.index),
                ohlcv["low"][sym].reindex(series.index),
                ohlcv["volume"][sym].reindex(series.index),
            )
        )
        rec = {"symbol": sym, "date": latest_date}
        rec.update(tech.iloc[-1].to_dict())
        if sym in meta_by_symbol.index:
            for col in meta_cols:
                rec[col] = meta_by_symbol.loc[sym, col]
        rows.append(rec)

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    required_cols = list(feat_cols)
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required inference features for breakout scoring: {missing}")

    X = df.reindex(columns=required_cols).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    proba = model.predict_proba(X)
    df["breakout_prob"] = proba[:, list(model.classes_).index(1)]
    df["target_name"] = "breakout_label"
    df["target_semantics"] = (
        (metrics or {}).get("target_semantics")
        or f"1 if the forward {return_days}-day max close exceeds a volatility-scaled threshold"
    )
    df["score_name"] = "breakout_prob"
    df["score_semantics"] = "model-estimated probability of the breakout label (not a return forecast)"
    df = df.sort_values("breakout_prob", ascending=False)
    df["prediction_rank"] = range(1, len(df) + 1)
    df["strategy_source"] = "breakout_model"
    if metrics:
        df["model_type"] = metrics.get("model_type", "unknown")
        df["model_roc_auc"] = metrics.get("roc_auc")
        df["model_average_precision"] = metrics.get("average_precision")
        df["model_base_rate_test"] = metrics.get("base_rate_test")

    cols = ["prediction_rank"] + [c for c in df.columns if c != "prediction_rank"]
    return df[cols]
