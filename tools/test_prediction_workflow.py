"""Test the complete prediction screener workflow with metadata.

This demonstrates:
1. Running the prediction screener
2. Verifying CSV output has metadata columns
3. Using the metadata to populate database (simulated)
"""
import sys
from pathlib import Path

# Add repo root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from database.strategy_metadata import bulk_set_strategy_metadata


def test_prediction_screener_with_metadata():
    """Test the complete workflow."""
    print("=" * 70)
    print("PREDICTION SCREENER WITH STRATEGY ATTRIBUTION - FULL TEST")
    print("=" * 70)
    
    # Step 1: Check latest predictions CSV
    print("\n[Step 1] Finding latest predictions CSV...")
    reports_dir = Path(__file__).resolve().parents[1] / "reports" / "screener_results"
    
    # Find predictions_YYYYMMDD.csv pattern (standard output format)
    pred_files = sorted([
        f for f in reports_dir.glob("predictions_*.csv")
        if f.stem.replace('predictions_', '').isdigit()  # Only YYYYMMDD format
    ])
    
    if not pred_files:
        print("❌ No predictions CSV found")
        print("\nTo generate predictions, run:")
        print("  python src/runners/predict_screener.py --limit 10")
        return False
    
    latest_file = pred_files[-1]
    print(f"✓ Found: {latest_file.name}")
    
    # Step 2: Load and inspect CSV
    print("\n[Step 2] Loading and inspecting CSV...")
    df = pd.read_csv(latest_file)
    print(f"  Rows: {len(df)}")
    print(f"  Columns: {len(df.columns)}")
    
    # Check for metadata columns
    required_metadata = ['screener_rank', 'strategy_source', 'model_type', 
                        'model_r2_score', 'model_spearman_ic', 'model_mae']
    
    missing = [col for col in required_metadata if col not in df.columns]
    
    if missing:
        print(f"❌ Missing metadata columns: {missing}")
        print("\nThis CSV was generated before metadata was added.")
        print("Run the screener again to get updated output:")
        print("  python src/runners/predict_screener.py --limit 10")
        return False
    
    print("✓ All metadata columns present!")
    
    # Step 3: Display sample data
    print("\n[Step 3] Sample predictions with metadata:")
    print("-" * 70)
    display_cols = ['symbol', 'screener_rank', 'pred_ret', 'strategy_source', 
                   'model_type', 'model_r2_score', 'model_spearman_ic']
    sample = df[display_cols].head(3)
    print(sample.to_string(index=False))
    
    # Step 4: Simulate database metadata update
    print("\n[Step 4] Simulating database metadata update...")
    print("  (Would update positions in database if they existed)")
    
    # Prepare metadata for top 3 picks
    top_3 = df.head(3)
    positions_metadata = []
    
    for _, row in top_3.iterrows():
        metadata = {
            'symbol': row['symbol'],
            'account': 'paper',
            'strategy_source': row['strategy_source'],
            'model_type': row['model_type'],
            'predicted_return': row['pred_ret'],
            'screener_rank': row['screener_rank'],
            'entry_notes': f"R²={row['model_r2_score']:.3f}, IC={row['model_spearman_ic']:.3f}"
        }
        positions_metadata.append(metadata)
        print(f"  - {metadata['symbol']}: rank={metadata['screener_rank']}, "
              f"pred_ret={metadata['predicted_return']:.4f}")
    
    # Try to update (will only work if positions exist)
    count = bulk_set_strategy_metadata(positions_metadata)
    
    if count > 0:
        print(f"\n✓ Updated {count} position(s) in database")
    else:
        print(f"\n  ℹ No positions updated (positions don't exist in database yet)")
        print("  This is expected - positions would be created after opening trades")
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print("✓ Prediction screener generates CSV with metadata")
    print("✓ CSV includes: screener_rank, model_type, R², Spearman IC, MAE")
    print("✓ Helper functions can populate database from CSV")
    print("\nWorkflow for production:")
    print("  1. Run prediction screener → generates CSV with metadata")
    print("  2. Check Alpaca API for current positions (never use DB)")
    print("  3. Filter out symbols already held")
    print("  4. Open positions via Alpaca")
    print("  5. Sync database → adds new positions")
    print("  6. Set metadata from CSV → enriches positions with attribution")
    print("  7. Analyze performance by strategy source, model type, etc.")
    
    return True


if __name__ == "__main__":
    success = test_prediction_screener_with_metadata()
    sys.exit(0 if success else 1)
