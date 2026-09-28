import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from runners.predict_screener import load_prediction_config, resolve_runtime_config


def test_load_prediction_config_reads_json_values(tmp_path):
    config_path = tmp_path / "prediction.json"
    config_path.write_text(
        json.dumps(
            {
                "candidates_file": "reports/screener_results/sample.csv",
                "sector": "Technology",
                "industry": "Software",
                "limit": 25,
                "return_days": 3,
                "lookback": 120,
                "download_batch_size": 12,
                "download_batch_pause": 3.5,
                "download_max_retries": 4,
                "download_retry_pause": 8.0,
                "out": "reports/screener_results/predictions.csv",
                "versioned": True,
                "model": {"type": "random_forest", "n_estimators": 150},
                "target": {"horizon_days": 3, "label_column": "fwd_ret"},
            }
        ),
        encoding="utf-8",
    )

    cfg = load_prediction_config(config_path)

    assert cfg["candidates_file"] == "reports/screener_results/sample.csv"
    assert cfg["sector"] == "Technology"
    assert cfg["industry"] == "Software"
    assert cfg["limit"] == 25
    assert cfg["return_days"] == 3
    assert cfg["lookback"] == 120
    assert cfg["download_batch_size"] == 12
    assert cfg["download_batch_pause"] == 3.5
    assert cfg["download_max_retries"] == 4
    assert cfg["download_retry_pause"] == 8.0
    assert cfg["out"] == "reports/screener_results/predictions.csv"
    assert cfg["versioned"] is True
    assert cfg["model"]["type"] == "random_forest"
    assert cfg["target"]["horizon_days"] == 3


def test_resolve_runtime_config_normalizes_download_settings(tmp_path):
    config_path = tmp_path / "prediction.json"
    config_path.write_text(
        json.dumps(
            {
                "download_batch_size": 10,
                "download_batch_pause": 1.25,
                "download_max_retries": 3,
                "download_retry_pause": 6.5,
            }
        ),
        encoding="utf-8",
    )

    class Args:
        config = config_path
        candidates_file = None
        sector = None
        industry = None
        limit = None
        return_days = None
        lookback = None
        out = None
        versioned = None

    runtime = resolve_runtime_config(Args())

    assert runtime["download_batch_size"] == 10
    assert runtime["download_batch_pause"] == 1.25
    assert runtime["download_max_retries"] == 3
    assert runtime["download_retry_pause"] == 6.5
