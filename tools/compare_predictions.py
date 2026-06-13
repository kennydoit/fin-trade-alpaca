"""Compare baseline vs enhanced feature predictions."""
import pandas as pd

baseline = pd.read_csv('reports/screener_results/test_3day.csv')
enhanced = pd.read_csv('reports/screener_results/enhanced_test_3day.csv')

print('=' * 80)
print('MODEL METRICS COMPARISON')
print('=' * 80)
print('\nBaseline (15 features):')
print(f'  Spearman IC: {baseline["model_spearman_ic"].iloc[0]:.4f}')
print(f'  R² Score:    {baseline["model_r2_score"].iloc[0]:.4f}')
print(f'  MAE:         {baseline["model_mae"].iloc[0]:.6f}')

print('\nEnhanced (43 features):')
ic_improvement = ((enhanced["model_spearman_ic"].iloc[0] / baseline["model_spearman_ic"].iloc[0]) - 1) * 100
print(f'  Spearman IC: {enhanced["model_spearman_ic"].iloc[0]:.4f} (↑{ic_improvement:.1f}%)')
print(f'  R² Score:    {enhanced["model_r2_score"].iloc[0]:.4f}')
print(f'  MAE:         {enhanced["model_mae"].iloc[0]:.6f}')

print('\n' + '=' * 80)
print('TOP 10 PREDICTIONS COMPARISON')
print('=' * 80)

print('\nBaseline Top 10 (sorted by pred_ret):')
baseline_top = baseline.nlargest(10, 'pred_ret')
for i, row in baseline_top.iterrows():
    print(f"  {row['screener_rank']:2d}. {row['symbol']:<6s} pred={row['pred_ret']:+.4f}  sector={row.get('sector', 'N/A')}")

print('\nEnhanced Top 10 (sorted by pred_ret):')
enhanced_top = enhanced.nlargest(10, 'pred_ret')
for i, row in enhanced_top.iterrows():
    print(f"  {row['screener_rank']:2d}. {row['symbol']:<6s} pred={row['pred_ret']:+.4f}  sector={row.get('sector', 'N/A')}")

# Find common symbols in top 10
baseline_top_symbols = set(baseline_top['symbol'])
enhanced_top_symbols = set(enhanced_top['symbol'])
common = baseline_top_symbols & enhanced_top_symbols

print(f'\n{len(common)} symbols appear in both top-10 lists: {", ".join(sorted(common))}')

print('\n' + '=' * 80)
print('RANKING AGREEMENT ANALYSIS')
print('=' * 80)

# Merge and compare rankings
merged = pd.merge(
    baseline[['symbol', 'pred_ret', 'screener_rank']],
    enhanced[['symbol', 'pred_ret', 'screener_rank']],
    on='symbol',
    suffixes=('_baseline', '_enhanced')
)

# Calculate rank correlation
from scipy.stats import spearmanr
rank_corr, _ = spearmanr(merged['screener_rank_baseline'], merged['screener_rank_enhanced'])
print(f'\nRank correlation between baseline and enhanced: {rank_corr:.4f}')
print(f'(1.0 = identical rankings, 0.0 = no agreement, -1.0 = opposite rankings)')

# Show symbols with biggest rank changes
merged['rank_change'] = merged['screener_rank_baseline'] - merged['screener_rank_enhanced']
merged['abs_rank_change'] = merged['rank_change'].abs()
print('\nTop 10 symbols with biggest rank improvements (baseline → enhanced):')
improved = merged.nlargest(10, 'rank_change')[['symbol', 'screener_rank_baseline', 'screener_rank_enhanced', 'rank_change']]
for _, row in improved.iterrows():
    print(f"  {row['symbol']:<6s}: rank {row['screener_rank_baseline']:3.0f} → {row['screener_rank_enhanced']:3.0f} (↑{row['rank_change']:+3.0f})")

print('\n' + '=' * 80)
