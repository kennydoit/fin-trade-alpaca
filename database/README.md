# Portfolio Database System

## Overview

The portfolio database system tracks positions, transactions, and strategy classifications (core/growth/short_term) for both paper and live accounts. It enforces protection rules to prevent liquidation of core and growth positions.

## Architecture

### Database Location
- Single SQLite database: `reports/portfolio_db/portfolio.db`
- Automatically created on first sync
- Git-ignored (contains sensitive data)

### Tables

#### 1. **assets** - Symbol metadata (shared across accounts)
- `symbol` (PRIMARY KEY) - Stock symbol
- `asset_id` - Alpaca UUID
- `exchange` - NASDAQ, NYSE, ARCA, etc.
- `asset_class` - US_EQUITY, etc.
- `asset_marginable` - Boolean flag
- `sector`, `industry`, `company_name` - Enrichment data
- `last_updated` - Timestamp

#### 2. **positions** - Current portfolio state
- `id` (PRIMARY KEY)
- `symbol`, `account` (UNIQUE together)
- `strategy` - 'core', 'growth', 'short_term'
- `qty`, `side`, `avg_entry_price`, `current_price`
- `market_value`, `cost_basis`
- `unrealized_pl`, `unrealized_plpc`
- `first_acquired_at` - Earliest acquisition date
- `last_updated` - Timestamp

#### 3. **transactions** - Complete order history
- `id` (PRIMARY KEY)
- `symbol`, `account`
- `order_id` (UNIQUE) - Alpaca order UUID
- `side` - 'BUY' or 'SELL'
- `qty`, `notional`, `filled_price`
- `submitted_at`, `filled_at` - Timestamps from Alpaca
- `status`, `order_type`, `time_in_force`
- `strategy` - Strategy at time of transaction
- `stop_loss`, `take_profit` - Order bracket prices

## Usage

### 1. Initialize and Sync Database

```powershell
# Sync paper account (with transaction backfill)
python tools\sync_portfolio_db.py paper

# Sync live account
python tools\sync_portfolio_db.py live

# Sync both accounts
python tools\sync_portfolio_db.py both

# Skip transaction backfill (positions only)
python tools\sync_portfolio_db.py paper --no-backfill
```

**What sync does:**
1. Fetches current positions from Alpaca
2. Classifies each position by strategy from `configs/strategy.json`
3. Backfills transaction history (filled orders)
4. Updates asset metadata

### 2. Query Protection Status

```python
from database.protection import is_protected, get_protected_symbols

# Check if a symbol is protected
if is_protected("AIQ", "paper"):
    print("AIQ is protected - cannot liquidate")

# Get all protected symbols for an account
protected = get_protected_symbols("paper")
print(f"Protected symbols: {protected}")
```

### 3. Filter Symbols for Short-Term Trading

```python
from database.protection import filter_protected_symbols

# Remove protected symbols from candidates
candidates = ["AIQ", "SCHB", "DTCR", "XERS", "ZVRA"]
tradeable = filter_protected_symbols(candidates, "paper")
# Returns: ["XERS", "ZVRA"] (assuming AIQ, SCHB, DTCR are protected)
```

### 4. Query Database Directly

```python
import sqlite3
from database.schema import get_database_path

db_path = get_database_path()
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Get positions by strategy
cursor.execute("""
    SELECT symbol, qty, market_value, unrealized_pl 
    FROM positions 
    WHERE account = 'paper' AND strategy = 'core'
    ORDER BY market_value DESC
""")

for row in cursor.fetchall():
    print(f"{row[0]}: ${row[2]:.2f}, P/L: ${row[3]:.2f}")

conn.close()
```

## Protection Rules

### Protected Strategies
- **core** - Long-term holdings, protected from liquidation
- **growth** - Growth positions, protected from liquidation

### Tradeable Strategies
- **short_term** - Short-term trades, can be liquidated
- **untracked** - Not in strategy.json, can be liquidated

### Rules
1. **Liquidation workflows** must check `is_protected()` before selling
2. **Short-term picker** must filter out protected symbols before buying
3. Protection is **account-specific** (paper core ≠ live core)
4. Symbols can only belong to **one strategy** per account

## Integration Points

### Liquidation Workflows (To Be Updated)
- `src/runners/liquidate_paper_account.py`
- `src/runners/clone_live_to_paper.py` (liquidation phase)

**Required changes:**
```python
from database.protection import is_protected

# Before selling a position
if is_protected(symbol, account):
    print(f"Skipping {symbol} - protected ({strategy})")
    continue

# Proceed with liquidation
```

### Buying Workflow (To Be Updated)
- `src/runners/optimize_and_buy.py` - `pick_top_n_from_screener()`

**Required changes:**
```python
from database.protection import filter_protected_symbols

# After selecting candidates from screener
candidates = [...]  # List of symbols
safe_candidates = filter_protected_symbols(candidates, account)
```

## Data Flow

```
┌─────────────────┐
│  Alpaca API     │
│  - Positions    │
│  - Orders       │
└────────┬────────┘
         │
         │ fetch
         ▼
┌─────────────────┐      ┌──────────────────┐
│ strategy.json   │─────▶│  sync_portfolio  │
│ (classifications)│      │      _db.py      │
└─────────────────┘      └────────┬─────────┘
                                  │
                                  │ write
                                  ▼
                         ┌────────────────┐
                         │  portfolio.db  │
                         │  - assets      │
                         │  - positions   │
                         │  - transactions│
                         └────────┬───────┘
                                  │
                                  │ read
                   ┌──────────────┼──────────────┐
                   ▼              ▼              ▼
            ┌──────────┐   ┌──────────┐  ┌──────────┐
            │liquidate │   │ optimize │  │ reports  │
            │workflows │   │_and_buy  │  │          │
            └──────────┘   └──────────┘  └──────────┘
```

## Maintenance

### Sync Frequency
- **Before trading**: Run sync to ensure protection data is current
- **After strategy changes**: Run sync to update classifications
- **Weekly**: Full sync with backfill for transaction history

### Backup
```powershell
# Backup database
Copy-Item reports\portfolio_db\portfolio.db reports\portfolio_db\portfolio_backup_$(Get-Date -Format 'yyyyMMdd').db
```

### Reset Database
```powershell
# Delete and reinitialize
Remove-Item reports\portfolio_db\portfolio.db
python tools\sync_portfolio_db.py both
```

## Testing

```powershell
# Test database initialization
python tools\test_database_init.py

# Test sync with paper account
python tools\sync_portfolio_db.py paper

# Verify data
python -c "from database.schema import get_database_path; import sqlite3; conn=sqlite3.connect(get_database_path()); print(conn.execute('SELECT COUNT(*) FROM positions').fetchone()[0], 'positions')"
```

## Troubleshooting

### Database locked
- Close any SQLite browser connections
- Check for `.db-journal` files

### Missing transactions
- Alpaca returns max 500 orders by default
- For older history, may need to query with date filters

### Strategy classification issues
- Verify symbol spelling matches exactly in strategy.json
- Check for duplicate symbols across buckets
- Resync after config changes

## Future Enhancements

1. **Portfolio reporting tool** - Generate comprehensive reports with charts
2. **Performance tracking** - Calculate realized P/L, IRR, Sharpe ratio
3. **Alerts** - Notify when positions drift from target weights
4. **Web dashboard** - View portfolio status in browser
5. **Multi-portfolio support** - Track multiple strategy configurations
