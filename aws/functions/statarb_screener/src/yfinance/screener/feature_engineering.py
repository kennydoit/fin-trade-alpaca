"""Feature engineering and training-dataset construction for the short-term predictor.

These functions are pure transforms over pandas DataFrames (aside from
``build_dataset``, which pulls in price history via
:mod:`yfinance.screener.data_loading`), which makes them straightforward to
unit test without any network access.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from .data_loading import download_ohlcv_history, download_price_history


def make_technical_features(adj: pd.Series) -> pd.DataFrame:
    """Compute simple technical indicators (returns, volatility, momentum) for one symbol."""
    df = pd.DataFrame({"close": adj})
    df["ret_1d"] = df["close"].pct_change()
    df["ret_3d"] = df["close"].pct_change(3)
    df["ret_5d"] = df["close"].pct_change(5)
    df["vol_10d"] = df["ret_1d"].rolling(10).std()
    df["mom_20d"] = df["close"].pct_change(20)
    df["sma_10"] = df["close"].rolling(10).mean()
    df["price_sma10_z"] = (df["close"] - df["sma_10"]) / df["close"].rolling(60).std()
    return df


def make_breakout_features(close: pd.Series, high: pd.Series, low: pd.Series, volume: pd.Series) -> pd.DataFrame:
    """Compute breakout-oriented features for one symbol.

    Every value at index ``t`` uses data up to and including ``t`` only (trailing
    windows), so these are valid point-in-time features.
    """
    prev_close = close.shift(1)
    true_range = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr_10 = true_range.rolling(10).mean()
    atr_30 = true_range.rolling(30).mean()
    bb_width = 4.0 * close.rolling(20).std() / close.rolling(20).mean()
    range_low_10 = low.rolling(10).min()
    range_high_10 = high.rolling(10).max()

    out = pd.DataFrame(index=close.index)
    out["dist_high_20d"] = close / high.rolling(20).max() - 1.0
    out["atr_ratio_10_30"] = atr_10 / atr_30
    out["bb_width_rel"] = bb_width / bb_width.rolling(60, min_periods=10).median()
    out["vol_surge_5_20"] = volume.rolling(5).mean() / volume.rolling(20).mean()
    out["close_in_range_10d"] = (close - range_low_10) / (range_high_10 - range_low_10)
    return out.replace([np.inf, -np.inf], np.nan)


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
    "screener_rank",
]


def build_dataset(  # noqa: PLR0915 - sequential dataset-construction pipeline; splitting further would obscure the single linear flow
    cand_df: pd.DataFrame,
    symbols: list[str],
    lookback: int,
    return_days: int,
    include_breakout: bool = False,
) -> pd.DataFrame:
    """Build a training dataset (technical features + forward returns) from historical prices.

    Args:
        cand_df: Candidate dataframe with symbol metadata (sector, screener ranks, etc.).
        symbols: List of symbols to process.
        lookback: Days of historical data to fetch.
        return_days: Forward return horizon.
        include_breakout: If True, download OHLCV, add breakout features, and record
            ``fwd_max_ret`` (max forward close over the horizon vs. today's close)
            so a breakout label can be derived with :func:`compute_breakout_target`.

    Returns:
        DataFrame with features and forward returns, or an empty DataFrame if
        insufficient data was available for any symbol.
    """
    print(f"Downloading {lookback + return_days + 5} days of history for {len(symbols)} symbols...")
    ohlcv: dict[str, pd.DataFrame] = {}
    if include_breakout:
        ohlcv = download_ohlcv_history(symbols, lookback + return_days + 5)
        adj = ohlcv.get("close", pd.DataFrame())
    else:
        adj = download_price_history(symbols, lookback + return_days + 5)

    if adj.empty:
        print("WARNING: No price data downloaded. Check yfinance connectivity or symbol validity.")
        return pd.DataFrame()

    print(f"Downloaded data for {len(adj.columns)} symbols, {len(adj)} trading days")

    symbols_missing = set(symbols) - set(adj.columns)
    if symbols_missing:
        print(f"WARNING: {len(symbols_missing)} symbols missing from download (may be delisted or invalid)")
        if len(symbols_missing) <= 10:
            print(f"  Missing: {', '.join(sorted(symbols_missing))}")

    rows = []
    symbols_processed = 0
    symbols_skipped_short = 0
    symbols_no_training_window = 0

    symbol_meta = cand_df.copy()
    if not symbol_meta.empty:
        symbol_meta["symbol"] = symbol_meta["symbol"].astype(str).str.upper()
        if "date" in symbol_meta.columns:
            symbol_meta["date"] = pd.to_datetime(symbol_meta["date"], errors="coerce")

    for sym in symbols:
        if sym not in adj.columns:
            continue
        if include_breakout and any(sym not in ohlcv[k].columns for k in ("high", "low", "volume")):
            continue
        series = adj[sym].dropna()
        if len(series) < 30:
            symbols_skipped_short += 1
            continue

        symbols_processed += 1
        tech = make_technical_features(series)
        if include_breakout:
            tech = tech.join(
                make_breakout_features(
                    series,
                    ohlcv["high"][sym].reindex(series.index),
                    ohlcv["low"][sym].reindex(series.index),
                    ohlcv["volume"][sym].reindex(series.index),
                )
            )

        training_window_size = len(tech) - return_days - 30
        if training_window_size <= 0:
            symbols_no_training_window += 1
            continue

        row_meta = symbol_meta[symbol_meta["symbol"].astype(str) == sym] if not symbol_meta.empty else pd.DataFrame()
        for t_idx in range(30, len(tech) - return_days):
            date = tech.index[t_idx]
            feat_row = tech.iloc[t_idx].to_dict()
            future_price = series.iloc[t_idx + return_days]
            price = series.iloc[t_idx]
            fwd_ret = (future_price / price) - 1.0
            meta = {col: np.nan for col in _CANDIDATE_METADATA_COLUMNS if col in row_meta.columns}
            if not row_meta.empty and "date" in row_meta.columns:
                candidate_dates = row_meta["date"].dropna()
                if not candidate_dates.empty:
                    candidate_snapshot = row_meta[row_meta["date"].eq(pd.Timestamp(date))]
                    if not candidate_snapshot.empty:
                        for col in _CANDIDATE_METADATA_COLUMNS:
                            if col in candidate_snapshot.columns:
                                val = candidate_snapshot.iloc[0].get(col)
                                if isinstance(val, (int, float)) and np.isinf(val):
                                    val = np.nan
                                meta[col] = val
            rec = {"date": date, "symbol": sym, "fwd_ret": fwd_ret}
            if include_breakout:
                rec["fwd_max_ret"] = series.iloc[t_idx + 1 : t_idx + return_days + 1].max() / price - 1.0
            rec.update({k: (v if v is not None else np.nan) for k, v in feat_row.items()})
            rec.update(meta)
            rows.append(rec)

    print("Processing summary:")
    print(f"  - Symbols processed successfully: {symbols_processed}")
    print(f"  - Skipped (< 30 days data): {symbols_skipped_short}")
    print(f"  - Skipped (no training window): {symbols_no_training_window}")
    print(f"  - Total training rows generated: {len(rows)}")

    df = pd.DataFrame(rows)
    if not df.empty:
        df_clean = df.dropna(subset=["fwd_ret"])
        if len(df_clean) < len(df):
            print(f"  - Rows dropped (NaN forward returns): {len(df) - len(df_clean)}")
        return df_clean
    return df


def compute_excess_return_target(df: pd.DataFrame) -> pd.DataFrame:
    """Convert raw forward returns into a cross-sectional rank target.

    The screener is fundamentally a ranking problem, so the model should learn to
    rank assets relative to one another within each date rather than predict raw
    return magnitudes. This makes the target more stable and better aligned with
    the use case.
    """
    df = df.copy()
    if "fwd_ret" not in df.columns:
        return df

    date_median = df.groupby("date")["fwd_ret"].transform("median")
    if "sector" in df.columns:
        sector_median = df.groupby(["date", "sector"])["fwd_ret"].transform("median")
        excess_ret = df["fwd_ret"] - sector_median.fillna(date_median)
    else:
        excess_ret = df["fwd_ret"] - date_median

    target = excess_ret.groupby(df["date"]).rank(pct=True).astype(float)
    df["fwd_ret_target"] = target.fillna(0.5)
    df = df.dropna(subset=["fwd_ret", "fwd_ret_target"])
    return df


def compute_breakout_target(df: pd.DataFrame, return_days: int, breakout_k: float = 1.5) -> pd.DataFrame:
    """Add a volatility-scaled forward breakout label.

    ``breakout_label = 1`` when the maximum close over the next ``return_days`` days
    exceeds today's close by at least ``breakout_k * vol_10d * sqrt(return_days)``,
    where ``vol_10d`` is the trailing daily-return volatility known at the label date.
    Scaling by volatility keeps the base rate comparable between quiet and volatile
    names. Rows without a usable volatility or forward path are dropped.
    """
    missing = [c for c in ("fwd_max_ret", "vol_10d") if c not in df.columns]
    if missing:
        raise ValueError(f"Breakout target requires columns {missing}; build the dataset with include_breakout=True")
    df = df.copy()
    vol = df["vol_10d"].where(df["vol_10d"] > 0)
    threshold = float(breakout_k) * vol * np.sqrt(int(return_days))
    valid = threshold.notna() & df["fwd_max_ret"].notna()
    df["breakout_threshold"] = threshold
    df["breakout_label"] = (df["fwd_max_ret"] >= threshold).astype(float).where(valid)
    return df.dropna(subset=["breakout_label"])


def prepare_features(
    df: pd.DataFrame, scaler=None, fit_scaler: bool = False, use_standardization: bool = False
) -> tuple[pd.DataFrame, list[str], StandardScaler | None]:
    """Prepare model-ready features with optional standardization.

    Args:
        df: Input dataframe with features.
        scaler: Fitted StandardScaler to use for transformation. If None and
            fit_scaler=True, creates new scaler.
        fit_scaler: If True, fits a new scaler on the data. Only set True for
            training data.
        use_standardization: Whether to apply standardization (only needed
            for linear models, not trees).

    Returns:
        Tuple of (transformed features, feature column names, fitted scaler or None).
    """
    drop_cols = [
        "date",
        "symbol",
        "fwd_ret",
        "fwd_ret_target",
        "fwd_max_ret",
        "breakout_label",
        "breakout_threshold",
        "sector",
        "industry",
        "screener_rank",
    ]
    feat_cols = [c for c in df.columns if c not in drop_cols]
    # Note: fillna handled by enhance_features, but add safety checks
    X = df[feat_cols].replace([np.inf, -np.inf], np.nan).fillna(0.0)

    if use_standardization:
        if fit_scaler:
            scaler = StandardScaler()
            X_scaled = scaler.fit_transform(X)
            X = pd.DataFrame(X_scaled, columns=feat_cols, index=X.index)
            return X, feat_cols, scaler
        if scaler is not None:
            X_scaled = scaler.transform(X)
            X = pd.DataFrame(X_scaled, columns=feat_cols, index=X.index)
            return X, feat_cols, scaler

    return X, feat_cols, None
