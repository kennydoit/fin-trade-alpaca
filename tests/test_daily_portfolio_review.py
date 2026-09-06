"""Test daily portfolio review logic."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unittest.mock import Mock

import pandas as pd

from tools.daily_portfolio_review import (
    AGGRESSIVENESS_PRESETS,
    evaluate_position_exit,
    load_predictions,
)


def test_aggressiveness_presets():
    """Verify aggressiveness presets are properly configured."""
    assert "conservative" in AGGRESSIVENESS_PRESETS
    assert "moderate" in AGGRESSIVENESS_PRESETS
    assert "aggressive" in AGGRESSIVENESS_PRESETS

    # Conservative should be least aggressive
    assert AGGRESSIVENESS_PRESETS["conservative"]["min_prediction_rank"] >= 20
    assert AGGRESSIVENESS_PRESETS["conservative"]["stop_loss_pct"] <= -5.0

    # Aggressive should be most aggressive
    assert AGGRESSIVENESS_PRESETS["aggressive"]["min_prediction_rank"] <= 5
    assert AGGRESSIVENESS_PRESETS["aggressive"]["stop_loss_pct"] >= -2.0


def test_evaluate_position_exit_by_rank():
    """Test exit decision based on prediction rank."""
    position = Mock()
    position.symbol = "AAPL"
    position.avg_entry_price = 100.0

    config = {"min_prediction_rank": 10}

    # Should hold if in top 10
    should_exit, reason = evaluate_position_exit(position, 105.0, 5, config)
    assert not should_exit

    # Should exit if rank drops below threshold
    should_exit, reason = evaluate_position_exit(position, 105.0, 15, config)
    assert should_exit
    assert "rank" in reason.lower()


def test_evaluate_position_exit_by_price():
    """Test exit decision based on price movement."""
    position = Mock()
    position.symbol = "AAPL"
    position.avg_entry_price = 100.0

    config = {"stop_loss_pct": -3.0, "take_profit_pct": 5.0}

    # Should hold if within range
    should_exit, reason = evaluate_position_exit(position, 102.0, None, config)
    assert not should_exit

    # Should exit on stop loss
    should_exit, reason = evaluate_position_exit(position, 96.0, None, config)
    assert should_exit
    assert "stop loss" in reason.lower()

    # Should exit on take profit
    should_exit, reason = evaluate_position_exit(position, 106.0, None, config)
    assert should_exit
    assert "take profit" in reason.lower()


def test_evaluate_position_exit_hybrid():
    """Test exit decision using both rank and price."""
    position = Mock()
    position.symbol = "AAPL"
    position.avg_entry_price = 100.0

    config = {
        "min_prediction_rank": 10,
        "stop_loss_pct": -3.0,
        "take_profit_pct": 5.0,
    }

    # Should hold if rank is good and price is acceptable
    should_exit, reason = evaluate_position_exit(position, 102.0, 5, config)
    assert not should_exit

    # Should exit if rank is bad even with good price
    should_exit, reason = evaluate_position_exit(position, 102.0, 20, config)
    assert should_exit

    # Should exit if price is bad even with good rank
    should_exit, reason = evaluate_position_exit(position, 96.0, 5, config)
    assert should_exit


def test_load_predictions(tmp_path):
    """Test loading predictions CSV."""
    csv_path = tmp_path / "predictions.csv"
    csv_path.write_text(
        "symbol,pred_ret,sector\nAAPL,0.05,Technology\nMSFT,0.04,Technology\nGOOGL,0.03,Technology\n", encoding="utf-8"
    )

    df = load_predictions(csv_path)

    assert len(df) == 3
    assert "symbol" in df.columns
    assert "rank" in df.columns
    assert df.iloc[0]["symbol"] == "AAPL"
    assert df.iloc[0]["rank"] == 1


def test_evaluate_position_not_in_predictions():
    """Test exit when position is not in current predictions."""
    position = Mock()
    position.symbol = "AAPL"
    position.avg_entry_price = 100.0

    config = {"min_prediction_rank": 10}

    # Should exit if not in predictions at all
    should_exit, reason = evaluate_position_exit(position, 102.0, None, config)
    assert should_exit
    assert "not in" in reason.lower()


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
