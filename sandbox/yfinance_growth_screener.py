"""yfinance_growth_screener.py

Compute short-term growth and momentum metrics for candidates produced by the
existing `yfinance_equity_screener.screen_equities()` function.

Outputs a CSV with the original fields plus computed metrics:
- pct_1w, pct_1m, rel_volume, revenueGrowth, earningsQuarterlyGrowth, pegRatio, trailingPE

This script is designed to be run as a separate tool; defaults perform a small
smoke test (limit=20). Use `--limit` to increase.
"""
from __future__ import annotations

import argparse
import csv
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Any


def get_candidates(limit: int = 200, **kwargs) -> List[Dict[str, Any]]:
    """Return candidate symbols by reusing the existing screener."""
    try:
        from sandbox.yfinance_equity_screener import screen_equities
    except Exception:
        # fallback import path if running as package
        from yfinance_equity_screener import screen_equities  # type: ignore

    return screen_equities(limit=limit, **kwargs)


def safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return float(x)
        s = str(x).replace(",", "")
        return float(s)
    except Exception:
        return None


def compute_metrics_for_symbol(symbol: str) -> Dict[str, Any]:
    """Fetch info/history for a symbol and compute metrics.

    Returns a dict of metrics (keys may be None when unavailable).
    """
    import yfinance as yf

    metrics: Dict[str, Any] = {
        "symbol": symbol,
        "pct_1w": None,
        "pct_1m": None,
        "rel_volume": None,
        "revenueGrowth": None,
        "earningsQuarterlyGrowth": None,
        "pegRatio": None,
        "trailingPE": None,
        # additional summaries
        "rec_latest": None,
        "rec_buy_count": None,
        "rec_hold_count": None,
        "rec_sell_count": None,
        "eps_trend_summary": None,
        "insider_buy_shares": None,
        "insider_sell_shares": None,
        "insider_tx_count": None,
    }

    try:
        t = yf.Ticker(symbol)
    except Exception:
        return metrics

    # fetch info
    info = {}
    try:
        info = t.info or {}
    except Exception:
        info = {}

    # trailingPE and peg if present
    metrics["trailingPE"] = safe_float(info.get("trailingPE") or info.get("trailingPERaw"))
    metrics["pegRatio"] = safe_float(info.get("pegRatio") or info.get("peg"))

    # revenue/earnings growth
    metrics["revenueGrowth"] = safe_float(info.get("revenueGrowth"))
    metrics["earningsQuarterlyGrowth"] = safe_float(info.get("earningsQuarterlyGrowth"))

    # relative volume: latest volume / averageDailyVolume3Month
    avg_vol = safe_float(info.get("averageDailyVolume3Month") or info.get("averageDailyVolume"))

    # history for momentum and latest volume
    hist = None
    try:
        hist = t.history(period="1mo", interval="1d", actions=False)
    except Exception:
        hist = None

    if hist is not None and len(hist) > 0:
        closes = list(hist["Close"].dropna())
        vols = list(hist["Volume"].dropna())
        if closes:
            # pct_1m: from first to last in the 1 month window
            try:
                first = float(closes[0])
                last = float(closes[-1])
                if first and not math.isclose(first, 0.0):
                    metrics["pct_1m"] = (last - first) / first * 100.0
            except Exception:
                metrics["pct_1m"] = None

            # pct_1w: use last 5 trading days if available
            try:
                if len(closes) >= 5:
                    first_w = float(closes[-5])
                    last = float(closes[-1])
                    if first_w and not math.isclose(first_w, 0.0):
                        metrics["pct_1w"] = (last - first_w) / first_w * 100.0
                else:
                    # fallback to earliest available within month
                    first_w = float(closes[0])
                    last = float(closes[-1])
                    if first_w and not math.isclose(first_w, 0.0):
                        metrics["pct_1w"] = (last - first_w) / first_w * 100.0
            except Exception:
                metrics["pct_1w"] = None

        # latest volume
        try:
            latest_vol = int(vols[-1]) if vols else None
        except Exception:
            latest_vol = None

        if latest_vol is not None and avg_vol is not None and avg_vol != 0:
            metrics["rel_volume"] = latest_vol / avg_vol
    else:
        # no history: try to get regularMarketVolume from info
        latest_vol = safe_float(info.get("regularMarketVolume") or info.get("volume"))
        if latest_vol is not None and avg_vol is not None and avg_vol != 0:
            metrics["rel_volume"] = latest_vol / avg_vol

    # compute peg if missing and possible: PEG = PE / (earnings_growth_percent)
    if metrics.get("pegRatio") is None:
        pe = metrics.get("trailingPE")
        eg = metrics.get("earningsQuarterlyGrowth")
        if pe is not None and eg is not None and eg > 0:
            # earningsQuarterlyGrowth is a decimal (e.g., 0.2 for 20%) — convert to percent
            try:
                metrics["pegRatio"] = float(pe) / (float(eg) * 100.0)
            except Exception:
                metrics["pegRatio"] = None

    # --- recommendations summary ---
    try:
        rec_df = None
        # yfinance exposes recommendations as attribute or via method
        if hasattr(t, "get_recommendations_summary"):
            try:
                rec_df = t.get_recommendations_summary()
            except Exception:
                rec_df = None
        if rec_df is None:
            # try recommendations attribute or callable
            attr = getattr(t, "recommendations", None)
            if callable(attr):
                try:
                    rec_df = attr()
                except Exception:
                    rec_df = None
            else:
                rec_df = attr

        if rec_df is not None:
            # try to find a text column with Buy/Hold/Sell values
            if hasattr(rec_df, "empty") and not rec_df.empty:
                # pick a candidate column
                text_col = None
                for c in rec_df.columns:
                    # sample first non-null
                    sample = rec_df[c].dropna().astype(str)
                    if not sample.empty:
                        s0 = sample.iloc[-1].lower()
                        if any(x in s0 for x in ("buy", "hold", "sell")):
                            text_col = c
                            break
                if text_col is None:
                    # fallback to first column
                    text_col = rec_df.columns[0]

                vals = rec_df[text_col].astype(str).str.lower()
                metrics["rec_latest"] = vals.dropna().iloc[-1] if not vals.dropna().empty else None
                metrics["rec_buy_count"] = int((vals.str.contains("buy")).sum())
                metrics["rec_hold_count"] = int((vals.str.contains("hold")).sum())
                metrics["rec_sell_count"] = int((vals.str.contains("sell")).sum())
    except Exception:
        pass

    # --- eps trend ---
    try:
        eps_df = None
        if hasattr(t, "get_eps_trend"):
            try:
                eps_df = t.get_eps_trend()
            except Exception:
                eps_df = None
        if eps_df is None:
            eps_df = getattr(t, "eps_trend", None) or getattr(t, "earnings_trend", None)
        if eps_df is not None and hasattr(eps_df, "empty") and not eps_df.empty:
            # summarize by taking last row as dict
            try:
                last = eps_df.dropna(how="all").iloc[-1].to_dict()
                # compact summary as key=val pairs
                metrics["eps_trend_summary"] = ";".join(f"{k}={v}" for k, v in last.items())
            except Exception:
                metrics["eps_trend_summary"] = None
    except Exception:
        pass

    # --- insider transactions ---
    try:
        ins_df = None
        if hasattr(t, "get_insider_transactions"):
            try:
                ins_df = t.get_insider_transactions()
            except Exception:
                ins_df = None
        if ins_df is None:
            ins_df = getattr(t, "insider_transactions", None)
        if ins_df is not None and hasattr(ins_df, "empty") and not ins_df.empty:
            # try to parse shares and type
            buys = 0
            sells = 0
            cnt = 0
            for _, row in ins_df.iterrows():
                cnt += 1
                # shares may be in 'shares' or 'transactionShares'
                s = None
                for k in ("shares", "transactionShares", "qty"):
                    if k in ins_df.columns:
                        try:
                            s = float(row[k])
                            break
                        except Exception:
                            s = None
                # type may be in 'type' or 'transaction'
                typ = None
                for k in ("type", "transaction", "insiderTitle"):
                    if k in ins_df.columns:
                        try:
                            typ = str(row[k]).lower()
                            break
                        except Exception:
                            typ = None
                if s is not None and typ is not None:
                    if "buy" in typ:
                        buys += s
                    elif "sell" in typ:
                        sells += s
            metrics["insider_buy_shares"] = buys if buys != 0 else None
            metrics["insider_sell_shares"] = sells if sells != 0 else None
            metrics["insider_tx_count"] = cnt
    except Exception:
        pass

    return metrics


