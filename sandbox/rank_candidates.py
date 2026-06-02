"""Rank candidates by a composite short-term gain score.

Reads a CSV produced by the growth screener, normalizes selected features,
computes a weighted composite score, and writes a ranked CSV with scores and
per-feature normalized values.

Usage:
    python sandbox/rank_candidates.py --in <input.csv> --out <output.csv>
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
from statistics import mean
from typing import Dict, List, Any, Optional


def safe_float(x: Any) -> Optional[float]:
    try:
        if x is None or x == "":
            return None
        return float(str(x).replace(",", ""))
    except Exception:
        return None


def normalize_minmax(vals: List[Optional[float]]) -> List[float]:
    clean = [v for v in vals if v is not None and not math.isnan(v)]
    if not clean:
        return [0.5 for _ in vals]
    lo = min(clean)
    hi = max(clean)
    if math.isclose(lo, hi):
        return [0.5 for _ in vals]
    out = []
    for v in vals:
        if v is None or math.isnan(v):
            out.append(0.5)
        else:
            out.append(max(0.0, min(1.0, (v - lo) / (hi - lo))))
    return out


def compute_rec_score(row: Dict[str, Any]) -> float:
    # Prefer counts if available
    buy = safe_float(row.get("rec_buy_count")) or 0.0
    sell = safe_float(row.get("rec_sell_count")) or 0.0
    total = buy + sell
    if total > 0:
        val = (buy - sell) / total  # in [-1,1]
        return (val + 1.0) / 2.0
    # fallback to latest text
    latest = (row.get("rec_latest") or "").lower()
    if "buy" in latest:
        return 1.0
    if "hold" in latest:
        return 0.5
    if "sell" in latest:
        return 0.0
    return 0.5


def compute_insider_score(row: Dict[str, Any]) -> float:
    b = safe_float(row.get("insider_buy_shares")) or 0.0
    s = safe_float(row.get("insider_sell_shares")) or 0.0
    net = b - s
    # use signed log1p to capture magnitude while compressing outliers
    if net == 0:
        return 0.5
    signed = math.copysign(math.log1p(abs(net)), net)
    # normalize later across rows; return raw signed value here
    return signed


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="infile", required=True)
    p.add_argument("--out", dest="outfile", required=True)
    p.add_argument("--top", type=int, default=20, help="number of top rows to print")
    args = p.parse_args()

    inp = Path(args.infile)
    out = Path(args.outfile)
    assert inp.exists(), f"Input not found: {inp}"

    rows: List[Dict[str, Any]] = []
    with inp.open("r", encoding="utf-8") as f:
        rdr = csv.DictReader(f)
        for r in rdr:
            rows.append(r)

    # extract raw features
    pct_1w = [safe_float(r.get("pct_1w")) for r in rows]
    pct_1m = [safe_float(r.get("pct_1m")) for r in rows]
    rel_vol = [safe_float(r.get("rel_volume")) for r in rows]
    rev_g = [safe_float(r.get("revenueGrowth")) for r in rows]
    earn_g = [safe_float(r.get("earningsQuarterlyGrowth")) for r in rows]
    trailing_pe = [safe_float(r.get("trailingPE")) for r in rows]
    peg = [safe_float(r.get("pegRatio")) for r in rows]

    # compute recommendation and insider raw
    rec_raw = [compute_rec_score(r) for r in rows]
    insider_raw = [compute_insider_score(r) for r in rows]

    # normalize momentum and liquidity: clip extreme momentum to reasonable bounds
    def clip_pct(v: Optional[float]) -> Optional[float]:
        if v is None:
            return None
        # clip to [-50, +200] percent
        return max(-50.0, min(200.0, v))

    pct_1w_clipped = [clip_pct(v) for v in pct_1w]
    pct_1m_clipped = [clip_pct(v) for v in pct_1m]

    n_pct_1w = normalize_minmax([v if v is not None else None for v in pct_1w_clipped])
    n_pct_1m = normalize_minmax([v if v is not None else None for v in pct_1m_clipped])
    n_rel_vol = normalize_minmax([v if v is not None else None for v in rel_vol])
    n_rev_g = normalize_minmax([v if v is not None else None for v in rev_g])
    n_earn_g = normalize_minmax([v if v is not None else None for v in earn_g])

    # valuation: lower is better -> invert after normalization
    n_trailing_pe = normalize_minmax([v if v is not None else None for v in trailing_pe])
    n_peg = normalize_minmax([v if v is not None else None for v in peg])
    # average valuation score (higher = better because inverted)
    val_scores = []
    for a, b in zip(n_trailing_pe, n_peg):
        comps = []
        if a is not None:
            comps.append(1.0 - a)
        if b is not None:
            comps.append(1.0 - b)
        if comps:
            val_scores.append(mean(comps))
        else:
            val_scores.append(0.5)

    # normalize rec and insider
    n_rec = normalize_minmax(rec_raw)
    # insider_raw may be signed floats; normalize by min/max
    n_insider = normalize_minmax(insider_raw)

    # weights (sum to 1)
    weights = {
        "pct_1w": 0.28,
        "pct_1m": 0.18,
        "rel_volume": 0.15,
        "revenueGrowth": 0.12,
        "earningsQuarterlyGrowth": 0.10,
        "rec": 0.08,
        "insider": 0.06,
        "valuation": 0.03,
    }

    scores_raw: List[float] = []
    norm_features = []
    for i, r in enumerate(rows):
        s = 0.0
        s += weights["pct_1w"] * n_pct_1w[i]
        s += weights["pct_1m"] * n_pct_1m[i]
        s += weights["rel_volume"] * n_rel_vol[i]
        s += weights["revenueGrowth"] * n_rev_g[i]
        s += weights["earningsQuarterlyGrowth"] * n_earn_g[i]
        s += weights["rec"] * n_rec[i]
        s += weights["insider"] * n_insider[i]
        s += weights["valuation"] * val_scores[i]
        scores_raw.append(s)
        norm_features.append({
            "pct_1w_n": n_pct_1w[i],
            "pct_1m_n": n_pct_1m[i],
            "rel_volume_n": n_rel_vol[i],
            "revenueGrowth_n": n_rev_g[i],
            "earningsQuarterlyGrowth_n": n_earn_g[i],
            "rec_n": n_rec[i],
            "insider_n": n_insider[i],
            "valuation_n": val_scores[i],
        })

    # normalize final score to 0-1
    score_min = min(scores_raw) if scores_raw else 0.0
    score_max = max(scores_raw) if scores_raw else 1.0
    if math.isclose(score_min, score_max):
        scores_norm = [0.5 for _ in scores_raw]
    else:
        scores_norm = [(s - score_min) / (score_max - score_min) for s in scores_raw]

    # attach scores and write CSV
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        fieldnames = list(rows[0].keys()) if rows else []
        extra = ["score_raw", "score_norm", "rank"] + list(norm_features[0].keys() if norm_features else [])
        w = csv.DictWriter(f, fieldnames=fieldnames + extra)
        w.writeheader()
        combined = []
        for i, r in enumerate(rows):
            new = dict(r)
            new["score_raw"] = f"{scores_raw[i]:.6f}"
            new["score_norm"] = f"{scores_norm[i]:.6f}"
            combined.append((scores_raw[i], scores_norm[i], new, norm_features[i]))
        # rank by raw score descending
        combined.sort(key=lambda x: x[0], reverse=True)
        for rank, (_, _, new, nf) in enumerate(combined, start=1):
            new["rank"] = rank
            for k, v in nf.items():
                new[k] = f"{v:.6f}"
            w.writerow(new)

    # print top N
    top = min(args.top, len(combined))
    if top:
        print(f"Top {top} candidates:")
        for i in range(top):
            score = combined[i][1]
            row = combined[i][2]
            print(f"{i+1}. {row.get('symbol') or row.get('ticker')} - score={score:.4f}")


if __name__ == "__main__":
    main()
 
