import argparse
import pandas as pd
import numpy as np
from pathlib import Path

def main():
    p = argparse.ArgumentParser(description='Simple backtest for prediction CSVs')
    p.add_argument('--pred-file', required=True, help='Predictions CSV file')
    p.add_argument('--horizon', type=int, default=5, choices=[1,3,5], help='Return horizon to evaluate (days)')
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument('--top-k', type=int, help='Take top k predicted symbols')
    grp.add_argument('--top-percent', type=float, help='Take top percent (0-100) of predicted symbols')
    p.add_argument('--capital', type=float, default=10000.0, help='Starting capital (for dollar allocation reporting)')
    p.add_argument('--out', help='Output CSV path (optional)')
    args = p.parse_args()

    df = pd.read_csv(args.pred_file)
    if df.empty:
        print('No rows in predictions file')
        return
    # infer prediction date from first row
    if 'date' in df.columns:
        pred_date = pd.to_datetime(df.loc[0,'date']).date()
    else:
        pred_date = pd.Timestamp.today().date()

    ret_col = f'ret_{args.horizon}d'
    if ret_col not in df.columns:
        print(f'ERROR: expected return column "{ret_col}" not found in predictions file')
        print('Available columns:', ','.join(df.columns))
        return

    df_sorted = df.sort_values('pred_ret', ascending=False).reset_index(drop=True)
    n = len(df_sorted)
    if args.top_k:
        k = min(args.top_k, n)
    else:
        k = max(1, int(np.floor(args.top_percent/100.0 * n)))
    sel = df_sorted.head(k).copy()

    # realized returns
    sel['realized'] = sel[ret_col]
    # equal weight portfolio
    sel['weight'] = 1.0/k
    # portfolio realized return (simple average of realized returns)
    port_return = (sel['weight'] * sel['realized']).sum()
    # dollar P&L
    pl = args.capital * port_return

    # diagnostics
    mean_pred = sel['pred_ret'].mean()
    mean_real = sel['realized'].mean()
    corr = sel[['pred_ret','realized']].dropna().corr().iloc[0,1]

    print(f'Prediction date: {pred_date}')
    print(f'Selected top {k} of {n} symbols (capital ${args.capital:,.0f})')
    print(f'Mean predicted ret: {mean_pred:.6f}')
    print(f'Mean realized ret (h={args.horizon}d): {mean_real:.6f}')
    print(f'Portfolio realized return: {port_return:.6f} => P/L ${pl:,.2f}')
    print(f'Predicted vs realized correlation (pearson): {corr:.4f}')

    out_path = args.out
    if not out_path:
        out_dir = Path('reports/screener_results')
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f'backtest_{pred_date}_h{args.horizon}_top{k}.csv'
    else:
        out_path = Path(out_path)
    sel.to_csv(out_path, index=False)
    print('Wrote backtest CSV:', out_path)

if __name__ == '__main__':
    main()
