#!/usr/bin/env pwsh
# Daily automated trading script
# This runs the prediction screener and optimize & buy

$ErrorActionPreference = "Stop"
$repoPath = "C:\Users\Kenrm\repositories\fin-trade-alpaca"

Set-Location $repoPath

# Activate virtual environment
& "$repoPath\.venv\Scripts\Activate.ps1"

# Ensure in-repo src package imports work for direct script execution.
$env:PYTHONPATH = "$repoPath\src"

# 1. Generate fresh predictions (if you want daily predictions)
Write-Host "`n========== Running Prediction Screener =========="
python src/runners/predict_screener.py --config configs/prediction_screener.json --strategy-config configs/strategy.json --limit 150 --lookback 180

# 2. Run optimize and buy to place orders
Write-Host "`n========== Running Optimize & Buy =========="
python src/runners/optimize_and_buy.py --mode paper --config configs/strategy.json

Write-Host "`n========== Trading Run Complete =========="
