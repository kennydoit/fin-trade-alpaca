"""
SEC fundamental data queries against a Snowflake table.

The expected schema for the fundamentals table is::

    TICKER           VARCHAR   -- stock ticker symbol
    PERIOD_OF_REPORT DATE      -- fiscal period end date
    REVENUE          FLOAT     -- total revenue
    NET_INCOME       FLOAT     -- net income / (loss)
    EPS              FLOAT     -- diluted earnings per share
    TOTAL_ASSETS     FLOAT     -- total assets
    TOTAL_LIABILITIES FLOAT    -- total liabilities
    OPERATING_CASH_FLOW FLOAT  -- operating cash flow

All columns except TICKER and PERIOD_OF_REPORT are treated as
optional — missing values are returned as None.

The table name is fully-qualified and configurable via the
SEC_FILINGS_TABLE environment variable.
"""

import logging
import re
from typing import Any, Optional

import snowflake.connector
from fundamentals.client import SnowflakeClient

logger = logging.getLogger(__name__)

# Columns we attempt to retrieve; missing columns will be None in results
_FUNDAMENTAL_COLUMNS = [
    "TICKER",
    "PERIOD_OF_REPORT",
    "REVENUE",
    "NET_INCOME",
    "EPS",
    "TOTAL_ASSETS",
    "TOTAL_LIABILITIES",
    "OPERATING_CASH_FLOW",
]

# Only allow table names matching <identifier>.<identifier>.<identifier>
# (letters, digits, underscores) to prevent SQL injection via the table parameter.
_TABLE_NAME_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*){1,2}$")


def _validate_table_name(table: str) -> None:
    """Raise ValueError if *table* does not match the expected safe pattern."""
    if not _TABLE_NAME_RE.match(table):
        raise ValueError(
            f"Invalid table name '{table}'. "
            "Expected format: DATABASE.SCHEMA.TABLE (alphanumeric/underscore identifiers)."
        )


def get_fundamentals(
    client: SnowflakeClient,
    symbols: list[str],
    table: str,
) -> dict[str, dict[str, Any]]:
    """Retrieve the most recent SEC fundamentals for each symbol.

    For each ticker the *latest* row by PERIOD_OF_REPORT is returned.

    Args:
        client:  An open :class:`SnowflakeClient` instance.
        symbols: List of ticker symbols to look up.
        table:   Fully-qualified table name (e.g. ``MY_DB.SEC.FUNDAMENTALS``).

    Returns:
        Mapping of symbol → fundamentals dict.  Symbols without data are
        omitted from the result.
    """
    if not symbols:
        return {}

    _validate_table_name(table)

    # Build a safe IN-list using positional bind variables
    placeholders = ", ".join([f"%(sym_{i})s" for i in range(len(symbols))])
    params: dict[str, Any] = {f"sym_{i}": sym for i, sym in enumerate(symbols)}

    query = f"""
        SELECT
            TICKER,
            PERIOD_OF_REPORT,
            TRY_CAST(REVENUE           AS FLOAT) AS REVENUE,
            TRY_CAST(NET_INCOME        AS FLOAT) AS NET_INCOME,
            TRY_CAST(EPS               AS FLOAT) AS EPS,
            TRY_CAST(TOTAL_ASSETS      AS FLOAT) AS TOTAL_ASSETS,
            TRY_CAST(TOTAL_LIABILITIES AS FLOAT) AS TOTAL_LIABILITIES,
            TRY_CAST(OPERATING_CASH_FLOW AS FLOAT) AS OPERATING_CASH_FLOW
        FROM {table}
        WHERE TICKER IN ({placeholders})
        QUALIFY ROW_NUMBER() OVER (PARTITION BY TICKER ORDER BY PERIOD_OF_REPORT DESC) = 1
    """  # nosec B608 — table is controlled by the application config, not user input

    try:
        rows = client.execute_query(query, params)
    except snowflake.connector.Error as exc:
        logger.error("Failed to query SEC fundamentals: %s", exc)
        return {}

    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = row.get("TICKER") or row.get("ticker")
        if ticker:
            result[str(ticker).upper()] = {k.lower(): v for k, v in row.items()}

    logger.info(
        "Retrieved SEC fundamentals for %d of %d requested symbols",
        len(result),
        len(symbols),
    )
    return result


def format_fundamentals_for_prompt(fundamentals: dict[str, Any]) -> str:
    """Format a fundamentals dict as a human-readable block for an LLM prompt.

    Args:
        fundamentals: Dict returned as a value in :func:`get_fundamentals`.

    Returns:
        Multi-line string suitable for inclusion in a Cortex prompt.
    """

    def fmt(val: Optional[float], prefix: str = "$") -> str:
        if val is None:
            return "N/A"
        if abs(val) >= 1_000_000_000:
            return f"{prefix}{val / 1_000_000_000:.2f}B"
        if abs(val) >= 1_000_000:
            return f"{prefix}{val / 1_000_000:.2f}M"
        return f"{prefix}{val:.2f}"

    period = fundamentals.get("period_of_report", "N/A")
    return (
        f"  Period: {period}\n"
        f"  Revenue: {fmt(fundamentals.get('revenue'))}\n"
        f"  Net Income: {fmt(fundamentals.get('net_income'))}\n"
        f"  EPS: {fmt(fundamentals.get('eps'), prefix='$')}\n"
        f"  Total Assets: {fmt(fundamentals.get('total_assets'))}\n"
        f"  Total Liabilities: {fmt(fundamentals.get('total_liabilities'))}\n"
        f"  Operating Cash Flow: {fmt(fundamentals.get('operating_cash_flow'))}\n"
    )
