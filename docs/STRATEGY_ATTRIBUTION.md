# Strategy Attribution Tracking

## Overview

Track **why** positions were opened and measure performance by strategy source. This helps answer:
- "Do predictive model picks outperform screener picks?"
- "Which model type (XGBoost, Random Forest) produces better returns?"
- "What predicted return threshold leads to actual gains?"

## Database Schema

The `positions` table now includes:
- `strategy_source` - Where the pick came from: `predictive_model`, `screener`, `manual`, `rebalance`
- `model_type` - Model used: `xgboost`, `random_forest`, etc.
- `predicted_return` - Expected return (e.g., 0.08 for 8%)
- `screener_rank` - Position in screener results (1, 2, 3...)
- `entry_notes` - Any additional context

## Setup

### 1. Migrate Existing Database

If you already have a portfolio database, run the migration:

```bash
python database/migrate_strategy_columns.py
```

This is safe to run multiple times - it skips columns that already exist.

### 2. For New Databases

New databases created via `sync_portfolio_db.py` automatically include these columns.

## Usage

### From Trading Scripts

After opening a position, record the strategy metadata:

```python
from database.strategy_metadata import set_position_strategy_metadata

# After successful order fill
set_position_strategy_metadata(
    symbol='AAPL',
    account='paper',  # or 'live'
    strategy_source='predictive_model',
    model_type='xgboost',
    predicted_return=0.085,  # 8.5% expected
    screener_rank=1,  # Top pick
    entry_notes='High momentum + earnings beat'
)
```

### Bulk Update (Efficient for Multiple Positions)

```python
from database.strategy_metadata import bulk_set_strategy_metadata

positions_metadata = [
    {
        'symbol': 'AAPL',
        'account': 'paper',
        'strategy_source': 'predictive_model',
        'model_type': 'xgboost',
        'predicted_return': 0.085,
        'screener_rank': 1
    },
    {
        'symbol': 'MSFT',
        'account': 'paper',
        'strategy_source': 'predictive_model',
        'model_type': 'xgboost',
        'predicted_return': 0.073,
        'screener_rank': 2
    }
]

count = bulk_set_strategy_metadata(positions_metadata)
print(f"Updated {count} positions")
```

### Integration Example: Prediction Screener

Add to your prediction screener after positions are opened:

```python
# In tools/predict_screener_cli.py or similar

# After getting top predictions and executing orders
top_picks = predictions.head(3)

for idx, row in top_picks.iterrows():
    symbol = row['symbol']
    
    # Submit order to Alpaca
    order = client.submit_order(...)
    
    # Wait for fill, then record metadata
    if order.status == 'filled':
        set_position_strategy_metadata(
            symbol=symbol,
            account=mode,
            strategy_source='predictive_model',
            model_type='xgboost',
            predicted_return=row['predicted_return'],
            screener_rank=idx + 1,
            entry_notes=f"R²={model.r_squared_:.3f}, Config: {config_file}"
        )
```

## Analysis Queries

### Compare Strategy Sources

```sql
SELECT 
    strategy_source,
    COUNT(*) as positions,
    AVG(unrealized_plpc) as avg_return_pct
FROM positions
WHERE strategy_source IS NOT NULL
GROUP BY strategy_source
ORDER BY avg_return_pct DESC;
```

### Model Performance by Type

```sql
SELECT 
    model_type,
    COUNT(*) as trades,
    AVG(predicted_return) as avg_predicted,
    AVG(unrealized_plpc) as avg_actual,
    AVG(unrealized_plpc - predicted_return) as prediction_error
FROM positions
WHERE model_type IS NOT NULL
GROUP BY model_type
ORDER BY avg_actual DESC;
```

### Top Performers vs Prediction Accuracy

```sql
SELECT 
    screener_rank,
    COUNT(*) as picks,
    AVG(predicted_return) as avg_predicted,
    AVG(unrealized_plpc) as avg_actual,
    AVG(ABS(unrealized_plpc - predicted_return)) as avg_abs_error
FROM positions
WHERE screener_rank IS NOT NULL
GROUP BY screener_rank
ORDER BY screener_rank;
```

### Best Entry Notes Patterns

```sql
SELECT 
    entry_notes,
    COUNT(*) as occurrences,
    AVG(unrealized_plpc) as avg_return
FROM positions
WHERE entry_notes IS NOT NULL
GROUP BY entry_notes
ORDER BY avg_return DESC
LIMIT 10;
```

## Workflow Recommendations

1. **Screener runs** → Generates candidates with predictions
2. **Check Alpaca** → Get current positions (never rely on database)
3. **Filter candidates** → Remove symbols already held
4. **Execute trades** → Submit orders to Alpaca
5. **Record metadata** → Set strategy attribution in database
6. **Regular sync** → Update prices and P&L with `sync_portfolio_db.py`
7. **Analyze** → Run queries to see what's working

## Notes

- Metadata fields are **optional** - NULL is fine
- Existing positions without metadata will continue to work
- Regular `sync_portfolio_db.py` doesn't overwrite metadata
- Set metadata **after** order fills, not before
- For live trading, always check positions via Alpaca API first
