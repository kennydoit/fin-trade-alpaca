# AWS Automated Trading System Deployment

This directory contains the AWS infrastructure and deployment code for running your fin-trade-alpaca trading system fully automated in the cloud.

## Architecture Overview

**Serverless Lambda-based architecture:**
- **Lambda Functions**: Execute trading workflows (screeners, optimization, monitoring, reviews)
- **EventBridge (CloudWatch Events)**: Schedule automated executions
- **S3**: Store database, predictions, reports, and configurations
- **SNS**: Send email/SMS notifications for alerts and status updates
- **Secrets Manager**: Securely store Alpaca API keys
- **CloudWatch**: Logs, metrics, and alarms for monitoring

**Estimated Cost**: $7-10/month (within $20 budget)

## Prerequisites

### 1. Install AWS SAM CLI

**Windows (PowerShell):**
```powershell
# Download installer from: https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html
# Or use Chocolatey:
choco install aws-sam-cli
```

**macOS:**
```bash
brew install aws-sam-cli
```

**Linux:**
```bash
# Follow instructions at: https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html
```

### 2. Configure AWS Credentials

```bash
aws configure
# Enter your AWS Access Key ID
# Enter your AWS Secret Access Key
# Default region: us-east-1 (or your preferred region)
# Default output format: json
```

**Verify configuration:**
```bash
aws sts get-caller-identity
```

### 3. Install Docker

SAM CLI requires Docker for building Lambda functions. Install from: https://www.docker.com/get-started

## Deployment Steps

### Step 1: Initial Deployment

From the `aws/` directory:

**Linux/macOS:**
```bash
chmod +x deploy.sh
./deploy.sh dev your-email@example.com +12345678900
```

**Windows (Git Bash or WSL):**
```bash
bash deploy.sh dev your-email@example.com +12345678900
```

**Windows (PowerShell) - Manual Deployment:**
```powershell
cd aws

# Build
sam build

# Deploy
sam deploy `
    --stack-name fin-trade-alpaca-dev `
    --s3-bucket YOUR_SAM_ARTIFACTS_BUCKET `
    --region us-east-1 `
    --capabilities CAPABILITY_NAMED_IAM `
    --parameter-overrides `
        Environment=dev `
        TradingEnabled=false `
        NotificationEmail=your-email@example.com `
        NotificationPhone=+12345678900
```

**Deployment will create:**
- S3 bucket: `fin-trade-alpaca-dev-{account-id}`
- 7 Lambda functions (prediction screener, equity screener, optimize & buy, portfolio monitor, add protections, daily review, daily summary)
- 2 SNS topics (alerts, status)
- 2 Secrets (Alpaca paper/live API keys)
- 6 EventBridge rules (scheduled executions)
- CloudWatch alarms and log groups

### Step 2: Confirm SNS Email Subscription

Check your email inbox for SNS subscription confirmation from AWS. Click the confirmation link to start receiving notifications.

### Step 3: Upload Alpaca API Keys

**Update Secrets Manager with your Alpaca credentials:**

```bash
# Paper trading API keys
aws secretsmanager put-secret-value \
    --secret-id alpaca-paper-api-key-dev \
    --secret-string '{"key":"YOUR_PAPER_API_KEY","secret":"YOUR_PAPER_API_SECRET"}'

# Live trading API keys (when ready)
aws secretsmanager put-secret-value \
    --secret-id alpaca-live-api-key-dev \
    --secret-string '{"key":"YOUR_LIVE_API_KEY","secret":"YOUR_LIVE_API_SECRET"}'
```

**Windows PowerShell:**
```powershell
aws secretsmanager put-secret-value `
    --secret-id alpaca-paper-api-key-dev `
    --secret-string '{\"key\":\"YOUR_PAPER_API_KEY\",\"secret\":\"YOUR_PAPER_API_SECRET\"}'
```

### Step 4: Upload Configuration Files to S3

```bash
# Get your S3 bucket name
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET="fin-trade-alpaca-dev-${ACCOUNT_ID}"

# Upload configs
aws s3 cp ../configs/strategy.json s3://${BUCKET}/configs/
aws s3 cp ../configs/prediction_screener.json s3://${BUCKET}/configs/
aws s3 cp ../configs/equity_screener.json s3://${BUCKET}/configs/

# Optionally upload existing database
aws s3 cp ../reports/portfolio_db/portfolio.db s3://${BUCKET}/portfolio_db/portfolio.db
```

**Windows PowerShell:**
```powershell
$ACCOUNT_ID = (aws sts get-caller-identity --query Account --output text)
$BUCKET = "fin-trade-alpaca-dev-$ACCOUNT_ID"

