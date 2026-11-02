from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


DEFAULT_INPUT = Path("user_dev/prediction_training_data_broad_with_index_20261028.csv")
DEFAULT_OUTPUT = Path("user_dev/prediction_training_data_broad_with_global_index_wide_20261028.csv")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build wide symbol-feature table with global (non-duplicated) index features"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    df = pd.read_csv(args.input)
    if not {"date", "symbol"}.issubset(df.columns):
        raise ValueError("Input must include date and symbol columns")

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "symbol"]).copy()
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()

    index_cols = [c for c in df.columns if c.startswith("IDX_")]

    # Symbol-specific numeric columns (exclude date/symbol and global index columns).
    symbol_value_cols = [
        c
        for c in df.columns
        if c not in {"date", "symbol"}
        and c not in index_cols
        and pd.api.types.is_numeric_dtype(df[c])
    ]
    if not symbol_value_cols:
        raise ValueError("No symbol-specific numeric columns found to pivot")

    # Keep one observation per date/symbol.
    base = df.sort_values(["date", "symbol"]).drop_duplicates(subset=["date", "symbol"], keep="last")

    wide = base.pivot_table(
        index="date",
        columns="symbol",
        values=symbol_value_cols,
        aggfunc="first",
    )
    wide.columns = [f"{symbol}_{feature}".upper() for feature, symbol in wide.columns]
    wide = wide.sort_index().sort_index(axis=1).reset_index()

    # Add one set of global index columns per date.
    if index_cols:
        idx = (
            df[["date"] + index_cols]
            .sort_values("date")
            .drop_duplicates(subset=["date"], keep="last")
        )
        wide = wide.merge(idx, on="date", how="left")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    wide.to_csv(args.output, index=False)

    print(f"Input rows: {len(df)}")
    print(f"Unique symbols: {df['symbol'].nunique()}")
    print(f"Output rows (dates): {len(wide)}")
    print(f"Output columns: {len(wide.columns)}")
    print(f"Global index columns included: {len(index_cols)}")
    print(f"Wrote: {args.output}")


if __name__ == "__main__":
    main()
