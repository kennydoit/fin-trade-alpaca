"""Packaged CLI wrapper for the prediction screener.

This mirrors the existing `tools/predict_screener_cli.py` but lives inside
the installed package so console-scripts can import it reliably.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from alpaca.trading.client import TradingClient

# Ensure the repo root is accessible for imports
REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"

# Prioritize src/ directory over installed package
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from fin_trade_alpaca.env_loader import load_environment_for_mode
from yfinance.screener.dedup_by_correlation import dedup_ranked_symbols
from yfinance.screener.predict_short_term import (
    build_dataset,
    find_latest_screener_file,
    score_latest_breakout,
    train_and_evaluate,
    train_and_evaluate_breakout,
)

DEFAULT_PREDICTION_CONFIG: dict[str, Any] = {
    "pipeline": "standard",
    "candidates_file": None,
    "sector": None,
    "industry": None,
    "limit": 200,
    "return_days": 5,
    "lookback": 180,
    "out": None,
    "versioned": False,
    "include_short_term_positions": True,
    "download_batch_size": 25,
    "download_batch_pause": 2.0,
    "download_max_retries": 2,
    "download_retry_pause": 5.0,
    "model": {
        "type": "lightgbm",
        "n_estimators": 200,
        "learning_rate": 0.05,
        "random_state": 42,
        "tune": False,
        "tune_n_iter": 6,
        "tune_cv": 3,
    },
    "target": {"horizon_days": 5, "label_column": "fwd_ret", "type": "ranking", "breakout_k": 1.5},
}

_TARGET_TYPES = ("ranking", "breakout")

_YFINANCE_ENV_KEYS = {
    "download_batch_size": "YFINANCE_DOWNLOAD_BATCH_SIZE",
    "download_batch_pause": "YFINANCE_DOWNLOAD_BATCH_PAUSE",
    "download_max_retries": "YFINANCE_DOWNLOAD_MAX_RETRIES",
    "download_retry_pause": "YFINANCE_DOWNLOAD_RETRY_PAUSE",
}


def resolve_credentials(mode: str) -> tuple[str | None, str | None, str | None, bool]:
    """Resolve Alpaca credentials for the given mode (paper or live).

    Returns: (api_key, api_secret, oauth_token, paper)
    """
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

    return api_key, api_secret, oauth_token, paper


def fetch_short_term_positions() -> set[str]:
    """Fetch current short-term positions from both live and paper accounts.

    Returns a set of symbol strings (uppercase).
    """
    positions: set[str] = set()

    # Save current environment to restore later
    original_env = dict(os.environ)  # Use dict() instead of copy.copy()

    for mode in ["paper", "live"]:
        try:
            # Restore original environment before loading mode-specific env
            os.environ.clear()
            os.environ.update(original_env)

            load_environment_for_mode(mode)
            api_key, api_secret, oauth_token, paper = resolve_credentials(mode)

            if not oauth_token and (not api_key or not api_secret):
                print(f"Skipping {mode} positions: credentials not found")
                continue

            client = TradingClient(
                api_key=api_key,
                secret_key=api_secret,
                oauth_token=oauth_token,
                paper=paper,
            )

            for pos in client.get_all_positions():
                symbol = pos.symbol.strip().upper()
                positions.add(symbol)
                print(f"Found {mode} position: {symbol}")

        except Exception as ex:
            print(f"Warning: Unable to fetch {mode} positions: {ex}")

    # Restore original environment
    os.environ.clear()
    os.environ.update(original_env)

    return positions


def load_prediction_config(config_path: str | Path | None) -> dict[str, Any]:
    """Load the prediction screener JSON config, if present."""
    if not config_path:
        return {}

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Prediction config not found: {path}")

    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    if not isinstance(data, dict):
        raise ValueError("Prediction config must be a JSON object")

    return data


def resolve_runtime_config(args: argparse.Namespace) -> dict[str, Any]:
    """Merge config-file defaults with CLI overrides."""
    config = load_prediction_config(getattr(args, "config", None))
    merged = dict(DEFAULT_PREDICTION_CONFIG)
    merged.update(config)

    for key, value in vars(args).items():
        if key == "config":
            continue
        if value is not None:
            merged[key] = value

    # normalize keys so CLI option names and config keys line up
    merged["candidates_file"] = merged.get("candidates_file")
    merged["sector"] = merged.get("sector")
    merged["industry"] = merged.get("industry")
    merged["limit"] = int(merged.get("limit", DEFAULT_PREDICTION_CONFIG["limit"]))
    merged["return_days"] = int(merged.get("return_days", DEFAULT_PREDICTION_CONFIG["return_days"]))
    merged["lookback"] = int(merged.get("lookback", DEFAULT_PREDICTION_CONFIG["lookback"]))
    if merged.get("return_days") is None:
        target_cfg = merged.get("target", {}) or {}
        merged["return_days"] = int(target_cfg.get("horizon_days", DEFAULT_PREDICTION_CONFIG["return_days"]))
    target_cfg = dict(merged.get("target") or {})
    target_cfg["horizon_days"] = int(target_cfg.get("horizon_days", merged["return_days"]))
    target_cfg["label_column"] = target_cfg.get("label_column", "fwd_ret")
    target_cfg["train_split_days"] = int(target_cfg.get("train_split_days", 30))
    target_type = str(merged.get("target_type") or target_cfg.get("type") or "ranking").lower()
    if target_type not in _TARGET_TYPES:
        raise ValueError(f"Unsupported target type '{target_type}'; expected one of {_TARGET_TYPES}")
    target_cfg["type"] = target_type
    target_cfg["breakout_k"] = float(target_cfg.get("breakout_k", DEFAULT_PREDICTION_CONFIG["target"]["breakout_k"]))
    merged["target"] = target_cfg
    merged["train_split_fraction"] = float(
        merged.get(
            "train_split_fraction",
            max(0.1, min(0.9, merged["target"]["train_split_days"] / max(30, merged["lookback"]))),
        )
    )
    merged["out"] = merged.get("out")
    merged["versioned"] = bool(merged.get("versioned", False))
    merged["download_batch_size"] = int(
        merged.get("download_batch_size", DEFAULT_PREDICTION_CONFIG["download_batch_size"])
    )
    merged["download_batch_pause"] = float(
        merged.get("download_batch_pause", DEFAULT_PREDICTION_CONFIG["download_batch_pause"])
    )
    merged["download_max_retries"] = int(
        merged.get("download_max_retries", DEFAULT_PREDICTION_CONFIG["download_max_retries"])
    )
    merged["download_retry_pause"] = float(
        merged.get("download_retry_pause", DEFAULT_PREDICTION_CONFIG["download_retry_pause"])
    )
    return merged


@contextmanager
def configured_yfinance_downloads(runtime: dict[str, Any]):
    previous: dict[str, str | None] = {}
    try:
        for config_key, env_key in _YFINANCE_ENV_KEYS.items():
            previous[env_key] = os.environ.get(env_key)
            os.environ[env_key] = str(runtime[config_key])
        yield
    finally:
        for env_key, old_value in previous.items():
            if old_value is None:
                os.environ.pop(env_key, None)
            else:
                os.environ[env_key] = old_value


def filter_candidates(df: pd.DataFrame, sector: str | None, industry: str | None) -> pd.DataFrame:
    if sector:
        if "sector" in df.columns:
            df = df[df["sector"].astype(str).str.contains(sector, case=False, na=False)]
        else:
            print("Warning: candidates file has no `sector` column; ignoring --sector")
    if industry:
        if "industry" in df.columns:
            df = df[df["industry"].astype(str).str.contains(industry, case=False, na=False)]
        else:
            print("Warning: candidates file has no `industry` column; ignoring --industry")
    return df


def _load_strategy_config(strategy_config_path: str | Path | None) -> dict[str, Any]:
    if not strategy_config_path:
        return {}
    path = Path(strategy_config_path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else {}


def _make_index_features(adj_close: pd.Series) -> pd.DataFrame:
    px = pd.DataFrame({"close": adj_close}).sort_index()
    out = pd.DataFrame(index=px.index)
    out["ret_1d"] = px["close"].pct_change()
    out["ret_3d"] = px["close"].pct_change(3)
    out["ret_5d"] = px["close"].pct_change(5)
    out["vol_10d"] = out["ret_1d"].rolling(10).std()
    out["mom_20d"] = px["close"].pct_change(20)
    out["sma_10"] = px["close"].rolling(10).mean()
    out["price_sma10_z"] = (px["close"] - out["sma_10"]) / px["close"].rolling(60).std()
    out["close"] = px["close"]
    return out


def _build_wide_with_global_indexes(df: pd.DataFrame, index_symbols: list[str]) -> pd.DataFrame:
    working = df.copy()
    working["date"] = pd.to_datetime(working["date"], errors="coerce")
    working = working.dropna(subset=["date", "symbol"])
    working["symbol"] = working["symbol"].astype(str).str.upper().str.strip()

    # Add global index features by date.
    if index_symbols:
        try:
            import yfinance as yf  # noqa: PLC0415

            min_date = working["date"].min() - pd.Timedelta(days=120)
            max_date = working["date"].max() + pd.Timedelta(days=5)
            idx_raw = yf.download(
                tickers=index_symbols,
                start=min_date.strftime("%Y-%m-%d"),
                end=(max_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                auto_adjust=True,
                group_by="ticker",
                progress=False,
            )

            idx_frames: list[pd.DataFrame] = []
            for sym in index_symbols:
                sym_u = sym.upper()
                if isinstance(idx_raw.columns, pd.MultiIndex):
                    if sym_u not in idx_raw.columns.get_level_values(0):
                        continue
                    close = idx_raw[sym_u]["Close"].dropna()
                else:
                    if "Close" not in idx_raw.columns:
                        continue
                    close = idx_raw["Close"].dropna()
                if close.empty:
                    continue
                feats = _make_index_features(close).add_prefix(f"IDX_{sym_u}_")
                feats = feats.reset_index().rename(columns={"Date": "date"})
                feats["date"] = pd.to_datetime(feats["date"], errors="coerce").dt.tz_localize(None)
                idx_frames.append(feats)

            if idx_frames:
                for feats in idx_frames:
                    working = working.merge(feats, on="date", how="left")
        except Exception as ex:
            print(f"Warning: Unable to add index features ({ex}); continuing without them")

    index_cols = [c for c in working.columns if c.startswith("IDX_")]
    symbol_value_cols = [
        c
        for c in working.columns
        if c not in {"date", "symbol"}
        and c not in index_cols
        and pd.api.types.is_numeric_dtype(working[c])
    ]
    deduped = working.sort_values(["date", "symbol"]).drop_duplicates(subset=["date", "symbol"], keep="last")
    wide = deduped.pivot_table(index="date", columns="symbol", values=symbol_value_cols, aggfunc="first")
    wide.columns = [f"{symbol}_{feature}".upper() for feature, symbol in wide.columns]
    wide = wide.sort_index(axis=1).sort_index().reset_index()

    if index_cols:
        idx_by_date = (
            working[["date"] + index_cols]
            .sort_values("date")
            .drop_duplicates(subset=["date"], keep="last")
        )
        wide = wide.merge(idx_by_date, on="date", how="left")

    return wide


def _run_wide_asset_based_prediction(
    runtime: dict[str, Any],
    strategy_short_term: dict[str, Any],
    dataset_df: pd.DataFrame,
    out_path: Path,
) -> None:
    wide_cfg = dict(strategy_short_term.get("wide_asset_based_prediction") or {})
    min_assets = int(wide_cfg.get("min_assets", 3))
    max_assets = int(wide_cfg.get("max_assets", 5))
    holdout_days = int(wide_cfg.get("holdout_days", 5))
    min_train_rows = int(wide_cfg.get("min_train_rows", 20))
    index_symbols = [str(s).upper() for s in (wide_cfg.get("index_symbols") or ["SPY", "QQQ", "IWM", "DIA"])]
    xgb_cfg = dict(wide_cfg.get("xgboost") or {})

    n_estimators = int(xgb_cfg.get("n_estimators", 200))
    learning_rate = float(xgb_cfg.get("learning_rate", 0.03))
    max_depth = int(xgb_cfg.get("max_depth", 2))
    subsample = float(xgb_cfg.get("subsample", 0.8))
    colsample_bytree = float(xgb_cfg.get("colsample_bytree", 0.7))
    reg_alpha = float(xgb_cfg.get("reg_alpha", 0.5))
    reg_lambda = float(xgb_cfg.get("reg_lambda", 5.0))
    min_child_weight = float(xgb_cfg.get("min_child_weight", 8.0))

    corr_cfg = dict(strategy_short_term.get("correlation_dedup") or {})
    corr_threshold = float(corr_cfg.get("threshold", 0.75))
    corr_period = str(corr_cfg.get("period", "3mo"))
    corr_interval = str(corr_cfg.get("interval", "1d"))
    corr_search_multiplier = int(corr_cfg.get("search_multiplier", 5))

    quality_gate_cfg = dict(strategy_short_term.get("model_quality_gate") or {})
    strict_r2_only = bool(wide_cfg.get("strict_r2_only", True))
    min_r2_score = float(wide_cfg.get("min_r2_score", quality_gate_cfg.get("min_r2", 0.0)))

    print("Running wide asset based prediction pipeline...")
    if dataset_df.empty:
        raise SystemExit("No training data built for wide asset based prediction")

    wide = _build_wide_with_global_indexes(dataset_df, index_symbols)
    wide["date"] = pd.to_datetime(wide["date"], errors="coerce")
    wide = wide.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    target_cols = [c for c in wide.columns if c.endswith("_FWD_RET")]
    if not target_cols:
        raise SystemExit("No *_FWD_RET target columns found in wide dataset")

    unique_dates = np.array(sorted(wide["date"].dropna().unique()))
    if len(unique_dates) <= holdout_days:
        raise SystemExit(f"Not enough dates ({len(unique_dates)}) for holdout_days={holdout_days}")
    holdout_dates = set(unique_dates[-holdout_days:])

    numeric_cols = [c for c in wide.columns if c != "date" and pd.api.types.is_numeric_dtype(wide[c])]
    base_feature_cols = [c for c in numeric_cols if not c.endswith("_FWD_RET")]

    try:
        from xgboost import XGBRegressor  # noqa: PLC0415
    except Exception as ex:
        raise SystemExit(f"xgboost is required for wide asset based prediction: {ex}") from ex

    rows: list[dict[str, Any]] = []
    latest_row = wide.iloc[-1:]

    for target_col in sorted(target_cols):
        symbol = target_col[: -len("_FWD_RET")]
        feature_cols = [c for c in base_feature_cols if c != target_col]

        model_df = wide[["date", target_col] + feature_cols].dropna(subset=[target_col]).copy()
        if model_df.empty:
            continue

        train_df = model_df[~model_df["date"].isin(holdout_dates)].copy()
        holdout_df = model_df[model_df["date"].isin(holdout_dates)].copy()
        if len(train_df) < min_train_rows or holdout_df.empty:
            continue

        feature_cols = [c for c in feature_cols if train_df[c].notna().any()]
        if not feature_cols:
            continue

        X_train = train_df[feature_cols]
        y_train = train_df[target_col].to_numpy(dtype=float)
        X_holdout = holdout_df[feature_cols]
        y_holdout = holdout_df[target_col].to_numpy(dtype=float)

        model = XGBRegressor(
            objective="reg:squarederror",
            n_estimators=n_estimators,
            learning_rate=learning_rate,
            max_depth=max_depth,
            subsample=subsample,
            colsample_bytree=colsample_bytree,
            reg_alpha=reg_alpha,
            reg_lambda=reg_lambda,
            min_child_weight=min_child_weight,
            random_state=42,
            n_jobs=-1,
        )
        model.fit(X_train, y_train)

        holdout_pred = model.predict(X_holdout)
        holdout_r2 = float(np.nan)
        if len(y_holdout) > 1:
            ss_res = float(np.sum((y_holdout - holdout_pred) ** 2))
            ss_tot = float(np.sum((y_holdout - np.mean(y_holdout)) ** 2))
            holdout_r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else np.nan
        holdout_mae = float(np.mean(np.abs(y_holdout - holdout_pred)))
        ranking_score = holdout_r2 if np.isfinite(holdout_r2) else -holdout_mae
        ranking_basis = "r2" if np.isfinite(holdout_r2) else "neg_mae_fallback"

        latest_pred = np.nan
        if not latest_row.empty:
            X_latest = latest_row[feature_cols].copy()
            X_latest = X_latest.fillna(train_df[feature_cols].median(numeric_only=True)).fillna(0.0)
            latest_pred = float(model.predict(X_latest)[0])

        close_col = f"{symbol}_CLOSE"
        latest_close = float(latest_row[close_col].iloc[0]) if close_col in latest_row.columns else np.nan
        rows.append(
            {
                "symbol": symbol,
                "date": wide["date"].max(),
                "close": latest_close,
                # Keep pred_ret as the ranking signal so existing consumers work unchanged.
                "pred_ret": ranking_score,
                "predicted_fwd_ret": latest_pred,
                "model_r2_score": holdout_r2,
                "model_mae": holdout_mae,
                "ranking_basis": ranking_basis,
                "model_type": "wide_asset_based_prediction",
            }
        )

    if not rows:
        raise SystemExit(
            "Wide asset based prediction produced no trainable symbol models; "
            "increase lookback or lower wide_asset_based_prediction.min_train_rows"
        )

    ranked = pd.DataFrame(rows)
    ranked = ranked.replace([np.inf, -np.inf], np.nan)
    ranked = ranked.dropna(subset=["symbol", "pred_ret"])

    if strict_r2_only:
        ranked = ranked[ranked["model_r2_score"].notna() & (ranked["model_r2_score"] >= min_r2_score)].copy()

    if ranked.empty:
        if strict_r2_only:
            raise SystemExit(
                "Wide asset based prediction produced no rows passing strict R2 gate; "
                "lower wide_asset_based_prediction.min_r2_score or disable strict_r2_only"
            )
        raise SystemExit("Wide asset based prediction produced no ranked rows")

    ranked = ranked.sort_values("pred_ret", ascending=False)
    pool_size = max(max_assets * max(1, corr_search_multiplier), max_assets)
    pool_symbols = ranked["symbol"].head(pool_size).tolist()
    kept_symbols, _drop_map = dedup_ranked_symbols(
        pool_symbols,
        threshold=corr_threshold,
        period=corr_period,
        interval=corr_interval,
        method="CORRELATION",
        batch_size=max(10, int(runtime.get("download_batch_size", 25))),
    )

    selected: list[str] = []
    for sym in kept_symbols:
        if sym not in selected:
            selected.append(sym)
        if len(selected) >= max_assets:
            break
    if len(selected) < min_assets:
        for sym in ranked["symbol"].tolist():
            if sym not in selected:
                selected.append(sym)
            if len(selected) >= min_assets:
                break

    selected_df = ranked[ranked["symbol"].isin(selected)].copy()
    selected_df = selected_df.sort_values("pred_ret", ascending=False)
    selected_df = selected_df.head(max_assets)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    selected_df.to_csv(out_path, index=False)
    print(
        f"Wrote wide asset based predictions to {out_path} "
        f"(selected {len(selected_df)} assets, target range {min_assets}-{max_assets})"
    )


def score_latest_local(
    model,
    feat_cols: list,
    cand_df: pd.DataFrame,
    symbols: list[str],
    return_days: int,
    out_path: Path,
    scaler=None,
    metrics: dict = None,
):
    """Score latest prices using enhanced features from predict_short_term module.

    Args:
        scaler: Fitted StandardScaler from training data (prevents data leakage)
    """
    from yfinance.screener.predict_short_term import score_latest

    # Use the updated score_latest that handles enhanced features
    df = score_latest(
        model, feat_cols, cand_df, symbols, return_days, scaler=scaler, metrics=metrics, use_enhanced_features=True
    )

    if df.empty:
        raise SystemExit("No rows to score")

    # Add avg_ret column if not present
    if "avg_ret" not in df.columns and all(c in df.columns for c in ["ret_1d", "ret_3d", "ret_5d"]):
        df["avg_ret"] = df[["ret_1d", "ret_3d", "ret_5d"]].mean(axis=1)

    # score_latest already handles ranking and column ordering
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"Wrote predictions to {out_path}")


def score_breakout_local(model, feat_cols, cand_df, symbols, return_days, out_path: Path, metrics: dict | None = None):
    """Score the latest day with the breakout classifier and write the CSV."""
    df = score_latest_breakout(model, feat_cols, cand_df, symbols, return_days, metrics=metrics)
    if df.empty:
        raise SystemExit("No rows to score")

    if "avg_ret" not in df.columns and all(c in df.columns for c in ["ret_1d", "ret_3d", "ret_5d"]):
        df["avg_ret"] = df[["ret_1d", "ret_3d", "ret_5d"]].mean(axis=1)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"Wrote breakout predictions to {out_path}")


def main():
    p = argparse.ArgumentParser(description="Run prediction screener with filtering options")
    p.add_argument("--config", help="Path to JSON config file for prediction screener settings")
    p.add_argument(
        "--strategy-config",
        default="configs/strategy.json",
        help="Path to main strategy config (used for wide asset based prediction mode)",
    )
    p.add_argument("--candidates-file", default=None, help="Input screener CSV (if omitted use latest)")
    p.add_argument("--sector", default=None, help="Filter symbols by sector (case-insensitive substring)")
    p.add_argument("--industry", default=None, help="Filter symbols by industry (case-insensitive substring)")
    p.add_argument("--limit", type=int, default=None, help="Max number of symbols to analyze")
    p.add_argument("--return-days", type=int, default=None, choices=[1, 3, 5], help="Forward return horizon to predict")
    p.add_argument("--lookback", type=int, default=None, help="Price history lookback (days)")
    p.add_argument("--out", default=None, help="Optional output path for predictions CSV")
    p.add_argument(
        "--training-data-out",
        default=None,
        help="Optional output path for training dataset CSV used by the prediction model",
    )
    p.add_argument(
        "--training-data-only",
        action="store_true",
        help="Build and export training data only; skip model training and scoring",
    )
    p.add_argument("--versioned", action="store_const", const=True, default=None, help="Also save a timestamped copy")
    p.add_argument(
        "--target-type",
        dest="target_type",
        choices=list(_TARGET_TYPES),
        default=None,
        help="Model target: 'ranking' (default, relative-return rank) or 'breakout' (breakout classifier)",
    )
    args = p.parse_args()

    runtime = resolve_runtime_config(args)
    strategy_cfg = _load_strategy_config(args.strategy_config)
    strategy_short_term = (strategy_cfg.get("buckets") or {}).get("short_term") or {}
    strategy_pipeline = str(strategy_short_term.get("prediction_pipeline") or "").strip().lower()
    runtime_pipeline = str(runtime.get("pipeline") or "").strip().lower()
    pipeline_mode = strategy_pipeline or runtime_pipeline or "standard"
    if pipeline_mode == "wide_asset_based_prediction":
        wide_cfg = dict(strategy_short_term.get("wide_asset_based_prediction") or {})
        if not bool(wide_cfg.get("enabled", True)):
            pipeline_mode = "standard"
    is_breakout = runtime["target"]["type"] == "breakout"

    repo = Path(__file__).resolve().parents[2]
    reports = repo / "reports" / "screener_results"

    if runtime["candidates_file"]:
        cand_file = Path(runtime["candidates_file"])
    else:
        cand_file = find_latest_screener_file(reports)

    print(f"Using candidates file: {cand_file}")
    cand_df = pd.read_csv(cand_file)
    cand_df = filter_candidates(cand_df, runtime["sector"], runtime["industry"])

    # Always include current short-term positions from both accounts
    short_term_positions: set[str] = set()
    if runtime.get("include_short_term_positions", True):
        short_term_positions = fetch_short_term_positions()
        if short_term_positions:
            print(
                f"Found {len(short_term_positions)} short-term positions to include: {', '.join(sorted(short_term_positions))}"
            )

    # Select symbols from filtered candidates (up to limit)
    candidate_symbols = list(cand_df["symbol"].astype(str).str.upper().unique())

    # Ensure short-term positions are always included
    symbols_from_screener = [s for s in candidate_symbols if s not in short_term_positions][: runtime["limit"]]

    # Combine: positions + screener symbols (incremental count)
    symbols = sorted(short_term_positions) + symbols_from_screener

    print(
        f"Analyzing {len(symbols)} symbols: {len(short_term_positions)} positions + {len(symbols_from_screener)} from screener (limit={runtime['limit']})"
    )

    # Try to build dataset with adaptive lookback fallback
    df = pd.DataFrame()
    lookback_attempts = [runtime["lookback"], 180, 90, 60]

    with configured_yfinance_downloads(runtime):
        for attempt, lookback in enumerate(lookback_attempts):
            if attempt > 0:
                print(f"\nRetrying with shorter lookback: {lookback} days...")

            df = build_dataset(cand_df, symbols, lookback, runtime["return_days"], include_breakout=is_breakout)

            if not df.empty:
                if attempt > 0:
                    print(f"[OK] Successfully built dataset with {lookback}-day lookback")
                break

            if attempt < len(lookback_attempts) - 1:
                print(f"[RETRY] Failed with {lookback}-day lookback, trying shorter period...")

    if df.empty:
        print("\n" + "=" * 60)
        print("ERROR: Unable to construct training data")
        print("=" * 60)
        print("\nPossible causes:")
        print("  1. Symbols may be newly listed with insufficient history")
        print("  2. yfinance download may have failed (check connectivity)")
        print("  3. Date range may be too far in the future")
        print("\nSuggestions:")
        print("  - Check if symbols in screener CSV have valid price history")
        print("  - Try running with fewer symbols (--limit 50)")
        print(
            '  - Verify yfinance is working: python -c \'import yfinance; print(yfinance.Ticker("AAPL").history(period="1mo"))\''
        )
        print("=" * 60)
        raise SystemExit(1)

    print(f"Constructed dataset with {len(df)} rows")

    if args.training_data_out:
        training_data_path = Path(args.training_data_out)
        training_data_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(training_data_path, index=False)
        print(f"Wrote training dataset to {training_data_path}")

    if args.training_data_only:
        print("Training-data-only mode enabled; skipping model training and scoring")
        return

    if pipeline_mode == "wide_asset_based_prediction":
        ts = datetime.utcnow().strftime("%Y%m%d")
        if runtime["out"]:
            out_path = Path(runtime["out"])
        else:
            out_path = reports / f"predictions_{ts}.csv"
        _run_wide_asset_based_prediction(runtime, strategy_short_term, df, out_path)
        return

    if is_breakout:
        model, feat_cols, metrics, feature_importance, scaler = train_and_evaluate_breakout(
            df,
            runtime["return_days"],
            model_config=runtime.get("model", {}),
            train_fraction=runtime.get("train_split_fraction", 0.7),
            embargo_days=max(0, int(runtime["return_days"])),
            breakout_k=runtime["target"]["breakout_k"],
        )
    else:
        model, feat_cols, metrics, feature_importance, scaler = train_and_evaluate(
            df,
            runtime["return_days"],
            model_config=runtime.get("model", {}),
            train_fraction=runtime.get("train_split_fraction", 0.7),
            embargo_days=max(0, int(runtime["return_days"])),
        )

    # determine output path. Breakout runs use a distinct filename prefix so they are never
    # picked up by the live buy bucket's ``predictions_*.csv`` discovery by accident.
    ts = datetime.utcnow().strftime("%Y%m%d")
    file_prefix = "breakout_" if is_breakout else ""
    if runtime["out"]:
        out_path = Path(runtime["out"])
    else:
        out_path = reports / f"{file_prefix}predictions_{ts}.csv"
    if not feature_importance.empty:
        # Use same directory as output path for feature importance
        fi_path = out_path.parent / f"{file_prefix}feature_importance_{ts}.csv"
        fi_path.parent.mkdir(parents=True, exist_ok=True)
        feature_importance.to_csv(fi_path, index=False)
        print(f"Wrote feature importances to {fi_path}")

    with configured_yfinance_downloads(runtime):
        if is_breakout:
            score_breakout_local(model, feat_cols, cand_df, symbols, runtime["return_days"], out_path, metrics=metrics)
        else:
            score_latest_local(
                model, feat_cols, cand_df, symbols, runtime["return_days"], out_path, scaler=scaler, metrics=metrics
            )

    if runtime["versioned"]:
        ver = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        ver_path = out_path.with_name(out_path.stem + f".v{ver}" + out_path.suffix)
        ver_path.parent.mkdir(parents=True, exist_ok=True)
        ver_path.write_text(out_path.read_text())
        print(f"Wrote versioned copy: {ver_path}")


if __name__ == "__main__":
    main()
