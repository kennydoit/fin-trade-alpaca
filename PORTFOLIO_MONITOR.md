# Portfolio Monitor - Automated Sell Logic

## Overview

The portfolio monitor Lambda function (`trading-portfolio-monitor-dev`) checks all open positions and executes sells based on:

1. **Stop-Loss**: Sells when current price hits stop-loss level
2. **Take-Profit**: Sells when current price hits take-profit level  
3. **ML Predictions**: Sells when model predicts drop (optional)
4. **Hold**: Does nothing if none of the above conditions are met

## Configuration

### Lambda Environment Variables
- `TRADING_ENABLED=true` - Must be true to execute sells (false = dry-run mode)
- `S3_BUCKET` - Where portfolio database is stored
- `ALPACA_PAPER_SECRET_NAME` - Paper trading API credentials
- `ALPACA_LIVE_SECRET_NAME` - Live trading API credentials
- `SNS_ALERT_TOPIC` - SNS topic for critical alerts
- `SNS_STATUS_TOPIC` - SNS topic for status updates

### Event Payload
```json
{
  "mode": "paper",                    // "paper" or "live"
  "check_ml_predictions": false,      // Enable ML-based sell signals
  "ml_sell_threshold": -0.05          // Sell if predicted return < -5%
}
```

## How It Works

### 1. Position Evaluation Loop

For each position:
1. Get current price from Alpaca
2. Get stop-loss/take-profit from portfolio database (set during buy)
3. Check conditions in priority order:
   - **Stop-Loss Hit?** → SELL (protect capital)
   - **Take-Profit Hit?** → SELL (lock in gains)
   - **ML Predicts Drop?** → SELL (if enabled and predicted return < threshold)
   - **None of above?** → HOLD

### 2. Sell Execution

When sell condition is met:
- Submit market sell order to Alpaca
- Record order details
- Send SNS notification
- Update portfolio database

### 3. Notifications

**SNS Topics:**
- Sells executed → Alert topic (`trading-alerts-dev`)
- No sells → Status topic (`trading-status-dev`)

**Email Format:**
```
Portfolio Monitor - Sell Orders Executed

Mode: PAPER
Trading Enabled: true
Timestamp: 2026-07-28 09:30:00
Total Positions: 5
Sells: 2
Holds: 3

📤 SELL ORDERS:
✓ VIAV: SELL 5.27 @ $40.15 (TAKE_PROFIT ($38.01)) - P/L: $11.77 (6.25%)
✓ VSH: SELL 5.45 @ $31.22 (STOP_LOSS ($36.67)) - P/L: -$29.70 (-15.00%)
```

## Testing

### Dry-Run Mode (TRADING_ENABLED=false)
```bash
aws lambda invoke --region us-east-1 \
  --function-name trading-portfolio-monitor-dev \
  --payload '{"mode":"paper","check_ml_predictions":false}' \
  --cli-binary-format raw-in-base64-out response.json
```

Shows what would be sold without executing:
```json
{
  "status": "SKIPPED_TRADING_DISABLED",
  "symbol": "VIAV",
  "reason": "TAKE_PROFIT ($38.01)"
}
```

### Live Mode (TRADING_ENABLED=true)
```bash
aws lambda invoke --region us-east-1 \
  --function-name trading-portfolio-monitor-dev \
  --payload '{"mode":"paper","check_ml_predictions":true,"ml_sell_threshold":-0.03}' \
  --cli-binary-format raw-in-base64-out response.json
```

Actually executes sells and sends notifications.

### With ML Predictions
```bash
aws lambda invoke --region us-east-1 \
  --function-name trading-portfolio-monitor-dev \
  --payload '{"mode":"paper","check_ml_predictions":true,"ml_sell_threshold":-0.05}' \
  --cli-binary-format raw-in-base64-out response.json
```

Enables ML-based sell signals:
- Downloads latest market data for each position
- Runs ML model to predict future return
- Sells if predicted return < -5% (or custom threshold)

## Current Portfolio (Paper Trading)

**Open Orders (Pending Market Open):**
1. VIAV: 5.27 shares @ $37.92 (stop=$37.86, take=$38.01)
2. VSH: 5.45 shares @ $36.73 (stop=$36.67, take=$38.82)
3. WOLF: 7.30 shares @ $27.41 (stop=$27.37, take=$27.48)
4. VISN: 14.20 shares @ $14.08 (stop=$14.06, take=$14.12)
5. VRNS: 3.73 shares @ $53.58 (stop=$53.50, take=$53.71)

**Total Investment:** $1,000

**Stop-Loss:** -15% per position
**Take-Profit:** +25% per position

## Scheduling

Set up EventBridge rule to run monitor automatically:

**Market Hours (Every 15 minutes during trading):**
```
cron(0/15 9-16 ? * MON-FRI *)  # 9:00 AM - 4:00 PM ET
```

**End of Day:**
```
cron(5 16 ? * MON-FRI *)  # 4:05 PM ET
```

## Database Integration

The monitor reads from `portfolio_db/portfolio.db` in S3:

**Transactions Table:**
```sql
SELECT stop_loss, take_profit 
FROM transactions 
WHERE symbol = ? AND account = ? AND status = 'filled'
ORDER BY filled_at DESC 
LIMIT 1
```

This retrieves the protection levels set when the position was opened.

## Safety Features

1. **Trading Disabled by Default**: Must explicitly enable with `TRADING_ENABLED=true`
2. **Paper/Live Separation**: Separate credentials and database for each mode
3. **SNS Notifications**: Email alerts for every sell execution
4. **CloudWatch Logs**: Full audit trail of decisions
5. **Dry-Run Testing**: Test logic without executing orders
6. **Priority Ordering**: Stop-loss checked before take-profit before ML

## Next Steps

1. **Wait for Orders to Fill**: Market opens Monday, orders will execute
2. **Test Monitor**: Run monitor after fills to verify hold logic
3. **Adjust Thresholds**: Tune stop-loss/take-profit based on results
4. **Enable ML Signals**: Add ML predictions once base logic is validated
5. **Schedule Automation**: Set up EventBridge rules for automatic monitoring
6. **Live Trading**: Switch to `mode: "live"` after paper testing validates strategy

## Example Scenarios

### Scenario 1: Take-Profit Hit
- Position: VIAV @ $37.92 entry, $38.01 take-profit
- Price moves to $38.05
- Monitor: ✓ SELL - Take-profit triggered
- Result: +$0.13/share = +0.34% gain

### Scenario 2: Stop-Loss Hit
- Position: VSH @ $36.73 entry, $36.67 stop-loss
- Price drops to $36.60
- Monitor: ✓ SELL - Stop-loss triggered
- Result: -$0.13/share = -0.35% loss (limited downside)

### Scenario 3: ML Prediction
- Position: WOLF @ $27.41 entry
- Current price: $27.50 (+0.33%)
- ML predicts: -8% return over next 5 days
- Monitor: ✓ SELL - ML sell signal (predicted drop)
- Result: Exit before potential drop

### Scenario 4: Hold
- Position: VISN @ $14.08 entry
- Current price: $14.15 (+0.50%)
- Stop-loss: $14.06, Take-profit: $14.12
- ML predicts: +2% return
- Monitor: ⏸️ HOLD - No sell conditions met
- Result: Continue holding position
