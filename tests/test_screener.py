"""
Unit tests for signals.screener — scoring, ranking, and symbol selection.
"""

import unittest

from signals.screener import rank_symbols, score_symbol, select_top_symbols

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _buy_signals(**overrides):
    """Return a strong-buy signal dict, with optional field overrides."""
    base = {
        "current_price": 100.0,
        "rsi": 28.0,            # oversold
        "sma_20": 98.0,
        "sma_50": 95.0,
        "ema_9": 99.0,
        "ema_21": 97.0,
        "macd": 0.5,
        "macd_signal": 0.2,
        "macd_diff": 0.3,
        "volume_ratio": 2.0,    # double average volume
        "above_sma_20": True,
        "above_sma_50": True,
        "sma_20_above_sma_50": True,
        "macd_bullish": True,
    }
    base.update(overrides)
    return base


def _sell_signals(**overrides):
    """Return a weak / bearish signal dict."""
    base = {
        "current_price": 100.0,
        "rsi": 75.0,            # overbought
        "sma_20": 96.0,
        "sma_50": 98.0,
        "ema_9": 97.0,
        "ema_21": 98.5,
        "macd": -0.3,
        "macd_signal": 0.1,
        "macd_diff": -0.4,
        "volume_ratio": 0.7,
        "above_sma_20": False,
        "above_sma_50": False,
        "sma_20_above_sma_50": False,
        "macd_bullish": False,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestScoreSymbol(unittest.TestCase):

    def test_score_in_valid_range(self):
        for signals in (_buy_signals(), _sell_signals()):
            score = score_symbol(signals)
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 100.0)

    def test_buy_signal_scores_higher_than_sell(self):
        self.assertGreater(score_symbol(_buy_signals()), score_symbol(_sell_signals()))

    def test_max_score_for_perfect_buy(self):
        score = score_symbol(_buy_signals(rsi=25.0, volume_ratio=2.5))
        self.assertGreater(score, 80.0)

    def test_zero_score_for_bearish_overbought(self):
        score = score_symbol(_sell_signals(rsi=80.0, volume_ratio=0.5))
        self.assertEqual(score, 0.0)

    # RSI component
    def test_rsi_below_30_gets_max_rsi_score(self):
        score_low_rsi = score_symbol(_buy_signals(rsi=20.0, volume_ratio=1.0, macd_bullish=False,
                                                   above_sma_20=False, above_sma_50=False,
                                                   sma_20_above_sma_50=False))
        score_high_rsi = score_symbol(_buy_signals(rsi=60.0, volume_ratio=1.0, macd_bullish=False,
                                                    above_sma_20=False, above_sma_50=False,
                                                    sma_20_above_sma_50=False))
        self.assertGreater(score_low_rsi, score_high_rsi)

    def test_rsi_between_30_and_50_gives_partial_score(self):
        score_40 = score_symbol(_buy_signals(rsi=40.0, volume_ratio=1.0, macd_bullish=False,
                                             above_sma_20=False, above_sma_50=False,
                                             sma_20_above_sma_50=False))
        score_50 = score_symbol(_buy_signals(rsi=50.0, volume_ratio=1.0, macd_bullish=False,
                                             above_sma_20=False, above_sma_50=False,
                                             sma_20_above_sma_50=False))
        self.assertGreater(score_40, score_50)

    # MA component
    def test_all_ma_aligned_is_better_than_none(self):
        score_aligned = score_symbol(_buy_signals(above_sma_20=True, above_sma_50=True,
                                                   sma_20_above_sma_50=True,
                                                   rsi=55.0, macd_bullish=False, volume_ratio=1.0))
        score_none = score_symbol(_buy_signals(above_sma_20=False, above_sma_50=False,
                                               sma_20_above_sma_50=False,
                                               rsi=55.0, macd_bullish=False, volume_ratio=1.0))
        self.assertGreater(score_aligned, score_none)

    # MACD component
    def test_macd_bullish_adds_to_score(self):
        score_bull = score_symbol(_buy_signals(macd_bullish=True, rsi=55.0, volume_ratio=1.0,
                                               above_sma_20=False, above_sma_50=False,
                                               sma_20_above_sma_50=False))
        score_bear = score_symbol(_buy_signals(macd_bullish=False, rsi=55.0, volume_ratio=1.0,
                                               above_sma_20=False, above_sma_50=False,
                                               sma_20_above_sma_50=False))
        self.assertGreater(score_bull, score_bear)

    # Volume component
    def test_higher_volume_ratio_scores_higher(self):
        score_high = score_symbol(_buy_signals(volume_ratio=2.0, rsi=55.0, macd_bullish=False,
                                               above_sma_20=False, above_sma_50=False,
                                               sma_20_above_sma_50=False))
        score_low = score_symbol(_buy_signals(volume_ratio=0.5, rsi=55.0, macd_bullish=False,
                                              above_sma_20=False, above_sma_50=False,
                                              sma_20_above_sma_50=False))
        self.assertGreater(score_high, score_low)

    def test_volume_ratio_capped_at_2x(self):
        score_2x = score_symbol(_buy_signals(volume_ratio=2.0))
        score_10x = score_symbol(_buy_signals(volume_ratio=10.0))
        self.assertEqual(score_2x, score_10x)


