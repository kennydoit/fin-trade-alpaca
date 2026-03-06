"""
Symbol screener: scores and ranks symbols by their technical signals.

The composite score rewards:
- Low RSI (oversold potential reversal)
- Price trading above key moving averages (uptrend confirmation)
- Bullish MACD crossover
- Above-average volume (conviction)
"""

import logging

logger = logging.getLogger(__name__)

# Scoring weights (tunable)
_W_RSI = 40.0        # Maximum contribution from RSI component
_W_MA_ALIGN = 30.0   # Maximum contribution from moving-average alignment (3 sub-signals × 10)
_W_MACD = 15.0       # MACD bullish cross
_W_VOLUME = 15.0     # Volume confirmation (capped at 2× average)


def score_symbol(signals: dict) -> float:
    """Compute a composite buy-signal score for a single symbol.

    Higher scores indicate stronger buy candidates.

    Args:
        signals: Dict returned by :func:`signals.technical.calculate_signals`.

    Returns:
        Float score in the range [0, 100].
    """
    # ------------------------------------------------------------------ RSI
    # Oversold (RSI < 30) → highest score; overbought (RSI > 70) → penalty
    rsi = signals.get("rsi", 50.0)
    if rsi <= 30:
        rsi_score = _W_RSI
    elif rsi <= 50:
        rsi_score = _W_RSI * (50.0 - rsi) / 20.0
    else:
        rsi_score = 0.0

    # -------------------------------------------------- Moving-average alignment
    ma_score = 0.0
    if signals.get("above_sma_20"):
        ma_score += _W_MA_ALIGN / 3
    if signals.get("above_sma_50"):
        ma_score += _W_MA_ALIGN / 3
    if signals.get("sma_20_above_sma_50"):
        ma_score += _W_MA_ALIGN / 3

    # ----------------------------------------------------------------- MACD
    macd_score = _W_MACD if signals.get("macd_bullish") else 0.0

    # -------------------------------------------------------------- Volume
    vol_ratio = signals.get("volume_ratio", 1.0)
    # Linear scale: 1× → 0 points; 2× or more → full weight
    vol_score = _W_VOLUME * min(max(vol_ratio - 1.0, 0.0), 1.0)

    total = rsi_score + ma_score + macd_score + vol_score
    return round(min(total, 100.0), 4)


def rank_symbols(symbol_signals: dict[str, dict]) -> list[tuple[str, float]]:
    """Score all symbols and return them sorted highest-score first.

    Args:
        symbol_signals: Mapping of symbol → signal dict.

    Returns:
        List of (symbol, score) tuples, descending by score.
    """
    scored = [(sym, score_symbol(sigs)) for sym, sigs in symbol_signals.items()]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def select_top_symbols(
    symbol_signals: dict[str, dict],
    top_n: int = 20,
    min_score: float = 0.0,
) -> list[str]:
    """Select the top-N symbols by composite buy-signal score.

    Args:
        symbol_signals: Mapping of symbol → signal dict.
        top_n:          Maximum number of symbols to return.
        min_score:      Minimum score threshold; symbols below this
                        are excluded even if they rank in the top N.

    Returns:
        List of symbols ordered best → worst.
    """
    ranked = rank_symbols(symbol_signals)
    selected = [sym for sym, score in ranked if score >= min_score][:top_n]
    logger.info(
        "Screener selected %d symbols from %d candidates (top_n=%d, min_score=%.1f)",
        len(selected),
        len(symbol_signals),
        top_n,
        min_score,
    )
    return selected
