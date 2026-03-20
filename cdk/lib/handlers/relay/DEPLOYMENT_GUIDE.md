# MVT Event Relay - Deployment Guide

## Quick Start

### 1. Deploy the Lambda Function

```bash
cd /sessions/zealous-ecstatic-meitner/mvt-trilogy-fresh/cdk/lib/handlers/relay/event-relay
./deploy.sh
```

This script will:
- Package Python dependencies (cryptography)
- Create/update the `mvt-event-relay` Lambda function
- Configure environment variables (GCP project & topic)
- Attach DynamoDB Stream event source from `mvt-dashboard-state` table

**Expected output:**
```
=== MVT Event Relay Lambda Deployment ===
...
=== Deployment Complete ===
Function ARN: arn:aws:lambda:us-east-1:572664774559:function:mvt-event-relay
```

### 2. Configure GCP Service Account Key

The service account key is required but must be set separately for security.

#### Get the Service Account Key from GCP

```bash
# In GCP Console or using gcloud CLI
gcloud iam service-accounts keys create sa-key.json \
    --iam-account=mvt-lambda@mvt-observer.iam.gserviceaccount.com
```

#### Store in AWS Secrets Manager (Recommended)

```bash
aws secretsmanager create-secret \
    --name gcp-mvt-sa-key \
    --secret-string file://sa-key.json
```

#### Set Environment Variable in Lambda

**Option A: Direct (Development)**
```bash
SA_KEY=$(cat sa-key.json | jq -c .)
aws lambda update-function-configuration \
    --function-name mvt-event-relay \
    --environment Variables="{
        GCP_PROJECT_ID=mvt-observer,
        GCP_PUBSUB_TOPIC=mvt-signals,
        GCP_SA_KEY_JSON='${SA_KEY}'
    }"
```

**Option B: From Secrets Manager (Production)**
```bash
aws lambda update-function-configuration \
    --function-name mvt-event-relay \
    --environment Variables='{
        "GCP_PROJECT_ID":"mvt-observer",
        "GCP_PUBSUB_TOPIC":"mvt-signals",
        "GCP_SA_KEY_JSON":{"Fn::Sub":"{{resolve:secretsmanager:gcp-mvt-sa-key:SecretString}}"}
    }'
```

### 3. Verify GCP Service Account Permissions

Ensure the service account has the `pubsub.publisher` role on the GCP project:

```bash
gcloud projects add-iam-policy-binding mvt-observer \
    --member="serviceAccount:mvt-lambda@mvt-observer.iam.gserviceaccount.com" \
    --role="roles/pubsub.publisher"

# Verify
gcloud projects get-iam-policy mvt-observer \
    --flatten="bindings[].members" \
    --filter="bindings.members:serviceAccount=mvt-lambda@mvt-observer.iam.gserviceaccount.com"
```

### 4. Verify Deployment

#### Check CloudWatch Logs

```bash
# Wait for first invocation, then view logs
aws logs tail /aws/lambda/mvt-event-relay --follow

# Or check for errors
aws logs filter-log-events \
    --log-group-name /aws/lambda/mvt-event-relay \
    --filter-pattern "ERROR"
```

#### Test the Integration

Trigger a DynamoDB write to `mvt-dashboard-state`:

```bash
# Example: Insert a test signal
aws dynamodb put-item \
    --table-name mvt-dashboard-state \
    --item '{
        "dashboard": {"S": "test_dashboard"},
        "panel": {"S": "test_panel"},
        "timestamp": {"S": "2026-03-20T14:30:00Z"},
        "event_type": {"S": "signal_update"},
        "severity": {"S": "info"},
        "value": {"N": "42.5"}
    }'
```

Check GCP Pub/Sub for the message:

