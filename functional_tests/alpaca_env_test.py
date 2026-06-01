import os
from alpaca.data.historical import StockHistoricalDataClient

# Load API credentials from environment variables
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET")

if not ALPACA_API_KEY or not ALPACA_API_SECRET:
    raise ValueError("Please set ALPACA_API_KEY and ALPACA_API_SECRET as environment variables.")

# Initialize Alpaca client for paper trading (default endpoint)
client = StockHistoricalDataClient(
    api_key=ALPACA_API_KEY,
    secret_key=ALPACA_API_SECRET,
)

print("Alpaca client initialized for paper trading.")
