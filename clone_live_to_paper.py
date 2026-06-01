from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from alpaca.trading.client import TradingClient

from optimize_and_buy import load_environment_for_mode, resolve_credentials


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Clone live Alpaca position dollar values into paper strategy format. "
            "Step 1 liquidates all paper positions. Step 2 writes a strategy JSON "
            "for optimize_and_buy.py to rebuild paper using live dollar targets."
        )
    )
    parser.add_argument(
        "--output",
        default="paper_clone_strategy.json",
        help="Output strategy JSON path. Defaults to paper_clone_strategy.json.",
    )
    parser.add_argument(
        "--wait-timeout-sec",
        type=int,
        default=180,
        help="Max seconds to wait for paper liquidations to clear. Defaults to 180.",
    )
    parser.add_argument(
        "--poll-interval-sec",
        type=int,
        default=2,
        help="Polling interval while waiting for paper positions to clear. Defaults to 2.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview actions and generated strategy without submitting paper liquidation orders.",
    )
    parser.add_argument(
        "--auto-execute-paper",
        action="store_true",
        help=(
            "After writing the clone strategy, immediately run optimize_and_buy.py "
            "in paper mode against that strategy file."
        ),
    )
    parser.add_argument(
        "--min-order-notional",
        type=Decimal,
        default=Decimal("0.01"),
        help="Minimum order notional forwarded to optimize_and_buy.py. Defaults to 0.01.",
    )
    return parser.parse_args()


def get_client_for_mode(mode: str) -> TradingClient:
    load_environment_for_mode(mode)
    creds = resolve_credentials(mode)
    return TradingClient(
        api_key=creds.api_key,
        secret_key=creds.api_secret,
        oauth_token=creds.oauth_token,
        paper=creds.paper,
    )


