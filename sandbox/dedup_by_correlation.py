"""Remove lower-ranked symbols that are highly correlated with higher-ranked ones.

Greedy algorithm:
- Read a ranked CSV (must contain `symbol` and `rank`).
- Download historical adjusted close prices for the symbols (default 3mo daily).
- Compute daily returns and pairwise Pearson correlations.
- Iterate symbols in ascending `rank` (best first). Keep a symbol if its
  correlation with all already-kept symbols is below the threshold; otherwise
  mark it as dropped and record which higher-ranked symbol caused the drop.

Outputs:
- `<infile>_deduped.csv` containing only kept symbols.
- `<infile>_with_flags.csv` original rows plus `kept` and `dropped_due_to`.

Usage:
  python sandbox/dedup_by_correlation.py --in <ranked.csv> --threshold 0.85
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf


def safe_symbols_from_csv(path: Path) -> List[str]:
    df = pd.read_csv(path)
    if "symbol" not in df.columns:
        # try ticker
        if "ticker" in df.columns:
            df = df.rename(columns={"ticker": "symbol"})
        else:
            raise SystemExit("Input CSV must contain a 'symbol' column")
    # preserve rank if present
    return df


def download_prices(symbols: List[str], period: str = "3mo", interval: str = "1d") -> pd.DataFrame:
    # yfinance allows multi-ticker download
    data = yf.download(symbols, period=period, interval=interval, progress=False, threads=True)
    # data['Adj Close'] may be a DataFrame (multi) or Series (single)
    if isinstance(data, tuple):
        # older yfinance may return (data, info)
        data = data[0]
    if "Adj Close" in data:
        adj = data["Adj Close"]
    else:
        # fallback to Close
        adj = data["Close"]
    # ensure DataFrame with columns for each symbol
    if isinstance(adj, pd.Series):
        adj = adj.to_frame()
    return adj


def compute_returns(adj: pd.DataFrame, transform: str = "NONE") -> pd.DataFrame:
    # transform: NONE -> simple pct_change; LOG -> log returns
    if transform == "LOG":
        # log returns: diff of logs
        returns = adj.apply(lambda col: col.dropna()).apply(lambda s: (s.apply(lambda x: float(x)))).apply(lambda s: pd.Series(pd.np.log(s))).diff().dropna(how="all")
        # The above ensures type conversion; fallback to numpy implementation if available
        try:
            import numpy as np

            returns = np.log(adj).diff().dropna(how="all")
            returns.columns = adj.columns
        except Exception:
            pass
    else:
        returns = adj.pct_change().dropna(how="all")
    return returns


def greedy_dedup(symbols: List[str], corr: pd.DataFrame, threshold: float) -> Dict[str, Optional[str]]:
    kept: List[str] = []
    dropped_due_to: Dict[str, Optional[str]] = {}
    for s in symbols:
        dropped = False
        for k in kept:
            # if either symbol missing in corr, skip
            if s not in corr.columns or k not in corr.columns:
                continue
            val = corr.at[s, k]
            if abs(val) >= threshold:
                dropped = True
                dropped_due_to[s] = k
                break
        if not dropped:
            kept.append(s)
            dropped_due_to[s] = None
    return dropped_due_to


def cluster_dedup(symbols: List[str], corr: pd.DataFrame, threshold: float) -> Dict[str, Optional[str]]:
    """Cluster symbols using hierarchical clustering on distance = 1 - abs(corr).

    Then assign cluster labels and keep the highest-ranked symbol per cluster.
    """
    try:
        from scipy.cluster.hierarchy import linkage, fcluster
        import numpy as np
    except Exception:
        raise SystemExit("scipy is required for CLUSTER method; install scipy and retry")

    # build condensed distance matrix from 1 - abs(corr)
    symbols_present = [s for s in symbols if s in corr.columns]
    if not symbols_present:
        return {s: None for s in symbols}
    sub = corr.loc[symbols_present, symbols_present].fillna(0.0)
    # distance
    dist = 1.0 - sub.abs()
    # convert to condensed form required by linkage
    mat = dist.values
    # ensure symmetric
    # convert to condensed
    triu_idx = np.triu_indices_from(mat, k=1)
    condensed = mat[triu_idx]
    Z = linkage(condensed, method="average")
    # form flat clusters: threshold on distance
    clusters = fcluster(Z, t=1.0 - threshold, criterion="distance")
    # clusters is array aligned with symbols_present
    cluster_map = dict(zip(symbols_present, clusters))

    dropped_due_to: Dict[str, Optional[str]] = {}
    kept_clusters = set()
    for s in symbols:
        if s not in cluster_map:
            # no data: keep
            dropped_due_to[s] = None
            continue
        c = cluster_map[s]
        if c in kept_clusters:
            # find representative kept symbol in that cluster (the higher-ranked one)
            # we can find a kept symbol in previous iteration
            # search earlier symbols for one with same cluster
            rep = next((x for x in symbols if x in cluster_map and cluster_map[x] == c and x != s and (cluster_map[x] == c) and x != s and (x == x)), None)
            dropped_due_to[s] = rep
        else:
            kept_clusters.add(c)
            dropped_due_to[s] = None

    return dropped_due_to


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="infile", required=True)
    p.add_argument("--threshold", type=float, default=0.85, help="correlation threshold (abs) to consider symbols as duplicates")
    p.add_argument("--period", default="3mo", help="price history period for correlation (e.g., 1mo,3mo,6mo)")
    p.add_argument("--interval", default="1d", help="price history interval")
    p.add_argument("--method", choices=["CORRELATION", "CLUSTER"], default="CORRELATION", help="dedup method")
    p.add_argument("--window", type=int, default=None, help="use only the latest N trading rows from downloaded prices (overrides period when provided)")
    p.add_argument("--transform", choices=["NONE", "LOG"], default="NONE", help="return transformation to compute (NONE or LOG)")
    args = p.parse_args()

    inp = Path(args.infile)
    assert inp.exists(), f"Input not found: {inp}"

    df = pd.read_csv(inp)
    if "symbol" not in df.columns and "ticker" in df.columns:
        df = df.rename(columns={"ticker": "symbol"})
    if "symbol" not in df.columns:
        raise SystemExit("Input CSV must contain 'symbol' or 'ticker' column")

    # preserve order by rank if present, else by current order
    if "rank" in df.columns:
        df = df.sort_values("rank")

    symbols = [str(x).strip() for x in df["symbol"].tolist()]

    print(f"Downloading price history for {len(symbols)} symbols (period={args.period})...")
    adj = download_prices(symbols, period=args.period, interval=args.interval)
    if adj.empty:
        raise SystemExit("Failed to download price data or no data returned")

    # optionally trim to the latest N rows (window)
    if args.window is not None:
        adj = adj.tail(args.window)

    # compute returns with specified transform
    returns = compute_returns(adj, transform=("LOG" if args.transform == "LOG" else "NONE"))
    # align columns to symbols (some tickers may be missing from data)
    corr = returns.corr()

    print(f"Dedup method={args.method}, threshold={args.threshold}, transform={args.transform}, window={args.window}")
    if args.method == "CORRELATION":
        print("Computing greedy deduplication (correlation)...")
        dropped_map = greedy_dedup(symbols, corr, args.threshold)
    else:
        print("Computing cluster-based deduplication...")
        dropped_map = cluster_dedup(symbols, corr, args.threshold)

    # write out flagged CSV
    out_flags = inp.with_name(inp.stem + "_with_flags.csv")
    df_out = df.copy()
    df_out["kept"] = df_out["symbol"].map(lambda s: dropped_map.get(s) is None)
    df_out["dropped_due_to"] = df_out["symbol"].map(lambda s: dropped_map.get(s))
    df_out.to_csv(out_flags, index=False)
    print(f"Wrote flags to {out_flags}")

    # write deduped CSV (kept only)
    out_dedup = inp.with_name(inp.stem + "_deduped.csv")
    df_out[df_out["kept"]].to_csv(out_dedup, index=False)
    print(f"Wrote deduped list to {out_dedup} (kept {df_out['kept'].sum()} / {len(df_out)})")
    # write dropped-only CSV
    out_dropped = inp.with_name(inp.stem + "_dropped.csv")
    df_out[~df_out["kept"]].to_csv(out_dropped, index=False)
    print(f"Wrote dropped list to {out_dropped} (dropped {(~df_out['kept']).sum()} )")


if __name__ == "__main__":
    main()
