"""Diagnose feature quality and coverage in prediction model."""
import pandas as pd
import numpy as np
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from yfinance.screener.predict_short_term import build_dataset, find_latest_screener_file

def analyze_feature_quality():
    """Analyze feature coverage and quality."""
    print("Feature Quality Analysis")
    print("=" * 80)
    
    # Load candidate file
    screener_folder = Path(__file__).parent.parent / "reports" / "screener_results"
    cand_file = find_latest_screener_file(screener_folder)
    cand_df = pd.read_csv(cand_file)
    
    symbols = cand_df["symbol"].tolist()[:50]  # Sample 50 symbols
    print(f"\nAnalyzing {len(symbols)} symbols from {cand_file.name}\n")
    
    # Build dataset
    print("Building dataset (this may take a minute)...")
    df = build_dataset(cand_df, symbols, lookback=120, return_days=5)
    print(f"Dataset shape: {df.shape[0]} rows x {df.shape[1]} columns\n")
    
    # Analyze each feature
    print("Feature Coverage Analysis:")
    print("-" * 80)
    print(f"{'Feature':<25} {'Non-Null %':<12} {'Mean':<12} {'Std':<12} {'Signal?'}")
    print("-" * 80)
    
    feature_cols = [c for c in df.columns if c not in ["date", "symbol", "fwd_ret"]]
    
    for col in sorted(feature_cols):
        non_null_pct = (1 - df[col].isna().sum() / len(df)) * 100
        mean_val = df[col].mean()
        std_val = df[col].std()
        
        # Check if feature has signal (correlation with forward return)
        valid_data = df[[col, "fwd_ret"]].dropna()
        if len(valid_data) > 100:
            corr = valid_data[col].corr(valid_data["fwd_ret"])
            signal = "✓" if abs(corr) > 0.02 else "weak"
            signal_str = f"{signal} (ρ={corr:.3f})"
        else:
            signal_str = "insufficient data"
        
        print(f"{col:<25} {non_null_pct:>10.1f}% {mean_val:>11.4f} {std_val:>11.4f} {signal_str}")
    
    print("\n" + "=" * 80)
    print("ISSUES IDENTIFIED:")
    print("=" * 80)
    
    # Identify problematic features
    issues = []
    
    for col in feature_cols:
        non_null_pct = (1 - df[col].isna().sum() / len(df)) * 100
        
        if non_null_pct < 50:
            issues.append(f"  • {col}: Only {non_null_pct:.1f}% coverage - consider removing or better imputation")
        
        if col in ["pegRatio", "trailingPE"] and (df[col] == 0).sum() / len(df) > 0.3:
            issues.append(f"  • {col}: Many zeros (likely from fillna) - use different imputation")
    
    if issues:
        for issue in issues:
            print(issue)
    else:
        print("  No major issues detected")
    
    print("\n" + "=" * 80)
    print("RECOMMENDATIONS:")
    print("=" * 80)
    print("""
1. BETTER IMPUTATION:
   - For ratios (P/E, PEG): Use median imputation or separate "missing" indicator
   - For growth rates: Use 0 only if truly means "no growth"
   
2. ADD CROSS-SECTIONAL FEATURES:
   - Rank-transform features within date (percentile ranks)
   - Sector-relative features (return vs sector mean)
   - Industry-relative valuations
   
3. ADD INTERACTION FEATURES:
   - Momentum × Volume (strong moves on high volume)
   - Growth × Valuation (GARP strategy)
   - Volatility-adjusted returns (Sharpe-like)
   
4. ADD REGIME INDICATORS:
   - Market volatility (VIX-like from price dispersion)
   - Sector rotation signals
   - Momentum regime (trending vs mean-reverting)
   
5. FEATURE ENGINEERING BEST PRACTICES:
   - Winsorize outliers (1st/99th percentile)
   - Standardize features by date (z-scores within cross-section)
   - Add lagged features (t-1, t-5, t-20)
    """)

if __name__ == "__main__":
    analyze_feature_quality()
