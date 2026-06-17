#!/usr/bin/env python3
"""Export a US equities sector/industry asset-count table to reports/."""

from __future__ import annotations

import csv
import glob
import os
from collections import Counter
from pathlib import Path

import pandas as pd

try:
    import yfinance as yf
    from yfinance.screener import EquityQuery, screen
except Exception:
    yf = None
    EquityQuery = None
    screen = None


REPO_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = REPO_ROOT / "reports"
OUTPUT_PATH = REPORTS_DIR / "us_equity_sector_industry_counts.csv"


def load_cached_us_equity_rows(limit: int = 1000) -> list[dict]:
    """Load cached YFinance screener rows from the reports folder as a fallback."""
    candidates = sorted(glob.glob(str(REPORTS_DIR / "screener_results" / "yfinance_screener_results*.csv")))
    seen: set[str] = set()
    rows: list[dict] = []

    for path in reversed(candidates):
        try:
            frame = pd.read_csv(path)
        except Exception:
            continue

        if not {"symbol", "sector", "industry"}.issubset(frame.columns):
            continue

        for _, row in frame.iterrows():
            if len(rows) >= limit:
                break

            symbol = str(row.get("symbol") or "").strip().upper()
            if not symbol or symbol in seen:
                continue

            seen.add(symbol)
            rows.append(
                {
                    "symbol": symbol,
                    "exchange": str(row.get("exchange") or "").upper(),
                    "sector": str(row.get("sector") or "").strip() or "UNKNOWN",
                    "industry": str(row.get("industry") or "").strip() or "UNKNOWN",
                }
            )

        if len(rows) >= limit:
            break

    return rows


def fetch_us_equity_metadata(limit: int = 1000) -> list[dict]:
    """Return a stable US-equity sector/industry inventory.

    Prefer the saved local YFinance screener CSVs for reliability; only attempt live screener
    requests when explicitly enabled via USE_LIVE_SCREENER=1.
    """
    if os.getenv("USE_LIVE_SCREENER", "0").lower() not in {"1", "true", "yes"} or EquityQuery is None or screen is None or yf is None:
        return load_cached_us_equity_rows(limit=limit)

    q = EquityQuery("is-in", ["region", "us"])
    page_size = 250
    max_pages = max(1, (limit + page_size - 1) // page_size)

    rows: list[dict] = []
    for offset in range(0, max_pages * page_size, page_size):
        if len(rows) >= limit:
            break

        try:
            raw = screen(q, offset=offset, size=page_size)
        except Exception:
            break

        quotes = raw.get("quotes", []) if isinstance(raw, dict) else []
        for quote in quotes:
            if len(rows) >= limit:
                break

            symbol = str(quote.get("symbol") or "").strip().upper()
            if not symbol:
                continue

            try:
                info = yf.Ticker(symbol).info or {}
            except Exception:
                info = {}

            rows.append(
                {
                    "symbol": symbol,
                    "exchange": str(quote.get("exchange") or "").upper(),
                    "sector": str(info.get("sector") or quote.get("sector") or "").strip() or "UNKNOWN",
                    "industry": str(info.get("industry") or quote.get("industry") or "").strip() or "UNKNOWN",
                }
            )

    if rows:
        return rows

    return load_cached_us_equity_rows(limit=limit)


def build_table(rows: list[dict]) -> list[dict]:
    counter = Counter((row["industry"], row["sector"]) for row in rows)
    table = [
        {
            "INDUSTRY": industry,
            "SECTOR": sector,
            "NUMBER_OF_ASSETS": count,
        }
        for (industry, sector), count in sorted(counter.items(), key=lambda item: (-item[1], item[0][0], item[0][1]))
    ]
    return table


def write_table(table: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["INDUSTRY", "SECTOR", "NUMBER_OF_ASSETS"])
        writer.writeheader()
        writer.writerows(table)


def main() -> None:
    rows = fetch_us_equity_metadata(limit=1000)
    table = build_table(rows)
    write_table(table, OUTPUT_PATH)

    source_label = "cached screener inventory" if os.getenv("USE_LIVE_SCREENER", "0").lower() not in {"1", "true", "yes"} else "live YFinance screener"
    print(f"Fetched {len(rows)} US equity records from {source_label}.")
    print(f"Wrote {len(table)} sector/industry rows to {OUTPUT_PATH}")
    print("\nPreview:")
    for row in table[:25]:
        print(f"  {row['INDUSTRY']} | {row['SECTOR']} | {row['NUMBER_OF_ASSETS']}")


if __name__ == "__main__":
    main()
