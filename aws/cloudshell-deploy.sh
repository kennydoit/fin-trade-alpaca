#!/bin/bash
# Deploy from AWS CloudShell (has Docker pre-installed)
# Usage: Run this in AWS CloudShell after uploading the aws/ directory

set -e

echo "========================================"
echo "AWS CloudShell Deployment"
echo "========================================"

# Install SAM CLI if not present
if ! command -v sam &> /dev/null; then
    echo "Installing AWS SAM CLI..."
    wget https://github.com/aws/aws-sam-cli/releases/latest/download/aws-sam-cli-linux-x86_64.zip
    unzip aws-sam-cli-linux-x86_64.zip -d sam-installation
    sudo ./sam-installation/install
    rm -rf sam-installation aws-sam-cli-linux-x86_64.zip
fi

# Configuration
ENVIRONMENT="dev"
REGION="us-east-1"
EMAIL="ken.r.moore@gmail.com"
PHONE="+16465959520"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
SAM_BUCKET="fin-trade-alpaca-sam-artifacts-${ACCOUNT_ID}"

echo "Building Lambda functions with Docker..."
sam build --use-container

echo "Deploying to AWS..."
sam deploy \
    --stack-name "fin-trade-alpaca-${ENVIRONMENT}" \
    --s3-bucket "${SAM_BUCKET}" \
    --s3-prefix "fin-trade-alpaca/${ENVIRONMENT}" \
    --region "${REGION}" \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides \
        "Environment=${ENVIRONMENT}" \
        "TradingEnabled=false" \
        "NotificationEmail=${EMAIL}" \
        "NotificationPhone=${PHONE}" \
    --no-confirm-changeset \
    --no-fail-on-empty-changeset

echo ""
echo "========================================"
echo "Deployment Complete!"
echo "========================================"
echo "Next steps:"
echo "1. Check your email and confirm SNS subscription"
echo "2. Upload your Alpaca API keys to Secrets Manager"
echo "3. Upload configs to S3"
