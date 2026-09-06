import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from yfinance.screener.predict_short_term import build_dataset, download_price_history

cf = Path("reports/screener_results/yfinance_screener_results_with_metrics_20260606.csv")
df = pd.read_csv(cf)
symbols = list(df["symbol"].astype(str).unique())[:50]
print("symbols len", len(symbols))
adj = download_price_history(symbols, 180 + 5)
print("adj shape", None if adj is None else adj.shape)

built = build_dataset(df, symbols, 180, 5)
print("built rows", len(built))
print("columns:", built.columns.tolist() if len(built) > 0 else "")
