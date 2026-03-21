#!/bin/bash
# Evolve Sprint 1: Deploy history Lambdas + create DynamoDB table
# Run in AWS CloudShell after: cd mvt-trilogy && git pull

set -e

echo "=== EVOLVE SPRINT 1 DEPLOYMENT ==="
echo ""

# Step 1: Create DynamoDB table (idempotent - will skip if exists)
echo "--- Step 1: Creating mvt-kpi-history table ---"
aws dynamodb describe-table --table-name mvt-kpi-history --region us-east-1 2>/dev/null && echo "Table already exists, skipping..." || {
  bash cdk/lib/handlers/history/create-table.sh
  echo "Waiting 10s for table to become active..."
  sleep 10
}

# Step 2: Create mvt-history-writer Lambda
echo ""
echo "--- Step 2: Creating/Updating mvt-history-writer Lambda ---"
cd cdk/lib/handlers/history/history-writer
zip -j /tmp/history-writer.zip index.py
cd -

# Check if Lambda exists
aws lambda get-function --function-name mvt-history-writer --region us-east-1 2>/dev/null && {
  echo "Updating existing Lambda..."
  aws lambda update-function-code \
    --function-name mvt-history-writer \
    --zip-file fileb:///tmp/history-writer.zip \
    --region us-east-1
} || {
  echo "Creating new Lambda..."
  aws lambda create-function \
    --function-name mvt-history-writer \
    --runtime python3.12 \
    --handler index.handler \
    --role arn:aws:iam::572664774559:role/mvt-processing-role \
    --zip-file fileb:///tmp/history-writer.zip \
    --timeout 30 \
    --memory-size 128 \
    --environment "Variables={HISTORY_TABLE=mvt-kpi-history,TTL_SECONDS=7776000}" \
    --region us-east-1
}

# Step 3: Create mvt-history-api Lambda
echo ""
echo "--- Step 3: Creating/Updating mvt-history-api Lambda ---"
cd cdk/lib/handlers/history/history-api
zip -j /tmp/history-api.zip index.py
cd -

aws lambda get-function --function-name mvt-history-api --region us-east-1 2>/dev/null && {
  echo "Updating existing Lambda..."
  aws lambda update-function-code \
    --function-name mvt-history-api \
    --zip-file fileb:///tmp/history-api.zip \
    --region us-east-1
} || {
  echo "Creating new Lambda..."
  aws lambda create-function \
    --function-name mvt-history-api \
    --runtime python3.12 \
    --handler index.handler \
    --role arn:aws:iam::572664774559:role/mvt-processing-role \
    --zip-file fileb:///tmp/history-api.zip \
    --timeout 30 \
    --memory-size 256 \
    --environment "Variables={HISTORY_TABLE=mvt-kpi-history}" \
    --region us-east-1
}

# Step 4: Wire DynamoDB Stream to history-writer
echo ""
echo "--- Step 4: Wiring DynamoDB Stream trigger ---"
STREAM_ARN=$(aws dynamodb describe-table --table-name mvt-dashboard-state --region us-east-1 --query 'Table.LatestStreamArn' --output text)
echo "Stream ARN: $STREAM_ARN"

# Check if mapping already exists
EXISTING=$(aws lambda list-event-source-mappings --function-name mvt-history-writer --region us-east-1 --query 'EventSourceMappings[0].UUID' --output text 2>/dev/null)
if [ "$EXISTING" != "None" ] && [ -n "$EXISTING" ]; then
  echo "Event source mapping already exists: $EXISTING"
else
  echo "Creating event source mapping..."
  aws lambda create-event-source-mapping \
    --function-name mvt-history-writer \
    --event-source-arn "$STREAM_ARN" \
    --starting-position LATEST \
    --batch-size 25 \
    --maximum-batching-window-in-seconds 5 \
    --region us-east-1
fi

# Step 5: Grant history-writer permission to write to history table
echo ""
echo "--- Step 5: Granting DynamoDB permissions ---"
# The mvt-processing-role should already have DynamoDB access, but let's ensure history table access
aws iam put-role-policy \
  --role-name mvt-processing-role \
  --policy-name mvt-history-table-access \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Action": ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:Query", "dynamodb:BatchWriteItem"],
      "Resource": [
        "arn:aws:dynamodb:us-east-1:572664774559:table/mvt-kpi-history",
        "arn:aws:dynamodb:us-east-1:572664774559:table/mvt-kpi-history/index/*"
      ]
    }]
  }' 2>/dev/null && echo "Policy attached" || echo "Policy may already exist"

