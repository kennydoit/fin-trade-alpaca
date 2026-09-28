from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest

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
    correlation: float | None
    zscore: float | None


@dataclass(frozen=True)
class ModeCredentials:
    api_key: str | None
    api_secret: str | None
    oauth_token: str | None
    paper: bool


@dataclass(frozen=True)
class OpenStatarbPair:
    pair_id: str
    long_symbol: str
    short_symbol: str
    long_qty: Decimal
    short_qty: Decimal
    entry_time_utc: datetime | None


@dataclass(frozen=True)
class LiquidationDecision:
    pair: OpenStatarbPair
    should_liquidate: bool
    reasons: tuple[str, ...]
    holding_days: float | None
    score: float | None
    zscore: float | None


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
            correlation = safe_float(row.get("correlation"))
            zscore = safe_float(row.get("zscore"))

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
                    correlation=correlation,
                    zscore=zscore,
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


def _decimal_qty(value) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")


def _position_side_text(position) -> str:
    side_value = getattr(position, "side", "")
    if hasattr(side_value, "value"):
        side_value = side_value.value
    return str(side_value or "").strip().lower()


def _build_pair_lookup(candidates: list[PairCandidate]) -> dict[frozenset[str], PairCandidate]:
    lookup: dict[frozenset[str], PairCandidate] = {}
    for candidate in candidates:
        lookup[frozenset((candidate.long_symbol, candidate.short_symbol))] = candidate
    return lookup


def _build_latest_entry_times_by_symbol_side(client: TradingClient, lookback_days: int) -> dict[tuple[str, str], datetime]:
    now_utc = datetime.now(timezone.utc)
    try:
        orders = client.get_orders(
            GetOrdersRequest(
                status=QueryOrderStatus.ALL,
                after=now_utc - timedelta(days=max(1, lookback_days) + 1),
                direction="desc",
                limit=500,
            )
        )
    except Exception as ex:
        print(f"Unable to load recent orders for statarb liquidation evaluation: {ex}")
        return {}

    latest_by_key: dict[tuple[str, str], datetime] = {}
    for order in orders:
        symbol = str(getattr(order, "symbol", "") or "").strip().upper()
        if not symbol:
            continue

        side_text = str(getattr(getattr(order, "side", ""), "value", getattr(order, "side", ""))).strip().lower()
        if side_text not in {"buy", "sell"}:
            continue

        ts = getattr(order, "filled_at", None) or getattr(order, "submitted_at", None)
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        key = (symbol, side_text)
        prev = latest_by_key.get(key)
        if prev is None or ts > prev:
            latest_by_key[key] = ts

    return latest_by_key


def identify_open_statarb_pairs(
    client: TradingClient,
    candidates: list[PairCandidate],
    liquidation_cfg: dict,
) -> list[OpenStatarbPair]:
    try:
        positions = list(client.get_all_positions())
    except Exception as ex:
        print(f"Unable to read positions for statarb liquidation evaluation: {ex}")
        return []

    pos_by_symbol = {str(getattr(position, "symbol", "") or "").strip().upper(): position for position in positions}
    lookup = _build_pair_lookup(candidates)
    orders_lookback_days = int(liquidation_cfg.get("orders_lookback_days", 90) or 90)
    latest_entry_by_side = _build_latest_entry_times_by_symbol_side(client, orders_lookback_days)

    open_pairs: list[OpenStatarbPair] = []
    for symbol_set, candidate in lookup.items():
        if len(symbol_set) != 2:
            continue

        long_pos = pos_by_symbol.get(candidate.long_symbol)
        short_pos = pos_by_symbol.get(candidate.short_symbol)
        if long_pos is None or short_pos is None:
            continue

        long_side = _position_side_text(long_pos)
        short_side = _position_side_text(short_pos)
        if long_side != "long" or short_side != "short":
            continue

        long_qty = abs(_decimal_qty(getattr(long_pos, "qty", "0")))
        short_qty = abs(_decimal_qty(getattr(short_pos, "qty", "0")))
        if long_qty <= 0 or short_qty <= 0:
            continue

        long_entry = latest_entry_by_side.get((candidate.long_symbol, "buy"))
        short_entry = latest_entry_by_side.get((candidate.short_symbol, "sell"))
        entry_time = None
        if long_entry and short_entry:
            entry_time = max(long_entry, short_entry)
        elif long_entry or short_entry:
            entry_time = long_entry or short_entry

        open_pairs.append(
            OpenStatarbPair(
                pair_id=candidate.pair_id,
                long_symbol=candidate.long_symbol,
                short_symbol=candidate.short_symbol,
                long_qty=long_qty,
                short_qty=short_qty,
                entry_time_utc=entry_time,
            )
        )

    return open_pairs