```bash
# Verify message was published
gcloud pubsub subscriptions pull mvt-signals-sub \
    --limit 1 \
    --project mvt-observer \
    --auto-ack

# Expected output includes the relayed signal data
```

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│           AWS MVT Infrastructure                         │
├─────────────────────────────────────────────────────────┤
│                                                           │
│  Processing Lambdas                                       │
│    (inequality-scorer, contagion-modeler, etc.)           │
│         ↓                                                 │
│  mvt-dashboard-state DynamoDB Table                       │
│  (dashboard_id, panel_id, value, timestamp, ...)          │
│         ↓                                                 │
│  DynamoDB Stream (NEW IMAGES ONLY)                        │
│         ↓                                                 │
│  mvt-event-relay Lambda (NEW)                             │
│    - Extract signal from DynamoDB record                  │
│    - Get GCP OAuth2 token (JWT flow)                      │
│    - Publish to Pub/Sub via HTTP REST API                │
│    - Handle Decimal → float conversion                    │
│         ↓                                                 │
└─────────────────────────────────────────────────────────┘
         ↓ (HTTPS)
    ┌────────────────────────────────────┐
    │   Google Cloud Platform (mvt-observer) │
    ├────────────────────────────────────┤
    │                                    │
    │  GCP Pub/Sub                       │
    │    Topic: mvt-signals              │
    │    - Cross-cloud event hub         │
    │    - Forward to subscriptions      │
    │                                    │
    │  Example Subscribers:              │
    │    - BigQuery (analytics)          │
    │    - Cloud Functions (processors)  │
    │    - Cloud Logging (audit trail)   │
    │                                    │
    └────────────────────────────────────┘
```

## Event Flow Example

### 1. Signal Generated in AWS

Dashboard processing lambda detects high inequality score:

```python
# In AWS Lambda (inequality-scorer)
inequality_score = 75.5
dynamodb.put_item(
    TableName='mvt-dashboard-state',
    Item={
        'dashboard': 'inequality_pulse',
        'panel': 'composite_score',
        'value': Decimal('75.5'),
        'timestamp': '2026-03-20T14:30:00Z',
        'event_type': 'signal_update',
        'severity': 'high'
    }
)
```

### 2. DynamoDB Stream Triggers Relay

DynamoDB Stream detects the INSERT/MODIFY:

```json
{
  "eventID": "1",
  "eventVersion": "1.0",
  "dynamodb": {
    "Keys": {
      "dashboard": {"S": "inequality_pulse"},
      "panel": {"S": "composite_score"}
    },
    "NewImage": {
      "dashboard": {"S": "inequality_pulse"},
      "panel": {"S": "composite_score"},
      "value": {"N": "75.5"},
      "timestamp": {"S": "2026-03-20T14:30:00Z"},
      "event_type": {"S": "signal_update"},
      "severity": {"S": "high"}
    }
  },
  "eventName": "INSERT",
  "eventSource": "aws:dynamodb",
  "eventSourceARN": "arn:aws:dynamodb:us-east-1:..."
}
```

### 3. Relay Lambda Processes Event

The `mvt-event-relay` Lambda:

1. Extracts the NewImage from the DynamoDB record
2. Converts DynamoDB format to Python dict (handles Decimals)
3. Formats as cross-cloud event message:

```json
{
  "source": "aws.mvt.dynamodb",
  "cloud_region": "us-east-1",
  "dashboard_id": "inequality_pulse",
  "panel_id": "composite_score",
  "signal_id": "inequality_pulse#composite_score",
  "event_type": "signal_update",
  "severity": "high",
  "value": 75.5,
  "timestamp": "2026-03-20T14:30:00Z",
  "relay_timestamp": "2026-03-20T14:30:01.123456Z",
  "raw_data": {
    "dashboard": "inequality_pulse",
    "panel": "composite_score",
    "value": 75.5,
    "timestamp": "2026-03-20T14:30:00Z",
    "event_type": "signal_update",
    "severity": "high"
  }
}
```

4. Obtains GCP OAuth2 access token using service account JWT
5. Base64-encodes the message
6. POSTs to: `https://pubsub.googleapis.com/v1/projects/mvt-observer/topics/mvt-signals:publish`

### 4. GCP Pub/Sub Receives Message

Pub/Sub stores the message for subscribers:

```json
{
  "messageIds": [
    "12345678901234567"
  ]
}
```

Subscribers (BigQuery, Cloud Functions, etc.) can now process the cross-cloud signal.

## Integration with Existing Stacks

### Current Architecture

The MVP has three main stacks:

1. **RealtimeStack** (`cdk/lib/stacks/realtime-stack.ts`)
   - WebSocket API
   - Lambda functions: ws-connect, ws-disconnect, ws-broadcast
   - **Uses**: DynamoDB Stream from dashboard-state table
   - **Effect**: Broadcasts signals to WebSocket clients

2. **ProcessingStack** (`cdk/lib/stacks/processing-stack.ts`)
   - Writes results to dashboard-state table
   - Triggered by EventBridge rules

3. **IngestionStack** (`cdk/lib/stacks/ingestion-stack.ts`)
   - Collects external data (GDELT, FRED, Yahoo Finance, etc.)
   - Publishes events to EventBridge

