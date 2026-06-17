from __future__ import annotations

import argparse
import calendar
import json
import os
import sys
import time
import csv
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Dict
from zoneinfo import ZoneInfo

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import GetCalendarRequest, MarketOrderRequest, TakeProfitRequest, StopLossRequest
from fin_trade_alpaca.env_loader import load_environment_for_mode

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


# Environment loading is delegated to `fin_trade_alpaca.env_loader.load_environment_for_mode`


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
        "--env-file",
        default=None,
        help="Path to env file (e.g. .env.paper) or 'none' to skip loading and use process env.",
    )
    parser.add_argument(
        "--target",
        choices=["paper", "live"],
        default=None,
        help="When --mode github, select which credential set to target (paper|live).",
    )
    parser.add_argument(
        "--max-notional",
        type=Decimal,
        default=None,
        help="Optional spend cap for this run. If omitted, uses all available cash.",
    )
    parser.add_argument(
        "--simulate-cash",
        type=Decimal,
        default=None,
        help="If set, skip Alpaca account lookup and simulate available cash with this value.",
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
    parser.add_argument(
        "--poll-fills",
        action="store_true",
        help="After submitting orders, poll until fills or timeout (disabled by default).",
    )
    parser.add_argument(
        "--poll-timeout",
        type=int,
        default=180,
        help="Polling timeout in seconds when --poll-fills is used. Defaults to 180.",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=5,
        help="Polling interval in seconds when --poll-fills is used. Defaults to 5.",
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


def safe_float(x):
    try:
        if x is None or x == "":
            return None
        return float(str(x).replace(",", ""))
    except Exception:
        return None


def compute_momentum_for_symbols(symbols, basis="pct_1m", clip=(-50, 200)):
    """Return a dict symbol->pct (percent) for the given basis (supports pct_1m or pct_1w)."""
    try:
        import yfinance as yf
    except Exception:
        print("yfinance not available; skipping dynamic tilt.")
        return {s: None for s in symbols}

    metrics = {}
    for sym in symbols:
        try:
            t = yf.Ticker(sym)
            period = "1mo" if basis == "pct_1m" else "7d"
            hist = None
            try:
                hist = t.history(period=period, interval="1d", actions=False)
            except Exception:
                hist = None
            pct = None
            if hist is not None and len(hist) > 0:
                closes = list(hist["Close"].dropna())
                if len(closes) >= 2:
                    first = safe_float(closes[0])
                    last = safe_float(closes[-1])
                    if first is not None and first != 0:
                        pct = (last - first) / first * 100.0
            # fallback: try info fields
            if pct is None:
                info = {}
                try:
                    info = t.info or {}
                except Exception:
                    info = {}
                if basis == "pct_1m":
                    pct = safe_float(info.get("monthChangePercent") or info.get("regularMarketChangePercent"))
                else:
                    pct = safe_float(info.get("weekChangePercent") or info.get("regularMarketChangePercent"))

            if pct is not None:
                lo, hi = clip
                pct = max(lo, min(hi, pct))
            metrics[sym] = pct
        except Exception:
            metrics[sym] = None

    return metrics


def apply_dynamic_tilt(strategy_config: dict, symbol_weights: Dict[str, Decimal], tilt_cfg: dict, spendable_cash: Decimal):
    """Apply dynamic tilt across combined core+growth assets and return new symbol_weights and a report list."""
    # collect symbols from core and growth
    buckets = strategy_config.get("buckets", {})
    combined = []
    for name in ("core", "growth"):
        assets = buckets.get(name, {}).get("assets", {})
        for s in assets.keys():
            combined.append(s.strip().upper())

    combined = sorted(set(combined))
    if not combined:
        return symbol_weights, []

    basis = tilt_cfg.get("basis", "pct_1m")
    alpha = float(tilt_cfg.get("alpha", 0.10))
    clip = tuple(tilt_cfg.get("clip", [-50, 200]))
    cap = float(tilt_cfg.get("cap_per_asset", 0.10))

    # compute momentum
    metrics = compute_momentum_for_symbols(combined, basis=basis, clip=clip)

    # build base weights for combined set
    base_weights = {s: float(symbol_weights.get(s, Decimal("0"))) for s in combined}
    combined_total = sum(base_weights.values())
    if combined_total <= 0:
        return symbol_weights, []

    # invert momentum: lower pct -> higher priority
    inv = {}
    for s in combined:
        m = metrics.get(s)
        if m is None:
            inv[s] = 0.0
        else:
            inv[s] = -float(m)

    min_inv = min(inv.values())
    pvals = {s: inv[s] - min_inv for s in combined}
    total_p = sum(pvals.values())
    if total_p == 0:
        norm = {s: 1.0 / len(combined) for s in combined}
    else:
        norm = {s: (pvals[s] / total_p) for s in combined}

    # tilt multipliers
    tf = {}
    for s in combined:
        tf_val = 1.0 + alpha * norm[s]
        # cap multiplier to [1-cap, 1+cap]
        tf_val = max(1.0 - cap, min(1.0 + cap, tf_val))
        tf[s] = tf_val

    # apply multipliers to base weights and renormalize to combined_total
    tilted = {s: base_weights[s] * tf[s] for s in combined}
    tilted_sum = sum(tilted.values())
    if tilted_sum == 0:
        # fallback to base
        new_weights = dict(symbol_weights)
        report = []
        for s in combined:
            report.append((s, base_weights[s], base_weights[s], metrics.get(s)))
        return new_weights, report

    scale = combined_total / tilted_sum
    new_weights = dict(symbol_weights)
    report = []
    for s in combined:
        new_w = Decimal(str(tilted[s] * scale)).quantize(Decimal("0.0000001"))
        orig_w = Decimal(str(base_weights[s]))
        new_weights[s] = new_w
        orig_invest = (spendable_cash * orig_w).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        new_invest = (spendable_cash * new_w).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        report.append({
            "symbol": s,
            "original_weight": float(orig_w),
            "tilt_weight": float(new_w),
            "original_investment": f"{orig_invest}",
            "tilt_investment": f"{new_invest}",
            "metric": metrics.get(s),
        })

    return new_weights, report


def write_tilt_report(report_rows, today: date, mode: str):
    if not report_rows:
        return None
    repo_root = Path(__file__).resolve().parents[2]
    out_dir = repo_root.joinpath("reports", "tilt_reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"tilt_report_{mode}_{today.strftime('%Y%m%d')}.txt"
    out_path = out_dir.joinpath(fname)

    # prepare text table
    cols = ["symbol", "original_weight", "tilt_weight", "original_investment", "tilt_investment", "metric"]
    # compute string values and column widths
    rows_str = []
    col_widths = {c: len(c) for c in cols}
    for r in report_rows:
        row = {
            "symbol": str(r.get("symbol") or ""),
            "original_weight": f"{r.get('original_weight'):.7f}" if r.get("original_weight") is not None else "",
            "tilt_weight": f"{r.get('tilt_weight'):.7f}" if r.get("tilt_weight") is not None else "",
            "original_investment": str(r.get("original_investment") or ""),
            "tilt_investment": str(r.get("tilt_investment") or ""),
            "metric": f"{r.get('metric'):.4f}" if r.get("metric") is not None else "",
        }
        for k, v in row.items():
            col_widths[k] = max(col_widths[k], len(v))
        rows_str.append(row)

    # build table lines
    sep = " | "
    header = sep.join(c.ljust(col_widths[c]) for c in cols)
    divider = "-+-".join("-" * col_widths[c] for c in cols)
    lines = [header, divider]
    for row in rows_str:
        line = sep.join(row[c].ljust(col_widths[c]) for c in cols)
        lines.append(line)

    # write to file
    with out_path.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return out_path


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


def find_latest_screener_csv(repo_root: Path):
    dirpath = repo_root.joinpath("reports", "screener_results")
    if not dirpath.exists():
        return None
    csvs = list(dirpath.glob("*.csv"))
    if not csvs:
        return None
    csvs_sorted = sorted(csvs, key=lambda p: p.stat().st_mtime, reverse=True)
    return csvs_sorted[0]


def pick_top_n_from_screener(csv_path: Path, n: int, existing_symbols: set[str] | None = None):
    """Return the top N symbols from a screener CSV.

    Prefer the prediction score (`pred_ret`) because it reflects the ranked
    model output. Fall back to `avg_ret` and other momentum fields only when
    needed. Also skip symbols that are already held in the account.
    """
    rows = []
    existing = {s.strip().upper() for s in (existing_symbols or []) if s and str(s).strip()}
    try:
        with csv_path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                sym = (r.get("symbol") or "").strip().upper()
                if not sym or sym in existing:
                    continue
                # price fields: regularMarketPrice, close, eodprice
                price = safe_float(r.get("regularMarketPrice") or r.get("close") or r.get("eodprice"))
                # Prefer the model ranking score first, then historical average return.
                score = None
                for fld in ("pred_ret", "avg_ret", "avg_pred", "pct_1w", "pct_1m"):
                    val = safe_float(r.get(fld))
                    if val is not None:
                        score = val
                        break
                rows.append((sym, price, score))
    except Exception:
        return []

    rows = [r for r in rows if r[2] is not None and r[1] is not None]
    if not rows:
        return []

    rows_sorted = sorted(rows, key=lambda x: (x[2], x[0]), reverse=True)
    top = rows_sorted[:n]
    return [(r[0], r[1]) for r in top]


def submit_short_term_orders(client: TradingClient, orders: list[dict], dry_run: bool) -> list[str]:
    """orders: list of dicts with keys: symbol, notional, price, stop_pct, take_pct"""
    submitted = []
    if not orders:
        return submitted
    print("Short-term order plan:")
    for o in orders:
        print(f"  BUY {o['symbol']}: ${o['notional']} with stop={o['stop_pct']}% take={o['take_pct']}%")

    if dry_run:
        print("Dry run enabled; no short-term orders submitted.")
        return submitted

    for o in orders:
        symbol = o["symbol"]
        notional = o["notional"]
        price = o.get("price")
        stop_pct = float(o.get("stop_pct", 0.0))
        take_pct = float(o.get("take_pct", 0.0))
        take_price = None
        stop_price = None
        try:
            if price is not None:
                take_price = float(price) * (1.0 + take_pct / 100.0)
                stop_price = float(price) * (1.0 + stop_pct / 100.0)

            tp = TakeProfitRequest(limit_price=take_price) if take_price is not None else None
            sl = StopLossRequest(stop_price=stop_price) if stop_price is not None else None

            req = MarketOrderRequest(
                symbol=symbol,
                notional=float(notional),
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
                take_profit=tp,
                stop_loss=sl,
            )
            order = client.submit_order(req)
            submitted.append(order.id)
            print(f"Submitted short-term {symbol} order id={order.id} notional=${notional}")
        except APIError as ex:
            print(f"Short-term order failed for {symbol}: {ex}")
        except Exception as ex:
            print(f"Short-term order unexpected error for {symbol}: {ex}")

    return submitted


def submit_orders(
    client: TradingClient,
    notionals: Dict[str, Decimal],
    dry_run: bool,
) -> list[str]:
    if not notionals:
        print("No orders to submit after min-order filter. Leaving cash unallocated.")
        return []

    print("Order plan:")
    for symbol, notional in sorted(notionals.items()):
        print(f"  BUY {symbol}: ${notional}")

    if dry_run:
        print("Dry run enabled; no orders submitted.")
        return []

    submitted_ids: list[str] = []
    for symbol, notional in sorted(notionals.items()):
        req = MarketOrderRequest(
            symbol=symbol,
            notional=float(notional),
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        try:
            order = client.submit_order(req)
            submitted_ids.append(order.id)
            print(f"Submitted {symbol} order id={order.id} notional=${notional}")
        except APIError as ex:
            print(f"Order failed for {symbol}: {ex}")

    return submitted_ids


def poll_orders_for_fills(client: TradingClient, order_ids: list[str], timeout_sec: int = 180, poll_interval: int = 5) -> dict:
    """Poll Alpaca for the given order IDs until all are filled or timeout.

    Returns a mapping order_id -> final_status.
    """
    if not order_ids:
        return {}

    deadline = time.time() + float(timeout_sec)
    pending = set(order_ids)
    final_status: dict = {}

    def _find_order_by_id(oid: str):
        # Try common SDK entrypoints in order of preference, falling back to list-and-find.
        try:
            return client.get_order(oid)
        except Exception:
            pass

        try:
            orders = client.get_orders()
            return next((x for x in orders if getattr(x, "id", None) == oid), None)
        except Exception:
            pass

        try:
            orders = client.get_all_orders()
            return next((x for x in orders if getattr(x, "id", None) == oid), None)
        except Exception:
            pass

        return None

    def _status_name_from_order(o) -> str:
        if o is None:
            return "unknown"
        st = getattr(o, "status", None)
        if st is None:
            return "unknown"
        if hasattr(st, "name"):
            return str(st.name).lower()
        return str(st).lower()

    print(f"Polling for fills for {len(order_ids)} orders, timeout={timeout_sec}s")
    while pending and time.time() < deadline:
        for oid in list(pending):
            try:
                o = _find_order_by_id(oid)
                status = _status_name_from_order(o)
            except Exception as ex:
                status = f"error:{ex}"

            print(f"  order {oid} status={status}")

            if status in ("filled", "canceled", "rejected", "expired"):
                final_status[oid] = status
                pending.remove(oid)

        if pending:
            time.sleep(max(1, poll_interval))

    # For any remaining pending orders, attempt one last time to capture a status
    for oid in pending:
        try:
            o = _find_order_by_id(oid)
            final_status[oid] = _status_name_from_order(o)
        except Exception:
            final_status[oid] = "unknown"

    return final_status


def choose_spendable_cash(available_cash: Decimal, max_notional: Decimal | None) -> Decimal:
    if available_cash <= Decimal("0"):
        return Decimal("0")
    if max_notional is None:
        return available_cash
    if max_notional <= Decimal("0"):
        return Decimal("0")
    return min(available_cash, max_notional)


def log_strategy_summary(strategy_config: dict) -> None:
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


def main() -> int:
    args = parse_args()
    explicit_mode = "--mode" in sys.argv
    explicit_dry_run = "--dry-run" in sys.argv

    try:
        strategy_config = load_strategy_config(Path(args.config))
        config_mode = (strategy_config.get("mode") or "").strip().lower()
        if config_mode in {"paper", "live"} and not explicit_mode:
            args.mode = config_mode

        config_dry_run = strategy_config.get("dry_run")
        if isinstance(config_dry_run, bool) and not explicit_dry_run:
            args.dry_run = config_dry_run
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        strategy_config = None
        config_mode = None

    load_environment_for_mode(args.mode, args.target, args.env_file)

    try:
        require_ci_approval_for_real_orders(args.mode, args.dry_run)
    except PermissionError as ex:
        print(str(ex))
        return 2

    if args.mode == "live" and not args.dry_run and not args.confirm_live:
        print("Live mode requires --confirm-live unless --dry-run is set.")
        return 2

    client = None
    if args.simulate_cash is None:
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
        if client is None:
            print("Scheduled run requires Alpaca client; provide credentials or remove --run-type scheduled.")
            return 2
        run_today, reason = should_run_today(client, today_et)
        print(reason)
        if not run_today:
            return 0

    if strategy_config is None:
        try:
            strategy_config = load_strategy_config(Path(args.config))
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as ex:
            print(f"Config error: {ex}")
            return 2

    log_strategy_summary(strategy_config)

    if args.simulate_cash is not None:
        available_cash = Decimal(str(args.simulate_cash)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        print(f"Simulating account cash=${available_cash}")
    else:
        account = client.get_account()
        available_cash = Decimal(str(account.cash)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

    total_investment = strategy_config.get("total_investment")
    if total_investment is not None and Decimal(str(total_investment)) > 0:
        spendable_cash = choose_spendable_cash(available_cash, Decimal(str(total_investment)))
        print(f"Using total_investment=${total_investment} from config for this run.")
    else:
        spendable_cash = choose_spendable_cash(available_cash, args.max_notional)

    print(f"Account cash=${available_cash}; spendable cash this run=${spendable_cash}")
    if spendable_cash <= Decimal("0"):
        print("No spendable cash available. Exiting.")
        return 0

    # Handle short-term allocation (use screener results)
    short_cfg = strategy_config.get("short_term_settings", {})
    short_bucket = strategy_config.get("buckets", {}).get("short_term", {})
    short_weight = Decimal(str(short_bucket.get("weight", 0)))
    short_order_ids: list[str] = []
    # determine number_of_assets: prefer bucket-level setting, fall back to short_term_settings
    n_assets = int(short_bucket.get("number_of_assets") or short_cfg.get("number_of_assets", 0))
    if short_weight > 0 and n_assets > 0:
        repo_root = Path(__file__).resolve().parents[2]
        # determine screener CSV path: prefer explicit path in bucket.screener
        screener_path = short_bucket.get("screener") or short_cfg.get("screener")
        csv_path = None
        if screener_path:
            p = Path(screener_path)
            if not p.is_absolute():
                csv_path = repo_root.joinpath(screener_path)
            else:
                csv_path = p
            if not csv_path.exists():
                print(f"Configured screener file {csv_path} not found. Attempting to find latest screener CSV.")
                csv_path = None

        if csv_path is None:
            csv_path = find_latest_screener_csv(repo_root)

        if csv_path is None:
            print("Short-term requested but no screener CSV found in reports/screener_results. Skipping short-term allocation.")
        else:
            existing_symbols = set()
            if client is not None:
                try:
                    existing_symbols = {p.symbol.strip().upper() for p in client.get_all_positions()}
                except Exception as ex:
                    print(f"Unable to read existing positions for short-term filter: {ex}")

            n = int(n_assets)
            picks = pick_top_n_from_screener(csv_path, n, existing_symbols=existing_symbols)
            if not picks:
                print(f"No valid picks found in screener {csv_path}. Skipping short-term allocation.")
            else:
                short_alloc = (spendable_cash * short_weight).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
                # decide per-asset allocation based on asset_weights (only 'equal' supported)
                asset_weights = (short_bucket.get("asset_weights") or short_cfg.get("asset_weights") or "equal")
                if asset_weights != "equal":
                    print(f"asset_weights='{asset_weights}' not supported, defaulting to 'equal'.")
                stop_pct = short_bucket.get("stop_loss", short_cfg.get("stop_loss", -3.0))
                take_pct = short_bucket.get("take_profit", short_cfg.get("take_profit", 15.0))
                per_asset = (short_alloc / Decimal(str(len(picks)))).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
                orders = []
                for sym, price in picks:
                    orders.append({
                        "symbol": sym,
                        "notional": per_asset,
                        "price": price,
                        "stop_pct": float(stop_pct),
                        "take_pct": float(take_pct),
                    })

                if short_alloc <= Decimal("0"):
                    print("Short-term allocation computed as $0. Skipping short-term orders.")
                else:
                    print(f"Allocating ${short_alloc} to short-term picks ({len(picks)} assets)")
                    if client is None and not args.dry_run:
                        print("No Alpaca client available to submit short-term orders. Use --simulate-cash or provide credentials.")
                        return 2
                    short_order_ids = submit_short_term_orders(client, orders, args.dry_run)
                    # reduce spendable cash for the remaining allocations
                    spent = sum(Decimal(str(o["notional"])) for o in orders)
                    spendable_cash = (spendable_cash - spent).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

    symbol_weights = flatten_symbol_weights(strategy_config)
    if not symbol_weights:
        print("No tradable symbols configured. Exiting.")
        return 0
    # Optionally apply dynamic tilt across core+growth assets
    tilt_cfg = strategy_config.get("dynamic_tilt") or {}
    tilt_report_path = None
    if tilt_cfg.get("enabled"):
        try:
            new_weights, report_rows = apply_dynamic_tilt(strategy_config, symbol_weights, tilt_cfg, spendable_cash)
            if report_rows:
                tilt_report_path = write_tilt_report(report_rows, today_et, args.mode)
                print(f"Wrote tilt report to {tilt_report_path}")
            symbol_weights = new_weights
        except Exception as ex:
            print(f"Dynamic tilt failed: {ex}. Continuing with base weights.")

    notionals = distribute_notionals(spendable_cash, symbol_weights, args.min_order_notional)
    order_ids = submit_orders(client, notionals, args.dry_run)

    if args.poll_fills and order_ids and not args.dry_run:
        statuses = poll_orders_for_fills(client, order_ids, timeout_sec=args.poll_timeout, poll_interval=args.poll_interval)
        print("Final order statuses:")
        for oid, st in statuses.items():
            print(f"  {oid}: {st}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
