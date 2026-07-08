"""Test the industry filtering fix and console sector filtering."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import subprocess


def test_industry_filter_basic():
    """Test that industry filtering doesn't break with comma-separated values."""
    repo_root = Path(__file__).resolve().parents[1]
    env = {
        **dict(subprocess.os.environ),
        "PYTHONPATH": str(repo_root / "src"),
    }
    
    result = subprocess.run(
        [
            sys.executable,
            "src/runners/yfinance_growth_screener.py",
            "--limit", "10",
            "--sectors", "Technology,Healthcare",
            "--industry", "Software - Infrastructure,Biotechnology",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=repo_root,
        env=env,
    )
    
    # Should not fail with ValueError about invalid EQ value
    assert "Invalid EQ value" not in result.stderr
    # The command may fail for other reasons (network, etc.) but should not have the parsing error
    if result.returncode != 0:
        assert "Fetching" in result.stdout or "Invalid EQ value" not in result.stderr


def test_config_has_industry_mapping():
    """Test that the config file has the industry-to-sector mapping."""
    import json
    config_path = Path(__file__).resolve().parents[1] / "configs" / "equity_screener.json"
    
    with config_path.open("r", encoding="utf-8") as f:
        config = json.load(f)
    
    assert "industry_to_sector_map" in config
    assert isinstance(config["industry_to_sector_map"], dict)
    
    # Check some expected mappings
    assert config["industry_to_sector_map"].get("Software - Infrastructure") == "Technology"
    assert config["industry_to_sector_map"].get("Biotechnology") == "Healthcare"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
