#!/bin/bash
# EV1-01: Create mvt-kpi-history DynamoDB table
# Run this in AWS CloudShell

echo "Creating mvt-kpi-history table..."
aws dynamodb create-table \
  --table-name mvt-kpi-history \
  --attribute-definitions \
    AttributeName=pk,AttributeType=S \
    AttributeName=sk,AttributeType=S \
    AttributeName=timestamp,AttributeType=S \
  --key-schema \
    AttributeName=pk,KeyType=HASH \
    AttributeName=sk,KeyType=RANGE \
  --global-secondary-indexes \
    '[{
      "IndexName": "by-timestamp",
      "KeySchema": [{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "timestamp", "KeyType": "RANGE"}],
      "Projection": {"ProjectionType": "ALL"}
    }]' \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1 \
  --tags Key=Project,Value=mvt-observatory

echo "Enabling TTL on mvt-kpi-history..."
aws dynamodb update-time-to-live \
  --table-name mvt-kpi-history \
  --time-to-live-specification "Enabled=true, AttributeName=ttl" \
  --region us-east-1

echo "Done! Table schema:"
echo "  PK: pk (String) - format: 'dashboard#metric' e.g. 'overview#distress_index'"
echo "  SK: sk (String) - format: ISO-8601 timestamp e.g. '2026-03-21T14:30:00Z'"
echo "  GSI: by-timestamp (pk + timestamp)"
echo "  TTL: 90 days (7776000 seconds)"
