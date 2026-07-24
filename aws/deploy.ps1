# AWS SAM Deployment Script for fin-trade-alpaca (PowerShell version)
# This script builds and deploys the automated trading system to AWS

param(
    [Parameter(Mandatory=$true)]
    [string]$Environment = "dev",
    
    [Parameter(Mandatory=$true)]
    [string]$NotificationEmail,
    
    [Parameter(Mandatory=$false)]
    [string]$NotificationPhone = "",
    
    [Parameter(Mandatory=$false)]
    [string]$Region = "us-east-1"
)

Write-Host "========================================" -ForegroundColor Blue
Write-Host "fin-trade-alpaca AWS Deployment" -ForegroundColor Blue
Write-Host "========================================" -ForegroundColor Blue
Write-Host ""

Write-Host "Configuration:" -ForegroundColor Green
Write-Host "  Environment: $Environment"
Write-Host "  Email: $NotificationEmail"
Write-Host "  Phone: $(if ($NotificationPhone) {$NotificationPhone} else {'none'})"
Write-Host "  Region: $Region"
Write-Host ""

# Check if SAM CLI is installed
try {
    $samVersion = sam --version
    Write-Host "✓ SAM CLI is installed: $samVersion" -ForegroundColor Green
} catch {
    Write-Host "ERROR: AWS SAM CLI is not installed" -ForegroundColor Red
    Write-Host "Install from: https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html"
    exit 1
}

# Check if AWS credentials are configured
try {
    $identity = aws sts get-caller-identity | ConvertFrom-Json
    Write-Host "✓ AWS credentials configured (Account: $($identity.Account))" -ForegroundColor Green
} catch {
    Write-Host "ERROR: AWS credentials not configured" -ForegroundColor Red
    Write-Host "Run: aws configure"
    exit 1
}

Write-Host ""

# Create S3 bucket for SAM artifacts if it doesn't exist
$AccountId = (aws sts get-caller-identity --query Account --output text)
$SamBucket = "fin-trade-alpaca-sam-artifacts-$AccountId"

$bucketExists = aws s3 ls "s3://$SamBucket" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Creating SAM artifacts bucket: $SamBucket" -ForegroundColor Blue
    aws s3 mb "s3://$SamBucket" --region $Region
} else {
    Write-Host "✓ SAM artifacts bucket exists: $SamBucket" -ForegroundColor Green
}

Write-Host ""

# Copy source code to function directories
Write-Host "Preparing function code..." -ForegroundColor Blue

$functions = @(
    "prediction_screener",
    "equity_screener",
    "optimize_and_buy",
    "portfolio_monitor",
    "add_protections",
    "daily_review",
    "daily_summary"
)

foreach ($func in $functions) {
    $funcDir = "functions\$func"
    Write-Host "  Copying source to $funcDir\"
    
    # Create src directory in function
    New-Item -ItemType Directory -Force -Path "$funcDir\src" | Out-Null
    
    # Copy entire src directory
    Copy-Item -Path "..\..\src\*" -Destination "$funcDir\src\" -Recurse -Force
    
    # Copy configs
    New-Item -ItemType Directory -Force -Path "$funcDir\configs" | Out-Null
    if (Test-Path "..\..\configs\*") {
        Copy-Item -Path "..\..\configs\*" -Destination "$funcDir\configs\" -Force
    }
}

Write-Host "✓ Function code prepared" -ForegroundColor Green
Write-Host ""

# Build Lambda layer for dependencies
Write-Host "Building Lambda layer..." -ForegroundColor Blue
Push-Location layers\dependencies

if (!(Test-Path "python")) {
    New-Item -ItemType Directory -Force -Path "python" | Out-Null
    pip install -r requirements.txt -t python\ --upgrade
    Write-Host "✓ Dependencies installed" -ForegroundColor Green
} else {
    Write-Host "✓ Dependencies already installed (delete layers\dependencies\python to rebuild)" -ForegroundColor Green
}

Pop-Location
Write-Host ""

# Build SAM application
Write-Host "Building SAM application..." -ForegroundColor Blue
sam build

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: SAM build failed" -ForegroundColor Red
    exit 1
}

Write-Host "✓ Build successful" -ForegroundColor Green
Write-Host ""

# Deploy SAM application
Write-Host "Deploying to AWS..." -ForegroundColor Blue
Write-Host "This may take several minutes..." -ForegroundColor Yellow
Write-Host ""

$paramOverrides = "Environment=$Environment TradingEnabled=false NotificationEmail=$NotificationEmail"
if ($NotificationPhone) {
    $paramOverrides += " NotificationPhone=$NotificationPhone"
}

sam deploy `
    --stack-name "fin-trade-alpaca-$Environment" `
    --s3-bucket $SamBucket `
    --s3-prefix "fin-trade-alpaca/$Environment" `
    --region $Region `
    --capabilities CAPABILITY_NAMED_IAM `
    --parameter-overrides $paramOverrides `
    --no-fail-on-empty-changeset `
    --no-confirm-changeset

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Deployment failed" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "✓ Deployment successful!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

# Get stack outputs
Write-Host "Stack Outputs:" -ForegroundColor Blue
aws cloudformation describe-stacks `
    --stack-name "fin-trade-alpaca-$Environment" `
    --region $Region `
    --query 'Stacks[0].Outputs[*].[OutputKey,OutputValue]' `
    --output table

Write-Host ""
Write-Host "Next Steps:" -ForegroundColor Blue
Write-Host ""
Write-Host "1. Confirm SNS email subscription (check your inbox)"
Write-Host ""
Write-Host "2. Update Alpaca API keys in Secrets Manager:"
Write-Host "   aws secretsmanager put-secret-value \"
Write-Host "     --secret-id alpaca-paper-api-key-$Environment \"
Write-Host "     --secret-string '{`"key`":`"YOUR_KEY`",`"secret`":`"YOUR_SECRET`"}'"
Write-Host ""
Write-Host "3. Upload strategy configuration to S3:"
$DataBucket = "fin-trade-alpaca-$Environment-$AccountId"
Write-Host "   aws s3 cp ..\configs\strategy.json s3://$DataBucket/configs/"
Write-Host "   aws s3 cp ..\configs\prediction_screener.json s3://$DataBucket/configs/"
Write-Host "   aws s3 cp ..\configs\equity_screener.json s3://$DataBucket/configs/"
Write-Host ""
Write-Host "4. Test a Lambda function manually:"
Write-Host "   aws lambda invoke \"
Write-Host "     --function-name trading-prediction-screener-$Environment \"
Write-Host "     --payload '{`"run_type`":`"adhoc`"}' \"
Write-Host "     response.json"
Write-Host ""
Write-Host "5. When ready to enable trading, update TradingEnabled parameter to 'true'"
Write-Host ""
Write-Host "Deployment complete!" -ForegroundColor Green
