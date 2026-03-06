"""
Unit tests for signals.technical — RSI, SMA, EMA, MACD calculations.
"""

import math
import unittest

import numpy as np
import pandas as pd

from signals.technical import MIN_BARS, calculate_signals, format_signals_for_prompt


def _make_df(close_prices, volumes=None):
    """Build a minimal OHLCV DataFrame from a list of close prices."""
    n = len(close_prices)
    if volumes is None:
        volumes = [1_000_000] * n
    return pd.DataFrame(
        {
            "open": close_prices,
            "high": close_prices,
            "low": close_prices,
            "close": close_prices,
            "volume": volumes,
        }
    )


def _trending_prices(n=90, start=100.0, slope=0.5):
    """Return a list of *n* prices rising by *slope* each bar."""
    return [start + i * slope for i in range(n)]


def _flat_prices(n=90, price=100.0):
    return [price] * n


class TestCalculateSignals(unittest.TestCase):

    # ------------------------------------------------------------------ happy path

    def test_returns_dict_for_valid_data(self):
        df = _make_df(_trending_prices())
        result = calculate_signals(df)
        self.assertIsNotNone(result)
        self.assertIsInstance(result, dict)

    def test_expected_keys_present(self):
        df = _make_df(_trending_prices())
        result = calculate_signals(df)
        expected_keys = {
            "current_price", "rsi", "sma_20", "sma_50", "ema_9", "ema_21",
            "macd", "macd_signal", "macd_diff", "volume_ratio",
            "above_sma_20", "above_sma_50", "sma_20_above_sma_50", "macd_bullish",
        }
        self.assertEqual(expected_keys, set(result.keys()))

    def test_current_price_matches_last_close(self):
        prices = _trending_prices()
        df = _make_df(prices)
        result = calculate_signals(df)
        self.assertAlmostEqual(result["current_price"], prices[-1], places=4)

    def test_rsi_within_valid_range(self):
        df = _make_df(_trending_prices())
        result = calculate_signals(df)
        self.assertGreaterEqual(result["rsi"], 0)
        self.assertLessEqual(result["rsi"], 100)

    def test_rsi_high_for_strong_uptrend(self):
        """Strongly rising prices should push RSI above 50."""
        df = _make_df(_trending_prices(n=90, slope=1.0))
        result = calculate_signals(df)
        self.assertGreater(result["rsi"], 50)

    def test_rsi_low_for_strong_downtrend(self):
        """Strongly falling prices should push RSI below 50."""
        prices = [100 - i * 0.5 for i in range(90)]
        df = _make_df(prices)
        result = calculate_signals(df)
        self.assertLess(result["rsi"], 50)

    def test_above_sma_flags_for_uptrending_price(self):
        df = _make_df(_trending_prices(n=90, slope=1.0))
        result = calculate_signals(df)
        # In a rising market the last close should be above both moving averages
        self.assertTrue(result["above_sma_20"])
        self.assertTrue(result["above_sma_50"])
        self.assertTrue(result["sma_20_above_sma_50"])

    def test_volume_ratio_uniform_volume(self):
        """With constant volume the ratio should be exactly 1.0."""
        df = _make_df(_trending_prices(), volumes=[500_000] * 90)
        result = calculate_signals(df)
        self.assertAlmostEqual(result["volume_ratio"], 1.0, places=4)

    def test_volume_ratio_spike(self):
        """A volume spike on the last bar should push ratio above 1."""
        volumes = [500_000] * 89 + [2_000_000]
        df = _make_df(_trending_prices(), volumes=volumes)
        result = calculate_signals(df)
        self.assertGreater(result["volume_ratio"], 1.0)

    def test_sma_20_less_than_sma_50_for_downtrend(self):
        """In a falling market SMA20 should be below SMA50."""
        prices = [200 - i * 1.0 for i in range(90)]
        df = _make_df(prices)
        result = calculate_signals(df)
        self.assertFalse(result["sma_20_above_sma_50"])

    # ------------------------------------------------------------------ edge cases / guards

    def test_returns_none_for_too_few_bars(self):
        df = _make_df(_trending_prices(n=MIN_BARS - 1))
        result = calculate_signals(df)
        self.assertIsNone(result)

    def test_returns_none_for_exactly_min_bars_minus_one(self):
        df = _make_df(_trending_prices(n=MIN_BARS - 1))
        self.assertIsNone(calculate_signals(df))

    def test_accepts_exactly_min_bars(self):
        df = _make_df(_trending_prices(n=MIN_BARS))
        result = calculate_signals(df)
        self.assertIsNotNone(result)

    def test_returns_none_for_missing_close_column(self):
        df = pd.DataFrame({"open": [1, 2, 3], "high": [1, 2, 3]})
        self.assertIsNone(calculate_signals(df))

    def test_works_without_volume_column(self):
        df = _make_df(_trending_prices()).drop(columns=["volume"])
        result = calculate_signals(df)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["volume_ratio"], 1.0, places=4)

    def test_custom_rsi_period(self):
        # Use alternating up/down prices so RSI is not pegged at 0 or 100
        prices = [100 + (5 if i % 3 != 0 else -3) * (i % 7 + 1) for i in range(90)]
        df = _make_df(prices)
        result_14 = calculate_signals(df, rsi_period=14)
        result_7 = calculate_signals(df, rsi_period=7)
        # Both should produce valid RSI values
        self.assertGreaterEqual(result_14["rsi"], 0)
        self.assertLessEqual(result_14["rsi"], 100)
        self.assertGreaterEqual(result_7["rsi"], 0)
        self.assertLessEqual(result_7["rsi"], 100)
        # Different periods should yield different RSI values for non-trivial data
        self.assertNotEqual(result_14["rsi"], result_7["rsi"])

    def test_all_values_are_finite(self):
        df = _make_df(_trending_prices())
        result = calculate_signals(df)
        for key, val in result.items():
            if isinstance(val, float):
                self.assertTrue(math.isfinite(val), f"{key} is not finite: {val}")


class TestFormatSignalsForPrompt(unittest.TestCase):

    def _make_signals(self):
        df = _make_df(_trending_prices())
        return calculate_signals(df)

    def test_returns_string(self):
        signals = self._make_signals()
        text = format_signals_for_prompt("AAPL", signals)
        self.assertIsInstance(text, str)

    def test_contains_symbol(self):
        signals = self._make_signals()
        text = format_signals_for_prompt("AAPL", signals)
        self.assertIn("AAPL", text)

    def test_contains_rsi_label(self):
        signals = self._make_signals()
        text = format_signals_for_prompt("AAPL", signals)
        self.assertIn("RSI", text)

    def test_contains_current_price(self):
        signals = self._make_signals()
        text = format_signals_for_prompt("AAPL", signals)
        self.assertIn("Current Price", text)


if __name__ == "__main__":
    unittest.main()
