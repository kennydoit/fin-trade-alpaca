import yfinance as yf
import inspect

print('yfinance version:', getattr(yf, '__version__', 'unknown'))
EQ = getattr(yf, 'EquityQuery', None)
print('EquityQuery in yfinance:', EQ)
if EQ is not None:
    try:
        sig = inspect.signature(EQ)
        print('EquityQuery signature:', sig)
    except Exception as e:
        print('Could not get signature:', e)
    doc = EQ.__doc__
    if doc:
        print('\nDoc snippet:\n', doc[:800])
else:
    try:
        import yfinance.screener as s
        print('yfinance.screener module:', dir(s)[:200])
    except Exception as e:
        print('Could not import yfinance.screener:', e)
