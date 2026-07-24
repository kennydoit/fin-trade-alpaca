#!/bin/bash

# AWS SAM Deployment Script for fin-trade-alpaca
# This script builds and deploys the automated trading system to AWS

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Default values
ENVIRONMENT="${1:-dev}"
NOTIFICATION_EMAIL="${2}"
NOTIFICATION_PHONE="${3:-}"
REGION="${AWS_REGION:-us-east-1}"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}fin-trade-alpaca AWS Deployment${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check required parameters
if [ -z "$NOTIFICATION_EMAIL" ]; then
    echo -e "${RED}ERROR: Notification email is required${NC}"
    echo "Usage: $0 <environment> <notification-email> [notification-phone]"
    echo "Example: $0 dev user@example.com +12345678900"
    exit 1
fi

echo -e "${GREEN}Configuration:${NC}"
echo "  Environment: $ENVIRONMENT"
echo "  Email: $NOTIFICATION_EMAIL"
echo "  Phone: ${NOTIFICATION_PHONE:-none}"
echo "  Region: $REGION"
echo ""

# Check if SAM CLI is installed
if ! command -v sam &> /dev/null; then
    echo -e "${RED}ERROR: AWS SAM CLI is not installed${NC}"
    echo "Install from: https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html"
    exit 1
fi

# Check if AWS credentials are configured
if ! aws sts get-caller-identity &> /dev/null; then
    echo -e "${RED}ERROR: AWS credentials not configured${NC}"
    echo "Run: aws configure"
    exit 1
fi

echo -e "${GREEN}✓ Prerequisites checked${NC}"
echo ""

# Create S3 bucket for SAM artifacts if it doesn't exist
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
SAM_BUCKET="fin-trade-alpaca-sam-artifacts-${ACCOUNT_ID}"

if ! aws s3 ls "s3://${SAM_BUCKET}" 2>&1 > /dev/null; then
    echo -e "${BLUE}Creating SAM artifacts bucket: ${SAM_BUCKET}${NC}"
    aws s3 mb "s3://${SAM_BUCKET}" --region "$REGION"
else
    echo -e "${GREEN}✓ SAM artifacts bucket exists: ${SAM_BUCKET}${NC}"
fi

echo ""

# Copy source code to function directories
echo -e "${BLUE}Preparing function code...${NC}"

for func in prediction_screener equity_screener optimize_and_buy portfolio_monitor add_protections daily_review daily_summary; do
    FUNC_DIR="functions/${func}"
    echo "  Copying source to ${FUNC_DIR}/"
    
    # Create src directory in function
    mkdir -p "${FUNC_DIR}/src"
    
    # Copy entire src directory
    cp -r ../../src/* "${FUNC_DIR}/src/"
    
    # Copy configs
    mkdir -p "${FUNC_DIR}/configs"
    cp -r ../../configs/* "${FUNC_DIR}/configs/" 2>/dev/null || true
done

echo -e "${GREEN}✓ Function code prepared${NC}"
echo ""

# Build Lambda layer for dependencies
echo -e "${BLUE}Building Lambda layer...${NC}"
cd layers/dependencies

if [ ! -d "python" ]; then
    mkdir -p python
    pip install -r requirements.txt -t python/ --upgrade
    echo -e "${GREEN}✓ Dependencies installed${NC}"
else
    echo -e "${GREEN}✓ Dependencies already installed (delete layers/dependencies/python to rebuild)${NC}"
fi

cd ../..
echo ""

# Build SAM application
echo -e "${BLUE}Building SAM application...${NC}"
sam build --use-container

if [ $? -ne 0 ]; then
    echo -e "${RED}ERROR: SAM build failed${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Build successful${NC}"
echo ""

# Deploy SAM application
echo -e "${BLUE}Deploying to AWS...${NC}"
echo "This may take several minutes..."
echo ""

sam deploy \
    --stack-name "fin-trade-alpaca-${ENVIRONMENT}" \
    --s3-bucket "${SAM_BUCKET}" \
    --s3-prefix "fin-trade-alpaca/${ENVIRONMENT}" \
    --region "${REGION}" \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides \
        Environment="${ENVIRONMENT}" \
        TradingEnabled="false" \
        NotificationEmail="${NOTIFICATION_EMAIL}" \
        NotificationPhone="${NOTIFICATION_PHONE}" \
    --no-fail-on-empty-changeset \
    --no-confirm-changeset

if [ $? -ne 0 ]; then
    echo -e "${RED}ERROR: Deployment failed${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}✓ Deployment successful!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Get stack outputs
echo -e "${BLUE}Stack Outputs:${NC}"
aws cloudformation describe-stacks \
    --stack-name "fin-trade-alpaca-${ENVIRONMENT}" \
    --region "${REGION}" \
    --query 'Stacks[0].Outputs[*].[OutputKey,OutputValue]' \
    --output table

echo ""
echo -e "${BLUE}Next Steps:${NC}"
echo ""
echo "1. Confirm SNS email subscription (check your inbox)"
echo ""
echo "2. Update Alpaca API keys in Secrets Manager:"
echo "   aws secretsmanager put-secret-value \\"
echo "     --secret-id alpaca-paper-api-key-${ENVIRONMENT} \\"
echo "     --secret-string '{\"key\":\"YOUR_KEY\",\"secret\":\"YOUR_SECRET\"}'"
echo ""
echo "3. Upload strategy configuration to S3:"
DATA_BUCKET="fin-trade-alpaca-${ENVIRONMENT}-${ACCOUNT_ID}"
echo "   aws s3 cp configs/strategy.json s3://${DATA_BUCKET}/configs/"
echo "   aws s3 cp configs/prediction_screener.json s3://${DATA_BUCKET}/configs/"
echo "   aws s3 cp configs/equity_screener.json s3://${DATA_BUCKET}/configs/"
echo ""
echo "4. Test a Lambda function manually:"
echo "   aws lambda invoke \\"
echo "     --function-name trading-prediction-screener-${ENVIRONMENT} \\"
echo "     --payload '{\"run_type\":\"adhoc\"}' \\"
echo "     response.json"
echo ""
echo "5. When ready to enable trading, update stack with TradingEnabled=true:"
echo "   ./deploy.sh ${ENVIRONMENT} ${NOTIFICATION_EMAIL} ${NOTIFICATION_PHONE}"
echo "   (Then update CloudFormation parameter TradingEnabled to 'true')"
echo ""
echo -e "${GREEN}Deployment complete!${NC}"
