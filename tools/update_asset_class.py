"""Update position strategies from all strategy.json files in configs/.

This standalone tool scans every JSON file in configs/ whose name contains
"strategy" and rebuilds the strategy classification for all rows in the
positions table:
  - core if the symbol appears in any core bucket
  - growth if the symbol appears in any growth bucket
  - short_term otherwise

Usage:
    python tools/update_asset_class.py
"""
import json
import sqlite3
import sys
from pathlib import Path
from typing import Dict, Iterable, Set

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))

from database.schema import get_database_path


def iter_strategy_files(configs_dir: Path) -> Iterable[Path]:
    """Return JSON files in configs/ whose name contains 'strategy'."""
    return sorted(
        [path for path in configs_dir.glob("*.json") if "strategy" in path.name.lower()]
    )


def collect_bucket_symbols(path: Path) -> Dict[str, Set[str]]:
    """Collect core/growth symbols from a strategy file."""
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    buckets = data.get("buckets", {})
    result = {"core": set(), "growth": set()}

    for bucket_name in ("core", "growth"):
        bucket = buckets.get(bucket_name, {})
        assets = bucket.get("assets", {})

        if isinstance(assets, dict):
            result[bucket_name].update(str(symbol).strip().upper() for symbol in assets.keys())
        elif isinstance(assets, list):
            result[bucket_name].update(str(symbol).strip().upper() for symbol in assets)

    return result


def build_strategy_map(configs_dir: Path) -> Dict[str, str]:
    """Build a symbol -> final strategy map from all strategy files."""
    core_symbols: Set[str] = set()
    growth_symbols: Set[str] = set()

    for path in iter_strategy_files(configs_dir):
        print(f"Loading {path.name}")
        bucket_symbols = collect_bucket_symbols(path)
        core_symbols.update(bucket_symbols["core"])
        growth_symbols.update(bucket_symbols["growth"])

    strategy_map = {}
    for symbol in core_symbols:
        strategy_map[symbol] = "core"
    for symbol in growth_symbols:
        strategy_map[symbol] = "growth"

    return strategy_map


def update_positions(db_path: Path, strategy_map: Dict[str, str]) -> Dict[str, int]:
    """Update every position row to use its final bucket strategy."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT symbol, strategy FROM positions")
    rows = cursor.fetchall()

    updated = {"core": 0, "growth": 0, "short_term": 0}

    for symbol, _existing in rows:
        symbol_upper = str(symbol).strip().upper()
        new_strategy = strategy_map.get(symbol_upper, "short_term")
        updated[new_strategy] += 1

        cursor.execute(
            "UPDATE positions SET strategy = ? WHERE symbol = ?",
            (new_strategy, symbol_upper),
        )

    conn.commit()
    conn.close()

    return updated


def main() -> int:
    configs_dir = repo_root / "configs"
    if not configs_dir.exists():
        print(f"Configs directory not found: {configs_dir}", file=sys.stderr)
        return 1

    strategy_files = list(iter_strategy_files(configs_dir))
    if not strategy_files:
        print("No strategy JSON files found under configs/", file=sys.stderr)
        return 1

    print(f"Found {len(strategy_files)} strategy file(s) under configs/")
    strategy_map = build_strategy_map(configs_dir)

    db_path = get_database_path(repo_root)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1

    print("Updating position strategies...")
    counts = update_positions(db_path, strategy_map)

    print("\nStrategy summary:")
    for label in ("core", "growth", "short_term"):
        print(f"  {label}: {counts.get(label, 0)}")

    print("\nDone")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
