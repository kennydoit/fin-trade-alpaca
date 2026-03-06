"""
Application configuration loaded from environment variables.
Copy .env.example to .env and fill in your credentials.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Alpaca configuration
# ---------------------------------------------------------------------------
ALPACA_API_KEY: str = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY: str = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_PAPER: bool = os.getenv("ALPACA_PAPER", "true").lower() == "true"

# ---------------------------------------------------------------------------
# Snowflake configuration (all optional — system degrades gracefully)
# ---------------------------------------------------------------------------
SNOWFLAKE_ACCOUNT: str = os.getenv("SNOWFLAKE_ACCOUNT", "")
SNOWFLAKE_USER: str = os.getenv("SNOWFLAKE_USER", "")
SNOWFLAKE_PASSWORD: str = os.getenv("SNOWFLAKE_PASSWORD", "")
SNOWFLAKE_DATABASE: str = os.getenv("SNOWFLAKE_DATABASE", "")
SNOWFLAKE_SCHEMA: str = os.getenv("SNOWFLAKE_SCHEMA", "PUBLIC")
SNOWFLAKE_WAREHOUSE: str = os.getenv("SNOWFLAKE_WAREHOUSE", "")
SNOWFLAKE_ROLE: str = os.getenv("SNOWFLAKE_ROLE", "")

# Fully-qualified table name for SEC fundamentals
SEC_FILINGS_TABLE: str = os.getenv("SEC_FILINGS_TABLE", "SEC_FILINGS.PUBLIC.FUNDAMENTALS")

# Snowflake Cortex LLM model name
CORTEX_MODEL: str = os.getenv("CORTEX_MODEL", "llama3-70b")

# ---------------------------------------------------------------------------
# Trading settings
# ---------------------------------------------------------------------------
TOP_N_SYMBOLS: int = int(os.getenv("TOP_N_SYMBOLS", "20"))
MAX_POSITION_SIZE: float = float(os.getenv("MAX_POSITION_SIZE", "1000.0"))
RSI_PERIOD: int = int(os.getenv("RSI_PERIOD", "14"))
RSI_OVERSOLD: float = float(os.getenv("RSI_OVERSOLD", "35.0"))
RSI_OVERBOUGHT: float = float(os.getenv("RSI_OVERBOUGHT", "65.0"))
LOOKBACK_DAYS: int = int(os.getenv("LOOKBACK_DAYS", "90"))

# Minimum number of data points needed to calculate all indicators
MIN_DATA_POINTS: int = 60

# Maximum number of symbols to fetch historical data for in one request batch
SYMBOL_BATCH_SIZE: int = 100


def is_snowflake_configured() -> bool:
    """Return True if all required Snowflake environment variables are set."""
    return all(
        [
            SNOWFLAKE_ACCOUNT,
            SNOWFLAKE_USER,
            SNOWFLAKE_PASSWORD,
            SNOWFLAKE_DATABASE,
            SNOWFLAKE_WAREHOUSE,
        ]
    )


def validate_alpaca_config() -> None:
    """Raise ValueError if Alpaca credentials are missing."""
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        raise ValueError(
            "ALPACA_API_KEY and ALPACA_SECRET_KEY environment variables must be set. "
            "Copy .env.example to .env and fill in your Alpaca paper trading credentials."
        )
