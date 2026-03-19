# CLAUDE.md — MVT Observatory Development Guide

## Project Structure

The MVT Observatory is a monorepo containing infrastructure-as-code (CDK + Terraform) and serverless functions for real-time global economic monitoring. Key directories:

```
mvt-trilogy/
├── cdk/                          # AWS CDK Infrastructure (TypeScript)
│   ├── bin/app.ts               # CDK app entry point
│   ├── lib/stacks/              # 7 main stacks
│   │   ├── storage-stack.ts      # DynamoDB, S3
│   │   ├── ingestion-stack.ts    # Event sources, SNS
│   │   ├── processing-stack.ts   # EventBridge, SQS
│   │   ├── realtime-stack.ts     # API Gateway WebSocket
│   │   ├── frontend-stack.ts     # CloudFront, static hosting
│   │   ├── monitoring-stack.ts   # CloudWatch alarms
│   │   └── app-stack.ts          # Main orchestrator
│   └── lib/handlers/             # 15+ Lambda function handlers
│       ├── ingestion/            # Data ingestion (FRED, Finnhub, etc.)
│       ├── processing/           # Data transformation & enrichment
│       ├── realtime/             # WebSocket connection managers
│       └── output/               # Telegram, dashboard updates
├── terraform/                    # GCP Infrastructure (HCL)
│   ├── main.tf                   # Main configuration
│   ├── variables.tf              # Input variables
│   ├── outputs.tf                # Output values
│   ├── backend.tf                # State configuration
│   └── modules/                  # GCP service modules
│       ├── pubsub/               # Pub/Sub topics & subscriptions
│       ├── firestore/            # Firestore collections
│       ├── functions/            # Cloud Functions
│       ├── hosting/              # Firebase Hosting
│       └── monitoring/           # Cloud Monitoring
├── telegram-bot/                 # Telegram bot handler
│   └── lambda_function.py        # AWS Lambda function
├── agents/                       # AI orchestration agents
├── .gitignore                    # Git ignore rules
├── .env.example                  # Environment template
├── CLAUDE.md                     # This file
└── README.md                     # Project documentation
```

## CRITICAL: Development Workflow (MUST FOLLOW)

### The Golden Rule

**ALL code changes MUST go through the GitHub repository. No direct deployments. No ad-hoc Lambda updates. No CLI-based infrastructure changes.**

Every change flows through the same process:
1. Feature branch in GitHub
2. Automated PR checks
3. Code review
4. Merge to main
5. Automated CI/CD deployment

This ensures auditability, reproducibility, and disaster recovery.

### For Every Code Change

Follow this exact process for all changes to code, infrastructure, or configuration:

1. **Create a feature branch** from `main`:
   ```bash
   git checkout main
   git pull origin main
   git checkout -b feature/MVT-XX-description
   ```

2. **Make changes** in the project files:
   - CDK stacks in `cdk/lib/stacks/`
   - Lambda handlers in `cdk/lib/handlers/`
   - Terraform modules in `terraform/modules/`
   - Configuration files in project root

3. **Commit with conventional commits** (follows spec at conventionalcommits.org):
   ```bash
   git add .
   git commit -m "feat(cdk): add alarm for DynamoDB throttles"
   git commit -m "fix(terraform): correct Pub/Sub topic IAM bindings"
   git commit -m "docs(claude.md): update handler documentation"
   ```

4. **Push to GitHub**:
   ```bash
   git push origin feature/MVT-XX-description
   ```

5. **Create a Pull Request** targeting `main`:
   - Title: `[MVT-XX] Brief description`
   - Description: Include what changed and why
   - Link related JIRA tickets if applicable

6. **PR checks must pass**:
   - Lint checks (ESLint for TS, Pylint for Python)
   - Unit tests (Jest for CDK, pytest for handlers)
   - CDK synthesis (generate CloudFormation)
   - Terraform validation and plan
   - Security scanning (SonarQube, Snyk)

7. **Merge to main** triggers automated deployment:
   - Staging deployment runs immediately
   - Canary deployment at 10% traffic for 5 minutes
   - Requires PO approval for production rollout
   - Automated rollback on CloudWatch alarms

### NEVER DO

These actions bypass version control and break disaster recovery:

- **Never deploy from local machine**: No `cdk deploy` or `terraform apply` from your CLI
- **Never update Lambda code via AWS Console**: No direct edits to function code
- **Never use AWS CLI for updates**: No `aws lambda update-function-code` or `aws apigateway`
- **Never upload to S3 manually**: No `aws s3 cp` for frontend assets
- **Never create AWS resources via CLI**: All infrastructure must be in CDK

