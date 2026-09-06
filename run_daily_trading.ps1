#!/usr/bin/env pwsh
# Daily automated trading script
# This runs the prediction screener and optimize & buy

$ErrorActionPreference = "Stop"
$repoPath = "C:\Users\Kenrm\repositories\fin-trade-alpaca"

Set-Location $repoPath

# Activate virtual environment
& "$repoPath\.venv\Scripts\Activate.ps1"

# 1. Generate fresh predictions (if you want daily predictions)
Write-Host "`n========== Running Prediction Screener =========="
python tools/predict_screener_cli.py --limit 150 --lookback 180

# 2. Run optimize and buy to place orders
Write-Host "`n========== Running Optimize & Buy =========="
python src/runners/optimize_and_buy.py --mode paper --config configs/strategy.json

Write-Host "`n========== Trading Run Complete =========="
