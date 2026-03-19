# MVT Trilogy - GCP Infrastructure & CI/CD Summary

## Overview
Complete Terraform modules for Google Cloud Platform infrastructure and GitHub Actions CI/CD pipelines for the Macro Vulnerability Trilogy project.

## Terraform Structure

### Core Configuration Files
- **terraform/main.tf** - Provider configuration and module orchestration
- **terraform/variables.tf** - Input variables (project_id, region, environment, AWS endpoint)
- **terraform/outputs.tf** - Output values for all created resources
- **terraform/backend.tf** - GCS state backend configuration

### Pub/Sub Module (terraform/modules/pubsub/)
Creates 3 publish/subscribe topics with subscriptions and dead-letter queues:
- **mvt-signals**: Cross-cloud event relay from AWS EventBridge
- **mvt-alerts**: High-priority alerts for Telegram notifications
- **mvt-analytics**: Events destined for BigQuery analytics

Each topic includes:
- Pull subscriptions with retry policies
- Dead-letter topics (max 5 delivery attempts)
- Message storage policy (US-central1)

### BigQuery Module (terraform/modules/bigquery/)
**Dataset:** mvt_analytics with 4 tables

**Tables:**
1. **inequality_signals** - Gini coefficients, poverty rates, inequality metrics
   - Partitioned by date, clustered by source
   - Fields: date, source, metric, value, region, raw_json, ingested_at

2. **sentiment_events** - Protest, riot, strike, boycott events
   - Partitioned by date, clustered by event_type
   - Fields: date, event_type, severity, source, countries, raw_json, ingested_at

3. **sovereign_indicators** - State fragility, conflict intensity, risk scores
   - Partitioned by date, clustered by country
   - Fields: date, country, indicator, value, risk_score, ingested_at

4. **gdelt_events** - GDELT event data with geographic coordinates
   - Partitioned by date, clustered by event_code
   - Fields: date, event_code, event_root_code, actors, Goldstein scale, mentions, sources, articles, tone, geo location

**Saved Queries:**
- **gdelt_extraction**: Extracts GDELT conflict events (root codes 14-20) with >5 mentions
- **daily_correlation**: Pearson correlation between inequality distress and sentiment triggers over 90 days

### Cloud Functions Module (terraform/modules/functions/)
**Gen2 Python 3.12 Functions** (256MB memory, 60s timeout, 0-5 instances)

1. **signal-processor**
   - Triggered by: mvt-signals Pub/Sub topic
   - Writes to: Firestore (dashboard_signals/{dashboard}/{signal_id})
   - Updates latest signal document per dashboard

2. **analytics-writer**
   - Triggered by: mvt-analytics Pub/Sub topic
   - Routes to appropriate BigQuery table based on event_type
   - Performs schema validation before streaming insert

3. **gdelt-extractor**
   - Triggered by: Cloud Scheduler (every 15 minutes)
   - Queries: GDELT BigQuery public dataset
   - Publishes results to mvt-signals topic for AWS relay
   - Tracks bytes processed for free tier monitoring

**IAM Setup:**
- Service account with Pub/Sub editor, BigQuery editor, Firestore user, Cloud Run invoker roles
- Cloud Scheduler creates HTTP POST requests with OIDC token authentication

### Firestore Module (terraform/modules/firestore/)
**Database:** Native mode in nam5 region

**Collections:**
- **dashboard_signals**: Real-time signal events (nested by dashboard_id)
- **dashboard_state**: UI configuration and state
- **processed_analytics**: Aggregated analytics and computed metrics

**Indexes:**
- dashboard_signals queries (dashboard_id + timestamp DESC)
- sentiment event correlation (event_type + date DESC)

### Firebase Hosting Module (terraform/modules/hosting/)
**Site:** mvt-analytics-dashboard

