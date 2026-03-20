#!/bin/bash
# Deploy cost-tracker Lambda via CloudShell
# Run from ~/mvt-trilogy-fresh after git pull

FUNCTION_NAME="mvt-cost-tracker"
HANDLER_DIR="cdk/lib/handlers/sre/cost-tracker"

echo "=== Deploying $FUNCTION_NAME ==="
cd ~/mvt-trilogy-fresh/$HANDLER_DIR
zip -j /tmp/${FUNCTION_NAME}.zip index.py
aws lambda update-function-code \
  --function-name $FUNCTION_NAME \
  --zip-file fileb:///tmp/${FUNCTION_NAME}.zip \
  --region us-east-1

echo "=== Verifying deployment ==="
aws lambda get-function-configuration \
  --function-name $FUNCTION_NAME \
  --region us-east-1 \
  --query '{LastModified: LastModified, Runtime: Runtime, MemorySize: MemorySize}' \
  --output table

echo "=== Done ==="
