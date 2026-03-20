#!/bin/bash
set -e

# MVT Event Relay Lambda Deployment Script
# Deploys the AWS EventBridge → GCP Pub/Sub relay Lambda for cross-cloud event forwarding

# Configuration
HANDLER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FUNCTION_NAME="mvt-event-relay"
RUNTIME="python3.12"
HANDLER="index.handler"
ROLE_ARN="arn:aws:iam::572664774559:role/mvt-lambda-role"
TIMEOUT=30
MEMORY_SIZE=256

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${YELLOW}=== MVT Event Relay Lambda Deployment ===${NC}"
echo "Handler directory: $HANDLER_DIR"
echo "Function name: $FUNCTION_NAME"
echo "Runtime: $RUNTIME"
echo "Memory: ${MEMORY_SIZE}MB"
echo "Timeout: ${TIMEOUT}s"
echo ""

# Check if we're in CloudShell (or have AWS CLI)
if ! command -v aws &> /dev/null; then
    echo -e "${RED}Error: AWS CLI not found. Please run this script in AWS CloudShell or an environment with AWS CLI installed.${NC}"
    exit 1
fi

# Create deployment package
echo -e "${YELLOW}Creating deployment package...${NC}"

# Create temporary directory for build
BUILD_DIR=$(mktemp -d)
trap "rm -rf $BUILD_DIR" EXIT

# Copy handler
cp "$HANDLER_DIR/index.py" "$BUILD_DIR/"

# Install dependencies
if [ -f "$HANDLER_DIR/requirements.txt" ]; then
    echo "Installing Python dependencies..."
    pip install -q -r "$HANDLER_DIR/requirements.txt" -t "$BUILD_DIR/" 2>/dev/null || true
fi

# Create zip file
ZIP_FILE="/tmp/${FUNCTION_NAME}.zip"
cd "$BUILD_DIR"
zip -q -r "$ZIP_FILE" . 2>/dev/null || true
echo -e "${GREEN}Deployment package created: $ZIP_FILE${NC}"
echo "Package size: $(du -h $ZIP_FILE | cut -f1)"
echo ""

# Check if function exists
echo -e "${YELLOW}Checking if Lambda function exists...${NC}"
FUNCTION_EXISTS=$(aws lambda get-function --function-name "$FUNCTION_NAME" 2>/dev/null || echo "false")

if [ "$FUNCTION_EXISTS" != "false" ]; then
    echo -e "${YELLOW}Function exists. Updating code...${NC}"
    aws lambda update-function-code \
        --function-name "$FUNCTION_NAME" \
        --zip-file "fileb://$ZIP_FILE" \
        > /dev/null
    echo -e "${GREEN}Function code updated successfully${NC}"
else
    echo -e "${YELLOW}Function does not exist. Creating new function...${NC}"
    aws lambda create-function \
        --function-name "$FUNCTION_NAME" \
        --runtime "$RUNTIME" \
        --handler "$HANDLER" \
        --role "$ROLE_ARN" \
        --zip-file "fileb://$ZIP_FILE" \
        --timeout "$TIMEOUT" \
        --memory-size "$MEMORY_SIZE" \
        --description "AWS EventBridge → GCP Pub/Sub relay for cross-cloud event forwarding" \
        --environment "Variables={GCP_PROJECT_ID=mvt-observer,GCP_PUBSUB_TOPIC=mvt-signals}" \
        > /dev/null
    echo -e "${GREEN}Function created successfully${NC}"
fi

echo ""
echo -e "${YELLOW}Configuring function environment variables...${NC}"

# Update environment variables
aws lambda update-function-configuration \
    --function-name "$FUNCTION_NAME" \
    --timeout "$TIMEOUT" \
    --memory-size "$MEMORY_SIZE" \
    --environment "Variables={
        GCP_PROJECT_ID=mvt-observer,
        GCP_PUBSUB_TOPIC=mvt-signals
    }" \
    > /dev/null

echo -e "${GREEN}Environment variables configured${NC}"
echo ""

# Get DynamoDB table name
echo -e "${YELLOW}Looking up DynamoDB table...${NC}"
DASHBOARD_STATE_TABLE=$(aws dynamodb list-tables --query "TableNames[?contains(@, 'mvt-dashboard-state')]" --output text 2>/dev/null || echo "")

if [ -z "$DASHBOARD_STATE_TABLE" ]; then
    echo -e "${YELLOW}Warning: Could not find mvt-dashboard-state table. You will need to manually configure the DynamoDB Stream event source.${NC}"
    echo "To add the event source, run:"
    echo "  aws lambda create-event-source-mapping \\"
    echo "    --event-source-arn <DYNAMODB_STREAM_ARN> \\"
    echo "    --function-name $FUNCTION_NAME \\"
    echo "    --enabled \\"
    echo "    --starting-position LATEST \\"
    echo "    --batch-size 10"
else
    echo -e "${GREEN}Found table: $DASHBOARD_STATE_TABLE${NC}"

    # Get table stream ARN
    STREAM_ARN=$(aws dynamodb describe-table --table-name "$DASHBOARD_STATE_TABLE" --query "Table.LatestStreamArn" --output text 2>/dev/null || echo "")

    if [ -n "$STREAM_ARN" ] && [ "$STREAM_ARN" != "None" ]; then
        echo -e "${YELLOW}Configuring DynamoDB Stream event source...${NC}"

        # Check if event source already exists
        EXISTING_MAPPING=$(aws lambda list-event-source-mappings \
            --function-name "$FUNCTION_NAME" \
            --event-source-arn "$STREAM_ARN" \
            --query "EventSourceMappings[0].UUID" \
            --output text 2>/dev/null || echo "")

        if [ -z "$EXISTING_MAPPING" ] || [ "$EXISTING_MAPPING" = "None" ]; then
            aws lambda create-event-source-mapping \
                --event-source-arn "$STREAM_ARN" \
                --function-name "$FUNCTION_NAME" \
                --enabled \
                --starting-position LATEST \
                --batch-size 10 \
                --parallelization-factor 2 \
                > /dev/null
            echo -e "${GREEN}DynamoDB Stream event source configured${NC}"
        else
            echo -e "${GREEN}DynamoDB Stream event source already configured${NC}"
        fi
    else
        echo -e "${YELLOW}Warning: Table does not have an active stream. Enable DynamoDB Streams for the table.${NC}"
    fi
fi

echo ""
echo -e "${GREEN}=== Deployment Complete ===${NC}"
echo ""
echo "Function ARN:"
aws lambda get-function --function-name "$FUNCTION_NAME" \
    --query "Configuration.FunctionArn" \
    --output text
echo ""

echo "Environment Variables Required:"
echo "  GCP_PROJECT_ID: mvt-observer (configured)"
echo "  GCP_PUBSUB_TOPIC: mvt-signals (configured)"
echo "  GCP_SA_KEY_JSON: <service account key JSON> (must be set manually)"
echo ""
echo "To set the GCP service account key:"
echo "  aws lambda update-function-configuration --function-name $FUNCTION_NAME \\"
echo "    --environment \"Variables={GCP_PROJECT_ID=mvt-observer,GCP_PUBSUB_TOPIC=mvt-signals,GCP_SA_KEY_JSON=\$(cat /path/to/sa-key.json | jq -c)}\""
echo ""

# Cleanup
rm -f "$ZIP_FILE"
echo -e "${GREEN}Done!${NC}"
