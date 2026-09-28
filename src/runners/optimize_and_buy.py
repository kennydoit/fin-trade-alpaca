from __future__ import annotations

import argparse
import calendar
import json
import os
import re
import sys
import time
import csv
import math
import importlib.util
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any, Dict
from urllib import error as urlerror
from urllib import request as urlrequest
from zoneinfo import ZoneInfo

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.trading.requests import GetCalendarRequest, GetOrdersRequest, MarketOrderRequest, TakeProfitRequest, StopLossRequest
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest
from fin_trade_alpaca.env_loader import load_environment_for_mode

try:
    from yfinance.screener.dedup_by_correlation import dedup_ranked_symbols
except ModuleNotFoundError:
    dedup_module_path = Path(__file__).resolve().parents[1] / "yfinance" / "screener" / "dedup_by_correlation.py"
    dedup_spec = importlib.util.spec_from_file_location("local_dedup_by_correlation", dedup_module_path)
    dedup_module = importlib.util.module_from_spec(dedup_spec)
    dedup_spec.loader.exec_module(dedup_module)
    dedup_ranked_symbols = dedup_module.dedup_ranked_symbols

EASTERN_TZ = ZoneInfo("America/New_York")
ORDER_SUMMARY_PREFIX = "ORDER_SUMMARY|"


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


def should_run_today(  # noqa: PLR0911 - branchy by design; one return per outcome keeps logging explicit
    client: TradingClient, today: date, trading_frequency: str = "monthly"
) -> tuple[bool, str]:
    trading_frequency = trading_frequency.lower()

    if trading_frequency == "daily":
        market_open_today = get_market_open_days(client, today, today)
        if market_open_today:
            return True, f"Date gate passed (daily trading, market open on {today.isoformat()})."
        return False, f"Date gate skipped (market closed on {today.isoformat()})."

    if trading_frequency == "weekly":
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)

        market_days = sorted(get_market_open_days(client, week_start, week_end))
        if not market_days:
            return False, f"Date gate skipped (no market days this week starting {week_start.isoformat()})."

        first_market_day = market_days[0]
        if today == first_market_day:
            return True, f"Date gate passed (weekly trading, first market day of week: {first_market_day.isoformat()})."
        return False, (
            f"Date gate skipped (weekly trading, waiting for first market day: {first_market_day.isoformat()})."
        )

    if trading_frequency == "monthly":
        effective_days = compute_effective_trade_days(client, today.year, today.month)
        reason = ", ".join(f"{k}={v.isoformat()}" for k, v in effective_days.items())
        if today in set(effective_days.values()):
            return True, f"Date gate passed ({reason})."
        return False, f"Date gate skipped. Today={today.isoformat()} with targets {reason}."

    raise ValueError(f"Invalid trading_frequency: {trading_frequency}. Must be 'daily', 'weekly', or 'monthly'.")


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


def safe_int(x, default: int) -> int:
    try:
        if x is None or x == "":
            return default
        return int(x)
    except Exception:
        return default


