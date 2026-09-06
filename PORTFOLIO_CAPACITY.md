# Portfolio Capacity Management

## Overview

The optimize_and_buy workflow now includes intelligent portfolio capacity management that:
1. Checks current open positions and their P/L
2. Monitors available cash
3. Respects a maximum asset limit (`max_assets`)
4. Only buys new positions if capacity is available

## Configuration

### Strategy Config (strategy.json)

```json
{
  "max_assets": 15,              // Maximum total positions allowed
  "total_investment": 1000,      // Amount to invest per run
  "buckets": {
    "short_term": {
      "weight": 1.0,
      "screener": "/tmp/predictions.csv",
      "number_of_assets": 10,    // Requested new positions
      "asset_weights": "equal",
      "stop_loss": -0.15,
      "take_profit": 0.25
    }
  }
}
```

**Key Parameters:**
- `max_assets`: Total portfolio limit (15 in example)
- `number_of_assets`: Desired new positions per run (10 in example)
- The workflow will automatically adjust if capacity is limited

## How It Works

### Workflow Logic

```
1. Connect to Alpaca API
2. Get current positions → Count = N
3. Check max_assets limit → Max = M
4. Calculate capacity → Available = M - N
5. Check requested new assets → Requested = R
6. Adjust if needed:
   - If R > Available: Buy only Available assets
   - If R <= Available: Buy R assets
7. Filter out existing symbols (no double-buys)
8. Execute buy orders
```

### Example Scenarios

#### Scenario 1: Full Capacity Available
- **Current Positions:** 0
- **max_assets:** 15
- **Requested:** 10
- **Result:** Buy 10 new positions ✅

```
📊 Current Portfolio Status:
  Positions: 0
  Market Value: $0.00
  Unrealized P/L: $0.00

🎯 Portfolio Capacity:
  Max Assets: 15
  Current Assets: 0
  Available Capacity: 15

✅ Buying 10 assets (10 requested, 15 available)
```

#### Scenario 2: Partial Capacity Available
- **Current Positions:** 5 (from previous run)
- **max_assets:** 15
- **Requested:** 10
- **Result:** Buy 10 new positions ✅

```
📊 Current Portfolio Status:
  Positions: 5
  Market Value: $1,000.00
  Unrealized P/L: $12.50

🎯 Portfolio Capacity:
  Max Assets: 15
  Current Assets: 5
  Available Capacity: 10

✅ Buying 10 assets (10 requested, 10 available)
```

#### Scenario 3: Limited Capacity
- **Current Positions:** 12
- **max_assets:** 15
- **Requested:** 10
- **Result:** Buy only 3 new positions (adjusted) ⚠️

```
📊 Current Portfolio Status:
  Positions: 12
  Market Value: $2,400.00
  Unrealized P/L: -$35.20

🎯 Portfolio Capacity:
  Max Assets: 15
  Current Assets: 12
  Available Capacity: 3

⚠️  Requested 10 new assets, but only 3 slots available.
  Adjusting to 3 assets to stay within max_assets=15 limit.

✅ Buying 3 assets (10 requested, 3 available)
```

#### Scenario 4: No Capacity
- **Current Positions:** 15
- **max_assets:** 15
- **Requested:** 10
- **Result:** Skip buy, exit with message ⛔

```
📊 Current Portfolio Status:
  Positions: 15
  Market Value: $3,000.00
  Unrealized P/L: $125.00

🎯 Portfolio Capacity:
  Max Assets: 15
  Current Assets: 15
  Available Capacity: 0

⚠️  Portfolio is at or above capacity (15/15). Cannot add new positions.
  To add new positions, either:
    1. Close some existing positions first
    2. Increase max_assets in strategy config
    3. Run portfolio monitor to close positions meeting sell criteria
```

## Integration with Portfolio Monitor

The workflows work together:

**Buy Workflow (optimize_and_buy):**
- Checks capacity before buying
- Respects max_assets limit
- Filters out existing positions

**Sell Workflow (portfolio_monitor):**
- Closes positions based on stop-loss/take-profit
- Frees up capacity for new positions
- Enables continuous rotation

### Complete Cycle

