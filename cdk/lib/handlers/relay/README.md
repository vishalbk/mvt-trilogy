# MVT Relay Handlers

Relay handlers forward MVT dashboard signals across cloud boundaries to enable cross-cloud monitoring and analysis.

## Overview

The relay subsystem bridges AWS and GCP infrastructure, ensuring that critical signals detected in the AWS MVP ecosystem are forwarded to GCP Pub/Sub for multi-cloud observability.

### Architecture

```
AWS DynamoDB (mvt-dashboard-state)
        ↓
   DynamoDB Stream
        ↓
mvt-event-relay Lambda
        ↓
(OAuth2 + JWT)
        ↓
GCP Pub/Sub (mvt-signals topic)
```

## Event-Relay Handler

### Purpose

The `mvt-event-relay` Lambda function receives DynamoDB Stream events from the `mvt-dashboard-state` table and publishes formatted signal data to the GCP `mvt-signals` Pub/Sub topic. This enables real-time cross-cloud event distribution.

### Features

- **DynamoDB Stream Trigger**: Listens to INSERT and MODIFY events on the dashboard state table
- **GCP OAuth2 Integration**: Uses service account JWT flow for secure authentication (no external dependencies)
- **Cross-Cloud Message Formatting**: Standardizes dashboard signals for multi-cloud consumption
- **Decimal Handling**: Properly converts DynamoDB Decimal values to JSON-serializable floats
- **Error Resilience**: Graceful error handling with partial success support (206 HTTP status)
- **Stdlib Only**: Uses only Python standard library + cryptography (minimal Lambda layer overhead)

### Configuration

#### Environment Variables

| Variable | Value | Notes |
|----------|-------|-------|
| `GCP_PROJECT_ID` | `mvt-observer` | GCP project ID where Pub/Sub topic lives |
| `GCP_PUBSUB_TOPIC` | `mvt-signals` | Pub/Sub topic name for cross-cloud events |
| `GCP_SA_KEY_JSON` | Service account key JSON | **REQUIRED**: Must be set manually via Secrets Manager or Lambda env vars |

#### DynamoDB Stream Configuration

The Lambda requires the DynamoDB Stream event source mapping:
- **Table**: `mvt-dashboard-state`
- **Starting Position**: LATEST (only new changes)
- **Batch Size**: 10 records per invocation
- **Parallelization Factor**: 2 (for throughput)

### Deployment

#### Option 1: Using the Deployment Script (Recommended)

```bash
cd /cdk/lib/handlers/relay/event-relay
./deploy.sh
```

The script will:
1. Package the Lambda with dependencies
2. Create or update the function
3. Configure environment variables
4. Set up DynamoDB Stream event source mapping
5. Provide instructions for setting the GCP service account key

#### Option 2: Manual Deployment

```bash
# Create deployment package
cd /cdk/lib/handlers/relay/event-relay
pip install -r requirements.txt -t /tmp/lambda-package
cp index.py /tmp/lambda-package/
cd /tmp/lambda-package
zip -r ../event-relay.zip .

# Create Lambda function
aws lambda create-function \
    --function-name mvt-event-relay \
    --runtime python3.12 \
    --handler index.handler \
    --role arn:aws:iam::572664774559:role/mvt-lambda-role \
    --zip-file fileb://../event-relay.zip \
    --timeout 30 \
    --memory-size 256 \
    --environment Variables='{
        GCP_PROJECT_ID=mvt-observer,
        GCP_PUBSUB_TOPIC=mvt-signals
    }'

# Get DynamoDB Stream ARN
STREAM_ARN=$(aws dynamodb describe-table \
    --table-name mvt-dashboard-state \
    --query Table.LatestStreamArn --output text)

# Add event source mapping
aws lambda create-event-source-mapping \
    --event-source-arn $STREAM_ARN \
    --function-name mvt-event-relay \
    --enabled \
    --starting-position LATEST \
    --batch-size 10 \
    --parallelization-factor 2
```

