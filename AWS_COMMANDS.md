# AWS Lambda Commands Reference

This file contains AWS CLI commands for invoking each Lambda function in the fin-trade-alpaca trading system.

## Prerequisites

```powershell
# Set region (all commands use us-east-1)
$region = "us-east-1"

# Ensure AWS credentials are configured
aws sts get-caller-identity
```

## Reliable Manual Trigger (Recommended)

Use the bundled retrying trigger script instead of one-off `aws lambda invoke` calls.

```powershell
# Default chain: equity-screener -> prediction-screener -> optimize-buy
powershell -ExecutionPolicy Bypass -File aws/trigger-jobs.ps1 `
  -Environment dev `
  -Region us-east-1 `
  -Mode paper `
  -DryRun $true
```

Run any subset of jobs with retries and optional logs:

```powershell
powershell -ExecutionPolicy Bypass -File aws/trigger-jobs.ps1 `
  -Jobs equity-screener,prediction-screener,statarb-screener,statarb-optimize-buy `
  -Environment dev `
  -Region us-east-1 `
  -Mode paper `
  -DryRun $true `
  -MaxAttempts 4 `
  -RetryDelaySeconds 15 `
  -TailLogs
```

Notes:
- Payload and response JSON files are written under `aws/dist/manual_triggers/<timestamp>/`.
- The script stops on first failure and exits non-zero for CI/manual safety.
- For real order submission, set `-DryRun $false` and confirm `TRADING_ENABLED=true` on the target function.

---

## 1. Equity Screener

**Purpose:** Runs growth/equity screening to identify candidate stocks  
**Output:** Uploads `equity_screener_{timestamp}.csv` to S3  
**Duration:** ~16 seconds (20 symbols)

```powershell
# Run equity screener
aws lambda invoke `
  --region us-east-1 `
  --function-name trading-equity-screener-dev `
  --payload '{"limit":20,"max_symbols":20}' `
  --cli-binary-format raw-in-base64-out `
  response.json

cat response.json

# View logs
aws logs tail /aws/lambda/trading-equity-screener-dev --region us-east-1 --since 10m --follow
```

**Custom Payload Options:**
```json
{
  "limit": 20,           // Max symbols to screen
  "max_symbols": 20,     // Max symbols to return
  "sectors": ["Technology", "Healthcare"],  // Optional sector filter
  "min_price": 5,        // Optional minimum price
  "max_price": 75        // Optional maximum price
}
```

---

## 2. Prediction Screener

**Purpose:** Downloads equity screener results and runs ML predictions  
**Input:** Latest `equity_screener_{timestamp}.csv` from S3  
**Output:** Uploads `predictions_{timestamp}.csv` to S3  
**Duration:** ~35 seconds (20 symbols, 60-day lookback)

```powershell
# Run prediction screener
aws lambda invoke `
  --region us-east-1 `
  --function-name trading-prediction-screener-dev `
  --payload '{"limit":20,"lookback":60,"return_days":5}' `
  --cli-binary-format raw-in-base64-out `
  response.json

cat response.json

# View logs
aws logs tail /aws/lambda/trading-prediction-screener-dev --region us-east-1 --since 10m --follow
```

**Custom Payload Options:**
```json
{
  "limit": 20,          // Max predictions to generate
  "lookback": 60,       // Historical days for ML model
  "return_days": 5      // Prediction horizon (days)
}
```

---

## 3. Optimize and Buy

**Purpose:** Downloads latest predictions, optimizes portfolio, executes trades  
**Input:** Latest `predictions_{timestamp}.csv` from S3, `strategy.json` config  
**Output:** Submits buy orders to Alpaca, updates `portfolio.db`  
**Duration:** ~2-5 seconds  
**⚠️ Trading Enabled:** `TRADING_ENABLED=true`

```powershell
# Run optimize and buy (paper trading)
aws lambda invoke `
  --region us-east-1 `
  --function-name trading-optimize-buy-dev `
  --payload '{\"run_type\":\"adhoc\",\"mode\":\"paper\",\"dry_run\":false,\"max_notional\":1000}' `
  --cli-binary-format raw-in-base64-out `
  response.json

