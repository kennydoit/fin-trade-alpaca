"""Strategy configuration loading, validation, and reporting.

Rationale (predictability + testability):
    The strategy config declares how real (or paper) money is split across
    buckets (core/growth/short_term) and, within each bucket, across
    individual symbols. Centralizing and unit-testing the schema validation
    here means malformed configs fail fast with a clear error message
    instead of silently misallocating cash deep inside the order-submission
    flow. These functions take a plain ``dict``/``Path`` and return
    plain data -- no Alpaca client, no I/O side effects beyond reading the
    config file itself -- so they're trivially testable with fixture JSON.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path


def load_strategy_config(config_path: Path) -> dict:
    """Load and validate a strategy JSON config file.

    Validates that:
      - the required buckets (``core``, ``growth``, ``short_term``) are present,
      - each bucket declares a non-negative ``weight``,
      - each asset weight within a bucket is non-negative and asset weights
        within a bucket do not exceed 1.0, and
      - bucket weights sum to 1.0 (within a small tolerance).

    Raises:
        FileNotFoundError: if ``config_path`` does not exist.
        ValueError: if the config fails schema validation.
        json.JSONDecodeError: if the file is not valid JSON.
    """
    if not config_path.exists():
        raise FileNotFoundError(
            f"Strategy config not found at {config_path}. Copy strategy.example.json to strategy.json first."
        )

    with config_path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    buckets = config.get("buckets", {})
    required_buckets = {"core", "growth", "short_term"}
    missing = required_buckets.difference(buckets.keys())
    if missing:
        raise ValueError(f"Config missing required buckets: {', '.join(sorted(missing))}")

    total_bucket_weight = Decimal("0")
    for bucket_name, bucket_data in buckets.items():
        if "weight" not in bucket_data:
            raise ValueError(f"Bucket '{bucket_name}' is missing 'weight'.")

        bucket_weight = Decimal(str(bucket_data["weight"]))
        if bucket_weight < 0:
            raise ValueError(f"Bucket '{bucket_name}' weight cannot be negative.")
        total_bucket_weight += bucket_weight

        assets = bucket_data.get("assets", {})
        if not isinstance(assets, dict):
            raise ValueError(f"Bucket '{bucket_name}' assets must be a symbol->weight map.")

        asset_weight_total = Decimal("0")
        for symbol, weight in assets.items():
            if not symbol.isalpha() and not symbol.replace("-", "").isalnum():
                raise ValueError(f"Invalid symbol '{symbol}' in bucket '{bucket_name}'.")
            w = Decimal(str(weight))
            if w < 0:
                raise ValueError(f"Asset weight for {symbol} in '{bucket_name}' cannot be negative.")
            asset_weight_total += w

        if assets and asset_weight_total > Decimal("1.0000001"):
            raise ValueError(f"Bucket '{bucket_name}' asset weights sum to {asset_weight_total}, which exceeds 1.0.")

    if abs(total_bucket_weight - Decimal("1")) > Decimal("0.0001"):
        raise ValueError(f"Bucket weights must sum to 1.0, found {total_bucket_weight}.")

    return config


def flatten_symbol_weights(strategy_config: dict) -> dict[str, Decimal]:
    """Flatten bucket-level weights into a single symbol -> effective portfolio weight map.

    A symbol appearing in multiple buckets has its effective weights summed.
    """
    symbol_weights: dict[str, Decimal] = {}
    for bucket in strategy_config["buckets"].values():
        bucket_weight = Decimal(str(bucket["weight"]))
        assets = bucket.get("assets", {})
        for symbol, asset_weight in assets.items():
            normalized_symbol = symbol.strip().upper()
            w = bucket_weight * Decimal(str(asset_weight))
            symbol_weights[normalized_symbol] = symbol_weights.get(normalized_symbol, Decimal("0")) + w
    return symbol_weights


def log_strategy_summary(strategy_config: dict) -> None:
    """Print a human-readable summary of the strategy configuration for operator visibility."""
    total_investment = strategy_config.get("total_investment")
    print("Strategy summary:")
    if total_investment is not None:
        print(f"  total_investment={total_investment}")
    for bucket_name, bucket in strategy_config["buckets"].items():
        print(f"  Bucket {bucket_name}: weight={bucket['weight']}")
        assets = bucket.get("assets", {})
        if not assets:
            print("    (no assets configured; funds remain as cash)")
            continue
        for symbol, asset_weight in assets.items():
            print(f"    - {symbol.upper()}: asset_weight={asset_weight}")
