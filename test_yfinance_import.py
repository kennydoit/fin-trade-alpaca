import sys
from pathlib import Path

# Add src to path like the screener does
sys.path.insert(0, str(Path(__file__).resolve().parents[0] / 'src'))

# Now import yfinance like the predict_short_term.py does
import yfinance as yf

print(f'yfinance module: {yf}')
print(f'yfinance file: {yf.__file__}')
print(f'yfinance version: {yf.__version__ if hasattr(yf, "__version__") else "no version"}')
print(f'Has download? {hasattr(yf, "download")}')

if hasattr(yf, 'download'):
    print(f'\nyfinance.download: {yf.download}')
    
    # Try a simple download
    print('\nTrying to download AAPL...')
    try:
        data = yf.download(['AAPL'], period='5d', progress=False)
        print(f'Success! Shape: {data.shape}')
    except Exception as e:
        print(f'ERROR: {e}')
else:
    print('\nERROR: yfinance.download not found!')
