from __future__ import annotations

import argparse
import calendar
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Dict
from zoneinfo import ZoneInfo

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import GetCalendarRequest, MarketOrderRequest
from dotenv import load_dotenv

EASTERN_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ModeCredentials:
    api_key: str | None
    api_secret: str | None
    oauth_token: str | None
    paper: bool


def is_ci_runtime() -> bool:
    return os.getenv("GITHUB_ACTIONS", "").lower() == "true" or os.getenv("CI", "").lower() == "true"


def require_ci_approval_for_real_orders(mode: str, dry_run: bool) -> None:
    if dry_run or not is_ci_runtime():
        return

    if os.getenv("ALLOW_REAL_ORDERS", "").lower() != "true":
        raise PermissionError(
            "Refusing non-dry-run order placement in CI. Set ALLOW_REAL_ORDERS=true in approved secrets."
        )

    if mode == "live" and os.getenv("ALLOW_LIVE_TRADING", "").lower() != "true":
        raise PermissionError(
            "Refusing live order placement in CI. Set ALLOW_LIVE_TRADING=true in approved secrets."
        )


def load_environment_for_mode(mode: str) -> None:
    # Load generic local env first, then mode-specific file with override.
    load_dotenv(".env", override=False)
    if mode == "paper":
        load_dotenv(".env.paper", override=True)
    else:
        load_dotenv(".env.live", override=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Allocate available Alpaca cash into configured strategy buckets "
            "using fractional notional buy orders."
        )
    )
    parser.add_argument(
        "--mode",
        choices=["paper", "live"],
        default="paper",
        help="Trading mode. Defaults to paper.",
    )
    parser.add_argument(
        "--run-type",
        choices=["adhoc", "scheduled"],
        default="adhoc",
        help="Use 'scheduled' to enforce the 15th/last-day gatekeeper.",
    )
    parser.add_argument(
        "--config",
        default="strategy.json",
        help="Path to strategy JSON config. Defaults to strategy.json.",
    )
    parser.add_argument(
        "--max-notional",
        type=Decimal,
        default=None,
        help="Optional spend cap for this run. If omitted, uses all available cash.",
    )
    parser.add_argument(
        "--min-order-notional",
        type=Decimal,
        default=Decimal("1.00"),
        help="Skip orders smaller than this notional. Defaults to 1.00.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned orders without sending them.",
    )
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Required when --mode live and not using --dry-run.",
    )
    return parser.parse_args()


def resolve_credentials(mode: str) -> ModeCredentials:
    if mode == "paper":
        oauth_token = os.getenv("ALPACA_PAPER_OAUTH_TOKEN") or os.getenv("ALPACA_OAUTH_TOKEN")
        api_key = os.getenv("ALPACA_PAPER_API_KEY") or os.getenv("ALPACA_API_KEY")
        api_secret = os.getenv("ALPACA_PAPER_API_SECRET") or os.getenv("ALPACA_API_SECRET")
        paper = True
    else:
        oauth_token = os.getenv("ALPACA_LIVE_OAUTH_TOKEN") or os.getenv("ALPACA_OAUTH_TOKEN")
        api_key = os.getenv("ALPACA_LIVE_API_KEY") or os.getenv("ALPACA_INDIVIDUAL_API_KEY")
        api_secret = os.getenv("ALPACA_LIVE_API_SECRET") or os.getenv("ALPACA_INDIVIDUAL_API_SECRET_KEY")
        paper = False

    if oauth_token:
        return ModeCredentials(api_key=None, api_secret=None, oauth_token=oauth_token, paper=paper)

    missing = []
    if not api_key:
        missing.append("API key")
    if not api_secret:
        missing.append("API secret")
    if missing:
        raise ValueError(
            f"Missing Alpaca credentials for {mode} mode: {', '.join(missing)}. "
            f"Alternatively set {'ALPACA_PAPER_OAUTH_TOKEN' if mode == 'paper' else 'ALPACA_LIVE_OAUTH_TOKEN'}"
        )

    return ModeCredentials(api_key=api_key, api_secret=api_secret, oauth_token=None, paper=paper)


def load_strategy_config(config_path: Path) -> dict:
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
            raise ValueError(
                f"Bucket '{bucket_name}' asset weights sum to {asset_weight_total}, which exceeds 1.0."
            )

    if abs(total_bucket_weight - Decimal("1")) > Decimal("0.0001"):
        raise ValueError(f"Bucket weights must sum to 1.0, found {total_bucket_weight}.")

    return config


def get_market_open_days(client: TradingClient, start: date, end: date) -> set[date]:
    cal = client.get_calendar(GetCalendarRequest(start=start, end=end))
    return {entry.date for entry in cal}


def compute_effective_trade_days(client: TradingClient, year: int, month: int) -> Dict[str, date]:
    month_last_day = calendar.monthrange(year, month)[1]
    anchors = {
        "mid_month": date(year, month, 15),
        "month_end": date(year, month, month_last_day),
    }

    effective_days: Dict[str, date] = {}
    for name, anchor in anchors.items():
        search_end = anchor + timedelta(days=7)
        market_days = sorted(get_market_open_days(client, anchor, search_end))
        valid_days = [d for d in market_days if d >= anchor]
        if not valid_days:
            raise RuntimeError(f"Unable to determine market day for {name} anchor {anchor}.")
        effective_days[name] = valid_days[0]

    return effective_days


