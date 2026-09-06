"""Quick script to check the next trading day using Alpaca API."""
from datetime import date, timedelta
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetCalendarRequest
from fin_trade_alpaca.env_loader import load_environment_for_mode
from src.runners.optimize_and_buy import resolve_credentials

# Load credentials for paper mode
load_environment_for_mode("paper")
creds = resolve_credentials("paper")

# Create client
client = TradingClient(
    api_key=creds.api_key,
    secret_key=creds.api_secret,
    paper=creds.paper
)

# Get calendar for next 10 days
today = date.today()
end_date = today + timedelta(days=10)

cal = client.get_calendar(GetCalendarRequest(start=today, end=end_date))

print(f"Today: {today} ({today.strftime('%A, %B %d, %Y')})")
print(f"\nNext trading days:")
for i, entry in enumerate(cal, 1):
    day_name = entry.date.strftime('%A, %B %d, %Y')
    if entry.date == today:
        print(f"  {i}. {entry.date} ({day_name}) ← TODAY")
    elif i == 1 and entry.date > today:
        print(f"  {i}. {entry.date} ({day_name}) ← NEXT TRADING DAY")
    else:
        print(f"  {i}. {entry.date} ({day_name})")