def to_money(value: str | float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def get_paper_position_count(paper_client: TradingClient) -> int:
    return len(paper_client.get_all_positions())


def liquidate_paper_positions(
    paper_client: TradingClient,
    dry_run: bool,
    wait_timeout_sec: int,
    poll_interval_sec: int,
) -> bool:
    positions = paper_client.get_all_positions()
    if not positions:
        print("Paper account already has no positions to liquidate.")
        return True

    print(f"Paper liquidation target count: {len(positions)}")
    for pos in positions:
        print(f"  SELL ALL {pos.symbol} qty={pos.qty}")

    if dry_run:
        print("Dry run enabled. Skipping paper liquidation order submission.")
        return True

    paper_client.close_all_positions()
    print("Submitted close_all_positions for paper account.")

    started = time.time()
    while True:
        remaining = get_paper_position_count(paper_client)
        if remaining == 0:
            print("Paper positions fully liquidated.")
            return True

        elapsed = int(time.time() - started)
        if elapsed >= wait_timeout_sec:
            print(
                "Timed out waiting for paper liquidations to fully clear. "
                f"Remaining positions: {remaining}."
            )
            return False

        print(f"Waiting for paper liquidations to clear... remaining={remaining}")
        time.sleep(max(1, poll_interval_sec))


def get_live_long_position_values(live_client: TradingClient) -> dict[str, Decimal]:
    symbol_values: dict[str, Decimal] = {}
    for pos in live_client.get_all_positions():
        market_value = Decimal(str(pos.market_value))
        if market_value <= Decimal("0"):
            # Skip non-long exposure for this cloning flow.
            continue

        symbol = str(pos.symbol).strip().upper()
        current = symbol_values.get(symbol, Decimal("0"))
        symbol_values[symbol] = current + market_value

    return symbol_values


def build_strategy_for_exact_dollar_targets(
    live_symbol_values: dict[str, Decimal],
    paper_cash_after_liquidation: Decimal,
) -> dict:
    # optimize_and_buy.py computes each order as spendable_cash * (bucket_weight * asset_weight).
    # Putting all assets into core with core weight 1.0 allows asset_weight = target_dollars / paper_cash.
    core_assets: dict[str, float] = {}
    for symbol, target_value in sorted(live_symbol_values.items()):
        if target_value <= Decimal("0"):
            continue
        weight = (target_value / paper_cash_after_liquidation).quantize(
            Decimal("0.000000001"),
            rounding=ROUND_DOWN,
        )
        if weight > Decimal("0"):
            core_assets[symbol] = float(weight)

    return {
        "buckets": {
            "core": {
                "weight": 1.0,
                "assets": core_assets,
            },
            "growth": {
                "weight": 0.0,
                "assets": {},
            },
            "short_term": {
                "weight": 0.0,
                "assets": {},
            },
        }
    }


def write_strategy(path: Path, strategy: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(strategy, f, indent=2)
        f.write("\n")


def execute_paper_clone_orders(
    strategy_path: Path,
    live_total: Decimal,
    min_order_notional: Decimal,
    dry_run: bool,
) -> int:
    cmd = [
        sys.executable,
        "optimize_and_buy.py",
        "--mode",
        "paper",
        "--run-type",
        "adhoc",
        "--config",
        str(strategy_path),
        "--max-notional",
        str(live_total.quantize(Decimal("0.01"), rounding=ROUND_DOWN)),
        "--min-order-notional",
        str(min_order_notional.quantize(Decimal("0.01"), rounding=ROUND_DOWN)),
    ]
    if dry_run:
        cmd.append("--dry-run")

    print("Auto-executing paper clone command:", " ".join(cmd))
    result = subprocess.run(cmd, check=False)
    return result.returncode


def main() -> int:
    args = parse_args()

    try:
        paper_client = get_client_for_mode("paper")
        live_client = get_client_for_mode("live")
    except Exception as ex:
        print(f"Credential/client initialization failed: {ex}")
        return 2

    print("Step 1: Liquidating all paper positions.")
    liquidated = liquidate_paper_positions(
        paper_client=paper_client,
        dry_run=args.dry_run,
        wait_timeout_sec=max(1, args.wait_timeout_sec),
        poll_interval_sec=max(1, args.poll_interval_sec),
    )
    if not liquidated:
        return 2

    paper_account = paper_client.get_account()
    paper_cash = to_money(paper_account.cash)

    print("Step 2: Snapshotting live position market values.")
    live_symbol_values = get_live_long_position_values(live_client)
    live_total = to_money(sum(live_symbol_values.values(), Decimal("0")))

    print(f"Paper cash after liquidation: ${paper_cash}")
    print(f"Live long position total: ${live_total}")

    if not live_symbol_values:
        print("No live long positions found. Writing empty tradable strategy.")

    if live_total > paper_cash:
        print(
            "Paper cash is lower than live long market value total. "
            "Cannot create exact dollar-match strategy without additional cash."
        )
        print(f"Shortfall: ${(live_total - paper_cash).quantize(Decimal('0.01'), rounding=ROUND_DOWN)}")
        return 2

    if paper_cash <= Decimal("0") and live_total > Decimal("0"):
        print("Paper cash is 0 after liquidation; unable to clone live dollar targets.")
        return 2

    strategy = build_strategy_for_exact_dollar_targets(live_symbol_values, paper_cash or Decimal("1"))

    output_path = Path(args.output)
    write_strategy(output_path, strategy)

    core_assets = strategy["buckets"]["core"]["assets"]
    print(f"Wrote clone strategy with {len(core_assets)} symbols to {output_path}")

    for symbol, value in sorted(live_symbol_values.items()):
        print(f"  {symbol}: target_live_value=${to_money(value)}")

    print("Next step:")
    print(
        f"  python optimize_and_buy.py --mode paper --run-type adhoc --config {output_path} --min-order-notional 0.01"
    )

    if args.auto_execute_paper:
        if live_total <= Decimal("0"):
            print("Auto-execute skipped: live long position total is 0.")
            return 0
        rc = execute_paper_clone_orders(
            strategy_path=output_path,
            live_total=live_total,
            min_order_notional=max(Decimal("0.01"), args.min_order_notional),
            dry_run=args.dry_run,
        )
        if rc != 0:
            print(f"Auto-execute command failed with exit code {rc}.")
            return rc
        print("Auto-execute completed successfully.")

    if args.dry_run:
        print("Dry run completed. No liquidation orders were submitted.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
