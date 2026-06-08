import pandas as pd
import numpy as np
from pathlib import Path
p=Path('reports/screener_results/predictions_20260607.csv')
if not p.exists():
    print('Predictions file not found:',p)
    raise SystemExit(1)

df=pd.read_csv(p)
print('File:',p)
for h in [1,3,5]:
    col=f'ret_{h}d'
    if col not in df.columns:
        continue
    common=df[['symbol','pred_ret',col]].dropna()
    n=len(common)
    mean_pred=common['pred_ret'].mean()
    mean_real=common[col].mean()
    median_pred=common['pred_ret'].median()
    median_real=common[col].median()
    corr=common[['pred_ret',col]].corr().iloc[0,1]
    k=max(1,int(np.floor(0.1*n)))
    top=common.sort_values('pred_ret',ascending=False).head(k)
    bottom=common.sort_values('pred_ret',ascending=True).head(k)
    print(f'--- horizon {h}d ---')
    print(f'rows={n}, mean_pred={mean_pred:.6f}, mean_real={mean_real:.6f}, median_pred={median_pred:.6f}, median_real={median_real:.6f}, corr={corr:.4f}')
    print(f'top {k} mean_pred={top.pred_ret.mean():.6f}, mean_real={top[col].mean():.6f}')
    print(f'bottom {k} mean_pred={bottom.pred_ret.mean():.6f}, mean_real={bottom[col].mean():.6f}')

# quick distribution counts
print('\nPredicted ret sign counts:')
print((df['pred_ret']>0).sum(), 'positive,', (df['pred_ret']<0).sum(), 'negative')
print('\nRealized 5d sign counts:')
print((df['ret_5d']>0).sum(), 'positive,', (df['ret_5d']<0).sum(), 'negative')