class TestRankSymbols(unittest.TestCase):

    def test_returns_list_of_tuples(self):
        sym_sigs = {"AAPL": _buy_signals(), "MSFT": _sell_signals()}
        ranked = rank_symbols(sym_sigs)
        self.assertIsInstance(ranked, list)
        for item in ranked:
            self.assertIsInstance(item, tuple)
            self.assertEqual(len(item), 2)

    def test_descending_order(self):
        sym_sigs = {
            "A": _buy_signals(),
            "B": _sell_signals(),
            "C": _buy_signals(rsi=35.0),
        }
        ranked = rank_symbols(sym_sigs)
        scores = [score for _, score in ranked]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_empty_input(self):
        self.assertEqual(rank_symbols({}), [])

    def test_all_symbols_returned(self):
        sym_sigs = {f"SYM{i}": _buy_signals(rsi=30 + i) for i in range(10)}
        ranked = rank_symbols(sym_sigs)
        self.assertEqual(len(ranked), 10)
        self.assertEqual({s for s, _ in ranked}, set(sym_sigs.keys()))


class TestSelectTopSymbols(unittest.TestCase):

    def _make_mixed_pool(self, n=30):
        """Create a pool of mixed buy/sell signals."""
        pool = {}
        for i in range(n):
            if i % 2 == 0:
                pool[f"BUY{i}"] = _buy_signals(rsi=28 + i % 5)
            else:
                pool[f"SELL{i}"] = _sell_signals(rsi=70 + i % 10)
        return pool

    def test_returns_at_most_top_n(self):
        pool = self._make_mixed_pool(30)
        selected = select_top_symbols(pool, top_n=10)
        self.assertLessEqual(len(selected), 10)

    def test_returns_exactly_top_n_when_enough_symbols(self):
        pool = self._make_mixed_pool(30)
        selected = select_top_symbols(pool, top_n=5)
        self.assertEqual(len(selected), 5)

    def test_first_symbol_has_highest_score(self):
        pool = self._make_mixed_pool(30)
        selected = select_top_symbols(pool, top_n=10)
        all_ranked = rank_symbols(pool)
        self.assertEqual(selected[0], all_ranked[0][0])

    def test_min_score_filter(self):
        pool = {
            "GOOD": _buy_signals(rsi=25.0),
            "BAD": _sell_signals(rsi=80.0),
        }
        selected = select_top_symbols(pool, top_n=20, min_score=50.0)
        self.assertIn("GOOD", selected)
        self.assertNotIn("BAD", selected)

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(select_top_symbols({}, top_n=10), [])

    def test_top_n_zero_returns_empty_list(self):
        pool = {"AAPL": _buy_signals()}
        self.assertEqual(select_top_symbols(pool, top_n=0), [])

    def test_pool_smaller_than_top_n(self):
        pool = {"AAPL": _buy_signals(), "MSFT": _buy_signals(rsi=32.0)}
        selected = select_top_symbols(pool, top_n=20)
        self.assertEqual(len(selected), 2)


if __name__ == "__main__":
    unittest.main()