```
Day 1 (Monday 9:30 AM):
  optimize_and_buy → Buy 5 positions (5/15 capacity used)

Day 1 (Throughout day):
  portfolio_monitor (every 15 min) → Checks positions
  - No sells (all positions holding)

Day 2 (Tuesday 9:30 AM):
  optimize_and_buy → Buy 5 more positions (10/15 capacity used)

Day 2 (11:00 AM):
  portfolio_monitor → 2 positions hit take-profit → SELL
  - Capacity now: 8/15 (freed 2 slots)

Day 2 (4:00 PM):
  optimize_and_buy → Buy 2 positions (back to 10/15)

... continues rotating ...
```

## Testing

### Test Capacity Awareness

```bash
# Test with current positions
aws lambda invoke --region us-east-1 \
  --function-name trading-optimize-buy-dev \
  --payload '{"run_type":"adhoc","mode":"paper","dry_run":false,"max_notional":1000}' \
  response.json

# Check output for capacity report
cat response.json
```

### Manual Local Test

```bash
# Activate venv
.\.venv\Scripts\Activate.ps1

# Run locally
python src/runners/optimize_and_buy.py \
  --mode paper \
  --run-type adhoc \
  --config configs/strategy.json \
  --dry-run
```

## Current Configuration

**Your Setup:**
- `max_assets`: 15
- `number_of_assets`: 10 (per run)
- `total_investment`: $1,000
- Current positions: 5 (pending Monday market open)

**After Monday Open:**
1. 5 pending orders will fill → 5/15 capacity
2. Available capacity → 10 slots
3. Next run can buy 10 more → 15/15 capacity
4. Portfolio monitor will free slots when sells execute
5. Continuous rotation enabled

## Monitoring Dashboard

**Key Metrics to Track:**
1. Current position count vs max_assets
2. Available capacity
3. Total portfolio value
4. Cash available for new positions
5. Turnover rate (buys + sells per day)

**CloudWatch Logs:**
```
📊 Current Portfolio Status:
  Positions: 5
  Market Value: $1,012.50
  Unrealized P/L: $12.50

🎯 Portfolio Capacity:
  Max Assets: 15
  Current Assets: 5
  Available Capacity: 10
```

## Adjusting Parameters

### Increase Capacity
```json
{
  "max_assets": 20  // Increase from 15 to 20
}
```

### Reduce Per-Run Buys
```json
{
  "short_term": {
    "number_of_assets": 5  // Reduce from 10 to 5
  }
}
```

### Remove Limit (Unlimited)
```json
{
  // Omit max_assets parameter entirely
  "total_investment": 1000
}
```

## Best Practices

1. **Start Conservative**: Begin with lower max_assets (10-15) to test
2. **Monitor Performance**: Track which positions close first
3. **Adjust Gradually**: Increase max_assets based on results
4. **Balance Turnover**: Too many positions = high turnover costs
5. **Watch Cash**: Ensure enough cash for desired position count

## Safety Features

- ✅ Never double-buys existing positions
- ✅ Respects max_assets limit automatically
- ✅ Adjusts requested buys if capacity limited
- ✅ Clear logging of capacity decisions
- ✅ Graceful exit if no capacity available
- ✅ Dry-run mode for testing

## Next Steps

1. **Wait for Fills**: 5 pending positions will fill Monday
2. **Test Capacity**: Run optimize_and_buy to see capacity logic
3. **Watch Rotation**: Monitor positions as they close/open
4. **Tune max_assets**: Adjust based on your strategy
5. **Automate Schedule**: Set up EventBridge rules

## Example: Full Day Cycle

**Monday 9:00 AM (Market Open):**
```
Pending orders fill → 5 positions
Capacity: 5/15 (10 available)
```

**Monday 12:00 PM (Portfolio Monitor):**
```
Check: All holding, no sells
Capacity: 5/15 (unchanged)
```

**Monday 4:00 PM (End of Day):**
```
optimize_and_buy runs → Buy 10 more
Capacity: 15/15 (full)
```

**Tuesday 10:30 AM (Portfolio Monitor):**
```
3 positions hit take-profit → SELL
Capacity: 12/15 (3 available)
```

**Tuesday 4:00 PM (End of Day):**
```
optimize_and_buy runs → Buy 3 more
Capacity: 15/15 (back to full)
```

This creates a continuous rotation where the portfolio stays near capacity while constantly refreshing positions based on ML predictions and market conditions.
