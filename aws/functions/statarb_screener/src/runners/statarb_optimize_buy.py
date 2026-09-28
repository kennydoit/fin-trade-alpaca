from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from fin_trade_alpaca.env_loader import load_environment_for_mode


@dataclass(frozen=True)
class PairCandidate:
    pair_id: str
    long_symbol: str
    short_symbol: str
    score: float
    pair_notional: Decimal | None
    hedge_ratio: float
    long_weight: float | None
    short_weight: float | None
    long_price: float | None
    short_price: float | None


@dataclass(frozen=True)
class ModeCredentials:
    api_key: str | None
    api_secret: str | None
    oauth_token: str | None
    paper: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Select and execute statarb pair trades using a dedicated candidate feed and config."
    )
    parser.add_argument("--mode", choices=["paper", "live"], default="paper")
    parser.add_argument("--run-type", choices=["adhoc", "scheduled"], default="adhoc")
    parser.add_argument("--config", default="configs/statarb_strategy.json")
    parser.add_argument("--pairs-csv", default=None, help="Path to the statarb pair-candidate CSV feed.")
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--max-pairs", type=int, default=None, help="Optional hard cap on selected pairs.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
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


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_path(path_value: str | None, default_path: str) -> Path:
    candidate = Path(path_value or default_path)
    if candidate.is_absolute():
        return candidate

    if candidate.exists():
        return candidate.resolve()

    return repo_root().joinpath(candidate).resolve()


def load_statarb_config(config_path: Path) -> dict:
    if not config_path.exists():
        raise FileNotFoundError(f"Statarb config not found at {config_path}.")

    with config_path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)

    statarb_cfg = config.get("statarb")
    if not isinstance(statarb_cfg, dict):
        raise ValueError("Statarb config must contain a top-level 'statarb' object.")

    if not statarb_cfg.get("enabled", False):
        raise ValueError("Statarb pipeline is disabled in config.")

    if not statarb_cfg.get("allow_shorting", True):
        raise ValueError("Statarb pairs require allow_shorting=true.")

    return config


def safe_float(value):
    try:
        if value is None or value == "":
            return None
        return float(str(value).replace(",", ""))
    except Exception:
        return None


def load_pair_candidates(csv_path: Path) -> list[PairCandidate]:
    if not csv_path.exists():
        raise FileNotFoundError(f"Statarb pair feed not found at {csv_path}")

    candidates: list[PairCandidate] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            long_symbol = str(row.get("long_symbol") or "").strip().upper()
            short_symbol = str(row.get("short_symbol") or "").strip().upper()
            if not long_symbol or not short_symbol:
                continue

            score = safe_float(row.get("score"))
            if score is None:
                continue

            pair_notional = safe_float(row.get("pair_notional"))
            hedge_ratio = safe_float(row.get("hedge_ratio"))
            long_weight = safe_float(row.get("long_weight"))
            short_weight = safe_float(row.get("short_weight"))
            long_price = safe_float(row.get("long_price"))
            short_price = safe_float(row.get("short_price"))

            candidates.append(
                PairCandidate(
                    pair_id=str(row.get("pair_id") or f"{long_symbol}_{short_symbol}").strip(),
                    long_symbol=long_symbol,
                    short_symbol=short_symbol,
                    score=score,
                    pair_notional=Decimal(str(pair_notional)).quantize(Decimal("0.01")) if pair_notional else None,
                    hedge_ratio=hedge_ratio if hedge_ratio is not None else 1.0,
                    long_weight=long_weight,
                    short_weight=short_weight,
                    long_price=long_price,
                    short_price=short_price,
                )
            )

    return candidates


def filter_existing_positions(client: TradingClient, candidates: list[PairCandidate], enabled: bool) -> list[PairCandidate]:
    if not enabled:
        return candidates

    try:
        existing_symbols = {position.symbol.strip().upper() for position in client.get_all_positions()}
    except Exception as ex:
        print(f"Unable to read existing positions for statarb filter: {ex}")
        return candidates

    filtered = [
        candidate
        for candidate in candidates
        if candidate.long_symbol not in existing_symbols and candidate.short_symbol not in existing_symbols
    ]
    removed = len(candidates) - len(filtered)
    if removed:
        print(f"Excluded {removed} statarb pairs overlapping current positions.")
    return filtered


def select_pair_candidates(candidates: list[PairCandidate], max_pairs: int, min_score: float = float("-inf")) -> list[PairCandidate]:
    selected: list[PairCandidate] = []
    used_symbols: set[str] = set()

    for candidate in sorted(candidates, key=lambda item: item.score, reverse=True):
        if candidate.score < min_score:
            continue
        if candidate.long_symbol in used_symbols or candidate.short_symbol in used_symbols:
            continue

        selected.append(candidate)
        used_symbols.add(candidate.long_symbol)
        used_symbols.add(candidate.short_symbol)

        if len(selected) >= max_pairs:
            break

    return selected


def derive_leg_weights(candidate: PairCandidate) -> tuple[float, float]:
    if candidate.long_weight is not None and candidate.short_weight is not None:
        total = candidate.long_weight + candidate.short_weight
        if total > 0:
            return candidate.long_weight / total, candidate.short_weight / total

    hedge_ratio = max(candidate.hedge_ratio, 0.0) or 1.0
    long_weight = 1.0 / (1.0 + hedge_ratio)
    short_weight = hedge_ratio / (1.0 + hedge_ratio)
    return long_weight, short_weight


