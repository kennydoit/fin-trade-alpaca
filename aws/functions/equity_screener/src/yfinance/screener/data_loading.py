"""Screener input discovery and raw price-history downloads.

This module is intentionally limited to I/O concerns (finding the latest
screener CSV, fetching historical prices from yfinance). It has no
dependency on feature engineering or model training so it can be tested and
reused independently of the rest of the prediction pipeline.
"""

from __future__ import annotations

import os
import time
from itertools import islice
from pathlib import Path

import pandas as pd

import yfinance as yf

DEFAULT_DOWNLOAD_BATCH_SIZE = 25
DEFAULT_DOWNLOAD_BATCH_PAUSE = 2.0
DEFAULT_DOWNLOAD_MAX_RETRIES = 2
DEFAULT_DOWNLOAD_RETRY_PAUSE = 5.0

ENV_DOWNLOAD_BATCH_SIZE = "YFINANCE_DOWNLOAD_BATCH_SIZE"
ENV_DOWNLOAD_BATCH_PAUSE = "YFINANCE_DOWNLOAD_BATCH_PAUSE"
ENV_DOWNLOAD_MAX_RETRIES = "YFINANCE_DOWNLOAD_MAX_RETRIES"
ENV_DOWNLOAD_RETRY_PAUSE = "YFINANCE_DOWNLOAD_RETRY_PAUSE"


def find_latest_screener_file(folder: Path) -> Path:
    """Return the most recently generated screener-with-metrics CSV in ``folder``.

    Raises:
        SystemExit: if no matching CSV files are found.
    """
    files = sorted(folder.glob("yfinance_screener_results_with_metrics*.csv"))
    if not files:
        raise SystemExit(f"No screener CSVs found in {folder}")
    return files[-1]


def _period_for_lookback(lookback_days: int) -> str:
    """Map a lookback window (in days) to a yfinance-friendly period string."""
    if lookback_days <= 60:
        return f"{lookback_days}d"
    if lookback_days <= 365:
        return f"{int(lookback_days / 30)}mo"
    if lookback_days <= 730:
        return "2y"
    return "5y"


def _extract_adjusted_close(df: pd.DataFrame) -> pd.DataFrame:
    """Pull the Adj Close (falling back to Close) frame out of a yfinance download."""
    is_multiindex = hasattr(df.columns, "nlevels") and df.columns.nlevels > 1
    if is_multiindex:
        metric_names = df.columns.get_level_values(0).unique()
        if "Adj Close" in metric_names:
            adj = df["Adj Close"].copy()
        elif "Close" in metric_names:
            adj = df["Close"].copy()
        else:
            print(f"WARNING: No Close/Adj Close in metrics: {list(metric_names)}")
            return pd.DataFrame()
    elif "Adj Close" in df.columns:
        adj = df["Adj Close"].copy()
    elif "Close" in df.columns:
        adj = df["Close"].copy()
    else:
        print(f"WARNING: No Close or Adj Close found. Columns: {list(df.columns[:10])}")
        return pd.DataFrame()

    if isinstance(adj, pd.Series):
        adj = adj.to_frame()
    adj.columns = [str(c) for c in adj.columns]
    return adj


def _normalize_symbols(symbols: list[str]) -> list[str]:
    return [symbol for symbol in dict.fromkeys(str(symbol).strip().upper() for symbol in symbols) if symbol]


def _batched(symbols: list[str], batch_size: int) -> list[list[str]]:
    iterator = iter(symbols)
    batches: list[list[str]] = []
    while True:
        batch = list(islice(iterator, batch_size))
        if not batch:
            break
        batches.append(batch)
    return batches


def _sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _download_batch(symbols: list[str], period: str, interval: str, auto_adjust: bool) -> pd.DataFrame:
    df = yf.download(
        symbols,
        period=period,
        interval=interval,
        progress=False,
        threads=False,
        auto_adjust=auto_adjust,
    )
    if isinstance(df, tuple):
        df = df[0]
    if df is None or df.empty:
        return pd.DataFrame()
    return _extract_adjusted_close(df)


