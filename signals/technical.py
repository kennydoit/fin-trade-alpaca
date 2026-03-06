"""
Technical indicator calculation for price DataFrames.

Computes RSI, simple/exponential moving averages, MACD, and volume ratio
for an OHLCV DataFrame and returns a flat signal dictionary ready for
scoring and AI analysis.
"""

import logging
from typing import Optional

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD, SMAIndicator

logger = logging.getLogger(__name__)

# Minimum number of data points required to compute all indicators reliably
MIN_BARS = 60

# MACD standard parameters
_MACD_FAST = 12
_MACD_SLOW = 26
_MACD_SIGN = 9


def calculate_signals(
    df: pd.DataFrame,
    rsi_period: int = 14,
) -> Optional[dict]:
    """Compute technical indicators and return a signal dictionary.

    Args:
        df:         DataFrame with at minimum a 'close' column and
                    optionally 'open', 'high', 'low', 'volume'.
                    Rows should be ordered oldest → newest.
        rsi_period: Lookback window for RSI (default 14).

    Returns:
        Dict with the following keys (all floats unless noted):
            current_price, rsi, sma_20, sma_50, ema_9, ema_21,
            macd, macd_signal, macd_diff, volume_ratio,
            above_sma_20 (bool), above_sma_50 (bool),
            sma_20_above_sma_50 (bool), macd_bullish (bool)
        Returns None if the DataFrame has too few rows or lacks a
        'close' column.
    """
    if "close" not in df.columns:
        logger.debug("DataFrame is missing 'close' column — skipping")
        return None

    if len(df) < MIN_BARS:
        logger.debug("Only %d bars available (need %d) — skipping", len(df), MIN_BARS)
        return None

    close = df["close"].astype(float)

    # RSI
    rsi_values = RSIIndicator(close=close, window=rsi_period).rsi()
    rsi = float(rsi_values.iloc[-1])

    # Simple moving averages
    sma_20 = float(SMAIndicator(close=close, window=20).sma_indicator().iloc[-1])
    sma_50 = float(SMAIndicator(close=close, window=50).sma_indicator().iloc[-1])

    # Exponential moving averages
    ema_9 = float(EMAIndicator(close=close, window=9).ema_indicator().iloc[-1])
    ema_21 = float(EMAIndicator(close=close, window=21).ema_indicator().iloc[-1])

    # MACD
    macd_indicator = MACD(
        close=close,
        window_fast=_MACD_FAST,
        window_slow=_MACD_SLOW,
        window_sign=_MACD_SIGN,
    )
    macd_val = float(macd_indicator.macd().iloc[-1])
    macd_signal_val = float(macd_indicator.macd_signal().iloc[-1])
    macd_diff_val = float(macd_indicator.macd_diff().iloc[-1])

    # Volume ratio (current volume vs 20-day average)
    volume_ratio = 1.0
    if "volume" in df.columns:
        volume = df["volume"].astype(float)
        vol_avg = float(volume.rolling(window=20).mean().iloc[-1])
        if vol_avg > 0:
            volume_ratio = float(volume.iloc[-1]) / vol_avg

    current_price = float(close.iloc[-1])

    return {
        "current_price": current_price,
        "rsi": rsi,
        "sma_20": sma_20,
        "sma_50": sma_50,
        "ema_9": ema_9,
        "ema_21": ema_21,
        "macd": macd_val,
        "macd_signal": macd_signal_val,
        "macd_diff": macd_diff_val,
        "volume_ratio": volume_ratio,
        # Derived boolean flags
        "above_sma_20": current_price > sma_20,
        "above_sma_50": current_price > sma_50,
        "sma_20_above_sma_50": sma_20 > sma_50,
        "macd_bullish": macd_val > macd_signal_val,
    }


def format_signals_for_prompt(symbol: str, signals: dict) -> str:
    """Format a signal dict into a human-readable block for an LLM prompt.

    Args:
        symbol:  Ticker symbol.
        signals: Dict returned by :func:`calculate_signals`.

    Returns:
        Multi-line string suitable for inclusion in a prompt.
    """
    macd_dir = "bullish (MACD above signal)" if signals.get("macd_bullish") else "bearish (MACD below signal)"
    trend = "uptrend" if signals.get("sma_20_above_sma_50") else "downtrend"
    return (
        f"Symbol: {symbol}\n"
        f"Current Price: ${signals['current_price']:.2f}\n"
        f"RSI (14): {signals['rsi']:.1f}\n"
        f"SMA 20: ${signals['sma_20']:.2f} | SMA 50: ${signals['sma_50']:.2f}\n"
        f"EMA 9: ${signals['ema_9']:.2f} | EMA 21: ${signals['ema_21']:.2f}\n"
        f"MACD: {signals['macd']:.4f} | Signal: {signals['macd_signal']:.4f} — {macd_dir}\n"
        f"Trend (SMA20 vs SMA50): {trend}\n"
        f"Volume Ratio (vs 20d avg): {signals['volume_ratio']:.2f}x\n"
    )