def evaluate_liquidation_decisions(
    open_pairs: list[OpenStatarbPair],
    candidates: list[PairCandidate],
    liquidation_cfg: dict,
    now_utc: datetime | None = None,
) -> list[LiquidationDecision]:
    if not open_pairs:
        return []

    now = now_utc or datetime.now(timezone.utc)
    candidate_by_id = {candidate.pair_id: candidate for candidate in candidates}

    max_holding_days = liquidation_cfg.get("max_holding_days")
    try:
        max_holding_days = float(max_holding_days) if max_holding_days is not None else None
    except (TypeError, ValueError):
        max_holding_days = None

    exit_abs_zscore = liquidation_cfg.get("exit_abs_zscore")
    try:
        exit_abs_zscore = float(exit_abs_zscore) if exit_abs_zscore is not None else None
    except (TypeError, ValueError):
        exit_abs_zscore = None

    decisions: list[LiquidationDecision] = []
    for open_pair in open_pairs:
        reasons: list[str] = []
        holding_days = None
        candidate = candidate_by_id.get(open_pair.pair_id)

        if open_pair.entry_time_utc is not None:
            holding_days = max(0.0, (now - open_pair.entry_time_utc).total_seconds() / 86400.0)
            if max_holding_days is not None and holding_days >= max_holding_days:
                reasons.append(f"max_holding_days reached ({holding_days:.1f}d >= {max_holding_days:.1f}d)")

        score = candidate.score if candidate is not None else None
        zscore = None
        if candidate is not None:
            # zscore may be absent for external pair feeds; missing means this rule is skipped.
            zscore = getattr(candidate, "zscore", None)

        if exit_abs_zscore is not None and zscore is not None and abs(zscore) <= exit_abs_zscore:
            reasons.append(f"spread mean-reverted (|zscore|={abs(zscore):.4f} <= {exit_abs_zscore:.4f})")

        should_liquidate = len(reasons) > 0
        decisions.append(
            LiquidationDecision(
                pair=open_pair,
                should_liquidate=should_liquidate,
                reasons=tuple(reasons),
                holding_days=holding_days,
                score=score,
                zscore=zscore,
            )
        )

    return decisions


def submit_pair_liquidation(client: TradingClient, decision: LiquidationDecision, dry_run: bool) -> list[str]:
    pair = decision.pair
    print(
        f"LIQUIDATE PLAN: {pair.pair_id} long={pair.long_symbol} qty={pair.long_qty} "
        f"short={pair.short_symbol} qty={pair.short_qty} reasons={'; '.join(decision.reasons)}"
    )
    if dry_run:
        return []

    submitted: list[str] = []
    sell_req = MarketOrderRequest(
        symbol=pair.long_symbol,
        qty=float(pair.long_qty),
        side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    )
    buy_req = MarketOrderRequest(
        symbol=pair.short_symbol,
        qty=float(pair.short_qty),
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    )

    sell_order = client.submit_order(sell_req)
    submitted.append(str(sell_order.id))
    print(f"  Submitted SELL {pair.long_symbol} close order id={sell_order.id}")

    buy_order = client.submit_order(buy_req)
    submitted.append(str(buy_order.id))
    print(f"  Submitted BUY {pair.short_symbol} close order id={buy_order.id}")
    return submitted


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

    liquidation_cfg = statarb_cfg.get("liquidation") or {}
    liquidation_enabled = bool(liquidation_cfg.get("enabled", False))
    evaluate_only = bool(liquidation_cfg.get("evaluate_only", False))
    auto_execute = bool(liquidation_cfg.get("auto_execute", False))

    if liquidation_enabled:
        open_pairs = identify_open_statarb_pairs(client, candidates, liquidation_cfg)
        decisions = evaluate_liquidation_decisions(open_pairs, candidates, liquidation_cfg)
        to_liquidate = [decision for decision in decisions if decision.should_liquidate]

        if decisions:
            print(f"Evaluated {len(decisions)} open statarb pairs for liquidation; {len(to_liquidate)} marked for exit.")
            for decision in to_liquidate:
                submit_pair_liquidation(client, decision, dry_run=True)
        else:
            print("No open statarb pairs detected for liquidation evaluation.")

        if to_liquidate and auto_execute and not args.dry_run:
            for decision in to_liquidate:
                submit_pair_liquidation(client, decision, dry_run=False)

        if evaluate_only:
            print("Liquidation evaluate_only=true; skipping new statarb entries.")
            return 0

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