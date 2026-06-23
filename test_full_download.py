import pandas as pd
import yfinance as yf

df = pd.read_csv('reports/screener_results/yfinance_screener_results_with_metrics_tech_health_75cap_20260616_ranked_with_flags_20260616.csv')
symbols = df['symbol'].tolist()

print(f'Total symbols: {len(symbols)}')
print(f'First 10: {symbols[:10]}')
print('Downloading all 198 symbols...')

data = yf.download(symbols, period='190d', interval='1d', progress=True, threads=True)

print(f'\nDownloaded shape: {data.shape}')
print(f'Has Close? {"Close" in data}')

if 'Close' in data:
    close_df = data['Close']
    print(f'Close shape: {close_df.shape}')
    print(f'Number of symbol columns: {len(close_df.columns)}')
    print(f'Columns with data: {close_df.columns.tolist()[:10]}...')
    
    # Check for any completely empty columns
    empty_cols = [col for col in close_df.columns if close_df[col].isna().all()]
    print(f'\nCompletely empty columns: {len(empty_cols)}')
    if empty_cols:
        print(f'Examples: {empty_cols[:10]}')