#### Option 3: CDK Stack (Future Enhancement)

The event-relay can be integrated into the CDK stack by adding it to a new `RelayStack`:

```typescript
// Create new relay-stack.ts
export class RelayStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: RelayStackProps) {
    super(scope, id, props);

    const eventRelayFunction = new lambda.Function(this, "EventRelayHandler", {
      functionName: "mvt-event-relay",
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "index.handler",
      code: lambda.Code.fromAsset(
        path.join(__dirname, "../handlers/relay/event-relay")
      ),
      memorySize: 256,
      timeout: cdk.Duration.seconds(30),
      environment: {
        GCP_PROJECT_ID: "mvt-observer",
        GCP_PUBSUB_TOPIC: "mvt-signals",
        GCP_SA_KEY_JSON: cdk.SecretValue.secretsManager("gcp-sa-key").toString()
      },
    });

    // Attach DynamoDB Stream
    eventRelayFunction.addEventSource(
      new DynamoEventSource(dashboardStateTable, {
        startingPosition: lambda.StartingPosition.LATEST,
        batchSize: 10,
        parallelizationFactor: 2,
      })
    );
  }
}
```

### Setting the GCP Service Account Key

The `GCP_SA_KEY_JSON` environment variable must be set with the actual service account key. Three approaches:

#### 1. **Via AWS Secrets Manager** (Recommended for Production)

```bash
# Store the key in Secrets Manager
aws secretsmanager create-secret \
    --name gcp-mvt-sa-key \
    --secret-string file:///path/to/sa-key.json

# Update Lambda to reference it
aws lambda update-function-configuration \
    --function-name mvt-event-relay \
    --environment Variables='{
        GCP_PROJECT_ID=mvt-observer,
        GCP_PUBSUB_TOPIC=mvt-signals,
        GCP_SA_KEY_JSON={"Fn::Sub":"{{resolve:secretsmanager:gcp-mvt-sa-key:SecretString}}"}
    }'

# Add permission for Lambda to read the secret
aws secretsmanager put-resource-policy \
    --secret-id gcp-mvt-sa-key \
    --resource-policy file:///path/to/policy.json
```

#### 2. **Direct Environment Variable** (Development Only)

```bash
SA_KEY=$(cat /path/to/sa-key.json | jq -c .)

aws lambda update-function-configuration \
    --function-name mvt-event-relay \
    --environment Variables="{
        GCP_PROJECT_ID=mvt-observer,
        GCP_PUBSUB_TOPIC=mvt-signals,
        GCP_SA_KEY_JSON='${SA_KEY}'
    }"
```

#### 3. **Lambda Layer** (Advanced)

Store the key in a Lambda Layer for better separation of concerns.

### Event Message Format

Messages published to GCP Pub/Sub have this structure:

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
  "timestamp": "2026-03-20T14:30:00.123456",
  "relay_timestamp": "2026-03-20T14:30:01.654321",
  "raw_data": {
    "dashboard": "inequality_pulse",
    "panel": "composite_score",
    "score": 75.5,
    "timestamp": "2026-03-20T14:30:00.123456",
    ...
  }
}
```

### Monitoring

#### CloudWatch Logs

```bash
# View recent logs
aws logs tail /aws/lambda/mvt-event-relay --follow

# Filter for errors
aws logs filter-log-events \
    --log-group-name /aws/lambda/mvt-event-relay \
    --filter-pattern "ERROR"

# Get invocation metrics
aws cloudwatch get-metric-statistics \
    --namespace AWS/Lambda \
    --metric-name Invocations \
    --dimensions Name=FunctionName,Value=mvt-event-relay \
    --start-time 2026-03-20T00:00:00Z \
    --end-time 2026-03-20T23:59:59Z \
    --period 3600 \
    --statistics Sum
