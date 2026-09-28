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

from .data_loading import download_price_history


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
    cand_df: pd.DataFrame, symbols: list[str], lookback: int, return_days: int
) -> pd.DataFrame:
    """Build a training dataset (technical features + forward returns) from historical prices.

    Args:
        cand_df: Candidate dataframe with symbol metadata (sector, screener ranks, etc.).
        symbols: List of symbols to process.
        lookback: Days of historical data to fetch.
        return_days: Forward return horizon.

    Returns:
        DataFrame with features and forward returns, or an empty DataFrame if
        insufficient data was available for any symbol.
    """
    print(f"Downloading {lookback + return_days + 5} days of history for {len(symbols)} symbols...")
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

    for sym in symbols:
        if sym not in adj.columns:
            continue
        series = adj[sym].dropna()
        if len(series) < 30:
            symbols_skipped_short += 1
            continue

        symbols_processed += 1
        tech = make_technical_features(series)

        training_window_size = len(tech) - return_days - 30
        if training_window_size <= 0:
            symbols_no_training_window += 1
            continue

        row_meta = cand_df[cand_df["symbol"].astype(str) == sym]
        for t_idx in range(30, len(tech) - return_days):
            date = tech.index[t_idx]
            feat_row = tech.iloc[t_idx].to_dict()
            future_price = series.iloc[t_idx + return_days]
            price = series.iloc[t_idx]
            fwd_ret = (future_price / price) - 1.0
            meta = {}
            if not row_meta.empty:
                for col in _CANDIDATE_METADATA_COLUMNS:
                    if col in row_meta.columns:
                        val = row_meta.iloc[0].get(col)
                        if isinstance(val, (int, float)) and np.isinf(val):
                            val = np.nan
                        meta[col] = val
            rec = {"date": date, "symbol": sym, "fwd_ret": fwd_ret}
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
    drop_cols = ["date", "symbol", "fwd_ret", "fwd_ret_target", "sector", "industry", "screener_rank"]
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