def compute_metrics(symbols: List[str], workers: int = 6, pause: float = 0.0) -> List[Dict[str, Any]]:
    """Compute metrics for a list of symbols in parallel.

    `pause` is a small sleep between scheduling jobs to be gentle on the API.
    """
    results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(compute_metrics_for_symbol, s): s for s in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                metrics = fut.result()
            except Exception:
                metrics = {"symbol": sym}
            results.append(metrics)
            if pause:
                time.sleep(pause)
    return results


def write_combined_csv(original_rows: List[Dict[str, Any]], metrics_list: List[Dict[str, Any]], out_file: Path) -> Path:
    """Merge original rows (from screener) with computed metrics and write CSV."""
    # map metrics by symbol
    metrics_by_symbol = {m.get("symbol"): m for m in metrics_list}

    headers_base = ["symbol", "shortName", "exchange", "sector", "industry", "eodprice", "regularMarketPrice"]
    metrics_headers = ["pct_1w", "pct_1m", "rel_volume", "revenueGrowth", "earningsQuarterlyGrowth", "pegRatio", "trailingPE"]
    headers = headers_base + metrics_headers

    out_file.parent.mkdir(parents=True, exist_ok=True)
    with out_file.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for row in original_rows:
            sym = None
            if isinstance(row, dict):
                sym = row.get("symbol") or row.get("ticker")
            if not sym:
                continue
            m = metrics_by_symbol.get(sym, {})
            get = lambda d, k: (d.get(k) if isinstance(d, dict) else "")
            base_vals = [str(get(row, h) or "") for h in headers_base]
            metric_vals = []
            for h in metrics_headers:
                v = m.get(h)
                if v is None:
                    metric_vals.append("")
                else:
                    # format floats
                    if isinstance(v, float):
                        metric_vals.append(f"{v:.4f}")
                    else:
                        metric_vals.append(str(v))
            w.writerow(base_vals + metric_vals)

    return out_file


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=200, help="number of candidates to fetch (default 200)")
    p.add_argument("--sector", default=None, help="sector filter to pass to the screener (e.g., Technology)")
    p.add_argument("--out", default="sandbox/yfinance_screener_results_with_metrics.csv")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--pause", type=float, default=0.0, help="pause (s) between metric fetches to be gentle")
    p.add_argument("--max-eod-price", type=float, default=None, help="filter candidates to eodprice < value")
    p.add_argument("--candidates-file", default=None, help="path to existing candidates CSV to use instead of running screener")
    args = p.parse_args()

    # fetch candidates using existing screener or from provided CSV
    candidates = []
    if args.candidates_file:
        cf = Path(args.candidates_file)
        print(f"Loading candidates from {cf}...")
        if cf.exists():
            with cf.open("r", encoding="utf-8") as f:
                rdr = csv.DictReader(f)
                for row in rdr:
                    candidates.append(row)
        else:
            print(f"Candidates file {cf} not found; falling back to screener")

    if not candidates:
        print(f"Fetching up to {args.limit} candidates from screener...")
        candidates = get_candidates(limit=args.limit, sector=args.sector)
    # attempt to enrich base metadata (sector, industry, eodprice) if available
    try:
        from sandbox.yfinance_equity_screener import enrich_results_with_info
        candidates = enrich_results_with_info(candidates)
    except Exception:
        try:
            from yfinance_equity_screener import enrich_results_with_info  # type: ignore
            candidates = enrich_results_with_info(candidates)
        except Exception:
            pass
    symbols = []
    for r in candidates:
        if isinstance(r, dict):
            sym = r.get("symbol") or r.get("ticker")
            if sym:
                symbols.append(sym)

    # optional filter by eodprice (after enrichment)
    if args.max_eod_price is not None:
        def _eod_ok(row):
            try:
                v = row.get("eodprice") if isinstance(row, dict) else None
                if v is None:
                    return False
                return safe_float(v) is not None and safe_float(v) < float(args.max_eod_price)
            except Exception:
                return False

        before = len(symbols)
        filtered = [r for r in candidates if isinstance(r, dict) and ((r.get("symbol") or r.get("ticker")) and _eod_ok(r))]
        candidates = filtered
        symbols = [r.get("symbol") or r.get("ticker") for r in candidates]
        print(f"Filtered candidates by eodprice < {args.max_eod_price}: {before} -> {len(symbols)}")

    print(f"Computing metrics for {len(symbols)} symbols (workers={args.workers})...")
    metrics = compute_metrics(symbols, workers=args.workers, pause=args.pause)

    out_path = Path(args.out)
    write_combined_csv(candidates, metrics, out_path)
    print(f"Saved {len(symbols)} candidates with metrics to {out_path}")


if __name__ == "__main__":
    main()
