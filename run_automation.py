from __future__ import annotations

import argparse
import calendar
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
import yaml
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetCalendarRequest

from optimize_and_buy import load_environment_for_mode, resolve_credentials

EASTERN_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ScheduleDecision:
    run: bool
    reason: str


def is_ci_runtime() -> bool:
    return os.getenv("GITHUB_ACTIONS", "").lower() == "true" or os.getenv("CI", "").lower() == "true"


def require_ci_toggle(env_var: str, human_name: str) -> bool:
    if not is_ci_runtime():
        return True
    if os.getenv(env_var, "").lower() == "true":
        return True
    print(f"Safety latch blocked {human_name}. Set {env_var}=true in approved secrets to allow it.")
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run YAML-configured automation for ACH funding and portfolio allocation."
        )
    )
    parser.add_argument("--config", default="automation.yaml", help="Path to YAML automation file.")
    parser.add_argument(
        "--action",
        choices=["funding", "allocation", "both"],
        default="both",
        help="Which automation action(s) to run.",
    )
    parser.add_argument(
        "--mode",
        choices=["paper", "live"],
        required=True,
        help="Trading mode for this run. Must be explicit per invocation.",
    )
    parser.add_argument(
        "--force-immediate",
        action="store_true",
        help="Ignore schedules and run selected action(s) immediately.",
    )
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help=(
            "Acknowledge live execution without interactive prompt. "
            "Useful for non-interactive automation contexts."
        ),
    )
    parser.add_argument(
        "--execute-live-now",
        action="store_true",
        help=(
            "Required safety flag for any live non-dry-run allocation. "
            "Without this, live execution is blocked."
        ),
    )
    parser.add_argument(
        "--investment-amount",
        default=None,
        help=(
            "Optional allocation spend amount override for this run. "
            "Takes precedence over allocation.max_notional_per_run in YAML."
        ),
    )
    return parser.parse_args()


def load_yaml_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"YAML config not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError("YAML root must be a mapping.")
    return cfg


def to_decimal(raw: Any, field_name: str) -> Decimal:
    try:
        value = Decimal(str(raw))
    except Exception as ex:  # pragma: no cover - defensive parse
        raise ValueError(f"Invalid decimal for {field_name}: {raw}") from ex
    if value < Decimal("0"):
        raise ValueError(f"{field_name} cannot be negative.")
    return value


def is_market_day(client: TradingClient, day: date) -> bool:
    cal = client.get_calendar(GetCalendarRequest(start=day, end=day))
    return len(cal) > 0


def format_percent(weight: Decimal) -> str:
    pct = (weight * Decimal("100")).normalize()
    pct_str = format(pct, "f").rstrip("0").rstrip(".")
    return pct_str if pct_str else "0"


def load_strategy_bucket_weights(strategy_file: str) -> list[tuple[str, Decimal]]:
    with Path(strategy_file).open("r", encoding="utf-8") as f:
        strategy = json.load(f)
    buckets = strategy.get("buckets", {})
    pairs: list[tuple[str, Decimal]] = []
    for name, bucket in buckets.items():
        weight = Decimal(str(bucket.get("weight", 0)))
        if weight > 0:
            pairs.append((name, weight))
    return pairs


def request_live_confirmation(investment_amount: Decimal, strategy_file: str) -> bool:
    try:
        bucket_weights = load_strategy_bucket_weights(strategy_file)
        if bucket_weights:
            strategy_summary = ", ".join(
                f"{format_percent(weight)}% {name}" for name, weight in bucket_weights
            )
        else:
            strategy_summary = "(no weighted buckets found)"
    except Exception as ex:
        strategy_summary = f"(unable to read strategy: {ex})"

    print(
        f"Confirm that you want to invest ${investment_amount} using the strategy: {strategy_summary}."
    )
    answer = input("Type YES to continue: ").strip()
    return answer == "YES"


def should_run_for_schedule(
    schedule_tokens: list[Any],
    today: date,
    force_immediate: bool,
    require_market_open: bool,
    market_open: bool,
) -> ScheduleDecision:
    if force_immediate:
        return ScheduleDecision(True, "Forced immediate run.")

    normalized = {str(token).strip().lower() for token in schedule_tokens}

    if "immediate" in normalized:
        decision = ScheduleDecision(True, "Schedule includes immediate.")
    elif str(today.day) in normalized:
        decision = ScheduleDecision(True, f"Matched day token {today.day}.")
    elif "last_day" in normalized and today.day == calendar.monthrange(today.year, today.month)[1]:
        decision = ScheduleDecision(True, "Matched last_day token.")
    else:
        decision = ScheduleDecision(False, f"No schedule match for {today.isoformat()} with tokens {sorted(normalized)}.")

    if decision.run and require_market_open and not market_open:
        return ScheduleDecision(False, "Schedule matched, but market is closed.")

    return decision