**Features:**
- SPA rewrite rules (/** → /index.html)
- Cache-Control headers:
  - HTML: 300 seconds, must-revalidate
  - CSS/JS/images: 31536000 seconds (1 year), immutable
- Security headers (CSP, X-Frame-Options, X-Content-Type-Options)
- Automatic HTTPS with HTTP/2

### Monitoring Module (terraform/modules/monitoring/)
**Dashboard:** MVT Observatory - GCP

**Metrics:**
- Cloud Function execution times and error rates
- Pub/Sub subscription backlog (num_unacked_messages)
- BigQuery streaming insert errors

**Alert Policies:**
1. Cloud Function error rate > 5%
2. Pub/Sub dead-letter messages > 0
3. Firestore quota usage > 80%

**Notification Channel:** Email alerts to alerts@mvt-observatory.local

## GitHub Actions CI/CD

### 1. PR Checks (.github/workflows/pr-checks.yml)
Triggered on pull_request to main/develop

**Jobs:**
- **Lint**: ESLint + Flake8
- **Type Check**: TypeScript + mypy
- **Unit Tests**: Jest + pytest with coverage reporting
- **Validate IaC**: Terraform fmt/validate + CDK synth
- **Security Scan**: detect-secrets + bandit

### 2. Deploy to Staging (.github/workflows/deploy-staging.yml)
Triggered on push to main

**Jobs:**
1. **Test**: Unit tests + build artifacts
2. **Deploy GCP**: Terraform plan/apply to staging
   - Environment: GCP project staging
   - Outputs: Pub/Sub topics, BigQuery dataset, Hosting URL
3. **Deploy AWS**: CDK synth + deploy
4. **Integration Tests**: Against staging endpoints
5. **Notify Results**: JIRA comment + Telegram message

### 3. Deploy Canary (.github/workflows/deploy-canary.yml)
Triggered on successful staging deployment

**Jobs:**
1. **Canary Checks**:
   - Configure Lambda alias: 10% traffic to new version
   - Configure Cloud Run: 10% traffic to new revision
   - Wait 5 minutes for stabilization
   - Check CloudWatch metrics (error rate, latency)
   - Check Cloud Monitoring metrics
   - Rollback if unhealthy
   - Trigger production workflow if healthy

2. **Notifications**: Telegram status + conditional production trigger

### 4. Deploy Production (.github/workflows/deploy-production.yml)
Triggered by workflow_dispatch with confirmation

**Requirements:**
- GitHub environment protection rules (production)
- Explicit "yes" confirmation in inputs

**Jobs:**
1. **Authorize**: Environment verification
2. **Deploy AWS**: 
   - Promote Lambda alias to 100% traffic
   - CDK deploy to production
3. **Deploy GCP**:
   - Terraform plan/apply to production
   - Cloud Run traffic to 100%
4. **Smoke Tests**: Against production endpoints
5. **Notify Completion**:
   - JIRA story update with evidence
   - Telegram notification
   - GitHub Release with notes

## Environment Configuration

### Development (environments/dev.tfvars)
```
project_id    = "mvt-trilogy-dev"
region        = "us-central1"
environment   = "dev"
```

### Staging (environments/staging.tfvars - create separately)
```
project_id    = "mvt-trilogy-staging"
region        = "us-central1"
environment   = "staging"
```

### Production (environments/prod.tfvars - create separately)
```
project_id    = "mvt-trilogy-prod"
region        = "us-central1"
environment   = "production"
```

## Required GitHub Secrets

### AWS
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`

### GCP
- `GCP_WORKLOAD_IDENTITY_PROVIDER` (for keyless auth)
- `GCP_SERVICE_ACCOUNT`
- `GCP_PROJECT_ID_STAGING`
- `GCP_PROJECT_ID_PROD`
- `GCP_TERRAFORM_BUCKET` (for state)

### Integration
- `JIRA_API_TOKEN`
- `JIRA_ISSUE_URL`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

### AWS EventBridge Relay
- `AWS_EVENT_RELAY_ENDPOINT`

## Deployment Flow

```
PR → pr-checks.yml
     ↓
merge to main
     ↓
deploy-staging.yml → GCP Terraform + AWS CDK → Integration Tests
     ↓
workflow_run: staging success
     ↓
deploy-canary.yml → 10% traffic canary → health checks
     ↓ (if healthy)
     ↓
deploy-production.yml (manual dispatch)
     ↓ (with confirmation)
     ↓
GCP Terraform prod + AWS CDK prod → 100% traffic
     ↓
Smoke Tests
     ↓
GitHub Release + Telegram notification
```

## Key Features

✅ Multi-environment infrastructure (dev/staging/prod)
✅ Cross-cloud event relay (AWS → GCP Pub/Sub)
✅ Real-time Firestore dashboard state
✅ Partitioned & clustered BigQuery tables for analytics
✅ Canary deployment with automatic health checks
✅ Comprehensive monitoring with Cloud Monitoring dashboard
✅ Rollback capability on canary failure
✅ GDELT event enrichment every 15 minutes
✅ Correlation analysis between inequality and sentiment
✅ Firebase Hosting with SPA routing and security headers
✅ GitHub Actions with proper concurrency controls
✅ JIRA integration for deployment evidence
✅ Telegram notifications for critical events

## File Structure

```
mvt-trilogy/
├── terraform/
│   ├── main.tf
│   ├── variables.tf
│   ├── outputs.tf
│   ├── backend.tf
│   └── modules/
│       ├── pubsub/main.tf
│       ├── bigquery/
│       │   ├── main.tf
│       │   ├── schemas/
│       │   │   ├── inequality_signals.json
│       │   │   ├── sentiment_events.json
│       │   │   ├── sovereign_indicators.json
│       │   │   └── gdelt_events.json
│       │   └── queries/
│       │       ├── gdelt_extraction.sql
│       │       └── daily_correlation.sql
│       ├── firestore/main.tf
│       ├── functions/
│       │   ├── main.tf
│       │   └── src/
│       │       ├── signal-processor/main.py
│       │       ├── analytics-writer/main.py
│       │       └── gdelt-extractor/main.py
│       ├── hosting/main.tf
│       └── monitoring/main.tf
├── environments/
│   └── dev.tfvars
└── .github/workflows/
    ├── pr-checks.yml
    ├── deploy-staging.yml
    ├── deploy-canary.yml
    └── deploy-production.yml
```
