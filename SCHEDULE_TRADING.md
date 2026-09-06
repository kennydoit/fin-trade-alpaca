# How to Schedule Automated Daily Trading

## Option 1: Windows Task Scheduler (Recommended for Daily)

### Quick Setup:
1. Open PowerShell as Administrator
2. Run this command:

```powershell
$action = New-ScheduledTaskAction -Execute "pwsh.exe" -Argument "-File C:\Users\Kenrm\repositories\fin-trade-alpaca\run_daily_trading.ps1"
$trigger = New-ScheduledTaskTrigger -Daily -At "9:50AM"
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName "Alpaca Daily Trading" -Action $action -Trigger $trigger -Principal $principal -Description "Run ML predictions and place orders daily at 9:50 AM ET"
```

### What This Does:
- Runs every weekday at **9:50 AM ET** (after market open at 9:30 AM)
- First generates fresh predictions
- Then runs optimize & buy to place orders
- Logs are saved (check output in Task Scheduler history)

### To Modify the Schedule:
```powershell
# Change to run at different time (e.g., 10:00 AM)
$trigger = New-ScheduledTaskTrigger -Daily -At "10:00AM"
Set-ScheduledTask -TaskName "Alpaca Daily Trading" -Trigger $trigger

# Run multiple times per day (e.g., 10 AM and 2 PM)
$trigger1 = New-ScheduledTaskTrigger -Daily -At "10:00AM"
$trigger2 = New-ScheduledTaskTrigger -Daily -At "2:00PM"
Set-ScheduledTask -TaskName "Alpaca Daily Trading" -Trigger @($trigger1, $trigger2)
```

### To Disable/Enable:
```powershell
# Disable
Disable-ScheduledTask -TaskName "Alpaca Daily Trading"

# Enable
Enable-ScheduledTask -TaskName "Alpaca Daily Trading"

# Remove completely
Unregister-ScheduledTask -TaskName "Alpaca Daily Trading" -Confirm:$false
```

### Manual Testing:
```powershell
# Test the script manually
pwsh -File C:\Users\Kenrm\repositories\fin-trade-alpaca\run_daily_trading.ps1
```

---

## Option 2: AWS Lambda (Recommended for Production)

Deploy to AWS for fully managed, cloud-based automation:

```powershell
cd C:\Users\Kenrm\repositories\fin-trade-alpaca\aws
.\no-docker-deploy.ps1
```

**Benefits:**
- Runs even if your computer is off
- Multiple scheduled times per day (pre-configured)
- Email notifications
- Error monitoring
- Costs ~$7-10/month

**Schedule (already configured in template.yaml):**
- 9:00 AM - Equity screener
- 9:30 AM - Prediction screener  
- 9:45 AM - Optimize & buy
- Hourly - Portfolio monitoring
- 4:15 PM - Daily review (exits)
- 5:00 PM - Daily summary email

---

## Option 3: Multiple Times Per Day (Intraday Trading)

To trade multiple times per day, you have two approaches:

### A. Add Multiple Scheduled Tasks (Windows)
```powershell
# Morning entry (9:50 AM)
$action = New-ScheduledTaskAction -Execute "pwsh.exe" -Argument "-File C:\Users\Kenrm\repositories\fin-trade-alpaca\run_daily_trading.ps1"
$trigger1 = New-ScheduledTaskTrigger -Daily -At "9:50AM"
Register-ScheduledTask -TaskName "Trading Morning" -Action $action -Trigger $trigger1

# Midday entry (12:30 PM)
$trigger2 = New-ScheduledTaskTrigger -Daily -At "12:30PM"
Register-ScheduledTask -TaskName "Trading Midday" -Action $action -Trigger $trigger2

# Afternoon entry (2:30 PM)
$trigger3 = New-ScheduledTaskTrigger -Daily -At "2:30PM"
Register-ScheduledTask -TaskName "Trading Afternoon" -Action $action -Trigger $trigger3
```

### B. Modify Strategy Config for Intraday
Currently supported frequencies in `configs/strategy.json`:
- `"trading_frequency": "daily"` - Every market day
- `"trading_frequency": "weekly"` - Every Monday
- `"trading_frequency": "monthly"` - 15th and last day

You can run the script multiple times per day - the frequency setting only affects the date gate check when running with `--run-type scheduled`.

---

## Monitoring Your Automated Trading

### Check Recent Orders:
```powershell
python tools/account_summary.py --mode paper
```

### View Task Scheduler Logs:
1. Open Task Scheduler
2. Find "Alpaca Daily Trading"
3. Click "History" tab
4. Review execution logs

### Test Without Placing Orders:
```powershell
# Dry run mode
python src/runners/optimize_and_buy.py --mode paper --config configs/strategy.json --dry-run
```

---

## Recommended Setup

For tuning the system with fake money:

**Daily execution at 9:50 AM ET:**
```powershell
$action = New-ScheduledTaskAction -Execute "pwsh.exe" -Argument "-File C:\Users\Kenrm\repositories\fin-trade-alpaca\run_daily_trading.ps1"
$trigger = New-ScheduledTaskTrigger -Daily -At "9:50AM"
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName "Alpaca Daily Trading" -Action $action -Trigger $trigger -Principal $principal
```

This ensures:
- ✅ Fresh predictions every day
- ✅ Portfolio stays fully invested (15 assets)
- ✅ Runs after market open (9:30 AM)
- ✅ Computer just needs to be on at 9:50 AM

---

## Troubleshooting

**Task doesn't run:**
- Ensure computer is on and awake at scheduled time
- Check Task Scheduler History for errors
- Verify Python virtual environment path is correct

**No orders placed:**
- Check if market is open (only runs on trading days)
- Verify predictions file exists (run prediction screener first)
- Check portfolio capacity (max_assets = 15)

**Want to run NOW:**
```powershell
pwsh -File C:\Users\Kenrm\repositories\fin-trade-alpaca\run_daily_trading.ps1
```