cat response.json

# View logs
aws logs tail /aws/lambda/trading-optimize-buy-dev --region us-east-1 --since 10m --follow
```

**Payload Options:**
```json
{
  "run_type": "adhoc",     // "adhoc" or "scheduled"
  "mode": "paper",         // "paper" or "live"
  "dry_run": false,        // true = simulate only, false = execute trades
  "max_notional": 1000     // Dollar amount to invest this run
}
```

**Important Notes:**
- Respects `max_assets: 15` limit in strategy.json
- Calculates available capacity: `available = max_assets - current_positions`
- Adjusts order count to fit available capacity
- Filters out symbols already held
- Sets stop-loss (-15%) and take-profit (+25%) per position

---

## 4. Portfolio Monitor

**Purpose:** Monitors open positions, executes sells based on conditions  
**Conditions:** Stop-loss hit, take-profit hit, ML prediction drop, or hold  
**Duration:** ~2-5 seconds  
**⚠️ Trading Enabled:** `TRADING_ENABLED=true`

```powershell
# Run portfolio monitor
aws lambda invoke `
  --region us-east-1 `
  --function-name trading-portfolio-monitor-dev `
  --payload '{"check_type":"scheduled"}' `
  --cli-binary-format raw-in-base64-out `
  response.json

cat response.json

# View logs
aws logs tail /aws/lambda/trading-portfolio-monitor-dev --region us-east-1 --since 10m --follow
```

**Payload Options:**
```json
{
  "check_type": "scheduled",    // "scheduled" or "manual"
  "ml_threshold": -0.02         // Optional: ML prediction threshold for sells
}
```

**Sell Logic:**
1. **Stop-Loss:** Sell if `current_price <= entry_price * (1 + stop_loss)` (-15%)
2. **Take-Profit:** Sell if `current_price >= entry_price * (1 + take_profit)` (+25%)
3. **ML Prediction:** Sell if ML predicts drop > threshold (default -2%)
4. **Hold:** Position stays open if none of above conditions met

---

## 5. Add Protections

**Purpose:** Adds stop-loss/take-profit orders after positions fill  
**Input:** Reads `portfolio.db` from S3  
**Output:** Submits bracket orders to Alpaca  
**Duration:** ~2-5 seconds

```powershell
# Add position protections
aws lambda invoke `
  --region us-east-1 `
  --function-name trading-add-protections-dev `
  --payload '{"mode":"paper","account":"PA3KAPFWQSEJ"}' `
  --cli-binary-format raw-in-base64-out `
  response.json

cat response.json

# View logs
aws logs tail /aws/lambda/trading-add-protections-dev --region us-east-1 --since 10m --follow
```

**Payload Options:**
```json
{
  "mode": "paper",              // "paper" or "live"
  "account": "PA3KAPFWQSEJ"     // Alpaca account ID
}
```

---

## 6. Daily Portfolio Review

**Purpose:** Generates daily performance report with position details  
**Output:** Sends email via SNS, saves report to S3  
**Duration:** ~2-5 seconds

```powershell
# Run daily portfolio review
aws lambda invoke `
  --region us-east-1 `
  --function-name trading-daily-review-dev `
  --payload '{"mode":"paper","account":"PA3KAPFWQSEJ"}' `
  --cli-binary-format raw-in-base64-out `
  response.json

cat response.json

# View logs
aws logs tail /aws/lambda/trading-daily-review-dev --region us-east-1 --since 10m --follow
```

**Payload Options:**
```json
{
  "mode": "paper",              // "paper" or "live"
  "account": "PA3KAPFWQSEJ",    // Alpaca account ID
  "email": true                 // Send via SNS (default true)
}
```

---

## 7. Daily Summary

**Purpose:** Generates end-of-day account summary  
**Output:** Sends email via SNS  
**Duration:** ~2-5 seconds

```powershell
# Run daily summary
aws lambda invoke `
  --region us-east-1 `
  --function-name trading-daily-summary-dev `
  --payload '{"mode":"paper"}' `
  --cli-binary-format raw-in-base64-out `
  response.json

cat response.json

