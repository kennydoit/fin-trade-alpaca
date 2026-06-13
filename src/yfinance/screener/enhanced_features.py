"""
Enhanced feature engineering for stock return prediction.

Improvements over basic technical features:
1. Better imputation strategies (median for ratios, missing indicators)
2. Cross-sectional rank features (percentile within date)
3. Sector-relative features
4. Interaction features (momentum×volume, growth×value)
5. Winsorization of outliers
6. Lagged features
"""
import pandas as pd
import numpy as np
from typing import List, Tuple


def winsorize(series: pd.Series, lower: float = 0.01, upper: float = 0.99) -> pd.Series:
    """Clip values at percentiles to handle outliers."""
    if series.isna().all():
        return series
    lower_bound = series.quantile(lower)
    upper_bound = series.quantile(upper)
    return series.clip(lower_bound, upper_bound)


def rank_normalize(df: pd.DataFrame, columns: List[str]) -> pd.DataFrame:
    """Convert features to percentile ranks within each date (cross-sectional)."""
    df = df.copy()
    
    for col in columns:
        if col not in df.columns:
            continue
        # Rank within each date, then normalize to [0, 1]
        df[f"{col}_rank"] = df.groupby("date")[col].rank(pct=True)
    
    return df


def create_sector_relative_features(df: pd.DataFrame, features: List[str]) -> pd.DataFrame:
    """Create sector-relative versions of features (feature - sector_mean)."""
    df = df.copy()
    
    if "sector" not in df.columns:
        return df
    
    for feat in features:
        if feat not in df.columns:
            continue
        # Subtract sector mean for each date
        sector_mean = df.groupby(["date", "sector"])[feat].transform("mean")
        df[f"{feat}_vs_sector"] = df[feat] - sector_mean
    
    return df


def create_interaction_features(df: pd.DataFrame) -> pd.DataFrame:
    """Create interaction features that capture combined effects."""
    df = df.copy()
    
    # Momentum × Volume (strong moves on high volume)
    if "mom_20d" in df.columns and "rel_volume" in df.columns:
        df["mom_volume"] = df["mom_20d"] * df["rel_volume"]
    
    # Volatility-adjusted return (Sharpe-like)
    if "mom_20d" in df.columns and "vol_10d" in df.columns:
        df["sharpe_like"] = df["mom_20d"] / (df["vol_10d"] + 1e-6)
    
    # Short-term reversal × volatility
    if "ret_1d" in df.columns and "vol_10d" in df.columns:
        df["reversal_vol"] = df["ret_1d"] * df["vol_10d"]
    
    # Growth at reasonable price (GARP)
    if "revenueGrowth" in df.columns and "trailingPE" in df.columns:
        df["garp"] = df["revenueGrowth"] / (df["trailingPE"] + 1e-6)
    
    # Momentum contrast (long-term vs short-term)
    if "pct_1m" in df.columns and "pct_1w" in df.columns:
        df["mom_contrast"] = df["pct_1m"] - df["pct_1w"]
    
    # Value × momentum (combination strategy)
    if "trailingPE" in df.columns and "mom_20d" in df.columns:
        # Low P/E (value) × positive momentum
        df["value_momentum"] = (-df["trailingPE"]) * df["mom_20d"]
    
    return df


def create_lagged_features(df: pd.DataFrame, features: List[str], lags: List[int] = [1, 5]) -> pd.DataFrame:
    """Create lagged versions of features for each symbol."""
    df = df.copy()
    df = df.sort_values(["symbol", "date"])
    
    for feat in features:
        if feat not in df.columns:
            continue
        for lag in lags:
            df[f"{feat}_lag{lag}"] = df.groupby("symbol")[feat].shift(lag)
    
    return df


def improve_imputation(df: pd.DataFrame) -> pd.DataFrame:
    """Better imputation strategy than fillna(0)."""
    df = df.copy()
    
    # For ratios (P/E, PEG), use median imputation + missing indicator
    ratio_cols = ["trailingPE", "pegRatio"]
    for col in ratio_cols:
        if col not in df.columns:
            continue
        
        # Winsorize first to handle outliers
        df[col] = winsorize(df[col])
        
        # Create missing indicator (binary feature)
        df[f"{col}_missing"] = df[col].isna().astype(int)
        
        # Fill with median (computed by date if enough data)
        if df.groupby("date")[col].count().min() > 5:
            df[col] = df.groupby("date")[col].transform(lambda x: x.fillna(x.median()))
        else:
            df[col] = df[col].fillna(df[col].median())
    
    # For growth rates, 0 might be reasonable but use median within sector if available
    growth_cols = ["revenueGrowth", "earningsQuarterlyGrowth"]
    for col in growth_cols:
        if col not in df.columns:
            continue
        
        # Winsorize
        df[col] = winsorize(df[col])
        
        # Missing indicator
        df[f"{col}_missing"] = df[col].isna().astype(int)
        
        # Fill with sector median if sector available, else overall median
        if "sector" in df.columns and df.groupby(["date", "sector"])[col].count().min() > 3:
            df[col] = df.groupby(["date", "sector"])[col].transform(lambda x: x.fillna(x.median()))
        else:
            df[col] = df[col].fillna(df[col].median())
    
    # For other features, use forward-fill then backward-fill then 0
    remaining_cols = [c for c in df.columns if df[c].isna().any() and c not in ratio_cols + growth_cols]
    for col in remaining_cols:
        if df[col].dtype in [np.float64, np.float32, np.int64, np.int32]:
            df[col] = df.groupby("symbol")[col].ffill().bfill().fillna(0)
    
    return df


def enhance_features(df: pd.DataFrame, add_sector_features: bool = True) -> pd.DataFrame:
    """
    Main function to enhance features with all improvements.
    
    Args:
        df: DataFrame with basic features (from build_dataset)
        add_sector_features: Whether to add sector-relative features (requires 'sector' column)
    
    Returns:
        DataFrame with enhanced features
    """
    print(f"Starting feature enhancement... Initial shape: {df.shape}")
    
    # 1. Winsorize key features
    for col in ["trailingPE", "pegRatio", "revenueGrowth", "earningsQuarterlyGrowth"]:
        if col in df.columns:
            df[col] = winsorize(df[col])
    
    # 2. Create interaction features
    df = create_interaction_features(df)
    print(f"After interactions: {df.shape}")
    
    # 3. Create lagged features for key signals
    lagged_features = ["mom_20d", "vol_10d", "rel_volume", "pct_1w"]
    df = create_lagged_features(df, lagged_features, lags=[1, 5])
    print(f"After lags: {df.shape}")
    
    # 4. Sector-relative features
    if add_sector_features and "sector" in df.columns:
        sector_features = ["mom_20d", "trailingPE", "revenueGrowth", "pct_1m"]
        df = create_sector_relative_features(df, sector_features)
        print(f"After sector features: {df.shape}")
    
    # 5. Cross-sectional ranks
    rank_features = ["mom_20d", "vol_10d", "trailingPE", "revenueGrowth", "pct_1m", "rel_volume"]
    df = rank_normalize(df, rank_features)
    print(f"After rank features: {df.shape}")
    
    # 6. Better imputation (do this last after creating derived features)
    df = improve_imputation(df)
    print(f"After imputation: {df.shape}")
    
    # Final: Drop any remaining NaN in target
    df = df.dropna(subset=["fwd_ret"])
    print(f"Final shape after dropping NaN targets: {df.shape}")
    
    return df