aws s3 cp ..\configs\strategy.json s3://$BUCKET/configs/
aws s3 cp ..\configs\prediction_screener.json s3://$BUCKET/configs/
aws s3 cp ..\configs\equity_screener.json s3://$BUCKET/configs/
```

### Step 5: Test Lambda Functions

**Test prediction screener:**
```bash
aws lambda invoke \
    --function-name trading-prediction-screener-dev \
    --payload '{"run_type":"adhoc","limit":50}' \
    response.json

cat response.json
```

**Test portfolio monitor (dry run):**
```bash
aws lambda invoke \
    --function-name trading-portfolio-monitor-dev \
    --payload '{"mode":"paper"}' \
    response.json
```

**Test optimize and buy (dry run):**
```bash
aws lambda invoke \
    --function-name trading-optimize-buy-dev \
    --payload '{"run_type":"adhoc","mode":"paper","dry_run":true}' \
    response.json
```

Check CloudWatch Logs for execution details:
```bash
# View logs for a function
aws logs tail /aws/lambda/trading-prediction-screener-dev --follow
```

### Step 6: Enable Trading (When Ready)

**⚠️ IMPORTANT: This will enable real order submissions in paper mode**

Update the CloudFormation stack to enable trading:

```bash
sam deploy \
    --stack-name fin-trade-alpaca-dev \
    --parameter-overrides \
        Environment=dev \
        TradingEnabled=true \
        NotificationEmail=your-email@example.com \
        NotificationPhone=+12345678900 \
    --no-confirm-changeset
```

Alternatively, update via AWS Console:
1. Go to CloudFormation → Stacks → fin-trade-alpaca-dev
2. Click "Update"
3. Select "Use current template"
4. Change `TradingEnabled` parameter to `true`
5. Review and confirm

## Scheduled Executions

Once deployed, the following schedules are active:

| Function | Schedule | Description |
|----------|----------|-------------|
| Prediction Screener | Daily 8:00 AM ET (weekdays) | Generate ML predictions |
| Equity Screener | Daily 8:00 AM ET (weekdays) | Run growth screener |
| Optimize & Buy | Daily 9:45 AM ET (weekdays) | Execute portfolio optimization |
| Portfolio Monitor | Hourly 9:30 AM - 4:30 PM ET (weekdays) | Monitor positions |
| Daily Review | Daily 4:15 PM ET (weekdays) | Evaluate exits |
| Daily Summary | Daily 5:00 PM ET (weekdays) | Send summary report |

**To disable schedules:**
```bash
# Disable a specific EventBridge rule
aws events disable-rule --name prediction-screener-schedule-dev
```

**To change schedules:**
Edit `template.yaml` and redeploy:
- Schedules use cron expressions in UTC
- Convert ET to UTC: ET + 5 hours (EST) or + 4 hours (EDT)
- Example: 9:00 AM ET = 1:00 PM UTC (EST) or 2:00 PM UTC (EDT)

## Manual Invocations

Trigger functions manually (overrides schedule):

**Run prediction screener on-demand:**
```bash
aws lambda invoke \
    --function-name trading-prediction-screener-dev \
    --payload '{"run_type":"adhoc","limit":200,"return_days":5}' \
    response.json
```

**Force optimize and buy (paper mode):**
```bash
aws lambda invoke \
    --function-name trading-optimize-buy-dev \
    --payload '{"run_type":"adhoc","mode":"paper","max_notional":500}' \
    response.json
```

**Daily review with custom aggressiveness:**
```bash
aws lambda invoke \
    --function-name trading-daily-review-dev \
    --payload '{"mode":"paper","aggressiveness":"aggressive","dry_run":false}' \
    response.json
```

## Monitoring

### CloudWatch Dashboards

View metrics and logs in AWS Console:
1. Navigate to CloudWatch → Dashboards
2. Or directly view logs: CloudWatch → Log Groups → `/aws/lambda/trading-*`

### Useful CloudWatch Insights Queries

**View all errors in last 24 hours:**
```
fields @timestamp, @message
| filter @message like /ERROR/ or @message like /failed/
| sort @timestamp desc
| limit 100
```

**Count executions by function:**
```
fields @message
| stats count() by @log
```

### Check Lambda Function Status

```bash
# List all trading functions
aws lambda list-functions --query 'Functions[?starts_with(FunctionName, `trading-`)].{Name:FunctionName,Runtime:Runtime,Updated:LastModified}' --output table

