import json
from pathlib import Path


def test_aws_prediction_screener_uses_wide_strategy_config():
    repo_root = Path(__file__).resolve().parents[1]
    handler = repo_root / "aws" / "functions" / "prediction_screener" / "handler.py"
    strategy = repo_root / "aws" / "functions" / "prediction_screener" / "configs" / "strategy.json"

    handler_text = handler.read_text(encoding="utf-8")
    assert "--strategy-config" in handler_text

    strategy_cfg = json.loads(strategy.read_text(encoding="utf-8"))
    short_term = strategy_cfg.get("buckets", {}).get("short_term", {})
    assert short_term.get("prediction_pipeline") == "wide_asset_based_prediction"
    assert bool(short_term.get("wide_asset_based_prediction", {}).get("enabled", False))