# View logs
aws logs tail /aws/lambda/trading-daily-summary-dev --region us-east-1 --since 10m --follow
```

**Payload Options:**
```json
{
  "mode": "paper",    // "paper" or "live"
  "email": true       // Send via SNS (default true)
}
```

---

## Complete Workflow (Sequential)

Run the complete trading workflow from screening to buying:

```powershell
# Step 1: Screen for candidates
Write-Host "🔍 Step 1: Running equity screener..." -ForegroundColor Cyan
aws lambda invoke --region us-east-1 --function-name trading-equity-screener-dev --payload '{"limit":20}' --cli-binary-format raw-in-base64-out response.json
cat response.json

# Wait for completion
Start-Sleep -Seconds 5

# Step 2: Generate predictions
Write-Host "`n🤖 Step 2: Running prediction screener..." -ForegroundColor Cyan
aws lambda invoke --region us-east-1 --function-name trading-prediction-screener-dev --payload '{"limit":20,"lookback":60}' --cli-binary-format raw-in-base64-out response.json
cat response.json

# Wait for completion
Start-Sleep -Seconds 5

# Step 3: Optimize and buy
Write-Host "`n💰 Step 3: Running optimize and buy..." -ForegroundColor Cyan
aws lambda invoke --region us-east-1 --function-name trading-optimize-buy-dev --payload '{\"run_type\":\"adhoc\",\"mode\":\"paper\",\"dry_run\":false,\"max_notional\":1000}' --cli-binary-format raw-in-base64-out response.json
cat response.json

Write-Host "`n✅ Workflow complete!" -ForegroundColor Green
```

---

## Monitoring Commands

### Check S3 Outputs

```powershell
# List equity screener results
aws s3 ls s3://fin-trade-alpaca-dev-441691361831/screener_results/ --recursive | Select-String "equity_screener"

# List prediction results
aws s3 ls s3://fin-trade-alpaca-dev-441691361831/screener_results/ --recursive | Select-String "predictions"

# Download latest predictions
aws s3 cp s3://fin-trade-alpaca-dev-441691361831/screener_results/ . --recursive --exclude "*" --include "predictions_*.csv"
```

### Check CloudWatch Logs

```powershell
# Tail logs for any function (live updates)
aws logs tail /aws/lambda/trading-optimize-buy-dev --region us-east-1 --follow

# Get last 30 minutes of logs
aws logs tail /aws/lambda/trading-portfolio-monitor-dev --region us-east-1 --since 30m

# Get logs for specific time range
aws logs tail /aws/lambda/trading-equity-screener-dev --region us-east-1 --since 2026-07-28T14:00:00 --until 2026-07-28T15:00:00
```

### Check Alpaca Orders

```powershell
# Create check script
@"
from alpaca.trading.client import TradingClient
import os
from dotenv import load_dotenv
load_dotenv('.env.paper')

client = TradingClient(
    os.environ['ALPACA_PAPER_API_KEY'],
    os.environ['ALPACA_PAPER_API_SECRET'],
    paper=True
)

# Get all orders (last 7 days)
orders = client.get_orders(status='all')
print(f"Total orders: {len(orders)}")
for order in orders[:10]:
    print(f"{order.symbol}: {order.side} {order.qty} @ {order.filled_avg_price or 'pending'} - {order.status}")

# Get all positions
positions = client.get_all_positions()
print(f"\nTotal positions: {len(positions)}")
for pos in positions:
    print(f"{pos.symbol}: {pos.qty} @ {pos.avg_entry_price} = ${float(pos.market_value):.2f} (P/L: ${float(pos.unrealized_pl):.2f})")
"@ | Out-File -Encoding utf8 check_orders.py

# Run check script
python check_orders.py
```

---

## Troubleshooting

### Function Returns Error

```powershell
# Check function configuration
aws lambda get-function-configuration --function-name trading-optimize-buy-dev --region us-east-1