def run_funding(config: dict[str, Any], today: date, force_immediate: bool, market_open: bool) -> None:
    funding_cfg = config.get("funding", {})
    if not funding_cfg.get("enabled", False):
        print("Funding disabled in YAML.")
        return

    schedule = funding_cfg.get("schedule", ["15", "last_day"])
    require_market_open = bool(funding_cfg.get("require_market_open", False))
    decision = should_run_for_schedule(schedule, today, force_immediate, require_market_open, market_open)
    print(f"Funding schedule decision: {decision.reason}")
    if not decision.run:
        return

    provider = str(funding_cfg.get("provider", "alpaca_broker")).strip().lower()
    if provider != "alpaca_broker":
        print(f"Unsupported funding provider '{provider}'. Skipping funding.")
        return

    amount = to_decimal(funding_cfg.get("amount", "0"), "funding.amount").quantize(
        Decimal("0.01"), rounding=ROUND_DOWN
    )
    if amount <= Decimal("0"):
        print("Funding amount must be > 0. Skipping funding.")
        return

    api_key_env = str(funding_cfg.get("api_key_env", "ALPACA_BROKER_API_KEY"))
    api_secret_env = str(funding_cfg.get("api_secret_env", "ALPACA_BROKER_API_SECRET"))
    account_id_env = str(funding_cfg.get("account_id_env", "ALPACA_BROKER_ACCOUNT_ID"))
    relationship_id_env = str(funding_cfg.get("relationship_id_env", "ALPACA_BANK_RELATIONSHIP_ID"))
    base_url = str(funding_cfg.get("broker_base_url", "https://broker-api.alpaca.markets")).rstrip("/")
    dry_run = bool(funding_cfg.get("dry_run", True))

    api_key = os.getenv(api_key_env)
    api_secret = os.getenv(api_secret_env)
    account_id = os.getenv(account_id_env)
    relationship_id = os.getenv(relationship_id_env)

    missing = [
        name
        for name, value in [
            (api_key_env, api_key),
            (api_secret_env, api_secret),
            (account_id_env, account_id),
            (relationship_id_env, relationship_id),
        ]
        if not value
    ]
    if missing:
        print(
            "Funding skipped: missing required Broker API env vars "
            + ", ".join(missing)
            + "."
        )
        return

    payload = {
        "transfer_type": "ach",
        "direction": "INCOMING",
        "timing": "immediate",
        "relationship_id": relationship_id,
        "amount": str(amount),
    }
    url = f"{base_url}/v1/accounts/{account_id}/transfers"

    if dry_run:
        print(f"Funding dry-run: POST {url} payload={payload}")
        return

    if not require_ci_toggle("ALLOW_BROKER_FUNDING", "broker ACH funding"):
        return

    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret,
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
    except requests.RequestException as ex:
        print(f"Funding request failed: {ex}")
        return

    if 200 <= resp.status_code < 300:
        print(f"Funding submitted successfully: status={resp.status_code}")
    else:
        print(f"Funding request returned status={resp.status_code}: {resp.text}")


