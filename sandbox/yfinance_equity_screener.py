"""
yfinance_equity_screener.py

Flexible equity screener using yfinance's EquityQuery.

Provides screen_equities(min_eod_price, max_eod_price, region='us', exchange=None,
                       industry=None, sector=None)

Prints the resulting symbols and a few key fields.

If the installed yfinance does not expose `EquityQuery`, the script will
inform you and exit gracefully.
"""
from typing import Optional, Sequence, List, Dict, Any
import sys
import csv
from pathlib import Path


def screen_equities(
    min_eod_price: Optional[float] = None,
    max_eod_price: Optional[float] = None,
    region: str = "us",
    exchange: Optional[Sequence[str]] = None,
    industry: Optional[str] = None,
    sector: Optional[str] = None,
    min_avg_volume: Optional[int] = 100_000,
    min_market_cap: Optional[float] = 300_000_000,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Run an equity screener and return a list of result dicts.

    Parameters mirror the common filters the yfinance EquityQuery supports.
    - min_eod_price / max_eod_price: applied to `eodprice` filter when supported.
    - region: default 'us'
    - exchange: sequence or single exchange (e.g., 'NASDAQ'). If None,
      defaults to ['NASDAQ', 'NYSE']
    - industry, sector: strings used as filters

    Returns a list of dict-like results (may vary depending on yfinance version).
    """
    try:
        import yfinance as yf
    except Exception as e:
        print("Error: could not import yfinance:", e, file=sys.stderr)
        return []

    # Obtain EquityQuery class
    EquityQuery = getattr(yf, "EquityQuery", None)
    if EquityQuery is None:
        # try alternate import path (older/newer versions may differ)
        try:
            from yfinance.screener import EquityQuery  # type: ignore
        except Exception:
            print(
                "EquityQuery not found in yfinance. Please install/upgrade yfinance",
                file=sys.stderr,
            )
            return []

    # Build filter kwargs. The EquityQuery API has varied between versions;
    # we construct sensible kwargs and attempt to call the common method names.
    params: Dict[str, Any] = {}
    if region:
        params["region"] = region
    if sector:
        params["sector"] = sector
    if industry:
        params["industry"] = industry

    # Exchange handling: default to NASDAQ and NYSE when not provided
    if exchange is None:
        params["exchange"] = ["NASDAQ", "NYSE"]
    else:
        if isinstance(exchange, str):
            params["exchange"] = [exchange]
        else:
            params["exchange"] = list(exchange)

    # eodprice filter
    if min_eod_price is not None or max_eod_price is not None:
        eod = {}
        if min_eod_price is not None:
            eod["min"] = float(min_eod_price)
        if max_eod_price is not None:
            eod["max"] = float(max_eod_price)
        # include under a key named 'eodprice' which recent yfinance versions expect
        params["eodprice"] = eod
    # Build EquityQuery filters according to the installed API
    try:
        from yfinance.screener import EquityQuery, screen as yf_screen
    except Exception as e:
        print("Error accessing yfinance.screener.EquityQuery:", e, file=sys.stderr)
        return []

    filters = []

    # exchange mapping used by yfinance screener (Yahoo codes)
    exchange_map = {"NASDAQ": "NMS", "NYSE": "NYQ"}
    if params.get("exchange"):
        codes = [exchange_map.get(x.upper(), x) for x in params["exchange"]]
        operand = ["exchange"] + codes
        filters.append(EquityQuery("is-in", operand))

    if sector:
        filters.append(EquityQuery("is-in", ["sector", sector]))
    if industry:
        filters.append(EquityQuery("is-in", ["industry", industry]))
    if region:
        filters.append(EquityQuery("is-in", ["region", region]))

    if min_eod_price is not None:
        filters.append(EquityQuery("gte", ["eodprice", float(min_eod_price)]))
    if max_eod_price is not None:
        filters.append(EquityQuery("lte", ["eodprice", float(max_eod_price)]))

    # liquidity filters
    # Try screener-level liquidity filters but fall back gracefully if the
    # EquityQuery implementation doesn't accept a particular field name.
    def try_append_gte_field(field_candidates, val):
        for field in field_candidates:
            try:
                filters.append(EquityQuery("gte", [field, val]))
                return True
            except Exception:
                continue
        return False

    if min_avg_volume is not None:
        avg_fields = ["averageDailyVolume3Month", "averageDailyVolume", "averageVolume", "regularMarketVolume"]
        try_append_gte_field(avg_fields, int(min_avg_volume))
    if min_market_cap is not None:
        cap_fields = ["marketCap", "market_cap", "marketcap"]
        try_append_gte_field(cap_fields, float(min_market_cap))

    if not filters:
        print("No filters specified; returning empty result to avoid broad queries.")
        return []

    if len(filters) == 1:
        q = filters[0]
    else:
        q = EquityQuery("and", filters)

    # Execute the screener query via yfinance.screener.screen
    try:
        res = yf_screen(q, size=limit)
    except Exception as e:
        print("Screener execution failed:", e, file=sys.stderr)
        return []

    # Normalize results: if dict-like, try to extract list of quotes
    results: List[Dict[str, Any]] = []
    if isinstance(res, dict):
        # try common keys
        for key in ("quotes", "data", "results"):
            if key in res and isinstance(res[key], list):
                results = res[key][:limit]
                break
        if not results:
            # maybe the dict itself maps symbol->data
            items = list(res.items())[:limit]
            for k, v in items:
                if isinstance(v, dict):
                    results.append({"symbol": k, **v})
                else:
                    results.append({"symbol": k, "value": v})
    elif isinstance(res, list):
        results = res[:limit]
    else:
        try:
            for i, item in enumerate(res):
                if i >= limit:
                    break
                results.append(item)
        except Exception:
            results = [res]

    if not results:
        print("No results returned.")
        return results

    # Post-filtering fallback: ensure liquidity thresholds by inspecting
    # result fields; if missing, fetch per-symbol info via yfinance.Ticker
    if min_avg_volume is not None or min_market_cap is not None:
        try:
            import yfinance as yf
        except Exception:
            yf = None

        filtered: List[Dict[str, Any]] = []
        for item in results:
            # extract symbol
            symbol = None
            if isinstance(item, dict):
                symbol = item.get("symbol") or item.get("ticker") or item.get("symbolRaw")
            if not symbol:
                # try to parse from string representations
                continue

            # attempt to read liquidity fields from item first
            def read_field(d, keys):
                for k in keys:
                    v = None
                    if isinstance(d, dict):
                        v = d.get(k)
                    else:
                        v = getattr(d, k, None)
                    if v is not None:
                        return v
                return None

            avg_keys = ["averageDailyVolume3Month", "averageDailyVolume", "averageVolume", "avgVolume", "regularMarketVolume"]
            cap_keys = ["marketCap", "market_cap", "marketcap"]

            avg = read_field(item, avg_keys)
            cap = read_field(item, cap_keys)

            if (avg is None or cap is None) and yf is not None:
                try:
                    info = yf.Ticker(symbol).info
                    if avg is None:
                        for k in avg_keys:
                            if k in info and info[k] is not None:
                                avg = info[k]
                                break
                    if cap is None:
                        for k in cap_keys:
                            if k in info and info[k] is not None:
                                cap = info[k]
                                break
                except Exception:
                    pass

            # coerce to numbers
            try:
                avg_val = int(avg) if avg is not None else None
            except Exception:
                avg_val = None
            try:
                cap_val = float(cap) if cap is not None else None
            except Exception:
                cap_val = None

            if min_avg_volume is not None and (avg_val is None or avg_val < min_avg_volume):
                continue
            if min_market_cap is not None and (cap_val is None or cap_val < min_market_cap):
                continue

            filtered.append(item)

        results = filtered

    return results


def print_results(results: List[Dict[str, Any]]) -> None:
    """Print a concise table of the results."""
    if not results:
        print("No results to show.")
        return

    # determine keys to show if present
    headers = ["symbol", "shortName", "exchange", "sector", "industry", "eodprice", "regularMarketPrice"]
    # print header
    print("\t".join(headers))
    for r in results:
        # r may be a mapping-like object
        get = lambda k: (r.get(k) if isinstance(r, dict) else getattr(r, k, ""))
        row = [str(get(h) or "") for h in headers]
        print("\t".join(row))


def enrich_results_with_info(results: List[Dict[str, Any]], fields: Sequence[str] = ("sector", "industry", "eodprice")) -> List[Dict[str, Any]]:
    """Enrich result entries by fetching missing fields from yfinance.Ticker.info.

    This will attempt to fill `sector`, `industry`, and `eodprice` when they are
    absent in the screener response. Returns the modified results list.
    """
    try:
        import yfinance as yf
    except Exception:
        return results

    for item in results:
        if not isinstance(item, dict):
            continue
        symbol = item.get("symbol") or item.get("ticker") or item.get("symbolRaw")
        if not symbol:
            continue

        need_fetch = any((item.get(f) is None or item.get(f) == "") for f in fields)
        if not need_fetch:
            continue

        try:
            info = yf.Ticker(symbol).info
        except Exception:
            continue

        if "sector" in fields and (not item.get("sector") or item.get("sector") == ""):
            item["sector"] = info.get("sector") or item.get("sector")
        if "industry" in fields and (not item.get("industry") or item.get("industry") == ""):
            item["industry"] = info.get("industry") or item.get("industry")
        if "eodprice" in fields and (not item.get("eodprice") or item.get("eodprice") == ""):
            for key in ("previousClose", "regularMarketPreviousClose", "regularMarketPrice", "currentPrice"):
                if key in info and info[key] is not None:
                    item["eodprice"] = info[key]
                    break

    return results


def save_results_csv(results: List[Dict[str, Any]], filename: Optional[str] = None) -> Path:
    """Save results to CSV in the same folder as this script (or given filename).

    Returns the path to the written file.
    """
    if filename is None:
        filename = "yfinance_screener_results.csv"
    out_path = Path(__file__).parent.joinpath(filename)

    headers = ["symbol", "shortName", "exchange", "sector", "industry", "eodprice", "regularMarketPrice"]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for r in results:
            get = lambda k: (r.get(k) if isinstance(r, dict) else getattr(r, k, ""))
            row = [str(get(h) or "") for h in headers]
            writer.writerow(row)

    return out_path


if __name__ == "__main__":
    # Example usage: search for Technology-sector equities related to AI/tech infra
    print("Running example screener: Technology sector, price 5-500 USD, region=us, min_avg_volume=100000, min_market_cap=300000000\n")
    results = screen_equities(min_eod_price=5, max_eod_price=500, region="us", exchange=None, sector="Technology", min_avg_volume=100_000, min_market_cap=300_000_000)
    # Enrich missing metadata (sector, industry, eodprice) via Ticker.info
    results = enrich_results_with_info(results)
    print_results(results)
    out = save_results_csv(results)
    print(f"\nSaved {len(results)} results to {out}")
