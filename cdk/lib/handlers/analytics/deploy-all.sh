#!/bin/bash
# Deploy all Sprint 6 Analytics Lambda functions via CloudShell
# Usage: cd /tmp/mvt && git pull && bash cdk/lib/handlers/analytics/deploy-all.sh

set -e

REPO_ROOT="/tmp/mvt"
ANALYTICS_DIR="$REPO_ROOT/cdk/lib/handlers/analytics"

# Lambda function name → handler directory mapping
declare -A FUNCTIONS=(
    ["mvt-distress-predictor"]="distress-predictor"
    ["mvt-backtester"]="backtester"
    ["mvt-anomaly-classifier"]="anomaly-classifier"
    ["mvt-history-api"]="history-api"
)

echo "=== MVT Analytics Lambda Deployment ==="
echo "Deploying ${#FUNCTIONS[@]} functions..."
echo ""

SUCCESS=0
FAIL=0

for func_name in "${!FUNCTIONS[@]}"; do
    handler_dir="${FUNCTIONS[$func_name]}"
    handler_path="$ANALYTICS_DIR/$handler_dir/index.py"

    echo "--- Deploying $func_name ---"

    if [ ! -f "$handler_path" ]; then
        echo "ERROR: Handler not found at $handler_path"
        FAIL=$((FAIL + 1))
        continue
    fi

    # Create zip package
    zip -j /tmp/deploy-analytics.zip "$handler_path" 2>/dev/null

    # Deploy to Lambda
    RESULT=$(aws lambda update-function-code \
        --function-name "$func_name" \
        --zip-file fileb:///tmp/deploy-analytics.zip \
        --query 'LastModified' \
        --output text 2>&1)

    if [ $? -eq 0 ]; then
        echo "SUCCESS: $func_name deployed at $RESULT"
        SUCCESS=$((SUCCESS + 1))
    else
        echo "FAILED: $func_name - $RESULT"
        FAIL=$((FAIL + 1))
    fi

    # Cleanup
    rm -f /tmp/deploy-analytics.zip
    echo ""
done

echo "=== Deployment Summary ==="
echo "Success: $SUCCESS / ${#FUNCTIONS[@]}"
echo "Failed:  $FAIL / ${#FUNCTIONS[@]}"

if [ $FAIL -gt 0 ]; then
    echo ""
    echo "NOTE: Failed deployments may need Lambda functions created first."
    echo "Create via AWS Console: Lambda → Create function → mvt-<name>"
    echo "Runtime: Python 3.12, Architecture: arm64"
    echo "Environment variables:"
    echo "  DASHBOARD_STATE_TABLE=mvt-dashboard-state"
    echo "  SIGNALS_TABLE=mvt-signals"
    exit 1
fi
