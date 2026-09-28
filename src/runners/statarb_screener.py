from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from yfinance.screener.data_loading import DEFAULT_DOWNLOAD_BATCH_PAUSE, DEFAULT_DOWNLOAD_BATCH_SIZE
from yfinance.screener.data_loading import DEFAULT_DOWNLOAD_MAX_RETRIES, DEFAULT_DOWNLOAD_RETRY_PAUSE
from yfinance.screener.data_loading import download_price_history_batched


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate statarb pair candidates from a dedicated universe.")
    parser.add_argument("--config", default="configs/statarb_screener.json")
    parser.add_argument("--out", default=None)
    parser.add_argument("--versioned", action="store_true")
    parser.add_argument("--lookback", type=int, default=None)
    parser.add_argument("--top-pairs", type=int, default=None)
    parser.add_argument("--min-correlation", type=float, default=None)
    return parser.parse_args()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_path(path_value: str | None, default_path: str) -> Path:
    candidate = Path(path_value or default_path)
    if candidate.is_absolute():
        return candidate
    if candidate.exists():
        return candidate.resolve()
    return repo_root().joinpath(candidate).resolve()


def load_config(config_path: Path) -> dict:
    if not config_path.exists():
        raise FileNotFoundError(f"Statarb screener config not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)

    statarb_cfg = config.get("statarb_screener")
    if not isinstance(statarb_cfg, dict):
        raise ValueError("Config must contain top-level 'statarb_screener' object.")

    if not statarb_cfg.get("enabled", False):
        raise ValueError("statarb_screener is disabled in config.")

    symbols = statarb_cfg.get("symbols", [])
    if not isinstance(symbols, list) or len(symbols) < 2:
        raise ValueError("statarb_screener.symbols must include at least 2 symbols.")

    return config


def normalize_symbols(raw_symbols: list[str], max_symbols: int) -> list[str]:
    symbols = []
    seen = set()
    for value in raw_symbols:
        symbol = str(value or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        symbols.append(symbol)
        if len(symbols) >= max_symbols:
            break
    return symbols


def download_close_prices(
    symbols: list[str],
    lookback_days: int,
    batch_size: int = DEFAULT_DOWNLOAD_BATCH_SIZE,
    batch_pause: float = DEFAULT_DOWNLOAD_BATCH_PAUSE,
    max_retries: int = DEFAULT_DOWNLOAD_MAX_RETRIES,
    retry_pause: float = DEFAULT_DOWNLOAD_RETRY_PAUSE,
) -> pd.DataFrame:
    period_days = max(lookback_days + 20, lookback_days)
    period = f"{period_days}d"
    data = download_price_history_batched(
        symbols,
        period=period,
        interval="1d",
        batch_size=batch_size,
        batch_pause=batch_pause,
        max_retries=max_retries,
        retry_pause=retry_pause,
        auto_adjust=True,
    )
    if data is None or data.empty:
        raise ValueError("No pricing data downloaded for statarb screener universe.")

    closes = data.sort_index().dropna(how="all")
    closes = closes.tail(lookback_days)
    return closes


def compute_hedge_ratio(x: pd.Series, y: pd.Series) -> float:
    x_vals = x.values
    y_vals = y.values
    y_var = np.var(y_vals)
    if np.isclose(y_var, 0.0):
        return 1.0
    beta = float(np.cov(x_vals, y_vals)[0, 1] / y_var)
    if not np.isfinite(beta) or np.isclose(beta, 0.0):
        return 1.0
    return abs(beta)


def evaluate_pair(symbol_a: str, symbol_b: str, closes: pd.DataFrame, min_history_days: int):
    subset = closes[[symbol_a, symbol_b]].dropna()
    if len(subset) < min_history_days:
        return None

    px_a = subset[symbol_a].astype(float)
    px_b = subset[symbol_b].astype(float)

    if (px_a <= 0).any() or (px_b <= 0).any():
        return None

    corr = float(np.corrcoef(px_a.pct_change().dropna(), px_b.pct_change().dropna())[0, 1])
    if not np.isfinite(corr):
        return None

    log_a = np.log(px_a)
    log_b = np.log(px_b)
    hedge_ratio = compute_hedge_ratio(log_a, log_b)

    spread = log_a - hedge_ratio * log_b
    spread_std = float(spread.std())
    if np.isclose(spread_std, 0.0):
        return None

    zscore = float((spread.iloc[-1] - spread.mean()) / spread_std)
    abs_z = abs(zscore)
    score = abs_z * max(corr, 0.0)

    if zscore > 0:
        long_symbol = symbol_b
        short_symbol = symbol_a
    else:
        long_symbol = symbol_a
        short_symbol = symbol_b

    return {
        "pair_id": f"{symbol_a}_{symbol_b}",
        "long_symbol": long_symbol,
        "short_symbol": short_symbol,
        "score": round(score, 6),
        "correlation": round(corr, 6),
        "zscore": round(zscore, 6),
        "hedge_ratio": round(hedge_ratio, 6),
        "long_price": round(float(subset[long_symbol].iloc[-1]), 4),
        "short_price": round(float(subset[short_symbol].iloc[-1]), 4),
    }


def build_pair_candidates(closes: pd.DataFrame, min_history_days: int, min_correlation: float) -> list[dict]:
    symbols = [col for col in closes.columns if closes[col].notna().sum() >= min_history_days]
    candidates = []
    for i in range(len(symbols)):
        for j in range(i + 1, len(symbols)):
            result = evaluate_pair(symbols[i], symbols[j], closes, min_history_days)
            if result is None:
                continue
            if result["correlation"] < min_correlation:
                continue
            candidates.append(result)
    return sorted(candidates, key=lambda item: item["score"], reverse=True)


def write_pair_candidates(rows: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    frame.to_csv(out_path, index=False)


def main() -> int:
    args = parse_args()
    config_path = resolve_path(args.config, "configs/statarb_screener.json")
    config = load_config(config_path)
    cfg = config["statarb_screener"]

    lookback_days = int(args.lookback or cfg.get("lookback_days", 90))
    min_history_days = int(cfg.get("min_history_days", 60))
    min_correlation = float(args.min_correlation if args.min_correlation is not None else cfg.get("min_correlation", 0.7))
    top_pairs = int(args.top_pairs or cfg.get("top_pairs", 20))
    max_symbols = int(cfg.get("max_symbols", 50))
    default_pair_notional = float(cfg.get("default_pair_notional", 1000.0))
    download_batch_size = int(cfg.get("download_batch_size", DEFAULT_DOWNLOAD_BATCH_SIZE))
    download_batch_pause = float(cfg.get("download_batch_pause", DEFAULT_DOWNLOAD_BATCH_PAUSE))
    download_max_retries = int(cfg.get("download_max_retries", DEFAULT_DOWNLOAD_MAX_RETRIES))
    download_retry_pause = float(cfg.get("download_retry_pause", DEFAULT_DOWNLOAD_RETRY_PAUSE))

    symbols = normalize_symbols(cfg.get("symbols", []), max_symbols=max_symbols)
    closes = download_close_prices(
        symbols,
        lookback_days,
        batch_size=download_batch_size,
        batch_pause=download_batch_pause,
        max_retries=download_max_retries,
        retry_pause=download_retry_pause,
    )
    candidates = build_pair_candidates(closes, min_history_days=min_history_days, min_correlation=min_correlation)
    selected = candidates[:top_pairs]

    for row in selected:
        row["pair_notional"] = round(default_pair_notional, 2)

    reports_dir = repo_root().joinpath("reports", "statarb_results")
    if args.out:
        out_path = resolve_path(args.out, "reports/statarb_results/pairs.csv")
    else:
        out_path = reports_dir.joinpath("pairs.csv")

    write_pair_candidates(selected, out_path)

    if args.versioned:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        versioned = out_path.with_name(f"pairs_{ts}.csv")
        write_pair_candidates(selected, versioned)
        print(f"Wrote versioned statarb pairs file: {versioned}")

    print(f"Wrote {len(selected)} statarb pair candidates to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())