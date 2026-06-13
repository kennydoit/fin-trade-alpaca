"""Diagnose model performance metrics."""
import pandas as pd

print("Comparing model metrics across CSV files:")
print("=" * 70)

files = [
    'reports/screener_results/predictions_20260613.csv',
    'reports/screener_results/test_debug.csv'
]

for f in files:
    try:
        df = pd.read_csv(f)
        r2 = df['model_r2_score'].iloc[0]
        ic = df['model_spearman_ic'].iloc[0]
        mae = df['model_mae'].iloc[0]
        
        print(f"\n{f.split('/')[-1]}:")
        print(f"  Rows: {len(df)}")
        print(f"  R² Score:    {r2:.6f}")
        print(f"  Spearman IC: {ic:.6f}")
        print(f"  MAE:         {mae:.6f}")
        
        # Interpret R²
        if r2 < 0:
            print(f"  ⚠️  NEGATIVE R² - Model worse than predicting mean!")
        elif r2 < 0.1:
            print(f"  ⚠️  Very low R² - Poor predictive power")
        elif r2 < 0.3:
            print(f"  ⚙️  Low R² - Weak predictive power")
        else:
            print(f"  ✓ R² indicates some predictive power")
            
    except Exception as e:
        print(f"\n{f}: Error - {e}")

print("\n" + "=" * 70)
print("DIAGNOSIS:")
print("=" * 70)
print("""
Negative R² means: Model predictions are worse than just predicting
the mean of the training data. This indicates:

1. Feature engineering issues - Current technical indicators may not
   predict short-term returns effectively
   
2. Data quality - Need more history or better candidate selection

3. Target variable - 5-day returns might be too noisy to predict

4. Overfitting - Random Forest with default params might overfit on
   small training sets

RECOMMENDATIONS:
- Increase --limit to get more training data (try 50-100 symbols)
- Increase --lookback for more historical samples
- Try different return horizons (--return-days 1 or 3)
- Add more predictive features (volume, fundamental ratios)
- Use LightGBM if available (install: pip install lightgbm)
- Consider ensemble of multiple models
""")
