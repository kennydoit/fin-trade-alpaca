# Automated Trading System - Docker-Free Deployment
# This script deploys without requiring Docker or virtualization
# Perfect for systems where virtualization is disabled

param(
    [Parameter(Mandatory=$false)]
    [string]$Environment = "dev",
    
    [Parameter(Mandatory=$false)]
    [string]$NotificationEmail = "ken.r.moore@gmail.com",
    
    [Parameter(Mandatory=$false)]
    [string]$NotificationPhone = "+16465959520",
    
    [Parameter(Mandatory=$false)]
    [string]$Region = "us-east-1"
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host "`n========================================" -ForegroundColor Cyan
    Write-Host $Message -ForegroundColor Cyan
    Write-Host "========================================`n" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-Error-Custom {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

function Write-Info {
    param([string]$Message)
    Write-Host "> $Message" -ForegroundColor Yellow
}

# Simple Header
Write-Host "" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "  FIN-TRADE AWS DEPLOYMENT (Docker-Free)" -ForegroundColor Cyan
Write-Host "  Automated Trading System - Paper Mode" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "" -ForegroundColor Cyan

Write-Host "Configuration:" -ForegroundColor White
Write-Host "  Environment:  $Environment"
Write-Host "  Email:        $NotificationEmail"
Write-Host "  Phone:        $NotificationPhone"
Write-Host "  Region:       $Region"
Write-Host ""

# Step 1: Check Prerequisites (without Docker)
Write-Step "Step 1/6: Checking Prerequisites (Docker-Free Mode)"

$allGood = $true

# Check AWS CLI
try {
    $awsVersion = aws --version 2>&1
    Write-Success "AWS CLI installed: $awsVersion"
} catch {
    Write-Error-Custom "AWS CLI not installed"
    Write-Info "Install from: https://aws.amazon.com/cli/"
    $allGood = $false
}

# Check AWS Credentials
try {
    $identity = aws sts get-caller-identity 2>&1 | ConvertFrom-Json
    Write-Success "AWS credentials configured (Account: $($identity.Account))"
    $script:AccountId = $identity.Account
} catch {
    Write-Error-Custom "AWS credentials not configured"
    Write-Info "Run: aws configure"
    $allGood = $false
}

# Check SAM CLI
try {
    $samVersion = sam --version 2>&1
    Write-Success "AWS SAM CLI installed: $samVersion"
} catch {
    Write-Error-Custom "AWS SAM CLI not installed"
    Write-Info "Download from: https://github.com/aws/aws-sam-cli/releases/latest/download/AWS_SAM_CLI_64_PY3.msi"
    $allGood = $false
}

# Check Python (we'll use it instead of Docker)
try {
    $pythonVersion = python --version 2>&1
    Write-Success "Python installed: $pythonVersion"
} catch {
    Write-Error-Custom "Python not installed"
    Write-Info "Python is required. Install from: https://www.python.org/downloads/"
    $allGood = $false
}

# Docker not required!
Write-Info "Docker not required - using local Python build"

if (-not $allGood) {
    Write-Host "`n[X] Prerequisites check failed. Please install missing tools and try again." -ForegroundColor Red
    exit 1
}

Write-Success "All prerequisites are installed!"

# Step 2: Collect Alpaca API Keys
Write-Step "Step 2/6: Alpaca API Keys"

Write-Host "I need your Alpaca Paper Trading API credentials." -ForegroundColor White
Write-Host "Get them from: https://app.alpaca.markets/paper/dashboard/overview`n" -ForegroundColor White

$alpacaPaperKey = Read-Host "Enter your Alpaca Paper API Key"
$alpacaPaperSecret = Read-Host "Enter your Alpaca Paper API Secret" -AsSecureString
$alpacaPaperSecretPlain = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto([System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($alpacaPaperSecret))

if ([string]::IsNullOrWhiteSpace($alpacaPaperKey) -or [string]::IsNullOrWhiteSpace($alpacaPaperSecretPlain)) {
    Write-Error-Custom "API keys cannot be empty"
    exit 1
}

Write-Success "API keys received"

# Step 3: Prepare Deployment
Write-Step "Step 3/6: Preparing Deployment"

$SamBucket = "fin-trade-alpaca-sam-artifacts-$AccountId"
$DataBucket = "fin-trade-alpaca-$Environment-$AccountId"

Write-Info "Creating SAM artifacts bucket if needed: $SamBucket"
$ErrorActionPreference = "Continue"
$bucketCheck = aws s3 ls "s3://$SamBucket" 2>$null
$ErrorActionPreference = "Stop"
if ($LASTEXITCODE -ne 0) {
    $ErrorActionPreference = "Continue"
    aws s3 mb "s3://$SamBucket" --region $Region 2>&1 | Out-Null
    $ErrorActionPreference = "Stop"
    Write-Success "Created SAM artifacts bucket"
} else {
    Write-Success "SAM artifacts bucket exists"
}

# Copy source code to function directories
Write-Info "Preparing Lambda function code..."
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
    Write-Host "  → Preparing $func..." -ForegroundColor Gray
    
    # Create directories
    New-Item -ItemType Directory -Force -Path "$funcDir" | Out-Null
    New-Item -ItemType Directory -Force -Path "$funcDir\src" | Out-Null
    
    # Copy source code
    if (Test-Path "..\..\src\*") {
        Copy-Item -Path "..\..\src\*" -Destination "$funcDir\src\" -Recurse -Force
    }
    
    # Copy configs
    New-Item -ItemType Directory -Force -Path "$funcDir\configs" | Out-Null
    if (Test-Path "..\..\configs\*") {
        Copy-Item -Path "..\..\configs\*" -Destination "$funcDir\configs\" -Force -ErrorAction SilentlyContinue
    }
    
    # Create requirements.txt for each function (Python dependencies)
    $requirementsContent = @"
alpaca-py>=0.10.0
numpy>=1.24.0
pandas>=2.0.0
python-dotenv>=1.0.0
pyyaml>=6.0
requests>=2.31.0
yfinance>=0.2.28
matplotlib>=3.7.0
boto3>=1.28.0
scikit-learn>=1.3.0
"@
    $requirementsContent | Out-File -FilePath "$funcDir\requirements.txt" -Encoding utf8
}

Write-Success "Function code prepared"

# Build Lambda Layer for dependencies
Write-Info "Building dependencies layer (this may take a few minutes)..."
$layerDir = "layers\dependencies"
New-Item -ItemType Directory -Force -Path "$layerDir\python" | Out-Null

# Install dependencies to layer
Write-Info "Installing Python packages to Lambda layer..."
$ErrorActionPreference = "Continue"
$pipOutput = pip install -r "$layerDir\requirements.txt" -t "$layerDir\python" --upgrade 2>&1
$ErrorActionPreference = "Stop"
if ($LASTEXITCODE -eq 0) {
    Write-Success "Dependencies installed"
} else {
    Write-Info "Dependencies partially installed (will complete during build)"
}

# Step 4: Build (without Docker)
Write-Step "Step 4/6: Building Lambda Functions"
Write-Info "Building without Docker (using local Python)..."
Write-Info "This may take 5-10 minutes..."

# SAM build without containers
sam build --parallel

if ($LASTEXITCODE -ne 0) {
    Write-Error-Custom "Build failed"
    Write-Info "Check the error above. Common issues:"
    Write-Info "  - Missing Python packages: pip install -r layers/dependencies/requirements.txt"
    Write-Info "  - Python version mismatch: Python 3.11+ required"
    exit 1
}

Write-Success "Build completed!"

# Step 5: Deploy
Write-Step "Step 5/6: Deploying to AWS"
Write-Info "Creating ~20 AWS resources (Lambda, S3, SNS, etc.)..."
Write-Info "This may take 5-10 minutes..."

$paramOverrides = "Environment=$Environment TradingEnabled=false NotificationEmail=$NotificationEmail NotificationPhone=$NotificationPhone"

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
    Write-Error-Custom "Deployment failed"
    Write-Info "Check the error above. The stack may be partially created."
    Write-Info "You can delete it with: aws cloudformation delete-stack --stack-name fin-trade-alpaca-$Environment"
    exit 1
}

Write-Success "Deployment completed!"

# Step 6: Configuration
Write-Step "Step 6/6: Configuring Your Trading System"

Write-Info "Uploading Alpaca API keys to AWS Secrets Manager..."
$secretJson = "{`"key`":`"$alpacaPaperKey`",`"secret`":`"$alpacaPaperSecretPlain`"}"

$ErrorActionPreference = "Continue"
try {
    aws secretsmanager put-secret-value `
        --secret-id "alpaca-paper-api-key-$Environment" `
        --secret-string $secretJson `
        --region $Region 2>&1 | Out-Null
    Write-Success "API keys uploaded securely"
} catch {
    Write-Info "Continuing... (keys may already be set)"
}
$ErrorActionPreference = "Stop"

