# Quick Start Guide - AWS Automated Trading

This is a condensed quick-start guide. See [README.md](README.md) for complete documentation.

## Prerequisites (5 minutes)

1. **Install AWS SAM CLI**: https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html
2. **Install Docker**: https://www.docker.com/get-started
3. **Configure AWS credentials**:
   ```bash
   aws configure
   # Enter your AWS Access Key, Secret, Region (us-east-1), Format (json)
   ```

## Deploy to AWS (10 minutes)

### Linux/macOS:
```bash
cd aws
chmod +x deploy.sh
./deploy.sh dev your-email@example.com +12345678900
```

### Windows (PowerShell):
```powershell
cd aws
.\deploy.ps1 -Environment dev -NotificationEmail your-email@example.com -NotificationPhone +12345678900
```

## Configure (5 minutes)

### 1. Confirm email subscription
Check your inbox and click the SNS confirmation link.

### 2. Add Alpaca API keys

**Bash:**
```bash
aws secretsmanager put-secret-value \
    --secret-id alpaca-paper-api-key-dev \
    --secret-string '{"key":"YOUR_PAPER_KEY","secret":"YOUR_PAPER_SECRET"}'
```

**PowerShell:**
```powershell
aws secretsmanager put-secret-value `
    --secret-id alpaca-paper-api-key-dev `
    --secret-string '{\"key\":\"YOUR_PAPER_KEY\",\"secret\":\"YOUR_PAPER_SECRET\"}'
```

### 3. Upload configs to S3

**Bash:**
```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET="fin-trade-alpaca-dev-${ACCOUNT_ID}"

aws s3 cp ../configs/strategy.json s3://${BUCKET}/configs/
aws s3 cp ../configs/prediction_screener.json s3://${BUCKET}/configs/
aws s3 cp ../configs/equity_screener.json s3://${BUCKET}/configs/
```

**PowerShell:**
```powershell
$ACCOUNT_ID = (aws sts get-caller-identity --query Account --output text)
$BUCKET = "fin-trade-alpaca-dev-$ACCOUNT_ID"

aws s3 cp ..\configs\strategy.json s3://$BUCKET/configs/
aws s3 cp ..\configs\prediction_screener.json s3://$BUCKET/configs/
aws s3 cp ..\configs\equity_screener.json s3://$BUCKET/configs/
```

## Test (2 minutes)

**Run prediction screener:**
```bash
aws lambda invoke \
    --function-name trading-prediction-screener-dev \
    --payload '{"run_type":"adhoc","limit":50}' \
    response.json
```

**View logs:**
```bash
aws logs tail /aws/lambda/trading-prediction-screener-dev --follow
```

## Enable Trading

When ready (after testing in paper mode for 1-2 weeks):

1. Update CloudFormation stack parameter `TradingEnabled` to `true`
2. Or redeploy with trading enabled:
   ```bash
   # Edit deploy.sh or deploy.ps1, change TradingEnabled="true"
   ```

## Automated Schedule

Once deployed, these run automatically on weekdays:

- **8:00 AM ET**: Prediction + Equity Screeners
- **9:45 AM ET**: Optimize & Buy (place orders)
- **Hourly 9:30 AM - 4:30 PM ET**: Portfolio Monitor
- **4:15 PM ET**: Daily Review (exit logic)
- **5:00 PM ET**: Daily Summary (email report)

## Manual Invocation

**Force optimize & buy now:**
```bash
aws lambda invoke \
    --function-name trading-optimize-buy-dev \
    --payload '{"run_type":"adhoc","mode":"paper","dry_run":true}' \
    response.json
```

**Add protections to positions:**
```bash
aws lambda invoke \
    --function-name trading-add-protections-dev \
    --payload '{"mode":"paper","dry_run":false}' \
    response.json
```

**Daily review (aggressive):**
```bash
aws lambda invoke \
    --function-name trading-daily-review-dev \
    --payload '{"mode":"paper","aggressiveness":"aggressive"}' \
    response.json
```

## Monitoring

**CloudWatch Logs:**
```bash
aws logs tail /aws/lambda/trading-prediction-screener-dev --follow
```

**Check recent executions:**
```bash
aws lambda list-functions --query 'Functions[?starts_with(FunctionName, `trading-`)].FunctionName'
```

**View S3 results:**
```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
aws s3 ls s3://fin-trade-alpaca-dev-${ACCOUNT_ID}/screener_results/
```

## Emergency Stop

**Disable all trading immediately:**
```bash
aws lambda update-function-configuration \
    --function-name trading-optimize-buy-dev \
    --environment Variables="{TRADING_ENABLED=false}"
```

Or update CloudFormation parameter `TradingEnabled` to `false`.

## Costs

Estimated: **$7-10/month** (under $20 budget)
- Budget alarm configured at $20/month (you'll get an email at 80% = $16)

## Troubleshooting

**Function errors:**
```bash
aws logs tail /aws/lambda/FUNCTION_NAME --follow
```

**Missing dependencies:**
- Delete `aws/layers/dependencies/python/` directory
- Redeploy: `./deploy.sh dev your-email@example.com`

**S3 access issues:**
```bash
aws s3 ls s3://fin-trade-alpaca-dev-ACCOUNT_ID/
```

**Secrets not found:**
```bash
aws secretsmanager list-secrets
```

## Cleanup

**Delete everything (WARNING: Irreversible):**
```bash
# Empty S3 bucket first
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
aws s3 rm s3://fin-trade-alpaca-dev-${ACCOUNT_ID} --recursive

# Delete stack
aws cloudformation delete-stack --stack-name fin-trade-alpaca-dev
```

## Next Steps

1. Monitor paper trading for 1-2 weeks
2. Review daily summary emails
3. Check CloudWatch Logs for any errors
4. When confident, enable live trading mode

---

**Full Documentation**: See [README.md](README.md) for complete details, advanced configuration, and troubleshooting.
