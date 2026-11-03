"""
Unit tests for alpaca.trader — position sizing and order submission.
"""

import unittest
from unittest.mock import MagicMock, patch

from trading.trader import AlpacaTrader


def _make_trader():
    """Return an AlpacaTrader with a mocked TradingClient."""
    mock_client = MagicMock()
    return AlpacaTrader(mock_client), mock_client


class TestCalculatePositionSize(unittest.TestCase):

    def setUp(self):
        self.trader, _ = _make_trader()

    def test_exact_divisible(self):
        # $1000 / $50 = 20 shares
        self.assertEqual(self.trader.calculate_position_size(50.0, 1000.0), 20)

    def test_rounds_down(self):
        # $1000 / $30 = 33.33 → 33 shares
        self.assertEqual(self.trader.calculate_position_size(30.0, 1000.0), 33)

    def test_price_exceeds_max(self):
        # $2000 price, $1000 limit → 0 shares
        self.assertEqual(self.trader.calculate_position_size(2000.0, 1000.0), 0)

    def test_zero_price_returns_zero(self):
        self.assertEqual(self.trader.calculate_position_size(0.0, 1000.0), 0)

    def test_zero_max_returns_zero(self):
        self.assertEqual(self.trader.calculate_position_size(50.0, 0.0), 0)

    def test_negative_price_returns_zero(self):
        self.assertEqual(self.trader.calculate_position_size(-10.0, 1000.0), 0)

    def test_negative_max_returns_zero(self):
        self.assertEqual(self.trader.calculate_position_size(50.0, -500.0), 0)

    def test_penny_stock(self):
        # $1000 / $0.50 = 2000 shares
        self.assertEqual(self.trader.calculate_position_size(0.5, 1000.0), 2000)

    def test_large_position_limit(self):
        self.assertEqual(self.trader.calculate_position_size(100.0, 10_000.0), 100)


class TestSubmitBuyOrder(unittest.TestCase):

    def setUp(self):
        self.trader, self.mock_client = _make_trader()

    def _mock_order(self, symbol="AAPL", qty=10, order_id="abc123"):
        order = MagicMock()
        order.id = order_id
        order.symbol = symbol
        order.qty = qty
        order.status = "accepted"
        order.side = "buy"
        return order

    def test_returns_order_dict(self):
        self.mock_client.submit_order.return_value = self._mock_order()
        result = self.trader.submit_buy_order("AAPL", 10)
        self.assertEqual(result["symbol"], "AAPL")
        self.assertEqual(result["side"], "buy")
        self.assertEqual(result["status"], "accepted")

    def test_calls_trading_client_once(self):
        self.mock_client.submit_order.return_value = self._mock_order()
        self.trader.submit_buy_order("AAPL", 10)
        self.mock_client.submit_order.assert_called_once()

    def test_raises_for_zero_qty(self):
        with self.assertRaises(ValueError):
            self.trader.submit_buy_order("AAPL", 0)

    def test_raises_for_negative_qty(self):
        with self.assertRaises(ValueError):
            self.trader.submit_buy_order("AAPL", -5)

    def test_propagates_api_exception(self):
        self.mock_client.submit_order.side_effect = RuntimeError("API error")
        with self.assertRaises(RuntimeError):
            self.trader.submit_buy_order("AAPL", 5)

    def test_order_id_in_result(self):
        self.mock_client.submit_order.return_value = self._mock_order(order_id="xyz789")
        result = self.trader.submit_buy_order("AAPL", 10)
        self.assertEqual(result["id"], "xyz789")


class TestSubmitSellOrder(unittest.TestCase):

    def setUp(self):
        self.trader, self.mock_client = _make_trader()

    def _mock_order(self, symbol="AAPL", qty=5, order_id="sell001"):
        order = MagicMock()
        order.id = order_id
        order.symbol = symbol
        order.qty = qty
        order.status = "accepted"
        order.side = "sell"
        return order

    def test_returns_sell_order_dict(self):
        self.mock_client.submit_order.return_value = self._mock_order()
        result = self.trader.submit_sell_order("AAPL", 5)
        self.assertEqual(result["side"], "sell")

    def test_raises_for_zero_qty(self):
        with self.assertRaises(ValueError):
            self.trader.submit_sell_order("AAPL", 0)

    def test_raises_for_negative_qty(self):
        with self.assertRaises(ValueError):
            self.trader.submit_sell_order("AAPL", -1)


class TestClosePosition(unittest.TestCase):

    def setUp(self):
        self.trader, self.mock_client = _make_trader()

    def _mock_order(self, symbol="AAPL"):
        order = MagicMock()
        order.id = "close001"
        order.symbol = symbol
        order.qty = 10
        order.status = "accepted"
        order.side = "sell"
        return order

    def test_returns_order_dict_on_success(self):
        self.mock_client.close_position.return_value = self._mock_order()
        result = self.trader.close_position("AAPL")
        self.assertIn("id", result)
        self.assertEqual(result["symbol"], "AAPL")

    def test_returns_empty_dict_on_exception(self):
        self.mock_client.close_position.side_effect = RuntimeError("not found")
        result = self.trader.close_position("AAPL")
        self.assertEqual(result, {})


class TestCloseAllPositions(unittest.TestCase):

    def setUp(self):
        self.trader, self.mock_client = _make_trader()

    def _mock_orders(self, symbols):
        orders = []
        for sym in symbols:
            order = MagicMock()
            order.id = f"order_{sym}"
            order.symbol = sym
            order.qty = 5
            order.status = "accepted"
            order.side = "sell"
            orders.append(order)
        return orders

    def test_returns_list_of_dicts(self):
        self.mock_client.close_all_positions.return_value = self._mock_orders(["AAPL", "MSFT"])
        results = self.trader.close_all_positions()
        self.assertEqual(len(results), 2)
        self.assertIn("id", results[0])

    def test_empty_positions(self):
        self.mock_client.close_all_positions.return_value = []
        results = self.trader.close_all_positions()
        self.assertEqual(results, [])

    def test_propagates_api_exception(self):
        self.mock_client.close_all_positions.side_effect = RuntimeError("blocked")
        with self.assertRaises(RuntimeError):
            self.trader.close_all_positions()


if __name__ == "__main__":
    unittest.main()
