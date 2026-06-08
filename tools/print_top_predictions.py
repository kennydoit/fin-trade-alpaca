import pandas as pd
p='reports/screener_results/predictions_20260607.csv'
df=pd.read_csv(p)
print('File:',p)
print(df[['symbol','pred_ret']].head(10).to_string(index=False))
