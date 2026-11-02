from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import yfinance as yf


DEFAULT_INPUT = Path("user_dev/prediction_training_data_broad_20261028.csv")
DEFAULT_OUTPUT = Path("user_dev/prediction_training_data_broad_with_index_20261028.csv")
DEFAULT_INDEXES = ("SPY", "QQQ", "IWM", "DIA")


def make_index_features(adj_close: pd.Series) -> pd.DataFrame:
    df = pd.DataFrame({"close": adj_close}).sort_index()
    out = pd.DataFrame(index=df.index)
    out["ret_1d"] = df["close"].pct_change()
    out["ret_3d"] = df["close"].pct_change(3)
    out["ret_5d"] = df["close"].pct_change(5)
    out["vol_10d"] = out["ret_1d"].rolling(10).std()
    out["mom_20d"] = df["close"].pct_change(20)
    out["sma_10"] = df["close"].rolling(10).mean()
    out["price_sma10_z"] = (df["close"] - out["sma_10"]) / df["close"].rolling(60).std()
    out["close"] = df["close"]
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Add market index features to vertical training dataset")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--indexes",
        nargs="+",
        default=list(DEFAULT_INDEXES),
        help="Index/ETF symbols to add as context features (default: SPY QQQ IWM DIA)",
    )
    args = parser.parse_args()

    df = pd.read_csv(args.input)
    if "date" not in df.columns:
        raise ValueError("Input dataset must contain 'date' column")

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).copy()

    min_date = df["date"].min() - pd.Timedelta(days=120)
    max_date = df["date"].max() + pd.Timedelta(days=5)

    print(f"Downloading index prices for: {', '.join(args.indexes)}")
    idx_raw = yf.download(
        tickers=args.indexes,
        start=min_date.strftime("%Y-%m-%d"),
        end=(max_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        progress=False,
        auto_adjust=True,
        group_by="ticker",
    )

    if idx_raw.empty:
        raise SystemExit("Index download returned empty data")

    index_feature_frames: list[pd.DataFrame] = []

    for sym in args.indexes:
        sym_u = sym.upper()

        # yfinance returns different shapes for one vs many tickers.
        if isinstance(idx_raw.columns, pd.MultiIndex):
            if sym_u not in idx_raw.columns.get_level_values(0):
                print(f"Skipping {sym_u}: not found in downloaded data")
                continue
            sym_close = idx_raw[sym_u]["Close"].dropna()
        else:
            if "Close" not in idx_raw.columns:
                print(f"Skipping {sym_u}: Close column missing")
                continue
            sym_close = idx_raw["Close"].dropna()

        if sym_close.empty:
            print(f"Skipping {sym_u}: empty close series")
            continue

        feats = make_index_features(sym_close)
        feats = feats.add_prefix(f"IDX_{sym_u}_")
        feats = feats.reset_index().rename(columns={"Date": "date"})

        # Ensure merge key is plain datetime without timezone.
        feats["date"] = pd.to_datetime(feats["date"], errors="coerce").dt.tz_localize(None)
        index_feature_frames.append(feats)

    out = df.copy()
    out["date"] = out["date"].dt.tz_localize(None)

    for feats in index_feature_frames:
        out = out.merge(feats, on="date", how="left")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.output, index=False)

    print(f"Input rows: {len(df)}")
    print(f"Output rows: {len(out)}")
    print(f"Added index feature columns: {len(out.columns) - len(df.columns)}")
    print(f"Wrote: {args.output}")


if __name__ == "__main__":
    main()
