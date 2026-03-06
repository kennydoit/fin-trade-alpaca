"""
fin-trade-alpaca — main trading loop.

Workflow
--------
1.  Validate configuration and connect to Alpaca.
2.  Fetch all tradable US-equity symbols.
3.  Download 90 days of daily bars in batches.
4.  Calculate RSI / MA / MACD signals for each symbol.
5.  Score and rank symbols; keep the top N candidates.
6.  (Optional) If Snowflake is configured:
      a. Query SEC fundamental data for the top candidates.
      b. For each candidate call Snowflake Cortex for a BUY / HOLD / SELL decision.
      c. Keep only AI-approved BUY signals.
7.  Execute market-buy orders for approved symbols that are not
    already held as open positions.
"""

import logging
import sys

import snowflake.connector
from alpaca.common.exceptions import APIError

import config
from trading.client import AlpacaClient
from trading.trader import AlpacaTrader
from signals.screener import select_top_symbols
from signals.technical import calculate_signals, format_signals_for_prompt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def run_trading_cycle() -> None:
    """Execute a single end-to-end trading cycle."""

    # ------------------------------------------------------------------ 1. Config
    try:
        config.validate_alpaca_config()
    except ValueError as exc:
        logger.error("Configuration error: %s", exc)
        sys.exit(1)

    # ------------------------------------------------------------------ 2. Alpaca
    alpaca = AlpacaClient(
        api_key=config.ALPACA_API_KEY,
        secret_key=config.ALPACA_SECRET_KEY,
        paper=config.ALPACA_PAPER,
    )
    account = alpaca.get_account()
    if account.get("trading_blocked") or account.get("account_blocked"):
        logger.error("Alpaca account is blocked — aborting.")
        sys.exit(1)
    logger.info(
        "Alpaca account: equity=$%.2f  buying_power=$%.2f",
        account["equity"],
        account["buying_power"],
    )

    # ------------------------------------------------------------------ 3. Symbols
    symbols = alpaca.get_tradable_assets()
    if not symbols:
        logger.error("No tradable assets found — aborting.")
        sys.exit(1)

    # ------------------------------------------------------------------ 4. Historical bars + signals
    bars = alpaca.get_historical_bars(
        symbols=symbols,
        days=config.LOOKBACK_DAYS,
        batch_size=config.SYMBOL_BATCH_SIZE,
    )

    symbol_signals: dict = {}
    for symbol, df in bars.items():
        signals = calculate_signals(df, rsi_period=config.RSI_PERIOD)
        if signals is not None:
            symbol_signals[symbol] = signals

    logger.info("Computed signals for %d symbols", len(symbol_signals))

    # ------------------------------------------------------------------ 5. Screener
    top_symbols = select_top_symbols(symbol_signals, top_n=config.TOP_N_SYMBOLS)
    if not top_symbols:
        logger.info("No symbols passed the screener — nothing to trade.")
        return

    logger.info("Top %d candidates: %s", len(top_symbols), ", ".join(top_symbols))

    # ------------------------------------------------------------------ 6. AI analysis (optional)
    approved_symbols = top_symbols

    if config.is_snowflake_configured():
        approved_symbols = _run_ai_analysis(top_symbols, symbol_signals)
    else:
        logger.info("Snowflake not configured — skipping AI analysis, using all top candidates.")

    if not approved_symbols:
        logger.info("No symbols approved for trading after AI analysis.")
        return

    logger.info("AI-approved symbols: %s", ", ".join(approved_symbols))

    # ------------------------------------------------------------------ 7. Execute trades
    existing_positions = alpaca.get_positions()
    trader = AlpacaTrader(alpaca.trading_client)

    orders_placed = 0
    for symbol in approved_symbols:
        if symbol in existing_positions:
            logger.info("Already holding %s — skipping.", symbol)
            continue

        current_price = symbol_signals[symbol]["current_price"]
        qty = trader.calculate_position_size(current_price, config.MAX_POSITION_SIZE)

        if qty == 0:
            logger.info("Price $%.2f exceeds max position size for %s — skipping.", current_price, symbol)
            continue

        try:
            order = trader.submit_buy_order(symbol, qty)
            logger.info(
                "Order placed: %s x%d @ ~$%.2f  (order_id=%s)",
                symbol,
                qty,
                current_price,
                order["id"],
            )
            orders_placed += 1
        except (APIError, ValueError, OSError) as exc:
            logger.error("Could not place order for %s: %s", symbol, exc)

    logger.info("Trading cycle complete — %d order(s) placed.", orders_placed)


def _run_ai_analysis(
    top_symbols: list[str],
    symbol_signals: dict,
) -> list[str]:
    """Query Snowflake for SEC fundamentals and obtain Cortex recommendations.

    Args:
        top_symbols:    Ranked list of candidate symbols.
        symbol_signals: Signal dicts keyed by symbol.

    Returns:
        Sub-list of symbols with a BUY recommendation.
    """
    # Import here to avoid hard dependency when Snowflake is not configured
    from fundamentals.client import SnowflakeClient  # noqa: PLC0415
    from fundamentals.cortex import analyze_trade  # noqa: PLC0415
    from fundamentals.sec_filings import (  # noqa: PLC0415
        format_fundamentals_for_prompt,
        get_fundamentals,
    )

    approved: list[str] = []

    try:
        with SnowflakeClient(
            account=config.SNOWFLAKE_ACCOUNT,
            user=config.SNOWFLAKE_USER,
            password=config.SNOWFLAKE_PASSWORD,
            database=config.SNOWFLAKE_DATABASE,
            schema=config.SNOWFLAKE_SCHEMA,
            warehouse=config.SNOWFLAKE_WAREHOUSE,
            role=config.SNOWFLAKE_ROLE,
        ) as sf_client:
            fundamentals = get_fundamentals(
                client=sf_client,
                symbols=top_symbols,
                table=config.SEC_FILINGS_TABLE,
            )

            for symbol in top_symbols:
                sigs = symbol_signals[symbol]
                tech_block = format_signals_for_prompt(symbol, sigs)
                fund_data = fundamentals.get(symbol, {})
                fund_block = format_fundamentals_for_prompt(fund_data) if fund_data else "  No SEC data available.\n"

                result = analyze_trade(
                    client=sf_client,
                    symbol=symbol,
                    technical_block=tech_block,
                    fundamentals_block=fund_block,
                    model=config.CORTEX_MODEL,
                )

                if result["recommendation"] == "BUY":
                    approved.append(symbol)
                    logger.info("[%s] APPROVED — %s", symbol, result["reasoning"][:120])
                else:
                    logger.info(
                        "[%s] REJECTED (%s) — %s",
                        symbol,
                        result["recommendation"],
                        result["reasoning"][:120],
                    )

    except (snowflake.connector.Error, ValueError, OSError) as exc:
        logger.error("AI analysis step failed: %s — falling back to all top candidates.", exc)
        return top_symbols

    return approved


if __name__ == "__main__":
    run_trading_cycle()