# Step 6: Add API Gateway route for /api/history
echo ""
echo "--- Step 6: API Gateway route setup ---"
echo "NOTE: You need to manually add the /api/history route in API Gateway console:"
echo "  1. Go to API Gateway > WebSocket API or REST API"
echo "  2. Add GET /api/history -> mvt-history-api Lambda integration"
echo "  3. Add GET /api/history/export -> mvt-history-api Lambda integration"
echo "  4. Deploy to 'prod' stage"
echo ""
echo "Alternatively, create an HTTP API:"
REST_API_ID=$(aws apigateway get-rest-apis --region us-east-1 --query "items[?name=='mvt-history-api'].id" --output text 2>/dev/null)
if [ -z "$REST_API_ID" ] || [ "$REST_API_ID" == "None" ]; then
  echo "Creating REST API for history endpoints..."
  REST_API_ID=$(aws apigateway create-rest-api \
    --name mvt-history-api \
    --description "MVT Observatory History API" \
    --endpoint-configuration types=REGIONAL \
    --region us-east-1 \
    --query 'id' --output text)
  echo "Created REST API: $REST_API_ID"

  # Get root resource ID
  ROOT_ID=$(aws apigateway get-resources --rest-api-id $REST_API_ID --region us-east-1 --query 'items[?path==`/`].id' --output text)

  # Create /api resource
  API_ID=$(aws apigateway create-resource --rest-api-id $REST_API_ID --parent-id $ROOT_ID --path-part api --region us-east-1 --query 'id' --output text)

  # Create /api/history resource
  HISTORY_ID=$(aws apigateway create-resource --rest-api-id $REST_API_ID --parent-id $API_ID --path-part history --region us-east-1 --query 'id' --output text)

  # Create GET method on /api/history
  aws apigateway put-method --rest-api-id $REST_API_ID --resource-id $HISTORY_ID --http-method GET --authorization-type NONE --region us-east-1

  # Lambda integration
  LAMBDA_ARN="arn:aws:lambda:us-east-1:572664774559:function:mvt-history-api"
  aws apigateway put-integration \
    --rest-api-id $REST_API_ID \
    --resource-id $HISTORY_ID \
    --http-method GET \
    --type AWS_PROXY \
    --integration-http-method POST \
    --uri "arn:aws:apigateway:us-east-1:lambda:path/2015-03-31/functions/$LAMBDA_ARN/invocations" \
    --region us-east-1

  # Grant API Gateway permission to invoke Lambda
  aws lambda add-permission \
    --function-name mvt-history-api \
    --statement-id apigateway-history \
    --action lambda:InvokeFunction \
    --principal apigateway.amazonaws.com \
    --source-arn "arn:aws:execute-api:us-east-1:572664774559:$REST_API_ID/*/GET/api/history" \
    --region us-east-1 2>/dev/null || true

  # Create /api/history/export resource
  EXPORT_ID=$(aws apigateway create-resource --rest-api-id $REST_API_ID --parent-id $HISTORY_ID --path-part export --region us-east-1 --query 'id' --output text)
  aws apigateway put-method --rest-api-id $REST_API_ID --resource-id $EXPORT_ID --http-method GET --authorization-type NONE --region us-east-1
  aws apigateway put-integration \
    --rest-api-id $REST_API_ID \
    --resource-id $EXPORT_ID \
    --http-method GET \
    --type AWS_PROXY \
    --integration-http-method POST \
    --uri "arn:aws:apigateway:us-east-1:lambda:path/2015-03-31/functions/$LAMBDA_ARN/invocations" \
    --region us-east-1

  # Enable CORS (OPTIONS method)
  for RES_ID in $HISTORY_ID $EXPORT_ID; do
    aws apigateway put-method --rest-api-id $REST_API_ID --resource-id $RES_ID --http-method OPTIONS --authorization-type NONE --region us-east-1
    aws apigateway put-integration --rest-api-id $REST_API_ID --resource-id $RES_ID --http-method OPTIONS --type MOCK --request-templates '{"application/json": "{\"statusCode\": 200}"}' --region us-east-1
    aws apigateway put-method-response --rest-api-id $REST_API_ID --resource-id $RES_ID --http-method OPTIONS --status-code 200 \
      --response-parameters '{"method.response.header.Access-Control-Allow-Headers":true,"method.response.header.Access-Control-Allow-Methods":true,"method.response.header.Access-Control-Allow-Origin":true}' --region us-east-1
    aws apigateway put-integration-response --rest-api-id $REST_API_ID --resource-id $RES_ID --http-method OPTIONS --status-code 200 \
      --response-parameters '{"method.response.header.Access-Control-Allow-Headers":"'"'"'Content-Type,Authorization'"'"'","method.response.header.Access-Control-Allow-Methods":"'"'"'GET,OPTIONS'"'"'","method.response.header.Access-Control-Allow-Origin":"'"'"'*'"'"'"}' --region us-east-1
  done

  # Deploy
  aws apigateway create-deployment --rest-api-id $REST_API_ID --stage-name prod --region us-east-1
  echo ""
  echo "API deployed! URL: https://${REST_API_ID}.execute-api.us-east-1.amazonaws.com/prod/api/history"
else
  echo "REST API already exists: $REST_API_ID"
fi

# Step 7: Deploy frontend
echo ""
echo "--- Step 7: Deploying frontend ---"
aws s3 sync frontend/ s3://mvt-frontend-572664774559-us-east-1/ --delete
aws cloudfront create-invalidation --distribution-id E2JPNGN2TCNNV8 --paths "/*"

echo ""
echo "=== EVOLVE SPRINT 1 DEPLOYMENT COMPLETE ==="
echo "Dashboard: https://d2p9otbgwjwwuv.cloudfront.net"