def should_run_today(client: TradingClient, today: date) -> tuple[bool, str]:
    effective_days = compute_effective_trade_days(client, today.year, today.month)
    if today in set(effective_days.values()):
        reason = ", ".join(f"{k}={v.isoformat()}" for k, v in effective_days.items())
        return True, f"Date gate passed ({reason})."

    reason = ", ".join(f"{k}={v.isoformat()}" for k, v in effective_days.items())
    return False, f"Date gate skipped. Today={today.isoformat()} with targets {reason}."


def flatten_symbol_weights(strategy_config: dict) -> Dict[str, Decimal]:
    symbol_weights: Dict[str, Decimal] = {}
    for bucket in strategy_config["buckets"].values():
        bucket_weight = Decimal(str(bucket["weight"]))
        assets = bucket.get("assets", {})
        for symbol, asset_weight in assets.items():
            normalized_symbol = symbol.strip().upper()
            w = bucket_weight * Decimal(str(asset_weight))
            symbol_weights[normalized_symbol] = symbol_weights.get(normalized_symbol, Decimal("0")) + w
    return symbol_weights


def distribute_notionals(
    spendable_cash: Decimal,
    symbol_weights: Dict[str, Decimal],
    min_order_notional: Decimal,
) -> Dict[str, Decimal]:
    notionals: Dict[str, Decimal] = {}
    for symbol, weight in symbol_weights.items():
        if weight <= 0:
            continue

        notional = (spendable_cash * weight).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if notional >= min_order_notional:
            notionals[symbol] = notional

    return notionals


def submit_orders(
    client: TradingClient,
    notionals: Dict[str, Decimal],
    dry_run: bool,
) -> None:
    if not notionals:
        print("No orders to submit after min-order filter. Leaving cash unallocated.")
        return

    print("Order plan:")
    for symbol, notional in sorted(notionals.items()):
        print(f"  BUY {symbol}: ${notional}")

    if dry_run:
        print("Dry run enabled; no orders submitted.")
        return

    for symbol, notional in sorted(notionals.items()):
        req = MarketOrderRequest(
            symbol=symbol,
            notional=float(notional),
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        try:
            order = client.submit_order(req)
            print(f"Submitted {symbol} order id={order.id} notional=${notional}")
        except APIError as ex:
            print(f"Order failed for {symbol}: {ex}")


def choose_spendable_cash(available_cash: Decimal, max_notional: Decimal | None) -> Decimal:
    if available_cash <= Decimal("0"):
        return Decimal("0")
    if max_notional is None:
        return available_cash
    if max_notional <= Decimal("0"):
        return Decimal("0")
    return min(available_cash, max_notional)


def log_strategy_summary(strategy_config: dict) -> None:
    print("Strategy summary:")
    for bucket_name, bucket in strategy_config["buckets"].items():
        print(f"  Bucket {bucket_name}: weight={bucket['weight']}")
        assets = bucket.get("assets", {})
        if not assets:
            print("    (no assets configured; funds remain as cash)")
            continue
        for symbol, asset_weight in assets.items():
            print(f"    - {symbol.upper()}: asset_weight={asset_weight}")


def main() -> int:
    args = parse_args()
    load_environment_for_mode(args.mode)

    try:
        require_ci_approval_for_real_orders(args.mode, args.dry_run)
    except PermissionError as ex:
        print(str(ex))
        return 2

    if args.mode == "live" and not args.dry_run and not args.confirm_live:
        print("Live mode requires --confirm-live unless --dry-run is set.")
        return 2

    try:
        creds = resolve_credentials(args.mode)
        client = TradingClient(
            api_key=creds.api_key,
            secret_key=creds.api_secret,
            oauth_token=creds.oauth_token,
            paper=creds.paper,
        )
    except ValueError as ex:
        print(str(ex))
        return 2

    now_et = datetime.now(EASTERN_TZ)
    today_et = now_et.date()
    print(f"Run mode={args.mode} run_type={args.run_type} date_et={today_et.isoformat()}")

    if args.run_type == "scheduled":
        run_today, reason = should_run_today(client, today_et)
        print(reason)
        if not run_today:
            return 0

    try:
        strategy_config = load_strategy_config(Path(args.config))
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as ex:
        print(f"Config error: {ex}")
        return 2

    log_strategy_summary(strategy_config)

    account = client.get_account()
    available_cash = Decimal(str(account.cash)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    spendable_cash = choose_spendable_cash(available_cash, args.max_notional)

    print(f"Account cash=${available_cash}; spendable cash this run=${spendable_cash}")
    if spendable_cash <= Decimal("0"):
        print("No spendable cash available. Exiting.")
        return 0

    symbol_weights = flatten_symbol_weights(strategy_config)
    if not symbol_weights:
        print("No tradable symbols configured. Exiting.")
        return 0

    notionals = distribute_notionals(spendable_cash, symbol_weights, args.min_order_notional)
    submit_orders(client, notionals, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
