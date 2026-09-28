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

import numpy as np
import pandas as pd


def winsorize(series: pd.Series, lower: float = 0.01, upper: float = 0.99) -> pd.Series:
    """Clip values at percentiles to handle outliers and infinities."""
    # Replace infinities with NaN first
    series = series.replace([np.inf, -np.inf], np.nan)

    if series.isna().all():
        return series

    lower_bound = series.quantile(lower)
    upper_bound = series.quantile(upper)
    return series.clip(lower_bound, upper_bound)


def rank_normalize(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Convert features to percentile ranks within each date (cross-sectional)."""
    df = df.copy()

    for col in columns:
        if col not in df.columns:
            continue
        # Rank within each date, then normalize to [0, 1]
        df[f"{col}_rank"] = df.groupby("date")[col].rank(pct=True)

    return df


def create_sector_relative_features(df: pd.DataFrame, features: list[str]) -> pd.DataFrame:
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


def create_lagged_features(df: pd.DataFrame, features: list[str], lags: list[int] = [1, 5]) -> pd.DataFrame:
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
    """
    Better imputation strategy than fillna(0).
    WARNING: This causes data leakage when used on combined train+test data.
    Use improve_imputation_train and improve_imputation_test instead.
    """
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


def improve_imputation_train(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    Improved imputation for TRAINING data, computing and returning statistics.
    Only uses within-date medians (cross-sectional) to avoid temporal leakage.

    Returns:
        Tuple of (imputed DataFrame, statistics dict)
    """
    df = df.copy()
    stats = {}

    # For ratios (P/E, PEG), use median imputation + missing indicator
    ratio_cols = ["trailingPE", "pegRatio"]
    for col in ratio_cols:
        if col not in df.columns:
            continue

        # Create missing indicator (binary feature)
        df[f"{col}_missing"] = df[col].isna().astype(int)

        # Compute fallback median from TRAIN data only
        fallback_median = df[col].median()
        stats[f"{col}_median"] = fallback_median

        # Fill with within-date median (cross-sectional, no temporal leakage)
        # For dates with insufficient data, use overall train median
        df[col] = df.groupby("date")[col].transform(
            lambda x: x.fillna(x.median()) if x.count() > 5 else x.fillna(fallback_median)
        )

    # For growth rates
    growth_cols = ["revenueGrowth", "earningsQuarterlyGrowth"]
    for col in growth_cols:
        if col not in df.columns:
            continue

        # Missing indicator
        df[f"{col}_missing"] = df[col].isna().astype(int)

        # Compute fallback median from TRAIN data only
        fallback_median = df[col].median()
        stats[f"{col}_median"] = fallback_median

        # Fill with sector median within date if available, else date median, else train median
        if "sector" in df.columns:
            # Try sector-date median first
            df[col] = df.groupby(["date", "sector"])[col].transform(
                lambda x: x.fillna(x.median()) if x.count() > 3 else x
            )
            # Fill remaining with date median
            df[col] = df.groupby("date")[col].transform(
                lambda x: x.fillna(x.median()) if x.count() > 5 else x.fillna(fallback_median)
            )
        else:
            df[col] = df.groupby("date")[col].transform(
                lambda x: x.fillna(x.median()) if x.count() > 5 else x.fillna(fallback_median)
            )

    # For other features, use 0 (avoid forward/backward fill which causes temporal leakage)
    remaining_cols = [c for c in df.columns if df[c].isna().any() and c not in ratio_cols + growth_cols]
    for col in remaining_cols:
        if df[col].dtype in [np.float64, np.float32, np.int64, np.int32]:
            # Use within-date median if available
            date_median = df.groupby("date")[col].transform("median")
            df[col] = df[col].fillna(date_median).fillna(0)

    return df, stats


def improve_imputation_test(df: pd.DataFrame, train_stats: dict) -> pd.DataFrame:
    """
    Improved imputation for TEST data, using statistics from training data.
    Only uses within-date medians (cross-sectional) to avoid temporal leakage.

    Args:
        df: Test DataFrame
        train_stats: Statistics from training data

    Returns:
        Imputed test DataFrame
    """
    df = df.copy()

    # For ratios (P/E, PEG)
    ratio_cols = ["trailingPE", "pegRatio"]
    for col in ratio_cols:
        if col not in df.columns:
            continue

        # Create missing indicator
        df[f"{col}_missing"] = df[col].isna().astype(int)

        # Use train median as fallback
        fallback_median = train_stats.get(f"{col}_median", 0)

        # Fill with within-date median (cross-sectional)
        df[col] = df.groupby("date")[col].transform(
            lambda x: x.fillna(x.median()) if x.count() > 5 else x.fillna(fallback_median)
        )

    # For growth rates
    growth_cols = ["revenueGrowth", "earningsQuarterlyGrowth"]
    for col in growth_cols:
        if col not in df.columns:
            continue

        # Missing indicator
        df[f"{col}_missing"] = df[col].isna().astype(int)

        # Use train median as fallback
        fallback_median = train_stats.get(f"{col}_median", 0)

        # Fill with sector median within date if available
        if "sector" in df.columns:
            # Try sector-date median first
            df[col] = df.groupby(["date", "sector"])[col].transform(
                lambda x: x.fillna(x.median()) if x.count() > 3 else x
            )
            # Fill remaining with date median
            df[col] = df.groupby("date")[col].transform(
                lambda x: x.fillna(x.median()) if x.count() > 5 else x.fillna(fallback_median)
            )
        else:
            df[col] = df.groupby("date")[col].transform(
                lambda x: x.fillna(x.median()) if x.count() > 5 else x.fillna(fallback_median)
            )

    # For other features, use 0
    remaining_cols = [c for c in df.columns if df[c].isna().any() and c not in ratio_cols + growth_cols]
    for col in remaining_cols:
        if df[col].dtype in [np.float64, np.float32, np.int64, np.int32]:
            # Use within-date median if available
            date_median = df.groupby("date")[col].transform("median")
            df[col] = df[col].fillna(date_median).fillna(0)

    return df


def enhance_features(df: pd.DataFrame, add_sector_features: bool = True) -> pd.DataFrame:
    """
    Main function to enhance features with all improvements.
    WARNING: This function should NOT be used for train/test splits as it causes data leakage.
    Use enhance_features_train and enhance_features_test instead.

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


def enhance_features_train(df: pd.DataFrame, add_sector_features: bool = True) -> tuple[pd.DataFrame, dict]:
    """
    Enhance features for TRAINING data, computing and returning statistics.
    This prevents data leakage by computing statistics only on training data.

    Args:
        df: Training DataFrame with basic features
        add_sector_features: Whether to add sector-relative features

    Returns:
        Tuple of (enhanced DataFrame, statistics dict for test set)
    """
    print(f"[TRAIN] Starting feature enhancement... Initial shape: {df.shape}")
    df = df.copy()
    stats = {}

    # 1. Compute winsorization bounds on TRAIN data only
    winsor_cols = ["trailingPE", "pegRatio", "revenueGrowth", "earningsQuarterlyGrowth"]
    for col in winsor_cols:
        if col in df.columns and not df[col].isna().all():
            lower_bound = df[col].quantile(0.01)
            upper_bound = df[col].quantile(0.99)
            stats[f"{col}_lower"] = lower_bound
            stats[f"{col}_upper"] = upper_bound
            df[col] = df[col].clip(lower_bound, upper_bound)

    # 2. Create interaction features (deterministic, no leakage)
    df = create_interaction_features(df)
    print(f"[TRAIN] After interactions: {df.shape}")

    # 3. Create lagged features (uses past data only, no leakage)
    lagged_features = ["mom_20d", "vol_10d", "rel_volume", "pct_1w"]
    df = create_lagged_features(df, lagged_features, lags=[1, 5])
    print(f"[TRAIN] After lags: {df.shape}")

    # 4. Sector-relative features (cross-sectional within date, no leakage)
    if add_sector_features and "sector" in df.columns:
        sector_features = ["mom_20d", "trailingPE", "revenueGrowth", "pct_1m"]
        df = create_sector_relative_features(df, sector_features)
        print(f"[TRAIN] After sector features: {df.shape}")

    # 5. Cross-sectional ranks (within date, no leakage)
    rank_features = ["mom_20d", "vol_10d", "trailingPE", "revenueGrowth", "pct_1m", "rel_volume"]
    df = rank_normalize(df, rank_features)
    print(f"[TRAIN] After rank features: {df.shape}")

    # 6. Compute imputation statistics on TRAIN data only
    df, impute_stats = improve_imputation_train(df)
    stats.update(impute_stats)
    print(f"[TRAIN] After imputation: {df.shape}")

    # Final: Drop any remaining NaN in target
    df = df.dropna(subset=["fwd_ret"])
    print(f"[TRAIN] Final shape: {df.shape}")

    return df, stats


def enhance_features_test(df: pd.DataFrame, train_stats: dict, add_sector_features: bool = True) -> pd.DataFrame:
    """
    Enhance features for TEST data, using statistics from training data.
    This prevents data leakage by using only training set statistics.

    Args:
        df: Test DataFrame with basic features
        train_stats: Statistics computed from training data
        add_sector_features: Whether to add sector-relative features

    Returns:
        Enhanced test DataFrame
    """
    print(f"[TEST] Starting feature enhancement... Initial shape: {df.shape}")
    df = df.copy()

    # 1. Apply winsorization using TRAIN bounds
    winsor_cols = ["trailingPE", "pegRatio", "revenueGrowth", "earningsQuarterlyGrowth"]
    for col in winsor_cols:
        if col in df.columns:
            lower_key = f"{col}_lower"
            upper_key = f"{col}_upper"
            if lower_key in train_stats and upper_key in train_stats:
                df[col] = df[col].clip(train_stats[lower_key], train_stats[upper_key])

    # 2. Create interaction features (deterministic, no leakage)
    df = create_interaction_features(df)
    print(f"[TEST] After interactions: {df.shape}")

    # 3. Create lagged features (uses past data only, no leakage)
    lagged_features = ["mom_20d", "vol_10d", "rel_volume", "pct_1w"]
    df = create_lagged_features(df, lagged_features, lags=[1, 5])
    print(f"[TEST] After lags: {df.shape}")

    # 4. Sector-relative features (cross-sectional within date, no leakage)
    if add_sector_features and "sector" in df.columns:
        sector_features = ["mom_20d", "trailingPE", "revenueGrowth", "pct_1m"]
        df = create_sector_relative_features(df, sector_features)
        print(f"[TEST] After sector features: {df.shape}")

    # 5. Cross-sectional ranks (within date, no leakage)
    rank_features = ["mom_20d", "vol_10d", "trailingPE", "revenueGrowth", "pct_1m", "rel_volume"]
    df = rank_normalize(df, rank_features)
    print(f"[TEST] After rank features: {df.shape}")

    # 6. Apply imputation using TRAIN statistics
    df = improve_imputation_test(df, train_stats)
    print(f"[TEST] After imputation: {df.shape}")

    # Final: Drop any remaining NaN in target
    df = df.dropna(subset=["fwd_ret"])
    print(f"[TEST] Final shape: {df.shape}")

    return df