# Check environment variables
aws lambda get-function-configuration --function-name trading-optimize-buy-dev --region us-east-1 --query 'Environment.Variables'
```

### Enable/Disable Trading

```powershell
# Disable trading (safety check - will not submit orders)
aws lambda update-function-configuration `
  --function-name trading-optimize-buy-dev `
  --region us-east-1 `
  --environment "Variables={TRADING_ENABLED=false}"

# Enable trading
aws lambda update-function-configuration `
  --function-name trading-optimize-buy-dev `
  --region us-east-1 `
  --environment "Variables={TRADING_ENABLED=true}"
```

### Update Secrets

```powershell
# Update Alpaca credentials
aws secretsmanager update-secret `
  --secret-id alpaca-paper-api-key-dev `
  --secret-string '{\"api_key\":\"YOUR_KEY\",\"api_secret\":\"YOUR_SECRET\"}' `
  --region us-east-1
```

### Optional: Enable AI Layer (Perplexity)

```powershell
# Store Perplexity key in Secrets Manager
aws secretsmanager create-secret `
  --name perplexity-api-key-dev `
  --secret-string '{"key":"YOUR_PERPLEXITY_API_KEY"}' `
  --region us-east-1
```

The optimize-and-buy Lambda now reads that secret through `PERPLEXITY_SECRET_NAME` and populates `PERPLEXITY_API_KEY` at runtime. Enable the `ai_decision` block in `configs/strategy.json` to turn the layer on.

Paper-only bounded actions are controlled in config:
- `apply_in_paper=true`
- `default_reduce_fraction`
- `max_reduce_fraction`
- `max_veto_fraction`
- `min_remaining_orders`

The job remains fail-safe:
- AI API timeout/error falls back to normal execution
- In live mode, AI remains advisory-only unless policy is changed

---

## Scheduling (Future Enhancement)

To automate these jobs, add EventBridge rules in `aws/template.yaml`:

```yaml
# Example: Run equity screener daily at 8 AM ET
EquityScreenerSchedule:
  Type: AWS::Events::Rule
  Properties:
    Description: Daily equity screening
    ScheduleExpression: cron(0 13 ? * MON-FRI *)  # 8 AM ET = 1 PM UTC
    State: ENABLED
    Targets:
      - Arn: !GetAtt EquityScreenerFunction.Arn
        Id: EquityScreenerTarget
```

**Suggested Schedule:**
- **Equity Screener:** Daily at 9:00 AM ET
- **Prediction Screener:** Daily at 9:30 AM ET
- **Pre-Buy Liquidation Review:** Fixed 14:35 UTC weekday rule (currently 10:35 AM during US daylight time)
- **Optimize & Buy:** Fixed 14:45 UTC weekday rule (currently 10:45 AM during US daylight time)
- **Portfolio Monitor:** Every 15 minutes during market hours (9:30 AM - 4:00 PM ET)
- **Daily Review:** Daily at 4:15 PM ET (after market close)
- **Daily Summary:** Daily at 5:00 PM ET (end of day)

---

## Quick Reference

| Function | Purpose | Trading? | Duration |
|----------|---------|----------|----------|
| equity-screener | Screen candidates | No | ~16s |
| prediction-screener | Generate ML predictions | No | ~35s |
| optimize-buy | Execute buy orders | **Yes** | ~2-5s |
| portfolio-monitor | Execute sell orders | **Yes** | ~2-5s |
| add-protections | Add stop-loss/take-profit | **Yes** | ~2-5s |
| daily-review | Performance report | No | ~2-5s |
| daily-summary | Account summary | No | ~2-5s |

---

## Notes

- All functions use **python3.12** runtime with 512MB memory, 900s timeout
- Stack: `fin-trade-alpaca-dev` in `us-east-1`
- S3 Bucket: `fin-trade-alpaca-dev-441691361831`
- SNS Topics: `trading-alerts-dev`, `trading-status-dev`
- Secrets: `alpaca-paper-api-key-dev`
- Account: `PA3KAPFWQSEJ` (Paper Trading)

**Safety Features:**
- `TRADING_ENABLED` environment variable guards actual order submission
- `dry_run: true` simulates trades without submitting
- `max_assets: 15` prevents over-allocation
- Capacity awareness prevents duplicate positions
- Stop-loss and take-profit limits downside risk
