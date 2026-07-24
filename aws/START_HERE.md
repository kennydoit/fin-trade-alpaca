# 🚀 One-Click Deployment - START HERE

This guide will get your automated trading system running on AWS in **~20 minutes**.

## What You Need (One-Time Setup)

### 1. Install AWS SAM CLI
**Required for deploying to AWS**

Download and install: https://github.com/aws/aws-sam-cli/releases/latest/download/AWS_SAM_CLI_64_PY3.msi

After installing:
1. Close and reopen PowerShell
2. Verify: `sam --version`

### 2. Python (Already Installed ✓)
**You already have Python** - no additional installation needed!

### 3. Get Your Alpaca Paper Trading API Keys
**You'll need these during deployment**

1. Go to: https://app.alpaca.markets/paper/dashboard/overview
2. Click "Generate API Key" (or view existing keys)
3. Copy both:
   - API Key ID
   - Secret Key

⚠️ **Important**: Don't close the Alpaca page until you've pasted the keys into the deployment script

---

## ⚠️ Docker Not Required!

**Good news**: You don't need Docker! This deployment uses your local Python environment instead. This means:
- ✅ No virtualization required
- ✅ Faster builds
- ✅ Works on any Windows machine

---

## Deploy Your Trading System

Once you have SAM CLI installed and your API keys ready:

### Open PowerShell in this directory:
```powershell
cd C:\Users\Kenrm\repositories\fin-trade-alpaca\aws
```

### Run the Docker-free deployment script:
```powershell
.\no-docker-deploy.ps1
```

That's it! The script will:
1. ✓ Check that everything is installed
2. ✓ Ask for your Alpaca API keys (paste them in)
3. ✓ Build your Lambda functions (5-10 min)
4. ✓ Deploy to AWS (5-10 min)
5. ✓ Configure everything automatically
6. ✓ Run a test to make sure it works

**Total time: ~20 minutes** (mostly waiting for AWS to create resources)

---

## What Happens After Deployment?

### Automated Schedule (Weekdays Only)
Your system will automatically run:

| Time (ET) | What Happens |
|-----------|--------------|
| **8:00 AM** | Generate ML predictions + screen for candidates |
| **9:45 AM** | Optimize portfolio & submit buy orders |
| **9:30 AM - 4:30 PM** | Check positions every hour for stop-loss |
| **4:15 PM** | Review positions & submit exit orders if needed |
| **5:00 PM** | Email you a daily P&L summary |

### You'll Get Emails For:
- ✉️ Daily portfolio summaries (5 PM every weekday)
- ✉️ Order confirmations (when trades execute)
- ✉️ Alerts (if positions move >5% or errors occur)
- 📱 SMS alerts for critical issues only (~10/month)

### Monitor Your System:
- **Email**: Daily summaries tell you everything
- **AWS Console**: https://console.aws.amazon.com/cloudwatch/
- **S3 Bucket**: All predictions and reports saved here

---

## Emergency Controls

### Pause All Trading
```powershell
aws lambda update-function-configuration `
    --function-name trading-optimize-buy-dev `
    --environment Variables='{TRADING_ENABLED=false}' `
    --region us-east-1
```

### View What's Happening
```powershell
# See latest logs
aws logs tail /aws/lambda/trading-prediction-screener-dev --follow --region us-east-1

# Check S3 for results
aws s3 ls s3://fin-trade-alpaca-dev-441691361831/screener_results/ --region us-east-1
```

### Delete Everything
```powershell
# Empty S3 bucket first
aws s3 rm s3://fin-trade-alpaca-dev-441691361831 --recursive --region us-east-1

# Delete CloudFormation stack
aws cloudformation delete-stack --stack-name fin-trade-alpaca-dev --region us-east-1
```

---

## Cost

**Estimated: $7-10/month**
- Budget alarm configured at $20/month
- You'll get an email if costs exceed $16

---

## Troubleshooting

### "SAM not found"
- Restart PowerShell after installing SAM CLI
- Verify: `sam --version`

### "Build failed"
- Make sure Python packages are installed: `pip install -r layers/dependencies/requirements.txt`
- Check Python version: `python --version` (need 3.11+)
- Try running the build again

### "Deployment failed"
- Check AWS credentials: `aws sts get-caller-identity`
- Make sure you have permissions to create resources
- Check if region is correct

### Still stuck?
See full documentation: [README.md](README.md)

---

## Next Steps After Successful Deployment

1. **Confirm email subscription** (check your inbox)
2. **Let it run for 1-2 weeks** in paper mode
3. **Review daily emails** to understand the system
4. **Check AWS CloudWatch** to see logs
5. **When confident**, enable live trading (see README.md)

---

## Ready? Let's Go! 🎯

```powershell
cd C:\Users\Kenrm\repositories\fin-trade-alpaca\aws
.\no-docker-deploy.ps1
```

The script will guide you through everything step-by-step (no Docker needed)!