```

#### CloudWatch Metrics

Key metrics to monitor:

| Metric | Threshold | Action |
|--------|-----------|--------|
| `Invocations` | Rising unexpectedly | Check DynamoDB write volume |
| `Duration` | > 20s consistently | Increase memory or optimize GCP auth |
| `Errors` | > 0% | Check GCP credentials, Pub/Sub topic, network |
| `Throttles` | > 0 | Increase concurrent execution limit |

#### GCP Pub/Sub Monitoring

```bash
# Monitor message publish rate
gcloud pubsub subscriptions pull mvt-signals-sub \
    --limit 100 \
    --project mvt-observer

# Check topic metrics
gcloud monitoring time-series list \
    --filter 'resource.type="pubsub_topic" AND metric.type="pubsub.googleapis.com/topic/publish_message_operation_count"'
```

### Troubleshooting

#### Issue: "GCP_SA_KEY_JSON environment variable not set"

**Solution**: Set the environment variable using AWS Lambda console or CLI (see "Setting the GCP Service Account Key" section).

#### Issue: "Error obtaining GCP access token: HTTP 401"

**Causes**:
- Invalid service account key
- Service account doesn't have `pubsub.publisher` role
- JWT timestamp skew (Lambda system clock out of sync)

**Solution**:
```bash
# Verify service account has correct roles
gcloud projects get-iam-policy mvt-observer \
    --flatten="bindings[].members" \
    --filter="bindings.members:serviceAccount=mvt-lambda@mvt-observer.iam.gserviceaccount.com"

# Grant required role
gcloud projects add-iam-policy-binding mvt-observer \
    --member="serviceAccount:mvt-lambda@mvt-observer.iam.gserviceaccount.com" \
    --role="roles/pubsub.publisher"
```

#### Issue: "Topic not found" (404 errors)

**Solution**: Verify the Pub/Sub topic exists:
```bash
gcloud pubsub topics describe mvt-signals --project mvt-observer
```

#### Issue: Lambda timing out (30s limit exceeded)

**Causes**:
- GCP auth taking too long
- Network latency to GCP
- Large batch of records

**Solution**:
1. Increase Lambda timeout to 60s: `aws lambda update-function-configuration --function-name mvt-event-relay --timeout 60`
2. Increase memory to 512MB: `aws lambda update-function-configuration --function-name mvt-event-relay --memory-size 512`
3. Reduce batch size: `aws lambda update-event-source-mapping --uuid <mapping-uuid> --batch-size 5`

### Cost Optimization

- **Lambda Duration**: Optimize GCP auth caching (JWT tokens valid for 1 hour)
- **DynamoDB Reads**: Use DynamoDB Streams (no additional charges beyond standard throughput)
- **Network**: Minimize payload size by filtering unnecessary fields before publishing
- **GCP Pub/Sub**: Monitor publish message volume; consider topic sharding if > 10K msg/s

### Security Considerations

1. **Service Account Key Storage**:
   - Always use AWS Secrets Manager for production
   - Never commit keys to version control
   - Rotate keys quarterly

2. **IAM Permissions**:
   - Lambda role should have minimal DynamoDB Stream permissions
   - GCP service account should only have `roles/pubsub.publisher` (not admin)

3. **Network**:
   - Consider VPC Endpoints for private connectivity to GCP (VPC + GCP VPC Peering)
   - Use HTTPS-only communication (enforced by Pub/Sub API)

4. **Audit Logging**:
   - Enable CloudTrail for Lambda invocations
   - Enable GCP Cloud Audit Logs for Pub/Sub publishes

## Future Enhancements

- [ ] Support for multiple GCP projects/topics
- [ ] Message filtering by dashboard or severity
- [ ] Dead-letter queue for failed publishes
- [ ] Batch-to-individual message rate limiting
- [ ] Lambda layer for reusable GCP OAuth2 utility
- [ ] CloudFormation template for easy deployment
- [ ] Integration tests with GCP emulator
