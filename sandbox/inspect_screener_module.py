import yfinance.screener as s
import inspect
print('module yfinance.screener attributes:')
for name in sorted(dir(s)):
    obj = getattr(s, name)
    if callable(obj):
        try:
            sig = inspect.signature(obj)
        except Exception:
            sig = '<signature unavailable>'
        print(name, '->', sig)
    else:
        print(name)
