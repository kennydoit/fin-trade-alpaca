param(
    [Parameter(Mandatory = $false)]
    [ValidateSet(
        "equity-screener",
        "prediction-screener",
        "statarb-screener",
        "optimize-buy",
        "statarb-optimize-buy",
        "portfolio-monitor",
        "add-protections",
        "daily-review",
        "daily-summary"
    )]
    [string[]]$Jobs = @("equity-screener", "prediction-screener", "optimize-buy"),

    [Parameter(Mandatory = $false)]
    [string]$Environment = "dev",

    [Parameter(Mandatory = $false)]
    [string]$Region = "us-east-1",

    [Parameter(Mandatory = $false)]
    [ValidateSet("paper", "live")]
    [string]$Mode = "paper",

    [Parameter(Mandatory = $false)]
    [bool]$DryRun = $true,

    [Parameter(Mandatory = $false)]
    [decimal]$MaxNotional = 1000,

    [Parameter(Mandatory = $false)]
    [ValidateRange(1, 10)]
    [int]$MaxAttempts = 3,

    [Parameter(Mandatory = $false)]
    [ValidateRange(1, 120)]
    [int]$RetryDelaySeconds = 10,

    [Parameter(Mandatory = $false)]
    [string]$OutputDir = "aws\dist\manual_triggers",

    [Parameter(Mandatory = $false)]
    [switch]$TailLogs
)

$ErrorActionPreference = "Stop"

function Get-FunctionName {
    param(
        [Parameter(Mandatory = $true)][string]$Job,
        [Parameter(Mandatory = $true)][string]$EnvironmentName
    )

    $map = @{
        "equity-screener" = "trading-equity-screener-$EnvironmentName"
        "prediction-screener" = "trading-prediction-screener-$EnvironmentName"
        "statarb-screener" = "trading-statarb-screener-$EnvironmentName"
        "optimize-buy" = "trading-optimize-buy-$EnvironmentName"
        "statarb-optimize-buy" = "trading-statarb-optimize-buy-$EnvironmentName"
        "portfolio-monitor" = "trading-portfolio-monitor-$EnvironmentName"
        "add-protections" = "trading-add-protections-$EnvironmentName"
        "daily-review" = "trading-daily-review-$EnvironmentName"
        "daily-summary" = "trading-daily-summary-$EnvironmentName"
    }

    if (-not $map.ContainsKey($Job)) {
        throw "Unsupported job '$Job'"
    }

    return $map[$Job]
}

function Get-JobPayload {
    param(
        [Parameter(Mandatory = $true)][string]$Job,
        [Parameter(Mandatory = $true)][string]$RunMode,
        [Parameter(Mandatory = $true)][bool]$IsDryRun,
        [Parameter(Mandatory = $true)][decimal]$Notional
    )

    switch ($Job) {
        "equity-screener" { return @{ run_type = "adhoc" } }
        "prediction-screener" { return @{ run_type = "adhoc" } }
        "statarb-screener" { return @{ run_type = "adhoc" } }
        "optimize-buy" {
            return @{
                run_type = "adhoc"
                mode = $RunMode
                dry_run = $IsDryRun
                max_notional = [string]$Notional
            }
        }
        "statarb-optimize-buy" {
            return @{
                run_type = "adhoc"
                mode = $RunMode
                dry_run = $IsDryRun
                max_notional = [string]$Notional
            }
        }
        "portfolio-monitor" {
            return @{
                check_type = "manual"
                mode = $RunMode
            }
        }
        "add-protections" { return @{ mode = $RunMode } }
        "daily-review" {
            return @{
                mode = $RunMode
                email = $true
            }
        }
        "daily-summary" {
            return @{
                mode = $RunMode
                email = $true
            }
        }
        default { throw "No payload mapping found for '$Job'" }
    }
}