def _download_individually(
    symbols: list[str], period: str, interval: str = "1d", auto_adjust: bool = False
) -> pd.DataFrame:
    """Fallback path: fetch each symbol's history one at a time.

    Used when the batched ``yf.download`` call fails outright (e.g. transient
    network/API errors) so a single bad symbol can't take down the whole batch.
    """
    cols = {}
    for s in symbols:
        try:
            t = yf.Ticker(s)
            h = t.history(period=period, interval=interval, actions=False, auto_adjust=auto_adjust)
            if h is None or h.empty:
                continue
            cols[s] = h["Adj Close"].rename(s) if "Adj Close" in h else h["Close"].rename(s)
        except Exception:
            continue
    if not cols:
        return pd.DataFrame()
    return pd.concat(cols.values(), axis=1)


def download_price_history_batched(
    symbols: list[str],
    period: str,
    interval: str = "1d",
    batch_size: int = DEFAULT_DOWNLOAD_BATCH_SIZE,
    batch_pause: float = DEFAULT_DOWNLOAD_BATCH_PAUSE,
    max_retries: int = DEFAULT_DOWNLOAD_MAX_RETRIES,
    retry_pause: float = DEFAULT_DOWNLOAD_RETRY_PAUSE,
    auto_adjust: bool = False,
) -> pd.DataFrame:
    """Download price history in paced batches to reduce yfinance burstiness."""
    normalized_symbols = _normalize_symbols(symbols)
    if not normalized_symbols:
        return pd.DataFrame()

    effective_batch_size = max(1, int(batch_size or len(normalized_symbols)))
    effective_batch_pause = _env_float(ENV_DOWNLOAD_BATCH_PAUSE, batch_pause)
    effective_max_retries = max(0, _env_int(ENV_DOWNLOAD_MAX_RETRIES, max_retries))
    effective_retry_pause = _env_float(ENV_DOWNLOAD_RETRY_PAUSE, retry_pause)
    frames: list[pd.DataFrame] = []
    batches = _batched(normalized_symbols, effective_batch_size)

    for batch_index, batch in enumerate(batches):
        batch_frame = pd.DataFrame()
        last_error: Exception | None = None

        for attempt in range(effective_max_retries + 1):
            try:
                batch_frame = _download_batch(batch, period=period, interval=interval, auto_adjust=auto_adjust)
            except Exception as exc:
                last_error = exc
                batch_frame = pd.DataFrame()

            if not batch_frame.empty:
                break

            if attempt < effective_max_retries:
                reason = f": {last_error}" if last_error else ""
                print(
                    f"WARNING: yfinance batch download failed for {len(batch)} symbols on attempt {attempt + 1}"
                    f"/{effective_max_retries + 1}{reason}"
                )
                _sleep(effective_retry_pause)

        if batch_frame.empty:
            if last_error:
                print(f"WARNING: Falling back to per-symbol history after batch failure: {last_error}")
            batch_frame = _download_individually(batch, period=period, interval=interval, auto_adjust=auto_adjust)

        if not batch_frame.empty:
            frames.append(batch_frame)

        if effective_batch_pause and batch_index < len(batches) - 1:
            _sleep(effective_batch_pause)

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, axis=1)
    combined = combined.loc[:, ~combined.columns.duplicated()]
    ordered_columns = [symbol for symbol in normalized_symbols if symbol in combined.columns]
    return combined.reindex(columns=ordered_columns)


_OHLCV_FIELDS = {"close": "Close", "high": "High", "low": "Low", "volume": "Volume"}


