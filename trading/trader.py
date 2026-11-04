"""
Trade execution logic for Alpaca paper/live trading.

Provides position-size calculation and order submission.
"""

import logging
import math

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

logger = logging.getLogger(__name__)


class AlpacaTrader:
    """Submits and manages orders via the Alpaca TradingClient."""

    def __init__(self, trading_client: TradingClient) -> None:
        self._client = trading_client

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def calculate_position_size(self, price: float, max_position_size: float) -> int:
        """Calculate whole-share quantity to buy without exceeding *max_position_size*.

        Args:
            price:             Current ask/last price of the asset.
            max_position_size: Maximum dollar amount to invest in a single position.

        Returns:
            Number of whole shares (0 if price is zero or exceeds the limit).
        """
        if price <= 0 or max_position_size <= 0:
            return 0
        qty = math.floor(max_position_size / price)
        return max(qty, 0)

    # ------------------------------------------------------------------
    # Order submission
    # ------------------------------------------------------------------

    def submit_buy_order(self, symbol: str, qty: int) -> dict:
        """Submit a market buy order for *qty* shares of *symbol*.

        Args:
            symbol: Ticker symbol (e.g. "AAPL").
            qty:    Number of whole shares to buy.

        Returns:
            Dict with order details (id, symbol, qty, status).

        Raises:
            ValueError: If qty is not positive.
            Exception:  Propagates API errors after logging.
        """
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty}")

        request = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        try:
            order = self._client.submit_order(request)
            logger.info("BUY order submitted: %s x%d (id=%s)", symbol, qty, order.id)
            return {
                "id": str(order.id),
                "symbol": order.symbol,
                "qty": float(order.qty or qty),
                "side": "buy",
                "status": str(order.status),
            }
        except Exception as exc:
            logger.error("Failed to submit BUY order for %s x%d: %s", symbol, qty, exc)
            raise

    def submit_sell_order(self, symbol: str, qty: int) -> dict:
        """Submit a market sell order for *qty* shares of *symbol*.

        Args:
            symbol: Ticker symbol.
            qty:    Number of whole shares to sell.

        Returns:
            Dict with order details.

        Raises:
            ValueError: If qty is not positive.
            Exception:  Propagates API errors after logging.
        """
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty}")

        request = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        try:
            order = self._client.submit_order(request)
            logger.info("SELL order submitted: %s x%d (id=%s)", symbol, qty, order.id)
            return {
                "id": str(order.id),
                "symbol": order.symbol,
                "qty": float(order.qty or qty),
                "side": "sell",
                "status": str(order.status),
            }
        except Exception as exc:
            logger.error("Failed to submit SELL order for %s x%d: %s", symbol, qty, exc)
            raise

    def close_position(self, symbol: str) -> dict:
        """Close the full open position for *symbol*.

        Args:
            symbol: Ticker symbol whose position should be liquidated.

        Returns:
            Dict with order details, or empty dict if no position exists.
        """
        try:
            order = self._client.close_position(symbol)
            logger.info("Closed position for %s (order_id=%s)", symbol, order.id)
            return {
                "id": str(order.id),
                "symbol": order.symbol,
                "qty": float(order.qty or 0),
                "side": str(order.side),
                "status": str(order.status),
            }
        except Exception as exc:
            logger.warning("Could not close position for %s: %s", symbol, exc)
            return {}

    def close_all_positions(self) -> list[dict]:
        """Close all open positions.

        Returns:
            List of order detail dicts for each closed position.
        """
        try:
            orders = self._client.close_all_positions(cancel_orders=True)
            results = []
            for order in orders:
                results.append(
                    {
                        "id": str(order.id),
                        "symbol": order.symbol,
                        "qty": float(order.qty or 0),
                        "side": str(order.side),
                        "status": str(order.status),
                    }
                )
            logger.info("Closed %d positions", len(results))
            return results
        except Exception as exc:
            logger.error("Failed to close all positions: %s", exc)
            raise