function Read-JsonFileSafe {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path $Path)) {
        return $null
    }

    try {
        $raw = Get-Content -Path $Path -Raw -Encoding UTF8
        if ([string]::IsNullOrWhiteSpace($raw)) {
            return $null
        }
        return $raw | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Invoke-JobWithRetry {
    param(
        [Parameter(Mandatory = $true)][string]$Job,
        [Parameter(Mandatory = $true)][string]$FunctionName,
        [Parameter(Mandatory = $true)][hashtable]$Payload,
        [Parameter(Mandatory = $true)][string]$AwsRegion,
        [Parameter(Mandatory = $true)][string]$RunOutputDir,
        [Parameter(Mandatory = $true)][int]$Attempts,
        [Parameter(Mandatory = $true)][int]$RetryDelay,
        [Parameter(Mandatory = $true)][bool]$Tail
    )

    $jobPrefix = $Job.Replace("-", "_")
    $payloadPath = Join-Path $RunOutputDir "$jobPrefix.payload.json"
    $responsePath = Join-Path $RunOutputDir "$jobPrefix.response.json"

    $payloadJson = $Payload | ConvertTo-Json -Depth 10
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($payloadPath, $payloadJson, $utf8NoBom)

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        Write-Host "[$Job] Attempt $attempt/$Attempts invoking $FunctionName" -ForegroundColor Cyan

        $invokeOutput = aws lambda invoke `
            --region $AwsRegion `
            --function-name $FunctionName `
            --cli-binary-format raw-in-base64-out `
            --payload fileb://$payloadPath `
            $responsePath 2>&1

        $invokeSucceeded = $LASTEXITCODE -eq 0
        if (-not $invokeSucceeded) {
            Write-Host "[$Job] AWS CLI invoke failed (exit code $LASTEXITCODE):" -ForegroundColor Yellow
            $invokeOutput | ForEach-Object { Write-Host $_ }
        }

        $invokeMeta = $null
        if ($invokeSucceeded) {
            try {
                $invokeMeta = $invokeOutput | Out-String | ConvertFrom-Json
            }
            catch {
                $invokeMeta = $null
            }
        }

        $responseJson = Read-JsonFileSafe -Path $responsePath
        $functionError = $null
        if ($invokeMeta -and $invokeMeta.PSObject.Properties.Name -contains "FunctionError") {
            $functionError = $invokeMeta.FunctionError
        }

        $statusCode = $null
        if ($responseJson -and $responseJson.PSObject.Properties.Name -contains "statusCode") {
            $statusCode = [int]$responseJson.statusCode
        }

        $isSuccess = $invokeSucceeded -and (-not $functionError) -and (($null -eq $statusCode) -or ($statusCode -lt 400))

        if ($isSuccess) {
            Write-Host "[$Job] Success" -ForegroundColor Green
            if ($Tail) {
                $logGroup = "/aws/lambda/$FunctionName"
                Write-Host "[$Job] Recent logs from $logGroup" -ForegroundColor DarkCyan
                aws logs tail $logGroup --region $AwsRegion --since 5m
            }

            return [pscustomobject]@{
                Job = $Job
                FunctionName = $FunctionName
                Attempt = $attempt
                Success = $true
                StatusCode = $statusCode
                FunctionError = $functionError
                ResponsePath = $responsePath
                PayloadPath = $payloadPath
            }
        }

        Write-Host "[$Job] Invocation failed." -ForegroundColor Yellow
        if ($responseJson) {
            Write-Host "[$Job] Response: $(Get-Content -Path $responsePath -Raw)" -ForegroundColor Yellow
        }

        if ($attempt -lt $Attempts) {
            Write-Host "[$Job] Retrying in $RetryDelay seconds..." -ForegroundColor DarkYellow
            Start-Sleep -Seconds $RetryDelay
        }
    }

    return [pscustomobject]@{
        Job = $Job
        FunctionName = $FunctionName
        Attempt = $Attempts
        Success = $false
        StatusCode = $null
        FunctionError = "Invocation failed after retries"
        ResponsePath = $responsePath
        PayloadPath = $payloadPath
    }
}

if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
    throw "AWS CLI not found on PATH. Install/configure AWS CLI before running this script."
}

if (-not [System.IO.Path]::IsPathRooted($OutputDir)) {
    $OutputDir = Join-Path (Get-Location) $OutputDir
}

New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$runOutputDir = Join-Path $OutputDir $timestamp
New-Item -ItemType Directory -Path $runOutputDir -Force | Out-Null

Write-Host "Manual trigger run directory: $runOutputDir" -ForegroundColor Gray
Write-Host "Jobs to run: $($Jobs -join ', ')" -ForegroundColor Gray

$results = @()
foreach ($job in $Jobs) {
    $functionName = Get-FunctionName -Job $job -EnvironmentName $Environment
    $payload = Get-JobPayload -Job $job -RunMode $Mode -IsDryRun $DryRun -Notional $MaxNotional

    $result = Invoke-JobWithRetry `
        -Job $job `
        -FunctionName $functionName `
        -Payload $payload `
        -AwsRegion $Region `
        -RunOutputDir $runOutputDir `
        -Attempts $MaxAttempts `
        -RetryDelay $RetryDelaySeconds `
        -Tail:$TailLogs.IsPresent

    $results += $result

    if (-not $result.Success) {
        Write-Host "Stopping run because '$job' failed after retries." -ForegroundColor Red
        break
    }
}

$summaryPath = Join-Path $runOutputDir "summary.json"
$results | ConvertTo-Json -Depth 6 | Set-Content -Path $summaryPath -Encoding UTF8

Write-Host "\nRun summary:" -ForegroundColor Cyan
$results | Format-Table Job, Success, Attempt, FunctionName, ResponsePath -AutoSize
Write-Host "Summary file: $summaryPath" -ForegroundColor Gray

if ($results.Where({ -not $_.Success }).Count -gt 0) {
    exit 1
}

exit 0
