
# Quick yfinance exploratory script (moved from sandbox)
import yfinance as yf

aapl = yf.Ticker("AAPL")
print(aapl.revenue_estimate)

aiq = yf.Ticker('AIQ').funds_data
