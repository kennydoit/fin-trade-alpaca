import csv

from src.yfinance.screener.rank_candidates import rank_rows


def test_rank_rows_writes_rank_and_score_columns(tmp_path):
    rows = [
        {
            "symbol": "AAA",
            "pct_1w": 10,
            "pct_1m": 5,
            "rel_volume": 1,
            "revenueGrowth": 20,
            "earningsQuarterlyGrowth": 10,
            "trailingPE": 10,
            "pegRatio": 1,
            "rec_buy_count": 4,
            "rec_sell_count": 1,
            "rec_latest": "Buy",
            "insider_buy_shares": 100,
            "insider_sell_shares": 0,
        },
        {
            "symbol": "BBB",
            "pct_1w": -5,
            "pct_1m": -2,
            "rel_volume": 0.2,
            "revenueGrowth": 0,
            "earningsQuarterlyGrowth": 0,
            "trailingPE": 50,
            "pegRatio": 5,
            "rec_buy_count": 1,
            "rec_sell_count": 4,
            "rec_latest": "Sell",
            "insider_buy_shares": 0,
            "insider_sell_shares": 100,
        },
    ]

    out = tmp_path / "ranked.csv"

    rank_rows(rows, out)

    with out.open("r", encoding="utf-8", newline="") as fh:
        data = list(csv.DictReader(fh))

    assert data[0]["symbol"] == "AAA"
    assert data[0]["rank"] == "1"
    assert "score_raw" in data[0]
    assert "score_norm" in data[0]
