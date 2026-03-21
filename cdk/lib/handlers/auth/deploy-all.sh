#!/bin/bash
# Deploy all Sprint 7 Auth Lambda functions via CloudShell
# Usage: cd /tmp/mvt && git pull && bash cdk/lib/handlers/auth/deploy-all.sh

set -e

REPO_ROOT="/tmp/mvt"
AUTH_DIR="$REPO_ROOT/cdk/lib/handlers/auth"

declare -A FUNCTIONS=(
    ["mvt-cognito-auth"]="cognito-auth"
    ["mvt-user-preferences"]="user-preferences"
    ["mvt-alert-config"]="alert-config"
    ["mvt-rbac-authorizer"]="rbac"
    ["mvt-watchlists"]="watchlists"
    ["mvt-audit-log"]="audit-log"
)

echo "=== MVT Auth Lambda Deployment ==="
echo "Deploying ${#FUNCTIONS[@]} functions..."
echo ""

SUCCESS=0
FAIL=0

for func_name in "${!FUNCTIONS[@]}"; do
    handler_dir="${FUNCTIONS[$func_name]}"
    handler_path="$AUTH_DIR/$handler_dir/index.py"

    echo "--- Deploying $func_name ---"

    if [ ! -f "$handler_path" ]; then
        echo "ERROR: Handler not found at $handler_path"
        FAIL=$((FAIL + 1))
        continue
    fi

    zip -j /tmp/deploy-auth.zip "$handler_path" 2>/dev/null

    RESULT=$(aws lambda update-function-code \
        --function-name "$func_name" \
        --zip-file fileb:///tmp/deploy-auth.zip \
        --query 'LastModified' \
        --output text 2>&1)

    if [ $? -eq 0 ]; then
        echo "SUCCESS: $func_name deployed at $RESULT"
        SUCCESS=$((SUCCESS + 1))
    else
        echo "FAILED: $func_name - $RESULT"
        FAIL=$((FAIL + 1))
    fi

    rm -f /tmp/deploy-auth.zip
    echo ""
done

echo "=== Deployment Summary ==="
echo "Success: $SUCCESS / ${#FUNCTIONS[@]}"
echo "Failed:  $FAIL / ${#FUNCTIONS[@]}"

if [ $FAIL -gt 0 ]; then
    echo ""
    echo "NOTE: Create Lambda functions first in AWS Console."
    echo "Runtime: Python 3.12, Architecture: arm64"
    echo "Environment variables:"
    echo "  USER_POOL_ID=<your-cognito-pool-id>"
    echo "  COGNITO_CLIENT_ID=<your-client-id>"
    echo "  PREFERENCES_TABLE=mvt-user-preferences"
    echo "  DASHBOARD_STATE_TABLE=mvt-dashboard-state"
    echo "  AUDIT_TABLE=mvt-audit-log"
    exit 1
fi
