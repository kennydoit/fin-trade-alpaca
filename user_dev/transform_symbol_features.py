from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


DEFAULT_INPUT = Path("user_dev/prediction_training_data_20261027.csv")
DEFAULT_OUTPUT = Path("user_dev/prediction_training_data_20261027_wide.csv")


def build_wide_symbol_features(df: pd.DataFrame, include_target: bool = True) -> pd.DataFrame:
    required = {"date", "symbol"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    working = df.copy()
    working["date"] = pd.to_datetime(working["date"], errors="coerce")
    working = working.dropna(subset=["date", "symbol"])
    working["symbol"] = working["symbol"].astype(str).str.upper().str.strip()

    excluded = {"date", "symbol"}
    if not include_target:
        excluded.add("fwd_ret")

    numeric_feature_cols = [
        c for c in working.columns if c not in excluded and pd.api.types.is_numeric_dtype(working[c])
    ]
    if not numeric_feature_cols:
        raise ValueError("No numeric feature columns available to pivot")

    # Keep one value per date/symbol pair before pivoting.
    deduped = working.sort_values(["date", "symbol"]).drop_duplicates(subset=["date", "symbol"], keep="last")

    wide = deduped.pivot_table(
        index="date",
        columns="symbol",
        values=numeric_feature_cols,
        aggfunc="first",
    )

    # Convert multi-index columns (feature, symbol) -> SYMBOL_FEATURE and normalize to uppercase.
    wide.columns = [f"{symbol}_{feature}".upper() for feature, symbol in wide.columns]
    wide = wide.sort_index(axis=1)
    wide = wide.sort_index()
    wide.index.name = "date"

    return wide.reset_index()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transform long training data into one-row-per-date symbol-feature columns"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Path to long-format training CSV")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Path to wide-format output CSV",
    )
    parser.add_argument(
        "--exclude-target",
        action="store_true",
        help="Exclude fwd_ret from generated symbol feature columns",
    )
    args = parser.parse_args()

    df = pd.read_csv(args.input)
    wide_df = build_wide_symbol_features(df, include_target=not args.exclude_target)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    wide_df.to_csv(args.output, index=False)

    print(f"Input rows: {len(df)}")
    print(f"Output rows (dates): {len(wide_df)}")
    print(f"Output columns: {len(wide_df.columns)}")
    print(f"Wrote wide dataset to: {args.output}")


if __name__ == "__main__":
    main()