def run_allocation(
    config: dict[str, Any],
    mode: str,
    today: date,
    force_immediate: bool,
    market_open: bool,
    investment_amount_override: Decimal | None,
    confirm_live: bool,
    execute_live_now: bool,
) -> int:
    allocation_cfg = config.get("allocation", {})
    if not allocation_cfg.get("enabled", False):
        print("Allocation disabled in YAML.")
        return 0
    is_dry_run = bool(allocation_cfg.get("dry_run", True))
    if mode == "live" and not is_dry_run and not execute_live_now:
        print("Live allocation blocked. Re-run with --execute-live-now to proceed.")
        return 2

    schedule = allocation_cfg.get("schedule", ["15", "last_day"])
    require_market_open = bool(allocation_cfg.get("require_market_open", True))
    decision = should_run_for_schedule(schedule, today, force_immediate, require_market_open, market_open)
    print(f"Allocation schedule decision: {decision.reason}")
    if not decision.run:
        return 0

    trading_cfg = config.get("trading", {})
    strategy_file = str(trading_cfg.get("strategy_file", "strategy.json"))

    creds = resolve_credentials(mode)
    trading_client = TradingClient(
        api_key=creds.api_key,
        secret_key=creds.api_secret,
        oauth_token=creds.oauth_token,
        paper=creds.paper,
    )
    account = trading_client.get_account()

    available_cash = Decimal(str(account.cash)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    reserve_cash = to_decimal(allocation_cfg.get("reserve_cash", "0"), "allocation.reserve_cash")
    min_new_cash = to_decimal(allocation_cfg.get("min_new_cash", "0"), "allocation.min_new_cash")
    min_order_notional = to_decimal(
        allocation_cfg.get("min_order_notional", "1.00"), "allocation.min_order_notional"
    )

    spendable = max(Decimal("0"), available_cash - reserve_cash)
    print(f"Allocation cash check: available={available_cash} reserve={reserve_cash} spendable={spendable}")

    if spendable < min_new_cash:
        print(f"Allocation skipped: spendable cash {spendable} is below min_new_cash {min_new_cash}.")
        return 0

    if investment_amount_override is not None:
        capped_notional = min(spendable, investment_amount_override)
    else:
        max_notional_cfg = allocation_cfg.get("max_notional_per_run", None)
        if max_notional_cfg is None:
            capped_notional = spendable
        else:
            capped_notional = min(spendable, to_decimal(max_notional_cfg, "allocation.max_notional_per_run"))

    if capped_notional <= Decimal("0"):
        print("Allocation skipped: capped notional is 0.")
        return 0

    source = "CLI --investment-amount" if investment_amount_override is not None else "YAML max_notional_per_run"
    print(f"Allocation notional source: {source}; capped_notional={capped_notional}")

    if mode == "live" and not is_dry_run:
        if confirm_live:
            print("Live confirmation acknowledged via --confirm-live.")
        elif sys.stdin.isatty():
            if not request_live_confirmation(capped_notional, strategy_file):
                print("Live allocation canceled by user.")
                return 0
        else:
            print("Live allocation requires interactive confirmation or --confirm-live.")
            return 2

    cmd = [
        sys.executable,
        "optimize_and_buy.py",
        "--mode",
        mode,
        "--run-type",
        "adhoc",
        "--config",
        strategy_file,
        "--max-notional",
        str(capped_notional),
        "--min-order-notional",
        str(min_order_notional),
    ]
    if is_dry_run:
        cmd.append("--dry-run")
    else:
        if not require_ci_toggle("ALLOW_REAL_ORDERS", "non-dry-run allocation orders"):
            return 0
        if mode == "live":
            if not require_ci_toggle("ALLOW_LIVE_TRADING", "live allocation orders"):
                return 0
            cmd.append("--confirm-live")

    print("Executing allocation command:", " ".join(cmd))
    result = subprocess.run(cmd, check=False)
    return result.returncode


def main() -> int:
    args = parse_args()
    config = load_yaml_config(Path(args.config))

    investment_amount_override: Decimal | None = None
    if args.investment_amount is not None:
        try:
            investment_amount_override = to_decimal(args.investment_amount, "--investment-amount")
        except ValueError as ex:
            print(str(ex))
            return 2

    mode = args.mode

    load_environment_for_mode(mode)
    now_et = datetime.now(EASTERN_TZ)
    today = now_et.date()

    print(f"Automation start: date_et={today.isoformat()} action={args.action} mode={mode}")

    try:
        creds = resolve_credentials(mode)
        trading_client = TradingClient(
            api_key=creds.api_key,
            secret_key=creds.api_secret,
            oauth_token=creds.oauth_token,
            paper=creds.paper,
        )
        market_open = is_market_day(trading_client, today)
    except Exception as ex:
        print(f"Unable to initialize trading calendar checks: {ex}")
        return 2

    print(f"Market day status: {market_open}")

    if args.action in {"funding", "both"}:
        run_funding(config, today, args.force_immediate, market_open)

    if args.action in {"allocation", "both"}:
        return run_allocation(
            config,
            mode,
            today,
            args.force_immediate,
            market_open,
            investment_amount_override,
            args.confirm_live,
            args.execute_live_now,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
