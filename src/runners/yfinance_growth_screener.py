"""Growth screener moved into package `yfinance.screener`.

This is largely the same as the sandbox script but imports the local
screener implementation via a relative import.

See the screener README for run instructions and troubleshooting:
src/yfinance/screener/README.md
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

REPO_SRC = Path(__file__).resolve().parents[2]
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

try:
    from yfinance.screener.rank_candidates import rank_rows
    from yfinance.screener.dedup_by_correlation import dedup_csv
except Exception:
    try:
        from .rank_candidates import rank_rows
        from .dedup_by_correlation import dedup_csv
    except Exception:
        from sandbox.rank_candidates import rank_rows
        from sandbox.dedup_by_correlation import dedup_csv


def get_candidates(limit: int = 200, sectors: Optional[List[str] | str] = None, **kwargs) -> List[Dict[str, Any]]:
    """Return candidate symbols by reusing the existing screener, including multi-sector support."""
    try:
        from .yfinance_equity_screener import screen_equities
    except Exception:
        try:
            from yfinance.screener.yfinance_equity_screener import screen_equities
        except Exception:
            try:
                from sandbox.yfinance_equity_screener import screen_equities
            except Exception:
                raise

    if sectors is None:
        sectors = kwargs.pop("sector", None)

    call_kwargs = dict(kwargs)
    call_kwargs.pop("sector", None)

    normalized_sectors = []
    if isinstance(sectors, str):
        normalized_sectors = [item.strip() for item in sectors.split(",") if item.strip()]
    elif sectors:
        normalized_sectors = [str(item).strip() for item in sectors if str(item).strip()]

    if not normalized_sectors:
        return screen_equities(limit=limit, **kwargs)

    results: List[Dict[str, Any]] = []
    seen_symbols = set()
    for sector in normalized_sectors:
        if len(results) >= limit:
            break

        batch = screen_equities(limit=max(1, limit - len(results)), sector=sector, **call_kwargs)
        for row in batch:
            sym = (row.get("symbol") or row.get("ticker") or "") if isinstance(row, dict) else ""
            if not sym or sym in seen_symbols:
                continue
            seen_symbols.add(sym)
            results.append(row)
            if len(results) >= limit:
                break

    return results


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

    info = {}
    try:
        info = t.info or {}
    except Exception:
        info = {}

    metrics["trailingPE"] = safe_float(info.get("trailingPE") or info.get("trailingPERaw"))
    metrics["pegRatio"] = safe_float(info.get("pegRatio") or info.get("peg"))
    metrics["revenueGrowth"] = safe_float(info.get("revenueGrowth"))
    metrics["earningsQuarterlyGrowth"] = safe_float(info.get("earningsQuarterlyGrowth"))

    avg_vol = safe_float(info.get("averageDailyVolume3Month") or info.get("averageDailyVolume"))

    hist = None
    try:
        hist = t.history(period="1mo", interval="1d", actions=False)
    except Exception:
        hist = None

    if hist is not None and len(hist) > 0:
        closes = list(hist["Close"].dropna())
        vols = list(hist["Volume"].dropna())
        if closes:
            try:
                first = float(closes[0])
                last = float(closes[-1])
                if first and not math.isclose(first, 0.0):
                    metrics["pct_1m"] = (last - first) / first * 100.0
            except Exception:
                metrics["pct_1m"] = None

            try:
                if len(closes) >= 5:
                    first_w = float(closes[-5])
                    last = float(closes[-1])
                    if first_w and not math.isclose(first_w, 0.0):
                        metrics["pct_1w"] = (last - first_w) / first_w * 100.0
                else:
                    first_w = float(closes[0])
                    last = float(closes[-1])
                    if first_w and not math.isclose(first_w, 0.0):
                        metrics["pct_1w"] = (last - first_w) / first_w * 100.0
            except Exception:
                metrics["pct_1w"] = None

        try:
            latest_vol = int(vols[-1]) if vols else None
        except Exception:
            latest_vol = None

        if latest_vol is not None and avg_vol is not None and avg_vol != 0:
            metrics["rel_volume"] = latest_vol / avg_vol
    else:
        latest_vol = safe_float(info.get("regularMarketVolume") or info.get("volume"))
        if latest_vol is not None and avg_vol is not None and avg_vol != 0:
            metrics["rel_volume"] = latest_vol / avg_vol

    if metrics.get("pegRatio") is None:
        pe = metrics.get("trailingPE")
        eg = metrics.get("earningsQuarterlyGrowth")
        if pe is not None and eg is not None and eg > 0:
            try:
                metrics["pegRatio"] = float(pe) / (float(eg) * 100.0)
            except Exception:
                metrics["pegRatio"] = None

    try:
        rec_df = None
        if hasattr(t, "get_recommendations_summary"):
            try:
                rec_df = t.get_recommendations_summary()
            except Exception:
                rec_df = None
        if rec_df is None:
            attr = getattr(t, "recommendations", None)
            if callable(attr):
                try:
                    rec_df = attr()
                except Exception:
                    rec_df = None
            else:
                rec_df = attr

        if rec_df is not None:
            if hasattr(rec_df, "empty") and not rec_df.empty:
                text_col = None
                for c in rec_df.columns:
                    sample = rec_df[c].dropna().astype(str)
                    if not sample.empty:
                        s0 = sample.iloc[-1].lower()
                        if any(x in s0 for x in ("buy", "hold", "sell")):
                            text_col = c
                            break
                if text_col is None:
                    text_col = rec_df.columns[0]

                vals = rec_df[text_col].astype(str).str.lower()
                metrics["rec_latest"] = vals.dropna().iloc[-1] if not vals.dropna().empty else None
                metrics["rec_buy_count"] = int((vals.str.contains("buy")).sum())
                metrics["rec_hold_count"] = int((vals.str.contains("hold")).sum())
                metrics["rec_sell_count"] = int((vals.str.contains("sell")).sum())
    except Exception:
        pass

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
            try:
                last = eps_df.dropna(how="all").iloc[-1].to_dict()
                metrics["eps_trend_summary"] = ";".join(f"{k}={v}" for k, v in last.items())
            except Exception:
                metrics["eps_trend_summary"] = None
    except Exception:
        pass

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
            buys = 0
            sells = 0
            cnt = 0
            for _, row in ins_df.iterrows():
                cnt += 1
                s = None
                for k in ("shares", "transactionShares", "qty"):
                    if k in ins_df.columns:
                        try:
                            s = float(row[k])
                            break
                        except Exception:
                            s = None
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
                    if isinstance(v, float):
                        metric_vals.append(f"{v:.4f}")
                    else:
                        metric_vals.append(str(v))
            w.writerow(base_vals + metric_vals)

    return out_file


def load_screener_config(config_path: Path) -> Dict[str, Any]:
    if not config_path.exists():
        raise FileNotFoundError(f"Screener config not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as fh:
        config = json.load(fh)

    if not isinstance(config, dict):
        raise ValueError("Screener config must be a JSON object.")

    return config


def parse_csv_list(value: Optional[Any]) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def filter_candidates(rows: List[Dict[str, Any]], sectors: List[str], industry: Optional[str], min_avg_volume: Optional[int], min_market_cap: Optional[float], min_eod_price: Optional[float], max_eod_price: Optional[float]) -> List[Dict[str, Any]]:
    filtered = []
    sector_set = {s.lower() for s in sectors}
    for row in rows:
        if not isinstance(row, dict):
            continue

        row_sector = str(row.get("sector") or "").strip().lower()
        if sector_set and row_sector:
            if row_sector not in sector_set:
                continue

        row_industry = str(row.get("industry") or "").strip().lower()
        if industry and row_industry:
            if industry.lower() not in row_industry:
                continue

        if min_avg_volume is not None:
            avg_vol = safe_float(row.get("averageDailyVolume3Month") or row.get("averageDailyVolume") or row.get("regularMarketVolume") or row.get("avgVolume"))
            if avg_vol is None or avg_vol < float(min_avg_volume):
                continue

        if min_market_cap is not None:
            cap = safe_float(row.get("marketCap") or row.get("market_cap") or row.get("marketcap"))
            if cap is None or cap < float(min_market_cap):
                continue

        if min_eod_price is not None:
            eod = safe_float(row.get("eodprice") or row.get("regularMarketPrice") or row.get("previousClose"))
            if eod is None or eod < float(min_eod_price):
                continue

        if max_eod_price is not None:
            eod = safe_float(row.get("eodprice") or row.get("regularMarketPrice") or row.get("previousClose"))
            if eod is None or eod > float(max_eod_price):
                continue

        filtered.append(row)

    return filtered


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="configs/equity_screener.json", help="path to a JSON screener config file")
    p.add_argument("--limit", type=int, default=None, help="maximum number of candidates to fetch")
    p.add_argument("--sector", default=None, help="single sector filter (e.g., Technology)")
    p.add_argument("--sectors", default=None, help="comma-separated sector filters (e.g., Technology,Healthcare)")
    p.add_argument("--industry", default=None, help="industry filter")
    p.add_argument("--min-avg-volume", type=int, default=None, help="minimum average daily volume")
    p.add_argument("--min-market-cap", type=float, default=None, help="minimum market cap")
    p.add_argument("--min-eod-price", type=float, default=None, help="minimum eod price")
    p.add_argument("--max-eod-price", type=float, default=None, help="maximum eod price")
    repo_root = Path(__file__).resolve().parents[2]
    default_reports = repo_root.joinpath("reports", "screener_results", "yfinance_screener_results_with_metrics.csv")
    p.add_argument("--out", default=None, help="output CSV path")
    p.add_argument("--pause", type=float, default=None, help="pause (s) between metric fetches to be gentle")
    p.add_argument("--candidates-file", default=None, help="path to existing candidates CSV to use instead of running screener")
    args = p.parse_args()

    try:
        config = load_screener_config(Path(args.config))
    except FileNotFoundError:
        config = {}

    limit = args.limit if args.limit is not None else int(config.get("limit", 200))
    max_symbols = int(config.get("max_symbols", limit))
    effective_limit = min(limit, max_symbols) if max_symbols else limit

    sectors = parse_csv_list(args.sectors or config.get("sectors") or args.sector or config.get("sector"))
    industry = args.industry if args.industry is not None else config.get("industry")
    min_avg_volume = args.min_avg_volume if args.min_avg_volume is not None else config.get("min_avg_volume")
    min_market_cap = args.min_market_cap if args.min_market_cap is not None else config.get("min_market_cap")
    min_eod_price = args.min_eod_price if args.min_eod_price is not None else config.get("min_eod_price")
    max_eod_price = args.max_eod_price if args.max_eod_price is not None else config.get("max_eod_price")
    out_path = Path(args.out) if args.out else Path(config.get("out", default_reports))
    workers = int(config.get("workers", 6))
    pause = args.pause if args.pause is not None else float(config.get("pause", 0.0))

    print(f"Loaded config from {args.config}")
    if sectors:
        print(f"Sectors: {', '.join(sectors)}")
    if industry:
        print(f"Industry: {industry}")
    print(f"Max symbols to process: {effective_limit}")

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

    if args.candidates_file:
        candidates = filter_candidates(candidates, sectors, industry, min_avg_volume, min_market_cap, min_eod_price, max_eod_price)
        if effective_limit and len(candidates) > effective_limit:
            candidates = candidates[:effective_limit]

    if not candidates:
        print(f"Fetching up to {effective_limit} candidates from screener...")
        candidates = get_candidates(
            limit=effective_limit,
            sectors=sectors,
            sector=sectors[0] if len(sectors) == 1 else None,
            industry=industry,
            min_eod_price=min_eod_price,
            max_eod_price=max_eod_price,
            min_avg_volume=min_avg_volume,
            min_market_cap=min_market_cap,
            region=config.get("region", "us"),
            exchange=config.get("exchange"),
        )

    try:
        from .yfinance_equity_screener import enrich_results_with_info
        candidates = enrich_results_with_info(candidates)
    except Exception:
        try:
            from sandbox.yfinance_equity_screener import enrich_results_with_info
            candidates = enrich_results_with_info(candidates)
        except Exception:
            try:
                from yfinance_equity_screener import enrich_results_with_info  # type: ignore
                candidates = enrich_results_with_info(candidates)
            except Exception:
                pass

    candidates = filter_candidates(candidates, sectors, industry, min_avg_volume, min_market_cap, min_eod_price, max_eod_price)
    if effective_limit and len(candidates) > effective_limit:
        candidates = candidates[:effective_limit]

    symbols = []
    for r in candidates:
        if isinstance(r, dict):
            sym = r.get("symbol") or r.get("ticker")
            if sym:
                symbols.append(sym)

    if max_eod_price is not None:
        def _eod_ok(row):
            try:
                if not isinstance(row, dict):
                    return False
                v = safe_float(row.get("eodprice") or row.get("regularMarketPrice") or row.get("previousClose") or row.get("currentPrice"))
                return v is not None and v < float(max_eod_price)
            except Exception:
                return False

        before = len(symbols)
        filtered = [r for r in candidates if isinstance(r, dict) and ((r.get("symbol") or r.get("ticker")) and _eod_ok(r))]
        candidates = filtered
        symbols = [r.get("symbol") or r.get("ticker") for r in candidates]
        print(f"Filtered candidates by eodprice < {max_eod_price}: {before} -> {len(symbols)}")

    print(f"Computing metrics for {len(symbols)} symbols (workers={workers})...")
    metrics = compute_metrics(symbols, workers=workers, pause=pause)

    # append UTC date suffix _YYYYMMDD to outfile if not already present
    date = datetime.utcnow().strftime("%Y%m%d")
    if not out_path.stem.endswith(f"_{date}"):
        out_path = out_path.with_name(f"{out_path.stem}_{date}{out_path.suffix}")
    write_combined_csv(candidates, metrics, out_path)
    print(f"Saved {len(symbols)} candidates with metrics to {out_path}")

    if str(config.get("correlation_dedup", "false")).lower() in {"1", "true", "yes", "on"}:
        ranked_path = out_path.with_name(f"{out_path.stem}_ranked{out_path.suffix}")
        with out_path.open("r", encoding="utf-8", newline="") as f:
            ranked_rows = list(csv.DictReader(f))
        rank_rows(ranked_rows, ranked_path)
        print(f"Wrote ranked CSV to {ranked_path}")

        dedup_method = str(config.get("correlation_dedup_method", "CORRELATION")).upper()
        threshold = float(config.get("correlation_dedup_threshold", 0.85))
        dedup_csv(
            ranked_path,
            threshold=threshold,
            period=str(config.get("correlation_dedup_period", "3mo")),
            interval=str(config.get("correlation_dedup_interval", "1d")),
            method=dedup_method,
            window=config.get("correlation_dedup_window"),
            transform=str(config.get("correlation_dedup_transform", "NONE")).upper(),
        )
        print("Correlation deduplication step completed.")


if __name__ == "__main__":
    main()
