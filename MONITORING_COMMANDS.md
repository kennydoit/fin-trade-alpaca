# Tomorrow's Prediction Screener Run - Monitoring Commands

## Run Details
- **Schedule:** 8:00 AM ET / 13:00 UTC (Monday-Friday only)
- **Expected Run Date:** August 14, 2026 (Thursday)
- **Function:** trading-prediction-screener-dev
- **EventBridge Rule:** PredictionScreenerSchedule

## What Was Fixed
✅ **Infinity handling** - Fixed 3-layer infinity value sanitization
✅ **Output path issue** - Fixed chart save path for Lambda environment  

## Quick Verification Commands

### 1. Check if the function ran (after 8:00 AM ET tomorrow)
```powershell
# Check CloudWatch logs for today's execution
aws logs tail /aws/lambda/trading-prediction-screener-dev --region us-east-1 --since 1h --format short
```

### 2. Verify success in logs
```powershell
# Look for success indicators
aws logs tail /aws/lambda/trading-prediction-screener-dev --region us-east-1 --since 1h --format short | Select-String -Pattern "completed successfully|Uploaded results|Wrote predictions"
```

### 3. Check for errors
```powershell
# Look for any errors or failures
aws logs tail /aws/lambda/trading-prediction-screener-dev --region us-east-1 --since 1h --format short | Select-String -Pattern "error|failed|exception" -CaseSensitive:$false
```

### 4. Verify S3 upload
```powershell
# List today's prediction files in S3
$today = (Get-Date).ToString("yyyyMMdd")
aws s3 ls s3://fin-trade-alpaca-dev-441691361831/screener_results/ --region us-east-1 | Select-String $today
```

### 5. Download and inspect predictions
```powershell
# Download the latest predictions file
$today = (Get-Date).ToString("yyyyMMdd")
aws s3 cp s3://fin-trade-alpaca-dev-441691361831/screener_results/ . --recursive --exclude "*" --include "predictions_${today}*.csv" --region us-east-1

# View the predictions
Get-Content predictions_${today}*.csv | Select-Object -First 10
```

## Success Criteria

The run is **successful** if you see:
- ✅ "Prediction screener completed successfully" in logs
- ✅ "Uploaded results to s3://..." in logs  
- ✅ No "infinity" or "non-existent directory" errors
- ✅ New predictions CSV file in S3 with today's date
- ✅ Prediction count between 15-25 symbols (typical range)

## What to Watch For

🔍 **Good signs:**
- "Training random_forest with 43 features"
- "Spearman IC=" (any value, could be positive or negative)
- "matplotlib not installed - skipped actual-vs-predicted chart" (expected)
- "Wrote predictions to /tmp/screener_results/predictions.csv"

⚠️ **Red flags:**
- "Input X contains infinity" - means infinity fix didn't work
- "Cannot save file into a non-existent directory" - means path fix didn't work  
- "Task timed out after 900.00 seconds" - function timeout
- Empty prediction count or no symbols scored

## Manual Test Anytime
```powershell
# Run the function manually to test
aws lambda invoke --region us-east-1 `
  --function-name trading-prediction-screener-dev `
  --payload '{"limit":20,"lookback":60,"return_days":5}' `
  --cli-binary-format raw-in-base64-out `
  response.json

# Check the result
Get-Content response.json | ConvertFrom-Json | ConvertTo-Json -Depth 10
```

## Scheduled Run Time Tomorrow
- **UTC:** 2026-08-14 13:00:00  
- **Eastern:** 2026-08-14 08:00:00 AM ET
- **Your local time:** Check `[System.TimeZoneInfo]::ConvertTimeFromUtc((Get-Date "2026-08-14 13:00:00"), [System.TimeZoneInfo]::Local)`

## EventBridge Schedule Status
```powershell
# Verify schedule is enabled
aws scheduler get-schedule --name PredictionScreenerSchedule --group-name default --region us-east-1 | ConvertFrom-Json | Select-Object Name, State, ScheduleExpression
```

## Compare With Today's Manual Test Results
Today's manual test (Aug 13, 4:53 PM ET):
- Status: ✅ SUCCESS
- Prediction count: 19 symbols
- S3 location: `s3://fin-trade-alpaca-dev-441691361831/screener_results/predictions_20260813_165345.csv`
- No errors in logs
- Both infinity and path issues resolved

Tomorrow's scheduled run should produce similar results with ~15-25 predictions.
