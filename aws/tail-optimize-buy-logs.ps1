param(
    [Parameter(Mandatory=$false)]
    [string]$Environment = "dev",

    [Parameter(Mandatory=$false)]
    [int]$SinceMinutes = 15,

    [Parameter(Mandatory=$false)]
    [string]$Region = "us-east-1",

    [Parameter(Mandatory=$false)]
    [switch]$Follow
)

$ErrorActionPreference = "Stop"
$env:AWS_PAGER = ""

$logGroupName = "/aws/lambda/trading-optimize-buy-$Environment"

Write-Host "Reading logs from $logGroupName in $Region" -ForegroundColor Cyan

if ($Follow) {
    aws logs tail $logGroupName --region $Region --since "$($SinceMinutes)m" --follow --format short --no-cli-pager
    exit $LASTEXITCODE
}

$startTime = [DateTimeOffset]::UtcNow.AddMinutes(-$SinceMinutes).ToUnixTimeMilliseconds()
aws logs filter-log-events `
    --log-group-name $logGroupName `
    --region $Region `
    --start-time $startTime `
    --query "events[].message" `
    --output text `
    --no-cli-pager