def build_pair_plan(candidates: list[PairCandidate], total_pair_notional: Decimal) -> list[dict]:
    if not candidates:
        return []

    per_pair_default = (total_pair_notional / Decimal(len(candidates))).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    plan = []

    for candidate in candidates:
        pair_notional = candidate.pair_notional or per_pair_default
        long_weight, short_weight = derive_leg_weights(candidate)
        long_notional = (pair_notional * Decimal(str(long_weight))).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        short_notional = (pair_notional - long_notional).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

        plan.append(
            {
                "pair_id": candidate.pair_id,
                "long_symbol": candidate.long_symbol,
                "short_symbol": candidate.short_symbol,
                "score": candidate.score,
                "pair_notional": pair_notional,
                "long_notional": long_notional,
                "short_notional": short_notional,
                "long_price": candidate.long_price,
                "short_price": candidate.short_price,
            }
        )

    return plan


def get_market_quote(data_client: StockHistoricalDataClient, symbol: str):
    request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
    quote = data_client.get_stock_latest_quote(request)
    return quote.get(symbol)


def submit_leg_order(
    client: TradingClient,
    data_client: StockHistoricalDataClient | None,
    symbol: str,
    notional: Decimal,
    side: OrderSide,
    fallback_price: float | None,
    dry_run: bool,
):
    print(f"  {side.value.upper()} {symbol}: ${notional}")
    if dry_run:
        return None

    current_price = fallback_price
    if data_client is not None:
        try:
            quote = get_market_quote(data_client, symbol)
            if quote is not None:
                current_price = float(quote.ask_price or quote.bid_price or current_price or 0.0)
        except Exception as ex:
            print(f"  {symbol}: could not fetch live quote ({ex}); using fallback price {current_price}")

    if current_price is None or current_price <= 0:
        print(f"  WARNING {symbol}: invalid price, skipping leg")
        return None

    qty = float(notional) / float(current_price)
    request = MarketOrderRequest(symbol=symbol, qty=qty, side=side, time_in_force=TimeInForce.DAY)
    order = client.submit_order(request)
    print(f"  Submitted {side.value.upper()} {symbol} order id={order.id} qty={qty:.6f}")
    return order.id


def run_statarb_optimize_buy() -> int:
    args = parse_args()
    env_file = None if args.env_file in (None, "none") else args.env_file
    load_environment_for_mode(args.mode, env_file)

    config_path = resolve_path(args.config, "configs/statarb_strategy.json")
    config = load_statarb_config(config_path)
    statarb_cfg = config["statarb"]

    pair_feed_path = resolve_path(args.pairs_csv, statarb_cfg.get("pairs_csv", "/tmp/statarb_pairs.csv"))
    candidates = load_pair_candidates(pair_feed_path)

    creds = resolve_credentials(args.mode)
    client = TradingClient(
        api_key=creds.api_key,
        secret_key=creds.api_secret,
        oauth_token=creds.oauth_token,
        paper=creds.paper,
    )
    data_client = None
    if creds.api_key and creds.api_secret:
        try:
            data_client = StockHistoricalDataClient(api_key=creds.api_key, secret_key=creds.api_secret)
        except Exception as ex:
            print(f"WARNING: statarb data client unavailable: {ex}")

    if not args.dry_run and not args.confirm_live and args.mode == "live":
        raise ValueError("Live mode requires --confirm-live.")

    candidates = filter_existing_positions(client, candidates, bool(statarb_cfg.get("exclude_existing_positions", True)))

    max_pairs = int(args.max_pairs or statarb_cfg.get("max_pairs", 2))
    min_score = safe_float(statarb_cfg.get("min_score")) or float("-inf")
    selected = select_pair_candidates(candidates, max_pairs=max_pairs, min_score=min_score)

    if not selected:
        print("No statarb pairs selected.")
        return 0

    total_pair_notional = Decimal(str(statarb_cfg.get("total_pair_notional", 1000))).quantize(Decimal("0.01"))
    plan = build_pair_plan(selected, total_pair_notional=total_pair_notional)

    print("Selected statarb pair plan:")
    for pair in plan:
        print(
            f"PAIR PLAN: {pair['pair_id']} score={pair['score']:.4f} "
            f"long={pair['long_symbol']} short={pair['short_symbol']} "
            f"pair_notional=${pair['pair_notional']} "
            f"long_leg=${pair['long_notional']} short_leg=${pair['short_notional']}"
        )

    if args.dry_run:
        print("Dry run enabled; no statarb orders submitted.")
        return 0

    for pair in plan:
        submit_leg_order(
            client,
            data_client,
            pair["long_symbol"],
            pair["long_notional"],
            OrderSide.BUY,
            pair.get("long_price"),
            dry_run=False,
        )
        submit_leg_order(
            client,
            data_client,
            pair["short_symbol"],
            pair["short_notional"],
            OrderSide.SELL,
            pair.get("short_price"),
            dry_run=False,
        )

    print("Statarb optimize/buy completed successfully.")
    return 0


def main() -> int:
    return run_statarb_optimize_buy()


if __name__ == "__main__":
    raise SystemExit(main())