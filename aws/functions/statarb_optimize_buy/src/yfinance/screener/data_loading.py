"""Screener input discovery and raw price-history downloads.

This module is intentionally limited to I/O concerns (finding the latest
screener CSV, fetching historical prices from yfinance). It has no
dependency on feature engineering or model training so it can be tested and
reused independently of the rest of the prediction pipeline.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

import yfinance as yf


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


def _download_individually(symbols: list[str], period: str) -> pd.DataFrame:
    """Fallback path: fetch each symbol's history one at a time.

    Used when the batched ``yf.download`` call fails outright (e.g. transient
    network/API errors) so a single bad symbol can't take down the whole batch.
    """
    cols = {}
    for s in symbols:
        try:
            t = yf.Ticker(s)
            h = t.history(period=period, interval="1d", actions=False)
            if h is None or h.empty:
                continue
            cols[s] = h["Adj Close"].rename(s) if "Adj Close" in h else h["Close"].rename(s)
        except Exception:
            continue
    if not cols:
        return pd.DataFrame()
    return pd.concat(cols.values(), axis=1)


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

    try:
        if hasattr(yf, "download"):
            df = yf.download(symbols, period=period, interval="1d", progress=False, threads=True)
            if isinstance(df, tuple):
                df = df[0]

            if df.empty:
                print(f"WARNING: yfinance.download returned empty DataFrame for period={period}")
                return pd.DataFrame()

            return _extract_adjusted_close(df)
    except Exception as e:
        print(f"ERROR in yfinance.download: {e}")
        print("Falling back to individual ticker downloads...")

    return _download_individually(symbols, period)
