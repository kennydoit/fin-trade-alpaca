"""
Alpaca API client for market data and trading operations.

Wraps alpaca-py to provide:
- Fetching the list of tradable US-equity assets
- Downloading historical daily bars in batches
- Account and position queries
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from alpaca.common.exceptions import APIError
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest

logger = logging.getLogger(__name__)

# Seconds to wait between batched historical-data requests to respect rate limits
_BATCH_SLEEP_SECONDS = 0.5


class AlpacaClient:
    """High-level Alpaca client that encapsulates both data and trading operations."""

    def __init__(self, api_key: str, secret_key: str, paper: bool = True) -> None:
        self._api_key = api_key
        self._secret_key = secret_key
        self._paper = paper

        self._trading_client = TradingClient(
            api_key=api_key,
            secret_key=secret_key,
            paper=paper,
        )
        self._data_client = StockHistoricalDataClient(
            api_key=api_key,
            secret_key=secret_key,
        )

    # ------------------------------------------------------------------
    # Asset discovery
    # ------------------------------------------------------------------

    def get_tradable_assets(
        self,
        exchanges: Optional[list[str]] = None,
    ) -> list[str]:
        """Return symbols for all active, tradable US-equity assets.

        Args:
            exchanges: Optional list of exchange names to filter by
                       (e.g. ["NYSE", "NASDAQ", "ARCA"]).  When *None*
                       all exchanges are included.

        Returns:
            Sorted list of ticker symbols.
        """
        if exchanges is None:
            exchanges = ["NYSE", "NASDAQ", "ARCA"]

        request = GetAssetsRequest(
            asset_class=AssetClass.US_EQUITY,
            status=AssetStatus.ACTIVE,
        )
        assets = self._trading_client.get_all_assets(request)

        symbols: list[str] = []
        for asset in assets:
            if not asset.tradable:
                continue
            if exchanges and (asset.exchange is None or str(asset.exchange) not in exchanges):
                continue
            symbols.append(asset.symbol)

        symbols.sort()
        logger.info("Found %d tradable assets across exchanges %s", len(symbols), exchanges)
        return symbols

    # ------------------------------------------------------------------
    # Historical market data
    # ------------------------------------------------------------------

    def get_historical_bars(
        self,
        symbols: list[str],
        days: int = 90,
        batch_size: int = 100,
    ) -> dict[str, pd.DataFrame]:
        """Download daily OHLCV bars for a list of symbols.

        Symbols are requested in batches to respect API rate limits.

        Args:
            symbols:    List of ticker symbols.
            days:       Number of calendar days of history to retrieve.
            batch_size: How many symbols to request per API call.

        Returns:
            Mapping of symbol -> DataFrame with columns
            [open, high, low, close, volume, trade_count, vwap].
        """
        end = datetime.now()
        start = end - timedelta(days=days)

        result: dict[str, pd.DataFrame] = {}

        for i in range(0, len(symbols), batch_size):
            batch = symbols[i : i + batch_size]
            try:
                request = StockBarsRequest(
                    symbol_or_symbols=batch,
                    timeframe=TimeFrame.Day,
                    start=start,
                    end=end,
                )
                bars = self._data_client.get_stock_bars(request)
                # bars is a BarSet; convert to dict[str, DataFrame]
                bars_df = bars.df
                if bars_df.empty:
                    continue

                # bars_df has a MultiIndex (symbol, timestamp)
                for symbol in bars_df.index.get_level_values("symbol").unique():
                    sym_df = bars_df.xs(symbol, level="symbol").copy()
                    sym_df.index.name = "timestamp"
                    sym_df.reset_index(inplace=True)
                    result[symbol] = sym_df

                logger.debug(
                    "Fetched bars for batch %d–%d (%d symbols returned)",
                    i,
                    i + len(batch),
                    len(result),
                )
            except (APIError, OSError, ValueError) as exc:
                # A single batch failure must not abort the entire scan —
                # continue with the remaining batches.
                logger.warning("Error fetching bars for batch starting at %d: %s", i, exc)

            if i + batch_size < len(symbols):
                time.sleep(_BATCH_SLEEP_SECONDS)

        logger.info("Retrieved historical bars for %d symbols", len(result))
        return result

    # ------------------------------------------------------------------
    # Account and positions
    # ------------------------------------------------------------------

    def get_account(self) -> dict:
        """Return account details as a plain dict."""
        account = self._trading_client.get_account()
        return {
            "id": str(account.id),
            "equity": float(account.equity or 0),
            "buying_power": float(account.buying_power or 0),
            "cash": float(account.cash or 0),
            "portfolio_value": float(account.portfolio_value or 0),
            "pattern_day_trader": account.pattern_day_trader,
            "trading_blocked": account.trading_blocked,
            "account_blocked": account.account_blocked,
        }

    def get_positions(self) -> dict[str, dict]:
        """Return current open positions keyed by symbol."""
        positions = self._trading_client.get_all_positions()
        return {
            pos.symbol: {
                "qty": float(pos.qty or 0),
                "avg_entry_price": float(pos.avg_entry_price or 0),
                "market_value": float(pos.market_value or 0),
                "unrealized_pl": float(pos.unrealized_pl or 0),
            }
            for pos in positions
        }

    @property
    def trading_client(self) -> TradingClient:
        """Expose the underlying TradingClient for the trader module."""
        return self._trading_client