### EventRelay Integration

**Current Situation:**
- RealtimeStack already attaches ws-broadcast Lambda to DynamoDB Stream
- Same stream can support multiple event sources

**Solution:**
- Deploy mvt-event-relay independently
- Attach to the SAME DynamoDB Stream as ws-broadcast
- No code changes needed in existing stacks

**Event Source Sharing:**
```
mvt-dashboard-state DynamoDB Stream
    ├─ ws-broadcast Lambda (WebSocket broadcast)
    └─ mvt-event-relay Lambda (GCP Pub/Sub forward) [NEW]
```

Both Lambdas process the same events independently.

## Performance Characteristics

### Lambda Resources

| Aspect | Value | Notes |
|--------|-------|-------|
| Runtime | Python 3.12 | Fast startup, minimal cold-start |
| Memory | 256 MB | Sufficient for GCP auth + Pub/Sub publish |
| Timeout | 30 seconds | GCP auth typically < 5s, publish < 2s |
| Ephemeral Storage | 512 MB | Default, not used |

### Latency

- **DynamoDB Stream → Lambda Invocation**: ~1-2 seconds
- **GCP OAuth2 Token Fetch**: ~1-2 seconds (first invocation, then cached per Lambda instance)
- **Pub/Sub Publish**: ~500ms-1s
- **Total Latency**: ~2-5 seconds from DynamoDB write to GCP

### Throughput

- **Batch Size**: 10 records per invocation
- **Parallelization Factor**: 2 (2x concurrent Lambda executions)
- **Max Throughput**: ~1000 signals/min (depends on write rate to mvt-dashboard-state)

### Cost

**Monthly Estimate** (assuming 10K signals/day):
- Lambda Invocations: 300 (10K ÷ 32 batch size) × $0.0000002 = negligible
- Lambda Duration: 300 × 30 seconds × $0.0000166667/second = ~$0.15/month
- DynamoDB Stream: Included (already paying for table)
- Network: Included (AWS-to-GCP transfer pricing applies)

**Total: ~$0.20/month**

## Troubleshooting Checklist

### Lambda Not Invoked

- [ ] Is DynamoDB Stream enabled? `aws dynamodb describe-table --table-name mvt-dashboard-state | grep StreamArn`
- [ ] Is event source mapping created? `aws lambda list-event-source-mappings --function-name mvt-event-relay`
- [ ] Is event source mapping enabled? Check status in above command

### Messages Not Reaching GCP

- [ ] Check CloudWatch logs: `aws logs tail /aws/lambda/mvt-event-relay --follow`
- [ ] Is GCP_SA_KEY_JSON set? `aws lambda get-function-configuration --function-name mvt-event-relay | grep GCP_SA_KEY_JSON`
- [ ] Is service account key valid? Try authenticating with it locally
- [ ] Does service account have pubsub.publisher role?

### Authentication Failures

```bash
# Test GCP auth locally
python3 << 'EOF'
import json
import os

os.environ['GCP_SA_KEY_JSON'] = open('sa-key.json').read()

# Import and test
import sys
sys.path.insert(0, '/path/to/event-relay')
from index import get_gcp_access_token

try:
    token = get_gcp_access_token()
    print(f"✓ Token obtained: {token[:20]}...")
except Exception as e:
    print(f"✗ Error: {e}")
EOF
```

### Network Issues

```bash
# Test connectivity to GCP Pub/Sub from Lambda environment
aws lambda invoke \
    --function-name mvt-event-relay \
    --invocation-type RequestResponse \
    --payload '{"Records":[]}' \
    /tmp/response.json

# Check response
cat /tmp/response.json
```

## Next Steps

1. **Monitoring**: Set up CloudWatch alarms for error rate
2. **Dead-Letter Queue**: Route failed publishes to SQS for replay
3. **Message Filtering**: Add capability to filter events by dashboard/severity
4. **Rate Limiting**: Implement GCP Pub/Sub rate limiting if needed
5. **Multi-Cloud**: Extend to relay to Azure Event Hubs, AWS EventBridge cross-region, etc.

## Files Reference

| File | Purpose |
|------|---------|
| `event-relay/index.py` | Main Lambda handler (11KB) |
| `event-relay/requirements.txt` | Python dependencies (cryptography) |
| `event-relay/deploy.sh` | Automated deployment script (executable) |
| `README.md` | Comprehensive documentation |
| `DEPLOYMENT_GUIDE.md` | This file |
