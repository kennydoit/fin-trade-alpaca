
# This script is a tour of the yfinance library, which is a popular library for accessing financial data from Yahoo Finance. It is not meant to be a comprehensive tutorial, but rather a quick overview of some of the most commonly used features of the library.
# The first function we will be using is Ticker, which allows us to access data for a specific stock. We will be using the ticker for Apple Inc. (AAPL) in this example.
# The first thing we will do is import the yfinance library and create a Ticker object for Apple Inc. We can then use this object to access various types of data about the stock, such as its historical price data, dividends, and financial statements.  


import yfinance as yf

aapl = yf.Ticker("AAPL")
# print(aapl.info)

# print(aapl.recommendations_summary)
print(aapl.revenue_estimate)

aiq = yf.Ticker('AIQ').funds_data
# print(aiq.description)
# print(aiq.top_holdings)