def _split_ohlcv(df: pd.DataFrame, symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Split a yfinance download into per-field frames; returns ``{}`` if any field is missing."""
    is_multiindex = hasattr(df.columns, "nlevels") and df.columns.nlevels > 1
    out: dict[str, pd.DataFrame] = {}
    for key, name in _OHLCV_FIELDS.items():
        if is_multiindex:
            if name not in df.columns.get_level_values(0):
                return {}
            frame = df[name].copy()
        else:
            if name not in df.columns or len(symbols) != 1:
                return {}
            frame = df[[name]].copy()
            frame.columns = [symbols[0]]
        frame.columns = [str(c) for c in frame.columns]
        out[key] = frame
    return out


def _download_ohlcv_individually(symbols: list[str], period: str) -> dict[str, pd.DataFrame]:
    """Fallback path: fetch each symbol's OHLCV history one at a time."""
    cols: dict[str, dict[str, pd.Series]] = {key: {} for key in _OHLCV_FIELDS}
    for s in symbols:
        try:
            h = yf.Ticker(s).history(period=period, interval="1d", actions=False, auto_adjust=True)
        except Exception:
            continue
        if h is None or h.empty or any(name not in h.columns for name in _OHLCV_FIELDS.values()):
            continue
        for key, name in _OHLCV_FIELDS.items():
            cols[key][s] = h[name].rename(s)
    if not cols["close"]:
        return {}
    return {key: pd.concat(series.values(), axis=1) for key, series in cols.items()}


def download_ohlcv_history(symbols: list[str], lookback_days: int) -> dict[str, pd.DataFrame]:
    """Download split/dividend-adjusted OHLCV history for ``symbols``.

    Returns:
        Dict with ``close``, ``high``, ``low`` and ``volume`` DataFrames (columns =
        symbols, all adjusted consistently), or an empty dict if nothing could be
        downloaded.
    """
    period = _period_for_lookback(lookback_days)
    normalized = _normalize_symbols(symbols)
    if not normalized:
        return {}

    batch_size = max(1, _env_int(ENV_DOWNLOAD_BATCH_SIZE, DEFAULT_DOWNLOAD_BATCH_SIZE))
    batch_pause = _env_float(ENV_DOWNLOAD_BATCH_PAUSE, DEFAULT_DOWNLOAD_BATCH_PAUSE)
    batches = _batched(normalized, batch_size)
    parts: dict[str, list[pd.DataFrame]] = {key: [] for key in _OHLCV_FIELDS}

    for batch_index, batch in enumerate(batches):
        fields: dict[str, pd.DataFrame] = {}
        try:
            raw = yf.download(
                batch, period=period, interval="1d", progress=False, threads=False, auto_adjust=True
            )
            if isinstance(raw, tuple):
                raw = raw[0]
            if raw is not None and not raw.empty:
                fields = _split_ohlcv(raw, batch)
        except Exception as exc:
            print(f"WARNING: yfinance OHLCV batch download failed for {len(batch)} symbols: {exc}")
        if not fields:
            fields = _download_ohlcv_individually(batch, period)
        for key, frame in fields.items():
            parts[key].append(frame)
        if batch_index < len(batches) - 1:
            _sleep(batch_pause)

    if not parts["close"]:
        return {}

    result: dict[str, pd.DataFrame] = {}
    for key, frames in parts.items():
        combined = pd.concat(frames, axis=1)
        combined = combined.loc[:, ~combined.columns.duplicated()]
        result[key] = combined.reindex(columns=[s for s in normalized if s in combined.columns])
    return result


def download_price_history(symbols: list[str], lookback_days: int) -> pd.DataFrame:
    """Download historical adjusted-close prices for ``symbols``.

    Args:
        symbols: List of stock symbols.
        lookback_days: Number of days of history to fetch.

    Returns:
        DataFrame with adjusted close prices (columns = symbols), or an empty
        DataFrame if the download fails entirely.
    """
    period = _period_for_lookback(lookback_days)
    batch_size = _env_int(ENV_DOWNLOAD_BATCH_SIZE, DEFAULT_DOWNLOAD_BATCH_SIZE)

    try:
        if hasattr(yf, "download"):
            df = download_price_history_batched(symbols, period=period, interval="1d", batch_size=batch_size)
            if df.empty:
                print(f"WARNING: yfinance.download returned empty DataFrame for period={period}")
                return pd.DataFrame()

            return df
    except Exception as e:
        print(f"ERROR in yfinance.download: {e}")
        print("Falling back to individual ticker downloads...")

    return _download_individually(_normalize_symbols(symbols), period)
