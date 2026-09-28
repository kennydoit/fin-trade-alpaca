import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import runners.yfinance_growth_screener as screener
import yfinance.screener.yfinance_equity_screener as eq_screener


def test_get_candidates_enforces_per_sector_quota(monkeypatch):
    def fake_screen_equities(limit=200, sector=None, **kwargs):
        assert limit == 4
        return [
            {"symbol": f"{sector[:2].upper()}{i}", "sector": sector, "industry": "X"}
            for i in range(1, 6)
        ]

    monkeypatch.setattr(eq_screener, "screen_equities", fake_screen_equities)

    results = screener.get_candidates(
        limit=6,
        sectors=["Technology", "Healthcare", "Energy"],
        use_sector_quotas=True,
        per_sector_quota=2,
        per_sector_fetch_limit=4,
    )

    assert len(results) == 6
    sector_counts = {}
    for row in results:
        sector = row["sector"]
        sector_counts[sector] = sector_counts.get(sector, 0) + 1

    assert sector_counts["Technology"] == 2
    assert sector_counts["Healthcare"] == 2
    assert sector_counts["Energy"] == 2


def test_get_candidates_spillover_fills_when_sector_short(monkeypatch):
    def fake_screen_equities(limit=200, sector=None, **kwargs):
        if sector == "Technology":
            return [{"symbol": "TE1", "sector": sector, "industry": "X"}]
        if sector == "Healthcare":
            return [
                {"symbol": "HE1", "sector": sector, "industry": "X"},
                {"symbol": "HE2", "sector": sector, "industry": "X"},
                {"symbol": "HE3", "sector": sector, "industry": "X"},
            ]
        return [
            {"symbol": "EN1", "sector": sector, "industry": "X"},
            {"symbol": "EN2", "sector": sector, "industry": "X"},
            {"symbol": "EN3", "sector": sector, "industry": "X"},
        ]

    monkeypatch.setattr(eq_screener, "screen_equities", fake_screen_equities)

    results = screener.get_candidates(
        limit=6,
        sectors=["Technology", "Healthcare", "Energy"],
        use_sector_quotas=True,
        per_sector_quota=2,
        per_sector_fetch_limit=3,
    )

    assert len(results) == 6
    symbols = [row["symbol"] for row in results]
    assert "TE1" in symbols
    assert len(set(symbols)) == 6
