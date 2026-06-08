import pandas as pd
from pathlib import Path

p = Path('reports/screener_results/predictions_20260607.csv')
if not p.exists():
    print('Predictions file not found:', p)
    raise SystemExit(1)

df = pd.read_csv(p)
# compute average of ret_1d, ret_3d, ret_5d ignoring NaNs
ret_cols = ['ret_1d','ret_3d','ret_5d']
existing = [c for c in ret_cols if c in df.columns]
if not existing:
    print('No return columns found in', p)
    raise SystemExit(1)

# row-wise mean over available columns
df['avg_ret'] = df[existing].mean(axis=1)
# sort descending
df_sorted = df.sort_values('avg_ret', ascending=False).reset_index(drop=True)
# overwrite file (create backup)
backup = p.with_suffix('.unsorted.csv')
if not backup.exists():
    p.replace(backup)
    # restore path variable to original name
    backup.rename(p)
# Actually we want to preserve original; instead write sorted to same path and also save backup
# We'll write backup separately to avoid accidental double-rename

# Save backup
backup = p.with_name(p.stem + '.unsorted.csv')
if not backup.exists():
    df.to_csv(backup, index=False)
# overwrite with sorted
df_sorted.to_csv(p, index=False)

print('Wrote sorted predictions to', p)
print('\nTop 10 by avg_ret:')
print(df_sorted[['symbol','date','avg_ret']].head(10).to_string(index=False))
