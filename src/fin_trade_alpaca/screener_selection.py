"""Screener CSV discovery and top-N candidate selection.

Rationale (testability):
    Selecting which symbols to buy from a screener CSV is pure
    data-transformation logic (read CSV -> sort by score -> take top N,
    excluding symbols already held). Isolating it here lets it be tested
    with small fixture CSVs, independent of Alpaca clients or live order
    submission.
"""

from __future__ import annotations

import csv
from pathlib import Path

from fin_trade_alpaca.dynamic_tilt import safe_float


def find_latest_screener_csv(repo_root: Path) -> Path | None:
    """Return the most recently modified prediction-screener CSV in reports/screener_results/, or None.

    Only matches ``predictions_*.csv`` files (the prediction pipeline's output
    naming convention). Earlier versions globbed for *any* CSV in the
    directory, which meant an unrelated, more-recently-written file (e.g. a
    growth/equity screener export or a debug CSV) could silently get picked
    up instead of the actual predictions file the short-term buy bucket
    needs -- with no error, just zero valid picks. Scoping the glob to the
    predictions naming convention makes "no fresh predictions available" fail
    loudly (returns None -> caller reports "no screener CSV found") instead
    of silently trading on the wrong data.
    """
    dirpath = repo_root.joinpath("reports", "screener_results")
    if not dirpath.exists():
        return None
    csvs = list(dirpath.glob("predictions_*.csv"))
    if not csvs:
        return None
    return sorted(csvs, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def pick_top_n_from_screener(
    csv_path: Path, n: int, existing_symbols: set[str] | None = None
) -> list[tuple[str, float]]:
    """Return the top N (symbol, price) pairs from a screener CSV.

    Prefers the model's prediction score (``pred_ret``) since it reflects the
    ranked model output; falls back to ``avg_ret`` and simple momentum
    fields (``pct_1w``, ``pct_1m``) only when a prediction score isn't
    present. Symbols already held (``existing_symbols``) are excluded so we
    don't re-buy positions we already have.
    """
    rows: list[tuple[str, float | None, float | None]] = []
    existing = {s.strip().upper() for s in (existing_symbols or []) if s and str(s).strip()}
    try:
        with csv_path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                sym = (r.get("symbol") or "").strip().upper()
                if not sym or sym in existing:
                    continue
                price = safe_float(r.get("regularMarketPrice") or r.get("close") or r.get("eodprice"))
                score = None
                for fld in ("pred_ret", "avg_ret", "avg_pred", "pct_1w", "pct_1m"):
                    val = safe_float(r.get(fld))
                    if val is not None:
                        score = val
                        break
                rows.append((sym, price, score))
    except OSError:
        return []

    scored_rows = [r for r in rows if r[2] is not None and r[1] is not None]
    if not scored_rows:
        return []

    rows_sorted = sorted(scored_rows, key=lambda x: (x[2], x[0]), reverse=True)
    return [(sym, price) for sym, price, _score in rows_sorted[:n]]
