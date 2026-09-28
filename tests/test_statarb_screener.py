import pandas as pd

from runners import statarb_screener


def test_build_pair_candidates_finds_ranked_pair_from_synthetic_data():
    dates = pd.date_range("2026-01-01", periods=80, freq="D")
    base = pd.Series(range(100, 180), index=dates, dtype=float)
    close_a = base
    close_b = base * 0.95
    close_c = pd.Series(range(200, 280), index=dates, dtype=float) * 1.3

    frame = pd.DataFrame({"AAA": close_a, "BBB": close_b, "CCC": close_c})
    pairs = statarb_screener.build_pair_candidates(frame, min_history_days=60, min_correlation=0.1)

    assert len(pairs) >= 1
    assert "long_symbol" in pairs[0]
    assert "short_symbol" in pairs[0]
    assert pairs[0]["score"] >= 0