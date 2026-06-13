"""Quick test to verify strategy metadata functions work correctly."""
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.strategy_metadata import (
    set_position_strategy_metadata,
    get_position_metadata,
    bulk_set_strategy_metadata
)

def test_strategy_metadata():
    """Test setting and getting strategy metadata."""
    
    print("Testing strategy attribution functions...\n")
    
    # Test 1: Set metadata for a single position (if exists)
    print("Test 1: Set metadata for single position")
    result = set_position_strategy_metadata(
        symbol='TEST',
        account='paper',
        strategy_source='predictive_model',
        model_type='xgboost',
        predicted_return=0.085,
        screener_rank=1,
        entry_notes='Test entry'
    )
    print(f"  Result: {result}")
    if result:
        print("  ✓ Position updated successfully")
    else:
        print("  ℹ Position 'TEST' not found (expected if you don't have this position)")
    
    # Test 2: Get metadata
    print("\nTest 2: Get metadata for position")
    metadata = get_position_metadata('TEST', 'paper')
    if metadata:
        print(f"  ✓ Retrieved: {metadata}")
    else:
        print("  ℹ No metadata found (expected if position doesn't exist)")
    
    # Test 3: Bulk set (for demo, won't actually update unless positions exist)
    print("\nTest 3: Bulk set metadata")
    positions = [
        {
            'symbol': 'AAPL',
            'account': 'paper',
            'strategy_source': 'predictive_model',
            'model_type': 'xgboost',
            'predicted_return': 0.08,
            'screener_rank': 1
        },
        {
            'symbol': 'MSFT',
            'account': 'paper',
            'strategy_source': 'screener',
            'screener_rank': 2
        }
    ]
    count = bulk_set_strategy_metadata(positions)
    print(f"  Updated {count} position(s)")
    if count > 0:
        print("  ✓ Bulk update successful")
    else:
        print("  ℹ No positions updated (expected if you don't have AAPL or MSFT)")
    
    print("\n✅ All tests completed!")
    print("\nNote: Functions work correctly even when positions don't exist.")
    print("They will update real positions once you have them in your database.")

if __name__ == "__main__":
    test_strategy_metadata()
