param(
    [Parameter(Mandatory=$false)]
    [string]$Environment = "dev",

    [Parameter(Mandatory=$false)]
    [ValidateSet("paper", "live")]
    [string]$Mode = "paper",

    [Parameter(Mandatory=$false)]
    [ValidateSet("adhoc", "scheduled")]
    [string]$RunType = "adhoc",

    [Parameter(Mandatory=$false)]
    [bool]$DryRun = $true,

    [Parameter(Mandatory=$false)]
    [decimal]$MaxNotional,

    [Parameter(Mandatory=$false)]
    [switch]$ConfirmLive,

    [Parameter(Mandatory=$false)]
    [string]$Region = "us-east-1",

    [Parameter(Mandatory=$false)]
    [string]$OutputPath = "aws\\dist\\optimize_buy_invoke_response.json"
)

$ErrorActionPreference = "Stop"

if ($Mode -eq "live" -and -not $ConfirmLive) {
    throw "Live mode requires -ConfirmLive."
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$functionName = "trading-optimize-buy-$Environment"
$payloadPath = Join-Path $scriptRoot "dist\\optimize_buy_payload.json"

if (-not $PSBoundParameters.ContainsKey("OutputPath")) {
    $OutputPath = Join-Path $scriptRoot "dist\\optimize_buy_invoke_response.json"
} elseif (-not [System.IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path (Get-Location) $OutputPath
}

$payload = @{
    run_type = $RunType
    mode = $Mode
    dry_run = $DryRun
}

if ($PSBoundParameters.ContainsKey("MaxNotional")) {
    $payload.max_notional = [string]$MaxNotional
}

if ($ConfirmLive.IsPresent) {
    $payload.confirm_live = $true
}

$payloadJson = $payload | ConvertTo-Json -Depth 4
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($payloadPath, $payloadJson, $utf8NoBom)

Write-Host "Invoking $functionName in $Region" -ForegroundColor Cyan
Write-Host "Payload:" -ForegroundColor Cyan
Get-Content $payloadPath
Write-Host ""

aws lambda invoke `
    --function-name $functionName `
    --region $Region `
    --cli-binary-format raw-in-base64-out `
    --payload fileb://$payloadPath `
    $OutputPath

Write-Host ""
Write-Host "Response written to $OutputPath" -ForegroundColor Green
Get-Content $OutputPath