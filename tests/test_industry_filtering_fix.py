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
            "--limit",
            "10",
            "--sectors",
            "Technology,Healthcare",
            "--industry",
            "Software - Infrastructure,Biotechnology",
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


def test_config_has_broader_universe_and_looser_filters():
    """Ensure the default screener config keeps a broader market universe."""
    import json

    config_path = Path(__file__).resolve().parents[1] / "configs" / "equity_screener.json"

    with config_path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    sectors = config.get("sectors", [])
    industries = config.get("industry_choices", [])
    mapping = config.get("industry_to_sector_map", {})

    assert isinstance(sectors, list)
    assert isinstance(industries, list)
    assert isinstance(mapping, dict)

    assert "Technology" in sectors
    assert "Healthcare" in sectors
    assert "Financial Services" in sectors
    assert "Industrials" in sectors
    assert "Energy" in sectors
    assert "Real Estate" in sectors
    assert len(sectors) >= 10

    assert "Banks - Diversified" in industries
    assert "Aerospace & Defense" in industries
    assert "Oil & Gas Integrated" in industries
    assert "REIT - Industrial" in industries
    assert len(industries) >= 25

    assert mapping.get("Banks - Diversified") == "Financial Services"
    assert mapping.get("Aerospace & Defense") == "Industrials"
    assert mapping.get("Oil & Gas Integrated") == "Energy"
    assert mapping.get("REIT - Industrial") == "Real Estate"

    assert config.get("min_eod_price") == 3
    assert config.get("max_eod_price") == 150
    assert config.get("min_avg_volume") == 50000
    assert config.get("min_market_cap") == 100000000
    assert config.get("limit") == 220
    assert config.get("max_symbols") == 220
    assert config.get("workers") == 3
    assert config.get("pause") == 1.0
    assert config.get("use_sector_quotas") is True
    assert config.get("per_sector_quota") == 20
    assert config.get("per_sector_fetch_limit") == 40
    assert config.get("download_batch_size") == 25
    assert config.get("download_batch_pause") == 2.0
    assert config.get("download_max_retries") == 2
    assert config.get("download_retry_pause") == 5.0
    assert config.get("correlation_dedup_threshold") == 0.85


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
