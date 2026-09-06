"""Test script to verify data leakage fix in prediction model."""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

import pandas as pd
import numpy as np
from yfinance.screener.predict_short_term import build_dataset, train_and_evaluate

# Test with a small set of symbols
test_symbols = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA']

# Create minimal candidate dataframe
cand_df = pd.DataFrame({
    'symbol': test_symbols,
    'sector': ['Technology'] * 5,
    'industry': ['Software'] * 5,
    'pct_1w': [0.02] * 5,
    'pct_1m': [0.05] * 5,
    'rel_volume': [1.0] * 5,
    'revenueGrowth': [0.15] * 5,
    'earningsQuarterlyGrowth': [0.10] * 5,
    'pegRatio': [2.0] * 5,
    'trailingPE': [25.0] * 5,
})

print("=" * 80)
print("TESTING DATA LEAKAGE FIX")
print("=" * 80)
print(f"\nTesting with {len(test_symbols)} symbols: {test_symbols}")
print("\nBuilding dataset...")

# Build dataset
df = build_dataset(cand_df, test_symbols, lookback=180, return_days=5)

if df.empty:
    print("\nERROR: Dataset is empty!")
    sys.exit(1)

print(f"\nDataset built successfully: {len(df)} rows")
print(f"Date range: {df['date'].min()} to {df['date'].max()}")
print(f"Unique dates: {df['date'].nunique()}")
print(f"Symbols: {df['symbol'].nunique()}")

# Train and evaluate
print("\n" + "=" * 80)
print("TRAINING AND EVALUATING MODEL")
print("=" * 80)

try:
    model, feat_cols, metrics, fi, scaler = train_and_evaluate(
        df, 
        return_days=5, 
        use_enhanced_features=True,
        model_config={"type": "random_forest"}
    )
    
    print("\n" + "=" * 80)
    print("RESULTS")
    print("=" * 80)
    print(f"\nModel type: {metrics['model_type']}")
    print(f"Number of features: {metrics['n_features']}")
    print(f"\nPerformance Metrics:")
    print(f"  R² Score:     {metrics['r2_score']:.4f}")
    print(f"  Spearman IC:  {metrics['spearman_ic']:.4f}")
    print(f"  MAE:          {metrics['mae']:.6f}")
    
    # Check if R² is reasonable
    if metrics['r2_score'] > -0.5:
        print("\n[SUCCESS] R² score is reasonable (> -0.5)")
        print("Data leakage fix appears to be working!")
    else:
        print(f"\n[WARNING] R² score is still very negative: {metrics['r2_score']:.4f}")
        print("Model may still have issues.")
    
except Exception as e:
    print(f"\n[ERROR] Training failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 80)
print("TEST COMPLETED")
print("=" * 80)