If you make a local change, it exists nowhere else. If the infrastructure needs updating, define it in code first.

## CDK Stacks (AWS)

The CDK app synthesizes 7 main stacks for AWS infrastructure:

### 1. **StorageStack**
- **Resources**: DynamoDB tables (events, alerts, market data), S3 buckets (raw data, processed data, logs)
- **Location**: `cdk/lib/stacks/storage-stack.ts`
- **Key settings**: Point-in-time recovery, encryption, TTL for old records

### 2. **IngestionStack**
- **Resources**: SNS topics (market data, events, risk), SQS queues for buffering
- **Location**: `cdk/lib/stacks/ingestion-stack.ts`
- **Key settings**: Dead-letter queues, message retention

### 3. **ProcessingStack**
- **Resources**: EventBridge rules (schedule, event routing), Lambda permissions
- **Location**: `cdk/lib/stacks/processing-stack.ts`
- **Key settings**: Error handling, retry policies

### 4. **RealtimeStack**
- **Resources**: API Gateway WebSocket API, IAM roles for WebSocket handlers
- **Location**: `cdk/lib/stacks/realtime-stack.ts`
- **Key settings**: Connection management, authorization

### 5. **FrontendStack**
- **Resources**: CloudFront distribution, S3 static hosting, ACM certificates
- **Location**: `cdk/lib/stacks/frontend-stack.ts`
- **Key settings**: Cache policies, origin access identity

### 6. **MonitoringStack**
- **Resources**: CloudWatch alarms, SNS notifications, custom metrics
- **Location**: `cdk/lib/stacks/monitoring-stack.ts`
- **Key settings**: Alarm thresholds, notification targets

### 7. **AppStack** (Orchestrator)
- **Location**: `cdk/bin/app.ts`
- **Responsibility**: Instantiates all stacks in dependency order

## Terraform Modules (GCP)

Terraform provisions GCP services organized into reusable modules:

### **pubsub Module**
- Pub/Sub topics for event streaming
- Topic subscriptions with push and pull modes
- Dead-letter topics for failed messages
- Location: `terraform/modules/pubsub/`

### **firestore Module**
- Firestore collections for market state, user preferences, alert history
- Security rules and index definitions
- Location: `terraform/modules/firestore/`

### **functions Module**
- Cloud Functions for data transformation
- Trigger configuration (Pub/Sub, HTTP, Scheduled)
- Runtime and memory settings
- Location: `terraform/modules/functions/`

### **hosting Module**
- Firebase Hosting for web dashboard
- Rewrite rules and headers configuration
- CDN caching policies
- Location: `terraform/modules/hosting/`

### **monitoring Module**
- Cloud Monitoring dashboards and alerts
- Log-based metrics
- Uptime checks
- Location: `terraform/modules/monitoring/`

## Lambda Handlers

15+ Lambda functions distributed across ingestion, processing, and output stages:

### Ingestion Handlers
- `fred-poller`: Poll Federal Reserve API for economic data
- `finnhub-connector`: Stream stock market data from Finnhub
- `yfinance-streamer`: Collect historical market data
- `gdelt-querier`: Fetch GDELT events for geopolitical risk
- `worldbank-poller`: Retrieve World Bank development indicators
- `trends-poller`: Monitor Google Trends for sentiment signals

### Processing Handlers
- `sentiment-aggregator`: NLP analysis of GDELT events and Twitter
- `contagion-modeler`: Model systemic risk propagation
- `inequality-scorer`: Calculate economic inequality metrics
- `vulnerability-composite`: Synthesize vulnerability index
- `cross-dashboard-router`: Route processed data to outputs

### Output Handlers
- `telegram-notifier`: Send alerts via Telegram bot
- `dashboard-updater`: Push real-time updates to frontend via WebSocket

### Realtime Handlers
- `ws-connect`: Manage WebSocket client connections
- `ws-disconnect`: Clean up disconnected clients
- `ws-broadcast`: Distribute updates to connected clients

All handlers are located in `cdk/lib/handlers/` with TypeScript or Python source code.

## Testing

### CDK Tests (TypeScript)
```bash
cd cdk
npm test
# Runs Jest on stack and construct definitions
```

### Handler Tests (Python)
```bash
cd cdk/lib/handlers
pytest
# Unit tests for Lambda function logic
```

### Terraform Validation
```bash
cd terraform
terraform init
terraform plan
# Validates syntax and checks GCP resource definitions
```

### Integration Tests (CI/CD)
Automated after merge to main:
- Deploy to staging environment
- Run smoke tests against live endpoints
- Canary deployment at 10% traffic
- Production rollout with approval gate

## Environment Variables