# Get function configuration
aws lambda get-function-configuration --function-name trading-prediction-screener-dev
```

### View Recent SNS Messages

```bash
# List SNS topics
aws sns list-topics

# View topic attributes
aws sns get-topic-attributes --topic-arn arn:aws:sns:us-east-1:ACCOUNT_ID:trading-alerts-dev
```

## Cost Management

**View estimated costs:**
```bash
# Get current month costs
aws ce get-cost-and-usage \
    --time-period Start=$(date -u -d "1 day ago" +%Y-%m-01),End=$(date -u +%Y-%m-%d) \
    --granularity MONTHLY \
    --metrics BlendedCost \
    --group-by Type=SERVICE
```

**Cost breakdown (estimated monthly):**
- Lambda compute: $3 (24 exec/day × 30 days)
- S3 storage: $1 (1GB)
- SNS (SMS): $0.50 (10 alerts)
- Secrets Manager: $0.80 (2 secrets)
- CloudWatch Logs: $1 (1GB)
- Data transfer: $0.50
- **Total: ~$7-10/month**

**Budget alarm is configured at $20/month**

## Troubleshooting

### Lambda Function Errors

**Check logs:**
```bash
aws logs tail /aws/lambda/trading-prediction-screener-dev --follow
```

**Common issues:**
1. **Timeout**: Increase Lambda timeout in `template.yaml` (max 15 minutes)
2. **Out of memory**: Increase MemorySize in `template.yaml`
3. **Missing dependencies**: Rebuild Lambda layer
4. **API credentials**: Verify Secrets Manager has correct values

### S3 Access Issues

```bash
# Verify bucket exists and is accessible
aws s3 ls s3://fin-trade-alpaca-dev-ACCOUNT_ID/

# Check IAM permissions
aws iam get-role-policy --role-name trading-lambda-role-dev --policy-name TradingSystemPolicy
```

### SNS Not Sending Notifications

1. Confirm email subscription (check spam folder)
2. Verify topic ARN is correct in Lambda environment variables
3. Check CloudWatch Logs for SNS publish errors

### EventBridge Schedules Not Triggering

```bash
# List EventBridge rules
aws events list-rules --name-prefix "prediction-screener"

# Check rule status
aws events describe-rule --name prediction-screener-schedule-dev

# View recent executions
aws events list-rule-names-by-target --target-arn $(aws lambda get-function --function-name trading-prediction-screener-dev --query 'Configuration.FunctionArn' --output text)
```

## Cleanup / Teardown

**Delete entire stack (WARNING: Deletes all resources):**
```bash
aws cloudformation delete-stack --stack-name fin-trade-alpaca-dev

# Wait for deletion to complete
aws cloudformation wait stack-delete-complete --stack-name fin-trade-alpaca-dev
```

**Note:** S3 bucket must be empty before stack deletion:
```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
aws s3 rm s3://fin-trade-alpaca-dev-${ACCOUNT_ID} --recursive
```

## Updating Deployment

**To update code or configuration:**

1. Modify code or `template.yaml`
2. Redeploy:
```bash
./deploy.sh dev your-email@example.com
```

SAM will only update changed resources (typically just Lambda functions).

## Security Best Practices

1. **Never commit API keys** to Git
2. **Use IAM least-privilege**: Review Lambda execution role permissions
3. **Enable S3 versioning**: Already enabled for database recovery
4. **Rotate Secrets**: Periodically update Alpaca API keys in Secrets Manager
5. **Monitor costs**: Set up additional budget alerts if needed
6. **Review logs**: Regularly check CloudWatch Logs for anomalies

## Support

For issues specific to:
- **AWS infrastructure**: Check AWS CloudFormation console
- **Lambda execution**: Check CloudWatch Logs
- **Trading logic**: See main project README.md and tools documentation

## Next Steps

1. Monitor paper trading for 1-2 weeks
2. Review daily summaries and validate behavior
3. When confident, enable live trading mode:
   - Update Lambda environment variables: `"mode": "live"`
   - Add `"confirm_live": true` to optimize-and-buy invocations
4. Consider adding:
   - CloudWatch custom dashboard
   - Additional alarms (position P&L thresholds)
   - Step Functions for complex workflows
   - S3 lifecycle policies for old reports

---

**Questions?** Refer to the main project documentation or session memory files for additional context.
