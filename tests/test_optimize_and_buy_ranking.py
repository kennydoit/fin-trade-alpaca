import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from runners import optimize_and_buy


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def test_pick_top_n_prefers_prediction_rank_over_avg_ret(tmp_path):
    csv_path = tmp_path / "predictions.csv"
    write_csv(
        csv_path,
        [
            {"symbol": "DQ", "regularMarketPrice": 10.0, "avg_ret": 0.01, "pred_ret": 0.90, "prediction_rank": 1},
            {"symbol": "SAIL", "regularMarketPrice": 10.0, "avg_ret": 0.02, "pred_ret": 0.80, "prediction_rank": 2},
            {"symbol": "COHU", "regularMarketPrice": 10.0, "avg_ret": 0.99, "pred_ret": 0.10, "prediction_rank": 3},
            {"symbol": "PDFS", "regularMarketPrice": 10.0, "avg_ret": 0.98, "pred_ret": 0.05, "prediction_rank": 4},
        ],
    )

    picks = optimize_and_buy.pick_top_n_from_screener(csv_path, 3)

    assert [symbol for symbol, _ in picks] == ["DQ", "SAIL", "COHU"]


def test_pick_top_n_skips_existing_positions(tmp_path):
    csv_path = tmp_path / "predictions.csv"
    write_csv(
        csv_path,
        [
            {"symbol": "DQ", "regularMarketPrice": 10.0, "avg_ret": 0.01, "pred_ret": 0.90, "prediction_rank": 1},
            {"symbol": "SAIL", "regularMarketPrice": 10.0, "avg_ret": 0.02, "pred_ret": 0.80, "prediction_rank": 2},
            {"symbol": "COHU", "regularMarketPrice": 10.0, "avg_ret": 0.99, "pred_ret": 0.10, "prediction_rank": 3},
            {"symbol": "PDFS", "regularMarketPrice": 10.0, "avg_ret": 0.98, "pred_ret": 0.05, "prediction_rank": 4},
        ],
    )

    picks = optimize_and_buy.pick_top_n_from_screener(csv_path, 3, existing_symbols={"PDFS"})

    assert [symbol for symbol, _ in picks] == ["DQ", "SAIL", "COHU"]


def test_pick_top_n_applies_correlation_dedup_and_expands_candidate_window(tmp_path, monkeypatch):
    csv_path = tmp_path / "predictions.csv"
    write_csv(
        csv_path,
        [
            {"symbol": "AAA", "regularMarketPrice": 10.0, "pred_ret": 0.95},
            {"symbol": "BBB", "regularMarketPrice": 11.0, "pred_ret": 0.94},
            {"symbol": "CCC", "regularMarketPrice": 12.0, "pred_ret": 0.93},
            {"symbol": "DDD", "regularMarketPrice": 13.0, "pred_ret": 0.92},
            {"symbol": "EEE", "regularMarketPrice": 14.0, "pred_ret": 0.91},
        ],
    )

    calls = []

    def fake_dedup_ranked_symbols(symbols, **kwargs):
        calls.append(list(symbols))
        dropped = {}
        for symbol in symbols:
            if symbol == "BBB":
                dropped[symbol] = "AAA"
            elif symbol == "DDD":
                dropped[symbol] = "CCC"
            else:
                dropped[symbol] = None
        kept = [symbol for symbol in symbols if dropped[symbol] is None]
        return kept, dropped

    monkeypatch.setattr(optimize_and_buy, "dedup_ranked_symbols", fake_dedup_ranked_symbols)

    picks = optimize_and_buy.pick_top_n_from_screener(
        csv_path,
        3,
        correlation_dedup={"enabled": True, "threshold": 0.75, "search_multiplier": 1},
    )

    assert [symbol for symbol, _ in picks] == ["AAA", "CCC", "EEE"]
    assert calls == [["AAA", "BBB", "CCC"], ["AAA", "BBB", "CCC", "DDD", "EEE"]]
