# Daily Portfolio Review - Usage Guide

## Overview

The Daily Portfolio Review tool evaluates your current positions and recommends exits based on configurable rules combining:
1. **Prediction-based triggers**: Position dropped out of top N predictions
2. **Price-based triggers**: Hit stop loss or take profit thresholds

## Access

From the console menu, select **Option 9: Daily Portfolio Review**

```bash
python console/console.py
# Select option 9
```

Or run directly:

```bash
python tools/daily_portfolio_review.py --mode paper --aggressiveness moderate
```

## Aggressiveness Levels

### Conservative
- **Exit if**: Rank > 20 OR P&L < -5%
- **Use when**: You want to hold positions longer, tolerate more drawdown
- **Best for**: Core/growth strategies with longer time horizons

### Moderate (Recommended)
- **Exit if**: Rank > 10 OR P&L < -3%
- **Use when**: Balanced approach between letting winners run and cutting losers
- **Best for**: Short-term prediction strategies

### Aggressive
- **Exit if**: Rank > 5 OR P&L < -2%
- **Use when**: You want tight discipline, quick turnover
- **Best for**: High-frequency trading, very short holding periods

### Custom
- **Exit if**: Your own thresholds
- **Use when**: You have specific risk parameters
- **Parameters**:
  - `--min-rank`: Min prediction rank to hold (e.g., 15)
  - `--stop-pct`: Stop loss percentage (e.g., -4.0)
  - `--take-pct`: Take profit percentage (e.g., 8.0)

## Workflow Example

### Daily Morning Routine

1. **Run prediction screener** (Option 4)
   ```
   Select option 4 → Generate fresh predictions
   ```

2. **Review portfolio** (Option 9)
   ```
   Select option 9
   → Paper account
   → Moderate aggressiveness
   → Dry run first to preview
   ```

3. **Execute exits** if recommendations look good
   ```
   Run again without dry-run to submit sell orders
   ```

4. **Place new buy orders** (Options 5/6)
   ```
   Select option 5 or 6 to buy top predictions
   ```

### Example Output

```
================================================================================
DAILY PORTFOLIO REVIEW (PAPER MODE)
================================================================================
Aggressiveness: moderate
  Min prediction rank: 10
  Stop loss: -3.0%
  Take profit: 7.0%

Loading predictions from: predictions_20260707.csv
  Loaded 100 predictions

Fetching current positions...
Found 8 position(s)

AAPL:
  Qty: 2.500000 @ $150.00 → $155.00
  P&L: +12.50 (+3.33%)
  Prediction rank: #3
  → HOLD

PDFS:
  Qty: 10.000000 @ $45.00 → $43.00
  P&L: -20.00 (-4.44%)
  Prediction rank: #25
  → EXIT: Dropped to rank #25 (min: 10)

ICHR:
  Qty: 5.000000 @ $80.00 → $76.00
  P&L: -20.00 (-5.00%)
  Prediction rank: #5
  → EXIT: Hit stop loss: -5.00% (threshold: -3.0%)

================================================================================
REVIEW SUMMARY
================================================================================
Total positions: 8
Hold: 6
Exit: 2

EXIT PLAN (2 positions):
  SELL 10.000000 PDFS @ $43.00
       P&L: -$20.00 (-4.44%) - Dropped to rank #25 (min: 10)
  SELL 5.000000 ICHR @ $76.00
       P&L: -$20.00 (-5.00%) - Hit stop loss: -5.00% (threshold: -3.0%)

Total P&L from exits: -$40.00

Submit sell orders to paper account? (yes/no):
```

## Command Line Usage

### Basic Usage
```bash
# Preview exits with moderate aggressiveness
python tools/daily_portfolio_review.py --mode paper --aggressiveness moderate --dry-run

# Execute exits
python tools/daily_portfolio_review.py --mode paper --aggressiveness moderate
```

### Custom Thresholds
```bash
# Exit if rank > 15 OR stop -4% OR take profit +8%
python tools/daily_portfolio_review.py \
  --mode paper \
  --aggressiveness custom \
  --min-rank 15 \
  --stop-pct -4.0 \
  --take-pct 8.0
```

### Price-only Mode
```bash
# Use only price triggers (no prediction rank)
python tools/daily_portfolio_review.py \
  --mode paper \
  --aggressiveness custom \
  --stop-pct -5.0 \
  --take-pct 10.0
```

### Prediction-only Mode
```bash
# Use only prediction rank trigger (no price stops)
python tools/daily_portfolio_review.py \
  --mode paper \
  --aggressiveness custom \
  --min-rank 20
```

## Integration with Existing Workflow

### With Static Stop Orders

The daily review **complements** your existing static stop orders:
- **Static stops** (from `add_position_protections.py`): Execute immediately when price hits threshold
- **Daily review**: Evaluates positions holistically using fresh predictions + price

You can use both:
1. Set static stops as emergency exits (-5% hard stop)
2. Run daily review with tighter thresholds (-3% stop + rank check)

### Scheduling (Future)

For automated daily execution:

**Windows Task Scheduler:**
```powershell
# Daily at 9:35 AM ET (after market open)
schtasks /create /tn "Portfolio Review" /tr "python C:\path\to\tools\daily_portfolio_review.py --mode paper --aggressiveness moderate" /sc daily /st 09:35
```

**Cron (Linux/Mac):**
```bash
# Daily at 9:35 AM ET
35 9 * * 1-5 cd /path/to/repo && python tools/daily_portfolio_review.py --mode paper --aggressiveness moderate
```

## Best Practices

1. **Always dry-run first**: Preview recommendations before executing
2. **Match aggressiveness to strategy**: 
   - Core/growth positions → conservative
   - Short-term predictions → moderate/aggressive
3. **Review exits manually**: Don't blindly execute, understand why each exit is recommended
4. **Run after generating fresh predictions**: Ensure you're using latest model scores
5. **Log results**: Keep track of exit decisions for performance analysis

## Troubleshooting

**"No predictions file found"**
- Run prediction screener first (Option 4)
- Or specify path: `--predictions /path/to/predictions.csv`

**"Could not fetch current price"**
- Market might be closed
- Symbol might be delisted
- Check Alpaca API status

**No positions recommended for exit**
- Your positions are all healthy (good!)
- Try more aggressive settings if testing
- Check if predictions are stale

## Next Steps

After implementing exits, remember to:
1. Sync portfolio database (Option 1)
2. Review freed capital
3. Run prediction screener for fresh picks
4. Place new buy orders (Options 5/6)