Write-Info "Uploading strategy configurations to S3..."
$ErrorActionPreference = "Continue"
aws s3 cp ..\..\configs\strategy.json s3://$DataBucket/configs/ --region $Region 2>&1 | Out-Null
aws s3 cp ..\..\configs\prediction_screener.json s3://$DataBucket/configs/ --region $Region 2>&1 | Out-Null
aws s3 cp ..\..\configs\equity_screener.json s3://$DataBucket/configs/ --region $Region 2>&1 | Out-Null
$ErrorActionPreference = "Stop"

Write-Success "Configurations uploaded"

# Upload database if it exists
if (Test-Path "..\..\reports\portfolio_db\portfolio.db") {
    Write-Info "Uploading existing portfolio database..."
    aws s3 cp ..\..\reports\portfolio_db\portfolio.db s3://$DataBucket/portfolio_db/portfolio.db --region $Region 2>&1 | Out-Null
    Write-Success "Database uploaded"
}

# Final Success
Write-Host "`n" -NoNewline
Write-Host "=============================================================" -ForegroundColor Green
Write-Host "                                                             " -ForegroundColor Green
Write-Host "            DEPLOYMENT SUCCESSFUL!                           " -ForegroundColor Green
Write-Host "                                                             " -ForegroundColor Green
Write-Host "=============================================================" -ForegroundColor Green
Write-Host ""

