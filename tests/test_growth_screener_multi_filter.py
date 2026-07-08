import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from runners.yfinance_growth_screener import filter_candidates


def test_filter_candidates_supports_multiple_sectors_and_industries():
    rows = [
        {"sector": "Technology", "industry": "Software - Infrastructure"},
        {"sector": "Healthcare", "industry": "Biotechnology"},
        {"sector": "Finance", "industry": "Banks - Regional"},
    ]

    filtered = filter_candidates(
        rows,
        ["Technology", "Healthcare"],
        ["Software", "Biotechnology"],
        None,
        None,
        None,
        None,
    )

    assert len(filtered) == 2
    assert {row["industry"] for row in filtered} == {"Software - Infrastructure", "Biotechnology"}
