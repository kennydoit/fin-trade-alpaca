from __future__ import annotations

import json
from pathlib import Path


def test_statarb_config_has_yfinance_pacing_defaults():
    config_path = Path(__file__).resolve().parents[1] / "configs" / "statarb_screener.json"

    with config_path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)

    statarb = config["statarb_screener"]

    assert statarb["max_symbols"] == 80
    assert statarb["top_pairs"] == 30
    assert statarb["min_correlation"] == 0.6
    assert statarb["download_batch_size"] == 20
    assert statarb["download_batch_pause"] == 2.0
    assert statarb["download_max_retries"] == 2
    assert statarb["download_retry_pause"] == 5.0
    assert len(statarb["symbols"]) >= 80
