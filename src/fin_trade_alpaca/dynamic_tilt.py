"""Dynamic momentum-based tilt for the core/growth buckets.

Rationale (testability + extensibility):
    This logic mixes a *pure* re-weighting algorithm (given momentum metrics,
    compute new weights) with an *impure* data-fetch step (download recent
    price history from yfinance). Separating them here -- rather than
    burying both inside the order-submission orchestrator -- lets the
    re-weighting math be unit-tested with hand-crafted metrics dicts, with
    ``compute_momentum_for_symbols`` mocked/stubbed out entirely.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any


def safe_float(x: Any) -> float | None:
    """Best-effort conversion to float, tolerating None/blank/comma-formatted input."""
    try:
        if x is None or x == "":
            return None
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def compute_momentum_for_symbols(
    symbols: list[str], basis: str = "pct_1m", clip: tuple[float, float] = (-50, 200)
) -> dict[str, float | None]:
    """Return a dict of symbol -> percent change for the given basis.

    Supports ``basis="pct_1m"`` (1-month) or ``basis="pct_1w"`` (1-week).
    Falls back to yfinance ``info`` fields when historical bars aren't
    available. Values are clipped to ``clip`` to limit the influence of
    outliers (e.g. post-split/anomalous data) on the tilt.
    """
    try:
        import yfinance as yf  # noqa: PLC0415 - optional heavy dependency, imported lazily on purpose
    except ImportError:
        print("yfinance not available; skipping dynamic tilt.")
        return dict.fromkeys(symbols)

    metrics: dict[str, float | None] = {}
    for sym in symbols:
        try:
            t = yf.Ticker(sym)
            period = "1mo" if basis == "pct_1m" else "7d"
            try:
                hist = t.history(period=period, interval="1d", actions=False)
            except Exception:
                hist = None
            pct = None
            if hist is not None and len(hist) > 0:
                closes = list(hist["Close"].dropna())
                if len(closes) >= 2:
                    first = safe_float(closes[0])
                    last = safe_float(closes[-1])
                    if first is not None and first != 0:
                        pct = (last - first) / first * 100.0
            if pct is None:
                try:
                    info = t.info or {}
                except Exception:
                    info = {}
                if basis == "pct_1m":
                    pct = safe_float(info.get("monthChangePercent") or info.get("regularMarketChangePercent"))
                else:
                    pct = safe_float(info.get("weekChangePercent") or info.get("regularMarketChangePercent"))

            if pct is not None:
                lo, hi = clip
                pct = max(lo, min(hi, pct))
            metrics[sym] = pct
        except Exception:
            metrics[sym] = None

    return metrics


def apply_dynamic_tilt(
    strategy_config: dict,
    symbol_weights: dict[str, Decimal],
    tilt_cfg: dict,
    spendable_cash: Decimal,
) -> tuple[dict[str, Decimal], list[dict]]:
    """Apply momentum tilt across the combined core+growth symbol set.

    Symbols with weaker recent momentum are tilted toward a larger weight
    (a simple mean-reversion / "buy the dip within your basket" heuristic),
    bounded by ``tilt_cfg["cap_per_asset"]`` so no single asset can be
    over/under-weighted beyond the configured cap.

    Returns:
        ``(new_symbol_weights, report_rows)`` where ``report_rows`` is a list
        of per-symbol dicts suitable for ``write_tilt_report``.
    """
    buckets = strategy_config.get("buckets", {})
    combined: list[str] = []
    for name in ("core", "growth"):
        assets = buckets.get(name, {}).get("assets", {})
        combined.extend(s.strip().upper() for s in assets)

    combined = sorted(set(combined))
    if not combined:
        return symbol_weights, []

    basis = tilt_cfg.get("basis", "pct_1m")
    alpha = float(tilt_cfg.get("alpha", 0.10))
    clip = tuple(tilt_cfg.get("clip", [-50, 200]))
    cap = float(tilt_cfg.get("cap_per_asset", 0.10))

    metrics = compute_momentum_for_symbols(combined, basis=basis, clip=clip)

    base_weights = {s: float(symbol_weights.get(s, Decimal("0"))) for s in combined}
    combined_total = sum(base_weights.values())
    if combined_total <= 0:
        return symbol_weights, []

    # Invert momentum: lower recent return -> higher tilt priority.
    inv = {s: (0.0 if metrics.get(s) is None else -float(metrics[s])) for s in combined}

    min_inv = min(inv.values())
    pvals = {s: inv[s] - min_inv for s in combined}
    total_p = sum(pvals.values())
    norm = {s: 1.0 / len(combined) for s in combined} if total_p == 0 else {s: pvals[s] / total_p for s in combined}

    tf = {}
    for s in combined:
        tf_val = 1.0 + alpha * norm[s]
        tf[s] = max(1.0 - cap, min(1.0 + cap, tf_val))  # cap multiplier to [1-cap, 1+cap]

    tilted = {s: base_weights[s] * tf[s] for s in combined}
    tilted_sum = sum(tilted.values())
    if tilted_sum == 0:
        report = [(s, base_weights[s], base_weights[s], metrics.get(s)) for s in combined]
        return dict(symbol_weights), report

    scale = combined_total / tilted_sum
    new_weights = dict(symbol_weights)
    report = []
    for s in combined:
        new_w = Decimal(str(tilted[s] * scale)).quantize(Decimal("0.0000001"))
        orig_w = Decimal(str(base_weights[s]))
        new_weights[s] = new_w
        orig_invest = (spendable_cash * orig_w).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        new_invest = (spendable_cash * new_w).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        report.append(
            {
                "symbol": s,
                "original_weight": float(orig_w),
                "tilt_weight": float(new_w),
                "original_investment": f"{orig_invest}",
                "tilt_investment": f"{new_invest}",
                "metric": metrics.get(s),
            }
        )

    return new_weights, report


def write_tilt_report(report_rows: list[dict], today: date, mode: str) -> Path | None:
    """Write a plain-text tilt report to reports/tilt_reports/ and return its path."""
    if not report_rows:
        return None
    repo_root = Path(__file__).resolve().parents[2]
    out_dir = repo_root.joinpath("reports", "tilt_reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"tilt_report_{mode}_{today.strftime('%Y%m%d')}.txt"
    out_path = out_dir.joinpath(fname)

    cols = ["symbol", "original_weight", "tilt_weight", "original_investment", "tilt_investment", "metric"]
    rows_str = []
    col_widths = {c: len(c) for c in cols}
    for r in report_rows:
        row = {
            "symbol": str(r.get("symbol") or ""),
            "original_weight": f"{r.get('original_weight'):.7f}" if r.get("original_weight") is not None else "",
            "tilt_weight": f"{r.get('tilt_weight'):.7f}" if r.get("tilt_weight") is not None else "",
            "original_investment": str(r.get("original_investment") or ""),
            "tilt_investment": str(r.get("tilt_investment") or ""),
            "metric": f"{r.get('metric'):.4f}" if r.get("metric") is not None else "",
        }
        for k, v in row.items():
            col_widths[k] = max(col_widths[k], len(v))
        rows_str.append(row)

    sep = " | "
    header = sep.join(c.ljust(col_widths[c]) for c in cols)
    divider = "-+-".join("-" * col_widths[c] for c in cols)
    lines = [header, divider]
    lines.extend(sep.join(row[c].ljust(col_widths[c]) for c in cols) for row in rows_str)

    with out_path.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return out_path
