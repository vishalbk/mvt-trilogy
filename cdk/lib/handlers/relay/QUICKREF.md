# MVT Event Relay - Quick Reference

## One-Line Deployment

```bash
cd cdk/lib/handlers/relay/event-relay && ./deploy.sh
```

## Set GCP Service Account Key

```bash
# Get key from GCP
gcloud iam service-accounts keys create sa-key.json \
    --iam-account=mvt-lambda@mvt-observer.iam.gserviceaccount.com

# Set in Lambda
aws lambda update-function-configuration \
    --function-name mvt-event-relay \
    --environment Variables="{GCP_PROJECT_ID=mvt-observer,GCP_PUBSUB_TOPIC=mvt-signals,GCP_SA_KEY_JSON='$(cat sa-key.json | jq -c .)'}"
```

## Verify Deployment

```bash
# Check Lambda status
aws lambda get-function --function-name mvt-event-relay | grep -A 5 FunctionArn

# Check event source
aws lambda list-event-source-mappings --function-name mvt-event-relay

# View logs
aws logs tail /aws/lambda/mvt-event-relay --follow

# Test by inserting a record
aws dynamodb put-item \
    --table-name mvt-dashboard-state \
    --item '{"dashboard":{"S":"test"},"panel":{"S":"test"},"value":{"N":"42"},"timestamp":{"S":"2026-03-20T14:30:00Z"}}'
```

## Troubleshoot

| Problem | Check |
|---------|-------|
| Lambda not invoked | DynamoDB Stream ARN + Event source mapping |
| GCP auth fails | `GCP_SA_KEY_JSON` env var + service account permissions |
| Message publish fails | CloudWatch logs + Pub/Sub topic exists |
| Timeout | Increase Lambda timeout to 60s |

```bash
# View all diagnostics
aws lambda get-function-configuration --function-name mvt-event-relay
aws dynamodb describe-table --table-name mvt-dashboard-state | grep StreamArn
aws logs get-log-events --log-group-name /aws/lambda/mvt-event-relay --log-stream-name $(aws logs describe-log-streams --log-group-name /aws/lambda/mvt-event-relay --order-by LastEventTime --descending --max-items 1 --query 'logStreams[0].logStreamName' --output text) --limit 50
```

## Key Files

- **Handler**: `event-relay/index.py` (11 KB)
- **Dependencies**: `event-relay/requirements.txt` (cryptography)
- **Deploy Script**: `event-relay/deploy.sh` (executable)
- **Docs**: `README.md` (comprehensive), `DEPLOYMENT_GUIDE.md` (step-by-step)

## Architecture in 30 Seconds

```
DynamoDB (mvt-dashboard-state) → Stream → mvt-event-relay Lambda
                                              ↓
                                    Format + GCP OAuth2
                                              ↓
                                    GCP Pub/Sub (mvt-signals)
```

## Lambda Details

| Property | Value |
|----------|-------|
| Function Name | mvt-event-relay |
| Runtime | Python 3.12 |
| Handler | index.handler |
| Memory | 256 MB |
| Timeout | 30 sec |
| Role | arn:aws:iam::572664774559:role/mvt-lambda-role |

## Environment Variables

| Variable | Value |
|----------|-------|
| `GCP_PROJECT_ID` | mvt-observer |
| `GCP_PUBSUB_TOPIC` | mvt-signals |
| `GCP_SA_KEY_JSON` | (from GCP service account) |

## Event Source

| Property | Value |
|----------|-------|
| Table | mvt-dashboard-state |
| Batch Size | 10 |
| Starting Position | LATEST |
| Parallelization | 2 |

## GCP Configuration

```bash
# Grant Pub/Sub Publisher role
gcloud projects add-iam-policy-binding mvt-observer \
    --member="serviceAccount:mvt-lambda@mvt-observer.iam.gserviceaccount.com" \
    --role="roles/pubsub.publisher"

# Verify topic exists
gcloud pubsub topics describe mvt-signals --project mvt-observer
```

## Common Commands

```bash
# Create function
aws lambda create-function --function-name mvt-event-relay --runtime python3.12 \
  --handler index.handler --role arn:aws:iam::572664774559:role/mvt-lambda-role \
  --zip-file fileb:///tmp/event-relay.zip --timeout 30 --memory-size 256

# Update code
aws lambda update-function-code --function-name mvt-event-relay \
  --zip-file fileb:///tmp/event-relay.zip

# Update config
aws lambda update-function-configuration --function-name mvt-event-relay \
  --timeout 60 --memory-size 512

# Add event source
aws lambda create-event-source-mapping \
  --event-source-arn arn:aws:dynamodb:us-east-1:572664774559:table/mvt-dashboard-state/stream/2026-03-20T... \
  --function-name mvt-event-relay --enabled --starting-position LATEST --batch-size 10

# View logs
aws logs tail /aws/lambda/mvt-event-relay --follow
aws logs filter-log-events --log-group-name /aws/lambda/mvt-event-relay --filter-pattern "ERROR"

# Get metrics
aws cloudwatch get-metric-statistics --namespace AWS/Lambda \
  --metric-name Invocations --dimensions Name=FunctionName,Value=mvt-event-relay \
  --start-time 2026-03-20T00:00:00Z --end-time 2026-03-20T23:59:59Z \
  --period 3600 --statistics Sum,Average
```

## Message Format (Published to Pub/Sub)

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
  "raw_data": {...}
}
```

## Performance

- **Latency**: ~2-5 seconds (DynamoDB → Pub/Sub)
- **Throughput**: ~1000 signals/min (depends on write rate)
- **Cost**: ~$0.20/month (for 10K signals/day)

## Monitoring

```bash
# CloudWatch Dashboard
aws cloudwatch put-metric-alarm --alarm-name mvt-event-relay-errors \
  --alarm-description "Alert on Lambda errors" \
  --metric-name Errors --namespace AWS/Lambda \
  --dimensions Name=FunctionName,Value=mvt-event-relay \
  --statistic Sum --period 300 --threshold 5 --comparison-operator GreaterThanThreshold

# View metrics
aws cloudwatch get-metric-statistics --namespace AWS/Lambda \
  --metric-name Errors --dimensions Name=FunctionName,Value=mvt-event-relay \
  --start-time 2026-03-20T00:00:00Z --end-time 2026-03-20T23:59:59Z \
  --period 3600 --statistics Sum
```

## Links

- **AWS Lambda**: https://console.aws.amazon.com/lambda/home#/functions/mvt-event-relay
- **CloudWatch Logs**: https://console.aws.amazon.com/logs/home?region=us-east-1#logStream:/aws/lambda/mvt-event-relay
- **DynamoDB**: https://console.aws.amazon.com/dynamodb/home?region=us-east-1#tables/mvt-dashboard-state
- **GCP Pub/Sub**: https://console.cloud.google.com/pubsub/topics/mvt-signals?project=mvt-observer
- **GCP IAM**: https://console.cloud.google.com/iam-admin/serviceaccounts?project=mvt-observer
