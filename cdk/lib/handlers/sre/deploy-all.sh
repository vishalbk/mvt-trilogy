#!/bin/bash
# Deploy all SRE Lambda functions via CloudShell
# Run from ~/mvt-trilogy-fresh after git pull

set -e
echo "=== Deploying MVT SRE Lambdas ==="

BASE_DIR="$HOME/mvt-trilogy-fresh/cdk/lib/handlers/sre"

# Function: deploy a lambda
deploy_lambda() {
    local FUNCTION_NAME=$1
    local HANDLER_DIR=$2

    echo ""
    echo "--- Deploying $FUNCTION_NAME ---"

    # Check if function exists
    if ! aws lambda get-function --function-name $FUNCTION_NAME --region us-east-1 2>/dev/null; then
        echo "  [SKIP] Function $FUNCTION_NAME does not exist yet. Create it first via CDK or console."
        return
    fi

    cd $BASE_DIR/$HANDLER_DIR
    zip -j /tmp/${FUNCTION_NAME}.zip index.py
    aws lambda update-function-code \
      --function-name $FUNCTION_NAME \
      --zip-file fileb:///tmp/${FUNCTION_NAME}.zip \
      --region us-east-1

    echo "  [OK] Deployed $FUNCTION_NAME"
}

deploy_lambda "mvt-cost-tracker" "cost-tracker"
deploy_lambda "mvt-health-check" "health-check"
deploy_lambda "mvt-perf-baseline" "perf-baseline"
deploy_lambda "mvt-capacity-planner" "capacity-planner"
deploy_lambda "mvt-cost-anomaly" "cost-anomaly"

echo ""
echo "=== All SRE Lambdas deployed ==="
echo ""
echo "=== Verification ==="
for fn in mvt-cost-tracker mvt-health-check mvt-perf-baseline mvt-capacity-planner mvt-cost-anomaly; do
    echo "  $fn: $(aws lambda get-function-configuration --function-name $fn --region us-east-1 --query 'LastModified' --output text 2>/dev/null || echo 'NOT FOUND')"
done