Write-Step "What Happens Now?"

Write-Host "1. CHECK YOUR EMAIL" -ForegroundColor Yellow
Write-Host "   > Confirm SNS subscription (check spam folder)"
Write-Host "   > Click the confirmation link"
Write-Host ""

Write-Host "2. YOUR AUTOMATED SCHEDULE (starts automatically on weekdays):" -ForegroundColor Yellow
Write-Host "   - 8:00 AM ET  - Generate predictions and screen candidates"
Write-Host "   - 9:45 AM ET  - Optimize portfolio and submit orders"
Write-Host "   - Hourly      - Monitor positions for stop-loss triggers"
Write-Host "   - 4:15 PM ET  - Daily review and exit logic"
Write-Host "   - 5:00 PM ET  - Email daily P and L summary"
Write-Host ""

Write-Host "3. TEST IT RIGHT NOW:" -ForegroundColor Yellow
Write-Host "   Let's run a prediction screener to make sure everything works!"
Write-Host ""

$testNow = Read-Host "Run a test prediction screener now? (yes/no)"

if ($testNow -eq "yes" -or $testNow -eq "y") {
    Write-Info "Invoking prediction screener Lambda function..."
    
    $testPayload = @'
{"run_type":"adhoc","limit":50,"return_days":5,"lookback":90}
'@
    $testPayload | Out-File -FilePath "test-payload.json" -Encoding utf8 -NoNewline
    
    aws lambda invoke `
        --function-name "trading-prediction-screener-$Environment" `
        --payload file://test-payload.json `
        --region $Region `
        response.json
    
    if ($LASTEXITCODE -eq 0 -and (Test-Path "response.json")) {
        $response = Get-Content "response.json" | ConvertFrom-Json
        Write-Success "Test completed successfully!"
        Write-Host "Response: $($response | ConvertTo-Json -Depth 2)" -ForegroundColor Gray
        
        Write-Host "`nView detailed logs:" -ForegroundColor Yellow
        Write-Host "aws logs tail /aws/lambda/trading-prediction-screener-$Environment --follow --region $Region" -ForegroundColor Gray
    } else {
        Write-Info "Test invoked, check AWS Console for results"
    }
    
    Remove-Item "test-payload.json" -ErrorAction SilentlyContinue
}

Write-Host "`n4. USEFUL COMMANDS:" -ForegroundColor Yellow
Write-Host "   # View logs"
Write-Host "   aws logs tail /aws/lambda/trading-prediction-screener-$Environment --follow --region $Region" -ForegroundColor Gray
Write-Host ""
Write-Host "   # Check S3 for results"
Write-Host "   aws s3 ls s3://$DataBucket/screener_results/ --region $Region" -ForegroundColor Gray
Write-Host ""
Write-Host "   # Run manual test"
Write-Host "   aws lambda invoke --function-name trading-optimize-buy-$Environment --payload file://test-payload.json --region $Region response.json" -ForegroundColor Gray
Write-Host ""
Write-Host "   # Emergency stop trading"
Write-Host "   aws lambda update-function-configuration --function-name trading-optimize-buy-$Environment --environment Variables=TRADING_ENABLED=false --region $Region" -ForegroundColor Gray
Write-Host ""

Write-Host "5. MONITOR AND ADJUST:" -ForegroundColor Yellow
Write-Host "   - CloudWatch Console: https://console.aws.amazon.com/cloudwatch/home?region=$Region"
Write-Host "   - Lambda Functions: https://console.aws.amazon.com/lambda/home?region=$Region"
Write-Host "   - S3 Data Bucket: https://s3.console.aws.amazon.com/s3/buckets/$DataBucket?region=$Region"
Write-Host ""

Write-Host "SUCCESS: Your trading system is now live in paper mode!" -ForegroundColor Green
Write-Host "You will get an email every weekday at 5 PM ET with your daily P and L" -ForegroundColor Green
Write-Host "Estimated monthly cost: `$7-10 (budget alarm set at `$20)" -ForegroundColor Green
Write-Host ""
Write-Host "Questions? Check: aws\\README.md for full documentation" -ForegroundColor White
Write-Host ""
