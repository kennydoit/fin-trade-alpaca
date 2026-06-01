import yfinance as yf
from yfinance import EquityQuery

# 1. Build a custom query for tech/high-growth profiles
# You can leverage pre-defined screeners or build conditions using EquityQuery
q = EquityQuery('and', [
    EquityQuery('eq', ['region', 'us']),
    EquityQuery('gt', ['intradaymarketcap', 2000000000]), # Mid/Large Caps for liquidity
    EquityQuery('gt', ['percentchange', 1.5])           # Capturing recent momentum
])

# Execute the screen
screen_results = yf.screen(q, sortField='percentchange', sortAsc=False)
potential_tickers = [result['ticker'] for result in screen_results['quotes']]
print(f"Screened Tickers: {potential_tickers}")