def clamp_float(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def emit_order_summary(summary: dict[str, Any]) -> None:
    print(f"{ORDER_SUMMARY_PREFIX}{json.dumps(summary, sort_keys=True, default=str)}")


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


def get_ai_decision_config(strategy_config: dict, mode: str) -> dict | None:
    """Return normalized AI decision config when enabled for this run, else None."""
    raw_cfg = strategy_config.get("ai_decision") or {}
    if not raw_cfg.get("enabled", False):
        return None

    advisory_mode = str(raw_cfg.get("mode", "advisory")).strip().lower()
    if advisory_mode != "advisory":
        print(f"AI decision mode '{advisory_mode}' is not supported yet. Skipping AI layer.")
        return None

    live_mode_policy = str(raw_cfg.get("live_mode_policy", "paper_only")).strip().lower()
    if mode == "live" and live_mode_policy != "allow_live":
        print("AI decision layer disabled for live mode by live_mode_policy.")
        return None

    api_key_env = str(raw_cfg.get("api_key_env", "PERPLEXITY_API_KEY")).strip()
    if not os.getenv(api_key_env):
        print(f"AI decision layer enabled but {api_key_env} is not set. Skipping AI layer.")
        return None

    timeout_seconds = raw_cfg.get("timeout_seconds", 8)
    max_candidates = raw_cfg.get("max_candidates", 25)
    max_tokens = raw_cfg.get("max_tokens", 500)
    temperature = raw_cfg.get("temperature", 0.0)

    try:
        timeout_seconds = max(1, int(timeout_seconds))
    except (TypeError, ValueError):
        timeout_seconds = 8

    try:
        max_candidates = max(1, int(max_candidates))
    except (TypeError, ValueError):
        max_candidates = 25

    try:
        max_tokens = max(50, int(max_tokens))
    except (TypeError, ValueError):
        max_tokens = 500

    try:
        temperature = float(temperature)
    except (TypeError, ValueError):
        temperature = 0.0

    apply_in_paper = bool(raw_cfg.get("apply_in_paper", False))
    default_reduce_fraction = safe_float(raw_cfg.get("default_reduce_fraction"))
    if default_reduce_fraction is None:
        default_reduce_fraction = 0.25
    default_reduce_fraction = clamp_float(float(default_reduce_fraction), 0.0, 0.95)

    max_reduce_fraction = safe_float(raw_cfg.get("max_reduce_fraction"))
    if max_reduce_fraction is None:
        max_reduce_fraction = 0.50
    max_reduce_fraction = clamp_float(float(max_reduce_fraction), 0.0, 0.95)

    max_veto_fraction = safe_float(raw_cfg.get("max_veto_fraction"))
    if max_veto_fraction is None:
        max_veto_fraction = 0.50
    max_veto_fraction = clamp_float(float(max_veto_fraction), 0.0, 0.95)

    min_remaining_orders = safe_int(raw_cfg.get("min_remaining_orders"), 1)
    min_remaining_orders = max(0, min_remaining_orders)

    return {
        "mode": advisory_mode,
        "api_key_env": api_key_env,
        "endpoint": str(raw_cfg.get("endpoint", "https://api.perplexity.ai/chat/completions")).strip(),
        "model": str(raw_cfg.get("model", "sonar-pro")).strip(),
        "timeout_seconds": timeout_seconds,
        "max_candidates": max_candidates,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "min_confidence": safe_float(raw_cfg.get("min_confidence")),
        "apply_in_paper": apply_in_paper,
        "default_reduce_fraction": default_reduce_fraction,
        "max_reduce_fraction": max_reduce_fraction,
        "max_veto_fraction": max_veto_fraction,
        "min_remaining_orders": min_remaining_orders,
    }


def _extract_json_object(raw_text: str) -> dict | None:
    """Extract the first JSON object from model text, including fenced output."""
    text = (raw_text or "").strip()
    if not text:
        return None

    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            text = "\n".join(lines[1:-1]).strip()

    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None

    try:
        payload = json.loads(match.group(0))
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        return None


def run_ai_advisory(
    ai_cfg: dict,
    section: str,
    candidates: list[dict],
    strategy_config: dict,
    mode: str,
    dry_run: bool,
    report_root: Path,
) -> tuple[dict | None, Path | None]:
    """Run non-blocking AI advisory and write a report file for auditability."""
    if not candidates:
        return None, None

    limited_candidates = candidates[: ai_cfg["max_candidates"]]
    request_payload: dict[str, Any] = {
        "date_et": datetime.now(EASTERN_TZ).isoformat(),
        "mode": mode,
        "dry_run": dry_run,
        "section": section,
        "trading_frequency": strategy_config.get("trading_frequency", "monthly"),
        "constraints": [
            "Do not add symbols.",
            "Do not increase notional.",
            "Only advisory feedback.",
        ],
        "candidates": limited_candidates,
    }

    user_prompt = (
        "Review these potential orders and provide an advisory-only risk review. "
        "Return valid JSON with keys: overall_decision, confidence, symbol_feedback, notes. "
        "overall_decision should be one of approve|mixed|caution. "
        "symbol_feedback should be a list of {symbol, action, reason} where action is approve|reduce|veto. "
        "Do not include markdown.\n\n"
        f"INPUT:\n{json.dumps(request_payload, separators=(",", ":"))}"
    )

    body = {
        "model": ai_cfg["model"],
        "temperature": ai_cfg["temperature"],
        "max_tokens": ai_cfg["max_tokens"],
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a conservative US equities risk reviewer. "
                    "Return only JSON and keep explanations brief."
                ),
            },
            {"role": "user", "content": user_prompt},
        ],
    }

    report_dir = report_root.joinpath("reports", "ai_decisions")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir.joinpath(
        f"ai_advisory_{section}_{mode}_{datetime.now(EASTERN_TZ).strftime('%Y%m%d_%H%M%S')}.json"
    )

    raw_response_text = ""
    advisory_json: dict | None = None
    error_text = None

    try:
        req = urlrequest.Request(
            ai_cfg["endpoint"],
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {os.getenv(ai_cfg['api_key_env'])}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlrequest.urlopen(req, timeout=ai_cfg["timeout_seconds"]) as resp:
            response_body = resp.read().decode("utf-8", errors="replace")
        response_json = json.loads(response_body)
        raw_response_text = str(((response_json.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        advisory_json = _extract_json_object(raw_response_text)

        min_confidence = ai_cfg.get("min_confidence")
        if advisory_json is not None and min_confidence is not None:
            confidence = safe_float(advisory_json.get("confidence"))
            if confidence is not None and confidence < float(min_confidence):
                advisory_json["below_min_confidence"] = True
    except (urlerror.URLError, TimeoutError, json.JSONDecodeError, ValueError) as ex:
        error_text = str(ex)
    except Exception as ex:  # noqa: BLE001 - guardrail path must never break trading flow
        error_text = str(ex)

    report_payload = {
        "request": request_payload,
        "api": {
            "endpoint": ai_cfg["endpoint"],
            "model": ai_cfg["model"],
            "timeout_seconds": ai_cfg["timeout_seconds"],
        },
        "response_text": raw_response_text,
        "advisory": advisory_json,
        "error": error_text,
    }
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report_payload, f, indent=2)

    if error_text:
        print(f"AI advisory skipped ({section}) due to API error: {error_text}")
    elif advisory_json is None:
        print(f"AI advisory returned non-JSON content for {section}; report saved at {report_path}")
    else:
        print(
            f"AI advisory ({section}): overall={advisory_json.get('overall_decision')} "
            f"confidence={advisory_json.get('confidence')} report={report_path}"
        )

    return advisory_json, report_path


def _normalize_ai_feedback(advisory_json: dict | None) -> dict[str, dict]:
    if not advisory_json:
        return {}

    feedback = advisory_json.get("symbol_feedback")
    if not isinstance(feedback, list):
        return {}

    normalized: dict[str, dict] = {}
    for item in feedback:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        action = str(item.get("action") or "").strip().lower()
        if not symbol or action not in {"approve", "reduce", "veto"}:
            continue
        normalized[symbol] = item
    return normalized


def _resolve_reduce_fraction(item: dict, default_fraction: float, max_fraction: float) -> float:
    reduce_fraction = safe_float(item.get("reduce_fraction"))
    if reduce_fraction is None:
        reduce_pct = safe_float(item.get("reduce_pct"))
        if reduce_pct is not None:
            reduce_fraction = reduce_pct / 100.0
    if reduce_fraction is None:
        reduce_fraction = default_fraction
    if reduce_fraction > 1.0:
        reduce_fraction = reduce_fraction / 100.0
    return clamp_float(float(reduce_fraction), 0.0, max_fraction)


def apply_ai_actions_to_short_orders(
    orders: list[dict],
    advisory_json: dict | None,
    ai_cfg: dict | None,
    mode: str,
    min_order_notional: Decimal,
) -> list[dict]:
    if not orders or not ai_cfg or mode != "paper" or not ai_cfg.get("apply_in_paper", False):
        return orders
    if not advisory_json or advisory_json.get("below_min_confidence"):
        return orders

    feedback = _normalize_ai_feedback(advisory_json)
    if not feedback:
        return orders

    symbols_in_order = [str(o.get("symbol") or "").strip().upper() for o in orders if o.get("symbol")]
    veto_candidates = [sym for sym in symbols_in_order if feedback.get(sym, {}).get("action") == "veto"]
    max_veto_fraction = float(ai_cfg.get("max_veto_fraction", 0.5))
    min_remaining_orders = int(ai_cfg.get("min_remaining_orders", 1))
    total_orders = len(orders)
    max_veto_by_fraction = int(math.floor(total_orders * max_veto_fraction))
    max_veto_by_remaining = max(0, total_orders - max(0, min_remaining_orders))
    allowed_veto_count = max(0, min(len(veto_candidates), max_veto_by_fraction, max_veto_by_remaining))
    allowed_veto = set(veto_candidates[:allowed_veto_count])

    adjusted_orders: list[dict] = []
    for order in orders:
        symbol = str(order.get("symbol") or "").strip().upper()
        item = feedback.get(symbol)
        action = str(item.get("action") or "").strip().lower() if item else ""

        if action == "veto" and symbol in allowed_veto:
            print(f"AI action applied (short_term): veto {symbol}")
            continue

        if action == "reduce":
            reduce_fraction = _resolve_reduce_fraction(
                item,
                float(ai_cfg.get("default_reduce_fraction", 0.25)),
                float(ai_cfg.get("max_reduce_fraction", 0.50)),
            )
            original_notional = Decimal(str(order.get("notional", "0")))
            reduced_notional = (original_notional * Decimal(str(1.0 - reduce_fraction))).quantize(
                Decimal("0.01"), rounding=ROUND_DOWN
            )
            if reduced_notional < min_order_notional:
                print(
                    f"AI action applied (short_term): reduced {symbol} below min notional; dropping order"
                )
                continue
            updated = dict(order)
            updated["notional"] = reduced_notional
            adjusted_orders.append(updated)
            print(
                f"AI action applied (short_term): reduce {symbol} by {reduce_fraction:.2%} "
                f"({original_notional} -> {reduced_notional})"
            )
            continue

        adjusted_orders.append(order)

    return adjusted_orders


def apply_ai_actions_to_notionals(
    notionals: Dict[str, Decimal],
    advisory_json: dict | None,
    ai_cfg: dict | None,
    mode: str,
    min_order_notional: Decimal,
) -> Dict[str, Decimal]:
    if not notionals or not ai_cfg or mode != "paper" or not ai_cfg.get("apply_in_paper", False):
        return notionals
    if not advisory_json or advisory_json.get("below_min_confidence"):
        return notionals

    feedback = _normalize_ai_feedback(advisory_json)
    if not feedback:
        return notionals

    symbols = list(sorted(notionals.keys()))
    veto_candidates = [sym for sym in symbols if feedback.get(sym, {}).get("action") == "veto"]
    max_veto_fraction = float(ai_cfg.get("max_veto_fraction", 0.5))
    min_remaining_orders = int(ai_cfg.get("min_remaining_orders", 1))
    total_orders = len(symbols)
    max_veto_by_fraction = int(math.floor(total_orders * max_veto_fraction))
    max_veto_by_remaining = max(0, total_orders - max(0, min_remaining_orders))
    allowed_veto_count = max(0, min(len(veto_candidates), max_veto_by_fraction, max_veto_by_remaining))
    allowed_veto = set(veto_candidates[:allowed_veto_count])

    adjusted: Dict[str, Decimal] = {}
    for symbol in symbols:
        notional = notionals[symbol]
        item = feedback.get(symbol)
        action = str(item.get("action") or "").strip().lower() if item else ""

        if action == "veto" and symbol in allowed_veto:
            print(f"AI action applied (core_growth): veto {symbol}")
            continue

        if action == "reduce":
            reduce_fraction = _resolve_reduce_fraction(
                item,
                float(ai_cfg.get("default_reduce_fraction", 0.25)),
                float(ai_cfg.get("max_reduce_fraction", 0.50)),
            )
            reduced_notional = (notional * Decimal(str(1.0 - reduce_fraction))).quantize(
                Decimal("0.01"), rounding=ROUND_DOWN
            )
            if reduced_notional < min_order_notional:
                print(
                    f"AI action applied (core_growth): reduced {symbol} below min notional; dropping order"
                )
                continue
            adjusted[symbol] = reduced_notional
            print(
                f"AI action applied (core_growth): reduce {symbol} by {reduce_fraction:.2%} "
                f"({notional} -> {reduced_notional})"
            )
            continue

        adjusted[symbol] = notional

    return adjusted


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


def build_symbol_order_context(strategy_config: dict, symbol_weights: Dict[str, Decimal]) -> dict[str, dict[str, Any]]:
    symbol_context: dict[str, dict[str, Any]] = {}
    buckets = strategy_config.get("buckets", {})
    for bucket_name, bucket in buckets.items():
        bucket_weight = Decimal(str(bucket.get("weight", 0)))
        for symbol, asset_weight in (bucket.get("assets") or {}).items():
            normalized_symbol = str(symbol).strip().upper()
            context = symbol_context.setdefault(
                normalized_symbol,
                {
                    "section": "core_growth",
                    "source_buckets": [],
                },
            )
            if bucket_name not in context["source_buckets"]:
                context["source_buckets"].append(bucket_name)
            context["configured_bucket_weight"] = float(bucket_weight)
            context["configured_asset_weight"] = float(Decimal(str(asset_weight)))
            context["effective_weight"] = float(symbol_weights.get(normalized_symbol, Decimal("0")))
            context["trigger_metric_name"] = "effective_weight"
            context["trigger_metric_value"] = float(symbol_weights.get(normalized_symbol, Decimal("0")))
    return symbol_context


def find_latest_screener_csv(repo_root: Path):
    dirpath = repo_root.joinpath("reports", "screener_results")
    if not dirpath.exists():
        return None
    csvs = list(dirpath.glob("*.csv"))
    if not csvs:
        return None
    csvs_sorted = sorted(csvs, key=lambda p: p.stat().st_mtime, reverse=True)
    return csvs_sorted[0]


def find_latest_statarb_pairs_csv(repo_root: Path) -> Path | None:
    dirpath = repo_root.joinpath("reports", "statarb_results")
    if not dirpath.exists():
        return None

    preferred = sorted(dirpath.glob("pairs_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if preferred:
        return preferred[0]

    fallback = dirpath.joinpath("pairs.csv")
    if fallback.exists():
        return fallback
    return None


def load_statarb_excluded_symbols(strategy_config: dict, repo_root: Path) -> set[str]:
    exclusion_cfg = strategy_config.get("statarb_exclusion") or {}
    if not exclusion_cfg.get("enabled", False):
        return set()

    pairs_path = exclusion_cfg.get("pairs_csv")
    csv_path: Path | None = None
    if pairs_path:
        p = Path(str(pairs_path))
        csv_path = p if p.is_absolute() else repo_root.joinpath(p)
        if not csv_path.exists():
            print(f"Configured statarb exclusion file {csv_path} not found. Attempting latest statarb pairs file.")
            csv_path = None

    if csv_path is None:
        csv_path = find_latest_statarb_pairs_csv(repo_root)

    if csv_path is None:
        print("Statarb exclusion enabled but no pairs CSV was found. Continuing without statarb exclusions.")
        return set()

    rows: list[tuple[str, str, float]] = []
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                long_symbol = str(row.get("long_symbol") or "").strip().upper()
                short_symbol = str(row.get("short_symbol") or "").strip().upper()
                if not long_symbol or not short_symbol:
                    continue
                score = safe_float(row.get("score"))
                if score is None:
                    score = float("-inf")
                rows.append((long_symbol, short_symbol, score))
    except OSError as ex:
        print(f"Unable to read statarb exclusion CSV {csv_path}: {ex}")
        return set()

    if not rows:
        return set()

    min_score = safe_float(exclusion_cfg.get("min_score"))
    if min_score is not None:
        rows = [row for row in rows if row[2] >= min_score]
    if not rows:
        return set()

    max_pairs = exclusion_cfg.get("max_pairs")
    if max_pairs is not None:
        try:
            max_pairs_int = max(0, int(max_pairs))
        except (TypeError, ValueError):
            max_pairs_int = len(rows)
    else:
        max_pairs_int = len(rows)

    sorted_rows = sorted(rows, key=lambda item: item[2], reverse=True)
    selected_rows = sorted_rows[:max_pairs_int]
    excluded_symbols = {symbol for long_symbol, short_symbol, _ in selected_rows for symbol in (long_symbol, short_symbol)}

    if excluded_symbols:
        preview = ", ".join(sorted(excluded_symbols)[:10])
        suffix = " ..." if len(excluded_symbols) > 10 else ""
        print(
            f"Statarb exclusion enabled: blocked {len(excluded_symbols)} symbols from optimize/buy using {csv_path}: "
            f"{preview}{suffix}"
        )

    return excluded_symbols


def pick_top_n_candidates_from_screener(
    csv_path: Path,
    n: int,
    existing_symbols: set[str] | None = None,
    correlation_dedup: dict | None = None,
):
    """Return the top N ranked candidates from a screener CSV.

    Prefer the prediction score (`pred_ret`) because it reflects the ranked
    model output. Fall back to `avg_ret` and other momentum fields only when
    needed. Also skip symbols that are already held in the account.
    """
    rows: list[dict[str, Any]] = []
    existing = {s.strip().upper() for s in (existing_symbols or []) if s and str(s).strip()}
    try:
        with csv_path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for index, r in enumerate(reader, start=1):
                sym = (r.get("symbol") or "").strip().upper()
                if not sym or sym in existing:
                    continue
                # price fields: regularMarketPrice, close, eodprice
                price = safe_float(r.get("regularMarketPrice") or r.get("close") or r.get("eodprice"))
                # Prefer the model ranking score first, then historical average return.
                score = None
                score_field = None
                for fld in ("pred_ret", "avg_ret", "avg_pred", "pct_1w", "pct_1m"):
                    val = safe_float(r.get(fld))
                    if val is not None:
                        score = val
                        score_field = fld
                        break
                prediction_rank = safe_int(r.get("prediction_rank"), index)
                rows.append(
                    {
                        "symbol": sym,
                        "price": price,
                        "score": score,
                        "score_field": score_field,
                        "prediction_rank": prediction_rank,
                        "pred_ret": safe_float(r.get("pred_ret")),
                        "avg_ret": safe_float(r.get("avg_ret")),
                    }
                )
    except Exception:
        return []

    rows = [r for r in rows if r["score"] is not None and r["price"] is not None]
    if not rows:
        return []

    rows_sorted = sorted(rows, key=lambda x: (x["score"], x["symbol"]), reverse=True)

    dedup_cfg = correlation_dedup or {}
    if not dedup_cfg.get("enabled", False):
        return rows_sorted[:n]

    threshold = safe_float(dedup_cfg.get("threshold"))
    if threshold is None or threshold <= 0:
        return rows_sorted[:n]

    search_multiplier = max(1, int(dedup_cfg.get("search_multiplier", 5)))
    candidate_window = min(len(rows_sorted), max(n, n * search_multiplier))
    period = str(dedup_cfg.get("period", "3mo"))
    interval = str(dedup_cfg.get("interval", "1d"))
    method = str(dedup_cfg.get("method", "CORRELATION")).upper()
    window = dedup_cfg.get("window")
    transform = str(dedup_cfg.get("transform", "NONE")).upper()
    batch_size = dedup_cfg.get("batch_size", 50)

    while candidate_window > 0:
        candidate_rows = rows_sorted[:candidate_window]
        candidate_symbols = [row["symbol"] for row in candidate_rows]
        kept_symbols, _dropped_map = dedup_ranked_symbols(
            candidate_symbols,
            threshold=threshold,
            period=period,
            interval=interval,
            method=method,
            window=window,
            transform=transform,
            batch_size=batch_size,
        )
        kept_set = set(kept_symbols)
        deduped_rows = [row for row in candidate_rows if row["symbol"] in kept_set]
        if len(deduped_rows) >= n or candidate_window >= len(rows_sorted):
            return deduped_rows[:n]

        next_window = min(len(rows_sorted), max(candidate_window + n, candidate_window * 2))
        if next_window == candidate_window:
            return deduped_rows[:n]
        candidate_window = next_window

    return []


def pick_top_n_from_screener(
    csv_path: Path,
    n: int,
    existing_symbols: set[str] | None = None,
    correlation_dedup: dict | None = None,
):
    candidates = pick_top_n_candidates_from_screener(
        csv_path,
        n,
        existing_symbols=existing_symbols,
        correlation_dedup=correlation_dedup,
    )
    return [(candidate["symbol"], candidate["price"]) for candidate in candidates]


def load_model_quality_metrics(csv_path: Path) -> dict[str, float | None]:
    """Read model quality metrics from the first screener row, if present."""
    metrics = {
        "spearman_ic": None,
        "r2": None,
        "mae": None,
    }
    try:
        with csv_path.open("r", encoding="utf-8") as f:
            first = next(csv.DictReader(f), None)
    except OSError:
        return metrics

    if not first:
        return metrics

    metrics["spearman_ic"] = safe_float(first.get("model_spearman_ic"))
    metrics["r2"] = safe_float(first.get("model_r2_score"))
    metrics["mae"] = safe_float(first.get("model_mae"))
    return metrics


def passes_model_quality_gate(csv_path: Path, short_bucket: dict, short_cfg: dict) -> tuple[bool, str]:
    """Return whether short-term entries should run given model quality thresholds."""
    gate_cfg = short_bucket.get("model_quality_gate") or short_cfg.get("model_quality_gate") or {}
    if not gate_cfg or not gate_cfg.get("enabled", False):
        return True, ""

    min_spearman = safe_float(gate_cfg.get("min_spearman_ic"))
    min_r2 = safe_float(gate_cfg.get("min_r2"))
    max_mae = safe_float(gate_cfg.get("max_mae"))
    require_metrics = bool(gate_cfg.get("require_metrics", True))

    metrics = load_model_quality_metrics(csv_path)
    missing = []
    if min_spearman is not None and metrics["spearman_ic"] is None:
        missing.append("model_spearman_ic")
    if min_r2 is not None and metrics["r2"] is None:
        missing.append("model_r2_score")
    if max_mae is not None and metrics["mae"] is None:
        missing.append("model_mae")

    if missing and require_metrics:
        return False, f"Skipping short-term allocation: missing model metrics ({', '.join(missing)})."

    print(
        "Model quality metrics: "
        f"spearman_ic={metrics['spearman_ic']}, r2={metrics['r2']}, mae={metrics['mae']}"
    )

    failures = []
    if min_spearman is not None and metrics["spearman_ic"] is not None and metrics["spearman_ic"] < min_spearman:
        failures.append(f"spearman_ic={metrics['spearman_ic']:.4f} < min_spearman_ic={min_spearman:.4f}")
    if min_r2 is not None and metrics["r2"] is not None and metrics["r2"] < min_r2:
        failures.append(f"r2={metrics['r2']:.4f} < min_r2={min_r2:.4f}")
    if max_mae is not None and metrics["mae"] is not None and metrics["mae"] > max_mae:
        failures.append(f"mae={metrics['mae']:.4f} > max_mae={max_mae:.4f}")

    if failures:
        return False, f"Skipping short-term allocation: model quality gate failed ({'; '.join(failures)})."
    return True, ""


def get_recent_trade_cooldown_symbols(client: TradingClient, cooldown_cfg: dict | None) -> dict[str, str]:
    """Return symbol->reason for entries blocked by recent buy/sell cooldown windows."""
    if not cooldown_cfg or not cooldown_cfg.get("enabled", False):
        return {}

    try:
        buy_days = max(0, int(cooldown_cfg.get("buy_cooldown_days", 0) or 0))
        sell_days = max(0, int(cooldown_cfg.get("sell_cooldown_days", 30) or 0))
    except (TypeError, ValueError):
        print("Invalid recent_trade_cooldown config; skipping cooldown filter.")
        return {}

    override_sell_days = os.getenv("FINTRADE_SELL_COOLDOWN_DAYS_OVERRIDE")
    if override_sell_days not in (None, ""):
        try:
            sell_days = max(0, int(override_sell_days))
            print(f"Applied sell cooldown override from FINTRADE_SELL_COOLDOWN_DAYS_OVERRIDE={sell_days}")
        except ValueError:
            print(
                "Invalid FINTRADE_SELL_COOLDOWN_DAYS_OVERRIDE value; "
                "using configured sell_cooldown_days instead."
            )

    max_cooldown_days = max(buy_days, sell_days)
    if max_cooldown_days <= 0:
        return {}

    try:
        lookback_days = int(cooldown_cfg.get("lookback_days", max_cooldown_days) or max_cooldown_days)
    except (TypeError, ValueError):
        lookback_days = max_cooldown_days
    lookback_days = max(lookback_days, max_cooldown_days)

    now_utc = datetime.now(timezone.utc)
    try:
        recent_orders = client.get_orders(
            GetOrdersRequest(
                status=QueryOrderStatus.ALL,
                after=now_utc - timedelta(days=lookback_days + 1),
                direction="desc",
                limit=500,
            )
        )
    except Exception as ex:
        print(f"Unable to load recent orders for cooldown filter: {ex}")
        return {}

    blocked: dict[str, tuple[int, str]] = {}
    for order in recent_orders:
        symbol = (getattr(order, "symbol", "") or "").strip().upper()
        if not symbol:
            continue

        side = str(getattr(getattr(order, "side", ""), "value", getattr(order, "side", ""))).lower()
        if side == "sell":
            cooldown_days = sell_days
        elif side == "buy":
            cooldown_days = buy_days
        else:
            continue

        if cooldown_days <= 0:
            continue

        trade_ts = getattr(order, "filled_at", None) or getattr(order, "submitted_at", None)
        if trade_ts is None:
            continue
        if trade_ts.tzinfo is None:
            trade_ts = trade_ts.replace(tzinfo=timezone.utc)

        age_days = (now_utc - trade_ts).total_seconds() / 86400.0
        if age_days < 0 or age_days >= cooldown_days:
            continue

        remaining_days = max(1, math.ceil(cooldown_days - age_days))
        reason = f"{side.upper()} {age_days:.1f}d ago ({remaining_days}d left)"

        prev = blocked.get(symbol)
        if prev is None or remaining_days > prev[0]:
            blocked[symbol] = (remaining_days, reason)

    if not blocked:
        return {}

    return {symbol: reason for symbol, (_remaining, reason) in blocked.items()}


def submit_short_term_orders(client: TradingClient, orders: list[dict], dry_run: bool, creds: ModeCredentials = None) -> list[str]:
    """orders: list of dicts with keys: symbol, notional, price, stop_pct, take_pct"""
    submitted = []
    if not orders:
        return submitted
        
    # Create data client for fetching current prices
    data_client = None
    if creds:
        try:
            data_client = StockHistoricalDataClient(
                api_key=creds.api_key,
                secret_key=creds.api_secret
            )
        except Exception as e:
            print(f"  WARNING: Could not create data client: {e}")
    
    print("Short-term order plan:")
    for o in orders:
        print(f"  BUY {o['symbol']}: ${o['notional']} with stop={o['stop_pct']}% take={o['take_pct']}%")
        emit_order_summary(
            {
                "status": "planned",
                "section": "short_term",
                "symbol": o["symbol"],
                "notional": str(o["notional"]),
                "trigger_metric_name": o.get("trigger_metric_name"),
                "trigger_metric_value": o.get("trigger_metric_value"),
                "trigger_description": o.get("trigger_description"),
                "prediction_rank": o.get("prediction_rank"),
            }
        )

    if dry_run:
        print("Dry run enabled; no short-term orders submitted.")
        return submitted

    for o in orders:
        symbol = o["symbol"]
        notional = o["notional"]
        screener_price = o.get("price")
        stop_pct = float(o.get("stop_pct", 0.0))
        take_pct = float(o.get("take_pct", 0.0))
        try:
            # Try to fetch current market price from Alpaca
            current_price = screener_price
            if data_client and screener_price:
                try:
                    request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
                    quote = data_client.get_stock_latest_quote(request)
                    if symbol in quote and quote[symbol].ask_price:
                        current_price = float(quote[symbol].ask_price)
                        print(f"  {symbol}: using live ask price ${current_price:.2f} (screener: ${screener_price:.2f})")
                    else:
                        print(f"  {symbol}: no live quote, using screener price ${screener_price:.2f}")
                except Exception as e:
                    print(f"  {symbol}: could not fetch live price ({e}), using screener price ${screener_price:.2f}")
            
            # Bracket orders require quantity, not notional
            if current_price is None or current_price <= 0:
                print(f"  WARNING {symbol}: price is None or invalid ({current_price}), skipping order")
                continue
            
            # Calculate quantity from notional and current price
            qty = float(notional) / float(current_price)
            # Round prices to penny increments (no sub-penny pricing allowed)
            take_price = round(float(current_price) * (1.0 + take_pct / 100.0), 2)
            stop_price = round(float(current_price) * (1.0 + stop_pct / 100.0), 2)
            
            print(f"  {symbol}: qty={qty:.6f}, stop=${stop_price:.2f} ({stop_pct}%), take=${take_price:.2f} ({take_pct}%)")

            # NOTE: Alpaca does NOT support bracket orders with fractional shares
            # We must submit a simple order first, then add stop loss/take profit after fill
            # For now, submit simple fractional order without protections
            # TODO: Add post-fill monitoring to add stop loss and take profit orders
            
            req = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
            )
            order = client.submit_order(req)
            submitted.append(order.id)
            emit_order_summary(
                {
                    "status": "submitted",
                    "section": "short_term",
                    "symbol": symbol,
                    "notional": str(notional),
                    "quantity": f"{qty:.6f}",
                    "trigger_metric_name": o.get("trigger_metric_name"),
                    "trigger_metric_value": o.get("trigger_metric_value"),
                    "trigger_description": o.get("trigger_description"),
                    "prediction_rank": o.get("prediction_rank"),
                }
            )
            
            print(f"Γ£ô Submitted {symbol} order id={order.id} qty={qty:.6f}")
            print(f"  ΓÜá∩╕Å  Note: Stop loss/take profit will need to be added separately after fill")
            print(f"     Target stop=${stop_price:.2f}, take=${take_price:.2f}")
        except APIError as ex:
            print(f"Short-term order failed for {symbol}: {ex}")
        except Exception as ex:
            print(f"Short-term order unexpected error for {symbol}: {ex}")

    return submitted


def submit_orders(
    client: TradingClient,
    notionals: Dict[str, Decimal],
    dry_run: bool,
    order_context: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    if not notionals:
        print("No orders to submit after min-order filter. Leaving cash unallocated.")
        return []

    print("Order plan:")
    for symbol, notional in sorted(notionals.items()):
        print(f"  BUY {symbol}: ${notional}")
        context = (order_context or {}).get(symbol, {})
        emit_order_summary(
            {
                "status": "planned",
                "section": context.get("section", "core_growth"),
                "symbol": symbol,
                "notional": str(notional),
                "trigger_metric_name": context.get("trigger_metric_name"),
                "trigger_metric_value": context.get("trigger_metric_value"),
                "trigger_description": context.get("trigger_description"),
                "source_buckets": context.get("source_buckets"),
            }
        )

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
            context = (order_context or {}).get(symbol, {})
            emit_order_summary(
                {
                    "status": "submitted",
                    "section": context.get("section", "core_growth"),
                    "symbol": symbol,
                    "notional": str(notional),
                    "trigger_metric_name": context.get("trigger_metric_name"),
                    "trigger_metric_value": context.get("trigger_metric_value"),
                    "trigger_description": context.get("trigger_description"),
                    "source_buckets": context.get("source_buckets"),
                }
            )
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
    creds = None
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

    if strategy_config is None:
        try:
            strategy_config = load_strategy_config(Path(args.config))
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as ex:
            print(f"Config error: {ex}")
            return 2

    now_et = datetime.now(EASTERN_TZ)
    today_et = now_et.date()
    print(f"Run mode={args.mode} run_type={args.run_type} date_et={today_et.isoformat()}")

    if args.run_type == "scheduled":
        if client is None:
            print("Scheduled run requires Alpaca client; provide credentials or remove --run-type scheduled.")
            return 2
        trading_frequency = strategy_config.get("trading_frequency", "monthly")
        run_today, reason = should_run_today(client, today_et, trading_frequency)
        print(reason)
        if not run_today:
            return 0

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

    ai_cfg = get_ai_decision_config(strategy_config, args.mode)
    if ai_cfg:
        print(
            f"AI advisory enabled (mode={ai_cfg['mode']}, model={ai_cfg['model']}, "
            f"endpoint={ai_cfg['endpoint']})"
        )

    # Handle short-term allocation (use screener results)
    short_cfg = strategy_config.get("short_term_settings", {})
    short_bucket = strategy_config.get("buckets", {}).get("short_term", {})
    repo_root = Path(__file__).resolve().parents[2]
    statarb_excluded_symbols = load_statarb_excluded_symbols(strategy_config, repo_root)
    short_weight = Decimal(str(short_bucket.get("weight", 0)))
    short_order_ids: list[str] = []
    # determine number_of_assets: prefer bucket-level setting, fall back to short_term_settings
    n_assets = int(short_bucket.get("number_of_assets") or short_cfg.get("number_of_assets", 0))
    if short_weight > 0 and n_assets > 0:
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
            gate_ok, gate_reason = passes_model_quality_gate(csv_path, short_bucket, short_cfg)
            if not gate_ok:
                print(gate_reason)
                picks = []
            else:
                picks = None

            existing_symbols = set()
            cooldown_cfg = short_bucket.get("recent_trade_cooldown") or strategy_config.get("recent_trade_cooldown")
            cooldown_blocked_symbols: dict[str, str] = {}
            if client is not None:
                try:
                    existing_symbols = {p.symbol.strip().upper() for p in client.get_all_positions()}
                except Exception as ex:
                    print(f"Unable to read existing positions for short-term filter: {ex}")

                cooldown_blocked_symbols = get_recent_trade_cooldown_symbols(client, cooldown_cfg)
                if cooldown_blocked_symbols:
                    preview_items = sorted(cooldown_blocked_symbols.items())[:10]
                    preview = ", ".join(f"{sym} ({reason})" for sym, reason in preview_items)
                    suffix = " ..." if len(cooldown_blocked_symbols) > len(preview_items) else ""
                    print(
                        f"Recent-trade cooldown excluded {len(cooldown_blocked_symbols)} symbols: {preview}{suffix}"
                    )

            excluded_symbols = existing_symbols.union(set(cooldown_blocked_symbols.keys()))
            excluded_symbols = excluded_symbols.union(statarb_excluded_symbols)

            if picks is None:
                n = int(n_assets)
                picks = pick_top_n_candidates_from_screener(
                    csv_path,
                    n,
                    existing_symbols=excluded_symbols,
                    correlation_dedup=short_bucket.get("correlation_dedup"),
                )
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
                for pick in picks:
                    sym = pick["symbol"]
                    price = pick["price"]
                    trigger_metric_name = pick.get("score_field") or "score"
                    trigger_metric_value = pick.get("score")
                    prediction_rank = pick.get("prediction_rank")
                    trigger_parts = []
                    if prediction_rank:
                        trigger_parts.append(f"prediction_rank={prediction_rank}")
                    if trigger_metric_name and trigger_metric_value is not None:
                        trigger_parts.append(f"{trigger_metric_name}={trigger_metric_value}")
                    orders.append({
                        "symbol": sym,
                        "notional": per_asset,
                        "price": price,
                        "stop_pct": float(stop_pct),
                        "take_pct": float(take_pct),
                        "prediction_rank": prediction_rank,
                        "trigger_metric_name": trigger_metric_name,
                        "trigger_metric_value": trigger_metric_value,
                        "trigger_description": ", ".join(trigger_parts),
                    })

                if short_alloc <= Decimal("0"):
                    print("Short-term allocation computed as $0. Skipping short-term orders.")
                else:
                    print(f"Allocating ${short_alloc} to short-term picks ({len(picks)} assets)")
                    short_term_advisory = None
                    if ai_cfg:
                        advisory_candidates = [
                            {
                                "symbol": str(o["symbol"]),
                                "notional": str(o["notional"]),
                                "price": o.get("price"),
                                "stop_pct": o.get("stop_pct"),
                                "take_pct": o.get("take_pct"),
                            }
                            for o in orders
                        ]
                        short_term_advisory, _report_path = run_ai_advisory(
                            ai_cfg,
                            section="short_term",
                            candidates=advisory_candidates,
                            strategy_config=strategy_config,
                            mode=args.mode,
                            dry_run=args.dry_run,
                            report_root=repo_root,
                        )
                        orders = apply_ai_actions_to_short_orders(
                            orders,
                            advisory_json=short_term_advisory,
                            ai_cfg=ai_cfg,
                            mode=args.mode,
                            min_order_notional=args.min_order_notional,
                        )
                        if not orders:
                            print("AI actions removed all short-term orders within configured safety bounds.")
                    if client is None and not args.dry_run:
                        print("No Alpaca client available to submit short-term orders. Use --simulate-cash or provide credentials.")
                        return 2
                    short_order_ids = submit_short_term_orders(client, orders, args.dry_run, creds)
                    # reduce spendable cash for the remaining allocations
                    spent = sum(Decimal(str(o["notional"])) for o in orders)
                    spendable_cash = (spendable_cash - spent).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

    symbol_weights = flatten_symbol_weights(strategy_config)
    if statarb_excluded_symbols:
        removed_symbols = {symbol for symbol in symbol_weights if symbol in statarb_excluded_symbols}
        if removed_symbols:
            symbol_weights = {symbol: weight for symbol, weight in symbol_weights.items() if symbol not in removed_symbols}
            print(
                f"Removed {len(removed_symbols)} symbols from optimize/buy allocation due to statarb exclusion: "
                f"{', '.join(sorted(removed_symbols))}"
            )
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
    order_context = build_symbol_order_context(strategy_config, symbol_weights)
    if ai_cfg and notionals:
        advisory_candidates = [
            {
                "symbol": symbol,
                "notional": str(notional),
            }
            for symbol, notional in sorted(notionals.items())
        ]
        core_growth_advisory, _report_path = run_ai_advisory(
            ai_cfg,
            section="core_growth",
            candidates=advisory_candidates,
            strategy_config=strategy_config,
            mode=args.mode,
            dry_run=args.dry_run,
            report_root=repo_root,
        )
        notionals = apply_ai_actions_to_notionals(
            notionals,
            advisory_json=core_growth_advisory,
            ai_cfg=ai_cfg,
            mode=args.mode,
            min_order_notional=args.min_order_notional,
        )
    order_ids = submit_orders(client, notionals, args.dry_run, order_context=order_context)

    if args.poll_fills and order_ids and not args.dry_run:
        statuses = poll_orders_for_fills(client, order_ids, timeout_sec=args.poll_timeout, poll_interval=args.poll_interval)
        print("Final order statuses:")
        for oid, st in statuses.items():
            print(f"  {oid}: {st}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
