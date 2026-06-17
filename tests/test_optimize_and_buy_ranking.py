import csv
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import runners.optimize_and_buy as optimize_and_buy


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