All runtime configuration goes in `.env` files or AWS Secrets Manager / GCP Secret Manager.

**See `.env.example` for the template.**

Required variables:
- **AWS**: Access keys, region, account ID
- **GCP**: Project ID, credentials path
- **Telegram**: Bot token, chat ID for notifications
- **JIRA**: Base URL, email, API token, project key
- **Data APIs**: FRED, Finnhub API keys
- **Dashboard**: CloudFront distribution URL

### Secrets Management

**NEVER commit `.env` files with real secrets to Git.**

For local development:
1. Copy `.env.example` to `.env`
2. Fill in your personal credentials
3. `.env` is git-ignored

For CI/CD (GitHub Actions):
- Use GitHub Secrets to store sensitive values
- Reference via `${{ secrets.SECRET_NAME }}`

For runtime (Lambda functions, Cloud Functions):
- Store in AWS Secrets Manager or GCP Secret Manager
- Reference by ARN or secret name in environment variables
- Lambda execution role must have `secretsmanager:GetSecretValue` permission

## CI/CD Pipeline

The project includes GitHub Actions workflows that automate deployment:

### pr-checks.yml (on Pull Request)
- Lint TypeScript and Python code
- Run unit tests
- Synthesize CDK to CloudFormation
- Validate Terraform
- Security scanning (SonarQube, Snyk)
- **Blocks merge if any check fails**

### deploy-staging.yml (on Merge to Main)
- CDK deploy to AWS staging account
- Terraform apply to GCP staging project
- Run integration tests against staging endpoints
- **If tests fail, deployment stops; rollback is automatic**

### deploy-canary.yml (on Staging Success)
- Deploy to production account at 10% traffic (canary)
- Monitor CloudWatch alarms for 5 minutes
- If error rate exceeds threshold, automatic rollback
- **Otherwise, waits for PO approval to proceed**

### deploy-production.yml (on Canary + Approval)
- Deploy to production account at 100% traffic
- Update CloudFront cache
- Run production smoke tests
- Send deployment notification

## Code Guidelines

### CDK (TypeScript)

```typescript
// Use construct naming conventions
export class StorageStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Define resources with descriptive IDs
    const table = new dynamodb.Table(this, 'EventsTable', {
      partitionKey: { name: 'eventId', type: dynamodb.AttributeType.STRING },
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      pointInTimeRecovery: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN, // For data protection
    });
  }
}
```

### Lambda Handlers (Python)

```python
import json
import os
from aws_lambda_powertools import Logger, Tracer

logger = Logger()
tracer = Tracer()

@logger.inject_lambda_context
@tracer.capture_lambda_handler
def lambda_handler(event, context):
    """Handler must be idempotent and handle failures gracefully."""
    try:
        # Process event
        result = process_data(event)
        return {
            'statusCode': 200,
            'body': json.dumps(result)
        }
    except Exception as e:
        logger.exception(f"Error processing event: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
```

### Terraform (HCL)

```hcl
# Use descriptive variable names
variable "alert_threshold" {
  description = "DynamoDB throttle threshold in %"
  type        = number
  default     = 80
}

# Use locals for computed values
locals {
  function_name = "${var.environment}-${var.function_type}"
}

# Define data sources for external references
data "google_client_config" "current" {}
```

## Troubleshooting

### Common Issues

**CDK synthesis fails**
- Run `npm install` in `cdk/` directory
- Check TypeScript compilation: `npx tsc --noEmit`
- Verify AWS credentials: `aws sts get-caller-identity`

**Lambda function fails in execution**
- Check CloudWatch logs: `aws logs tail /aws/lambda/<function-name> --follow`
- Verify IAM execution role has required permissions
- Test locally with `sam local invoke`

**Terraform plan shows unexpected changes**
- Run `terraform refresh` to sync state
- Check for AWS console changes (conflicts)
- Verify variable overrides in `terraform.tfvars`

**Webhook not receiving events**
- Confirm SNS/SQS subscription is active
- Check DLQ for failed messages
- Verify Lambda execution role has Invoke permission

### Getting Help

For architecture questions, consult:
- `/README.md` for project overview
- CDK construct documentation: https://docs.aws.amazon.com/cdk/latest/guide/
- Terraform GCP provider docs: https://registry.terraform.io/providers/hashicorp/google/latest/docs

## Summary: The Three Rules

1. **All changes go through GitHub**: Create branch → commit → PR → review → merge
2. **Infrastructure is code**: Define everything in CDK (AWS) or Terraform (GCP), not CLI
3. **Secrets stay secret**: Use Secrets Manager, GitHub Secrets, never commit .env with real values
