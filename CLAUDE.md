# CLAUDE.md — MVT Trilogy Development Guide

This is the **definitive reference document** for the MVT Trilogy project. Read this first before working with the codebase.

---

## Section 1: Project Overview

**MVT Observatory** is a production-grade sprint automation platform that orchestrates story execution across specialized AI subagents with full JIRA integration, GitHub-native CI/CD, and Telegram command interface. It monitors global macro-economic vulnerability indicators through real-time data ingestion (FRED, Finnhub, GDELT), streams events via AWS EventBridge and GCP Pub/Sub, performs AI-driven sentiment analysis and contagion modeling, and delivers alerts via Telegram. The system implements canary deployments, automatic health checks, and multi-cloud (AWS + GCP) infrastructure-as-code.

**Live URLs:**
- Dashboard: https://d2p9otbgwjwwuv.cloudfront.net
- Telegram Bot: @mvt_observatory_bot (ID: 8632265408)
- JIRA: https://themarketgraph.atlassian.net/browse/MVT
- Confluence: https://themarketgraph.atlassian.net/wiki/spaces/MVTCO
- GitHub: https://github.com/vishalbk/mvt-trilogy

---

## Section 2: Project Structure

Complete directory tree with descriptions:

```
mvt-trilogy/
├── .github/workflows/              # CI/CD pipelines (GitHub Actions)
│   ├── pr-checks.yml               # Lint, test, synth on PRs (blocks merge if fails)
│   ├── deploy-staging.yml          # CDK + Terraform deploy on merge to main
│   ├── deploy-canary.yml           # 10% traffic canary with CloudWatch monitoring
│   └── deploy-production.yml       # Manual dispatch (requires confirmation)
│
├── agents/                         # AI agent orchestration framework
│   ├── __init__.py                 # Package marker
│   ├── config.py                   # Shared configuration (JIRA, GitHub, Telegram, Claude)
│   ├── orchestrator.py             # Master orchestrator (sprint coordination, dependency resolution)
│   └── subagents/                  # Specialized agents
│       ├── __init__.py
│       ├── code_agent.py           # Code generation (Lambda, React, CDK, specs)
│       ├── test_agent.py           # Testing & security scanning
│       ├── deploy_agent.py         # Deployment orchestration & monitoring
│       └── docs_agent.py           # Confluence documentation generation
│
├── cdk/                            # AWS CDK Infrastructure (TypeScript + Python)
│   ├── bin/
│   │   └── app.ts                  # CDK app entry point (7 stack composition)
│   ├── lib/
│   │   ├── stacks/                 # 7 CloudFormation stacks
│   │   │   ├── storage-stack.ts    # DynamoDB tables + S3 buckets
│   │   │   ├── ingestion-stack.ts  # SNS topics + SQS queues
│   │   │   ├── processing-stack.ts # EventBridge rules + Lambda permissions
│   │   │   ├── realtime-stack.ts   # API Gateway WebSocket
│   │   │   ├── frontend-stack.ts   # CloudFront + static S3 hosting
│   │   │   ├── monitoring-stack.ts # CloudWatch alarms + SNS
│   │   │   └── events-stack.ts     # Additional event routing
│   │   └── handlers/               # 15+ Lambda function code
│   │       ├── ingestion/          # Data ingestion (FRED, Finnhub, GDELT, pytrends, etc.)
│   │       ├── processing/         # Sentiment, contagion, inequality, vulnerability scoring
│   │       ├── realtime/           # WebSocket connect/disconnect/broadcast
│   │       └── output/             # Telegram notifier, dashboard updater
│   ├── package.json                # Node.js dependencies (aws-cdk, cdk-assets)
│   └── tsconfig.json               # TypeScript configuration
│
├── terraform/                      # GCP Terraform Infrastructure (HCL)
│   ├── main.tf                     # Provider + module composition
│   ├── variables.tf                # Input variables (project_id, region, environment)
│   ├── outputs.tf                  # Output values (topic names, URLs, etc.)
│   ├── backend.tf                  # GCS state backend
│   └── modules/                    # Reusable GCP service modules (26 resources total)
│       ├── pubsub/                 # 6 Pub/Sub topics + subscriptions + DLQ
│       ├── bigquery/               # Dataset + 4 partitioned/clustered tables
│       ├── firestore/              # Native mode database (3 collections)
│       ├── functions/              # 3 Cloud Functions (Gen2)
│       ├── hosting/                # Firebase Hosting (SPA with security headers)
│       └── monitoring/             # Cloud Monitoring dashboard + alert policies
│
├── telegram-bot/                   # Telegram webhook handler
│   └── lambda_function.py          # AWS Lambda webhook (HTML parse mode, JIRA integration)
│                                    # Deployed to: API Gateway endpoint
│
├── frontend/                       # Dashboard (static)
│   └── dist/
│       └── index.html              # Self-contained Chart.js dashboard (CloudFront served)
│
├── environments/                   # Terraform variable files
│   └── dev.tfvars                  # Development environment configuration
│
├── CLAUDE.md                       # THIS FILE (read first)
├── README.md                       # Project overview
├── ARCHITECTURE.md                 # Detailed architecture diagrams
├── INFRASTRUCTURE_SUMMARY.md       # Complete Terraform + CI/CD breakdown
├── DEPLOYMENT_GUIDE.md             # Manual deployment instructions
├── .env.example                    # Required environment variables template
├── .gitignore                      # Git ignore rules (excludes .env, node_modules, etc.)
└── [git history]                   # Full commit history in .git/
```

---

## Section 3: THE GOLDEN RULE — Development Workflow

### ⚠️ CRITICAL: ALL CODE CHANGES MUST GO THROUGH GITHUB

**No exceptions. No direct deployments. No ad-hoc CLI changes.**

This applies to **human developers AND AI agents equally**.

Every change—whether code, infrastructure, or configuration—flows through the exact same process:

### Step-by-Step Workflow

1. **Create a feature branch from `main`:**
   ```bash
   git checkout main
   git pull origin main
   git checkout -b feature/MVT-XX-description
   ```
   - Branch name format: `feature/MVT-{STORY_ID}-{slug}`
   - Example: `feature/MVT-101-add-lambda-handler`

2. **Make changes in project files:**
   - CDK stacks in `cdk/lib/stacks/*.ts`
   - Lambda handlers in `cdk/lib/handlers/*/`
   - Terraform modules in `terraform/modules/*/`
   - Configuration files in project root
   - Agent code in `agents/**/*.py`

3. **Stage and commit with conventional commits:**
   ```bash
   git add .
   git commit -m "feat(scope): description"
   git commit -m "fix(terraform): description"
   git commit -m "docs(claude.md): description"
   ```
   Format: `type(scope): message` (see https://conventionalcommits.org)

4. **Push to GitHub:**
   ```bash
   git push origin feature/MVT-XX-description
   ```

5. **Create a Pull Request (via GitHub UI or API):**
   - Title: `[MVT-XX] Brief description`
   - Description: Include what changed, why, and link JIRA tickets
   - Target: `main` branch

6. **PR checks must pass** (automatically triggered):
   - **Lint**: ESLint (TypeScript), Flake8 (Python)
   - **Tests**: Jest (CDK), pytest (handlers)
   - **CDK Synth**: Generate CloudFormation
   - **Terraform**: `terraform fmt` and `terraform validate`
   - **Security**: Snyk, detect-secrets, bandit
   - **Coverage**: Must meet project thresholds

7. **Code review & merge:**
   - Wait for at least one approval
   - All checks must pass
   - Merge to `main` (via UI, not command line)

8. **Automatic deployment triggered:**
   - Merge to `main` → `deploy-staging.yml` runs automatically
   - Staging validates successfully → `deploy-canary.yml` runs automatically
   - Canary healthy → `deploy-production.yml` awaits approval (via Telegram)
   - PO approves → automatic production deployment at 100% traffic

### Why This Matters

- **Auditability**: Every change is tracked in Git
- **Reproducibility**: Exact state of code at any time
- **Disaster Recovery**: Can revert any commit instantly
- **Team Visibility**: All PRs are visible to the team
- **Automated Testing**: No human error in deployment steps
- **Compliance**: Audit trail for regulatory requirements

---

## Section 4: What NEVER to do

These actions bypass version control and break disaster recovery:

### ❌ NEVER deploy from local machine

```bash
# WRONG
cdk deploy --stage production
terraform apply --var-file=prod.tfvars
aws lambda update-function-code --function-name my-function
aws s3 cp dist/index.html s3://bucket/
```

**Why**: Local deployments are invisible to Git, making rollback impossible.

### ❌ NEVER edit Lambda code in AWS Console

Don't use the inline editor or code upload in the AWS Lambda console. All code must be in the repository.

### ❌ NEVER use AWS CLI to create/modify resources

```bash
# WRONG
aws dynamodb create-table ...
aws apigateway create-rest-api ...
aws sns create-topic ...
```

**Why**: Resources created outside of CDK/Terraform will drift and may be lost.

### ❌ NEVER commit secrets or .env files

```bash
# WRONG
git add .env
git commit -m "add production secrets"
git push
```

**Why**: Secrets exposed in Git are **permanently compromised**, even if deleted later.

**Correct approach:**
- Use GitHub Secrets (for CI/CD)
- Use AWS Secrets Manager (for runtime)
- Use GCP Secret Manager (for GCP)
- Store in `.env` locally (git-ignored)

### ❌ NEVER directly modify state files

```bash
# WRONG
# Manually editing terraform.tfstate in GCS
# Manually editing AWS CloudFormation stack
```

**Why**: State corruption can make infrastructure unrecoverable.

---

## Section 5: AWS Infrastructure (CDK)

**Account ID**: (Retrieved from AWS_ACCOUNT_ID secret)
**Region**: us-east-1

### 7 CloudFormation Stacks

1. **StorageStack**
   - DynamoDB: `mvt-events`, `mvt-alerts`, `mvt-market-data`, `mvt-conversation-state`
   - S3: `mvt-raw-data`, `mvt-processed-data`, `mvt-logs`
   - Encryption: AWS-managed (at-rest)
   - TTL: 90 days for time-series data

2. **IngestionStack**
   - SNS: `mvt-market-data-topic`, `mvt-events-topic`, `mvt-risk-topic`
   - SQS: `mvt-event-queue`, `mvt-alert-queue` (with DLQ)
   - Message retention: 14 days

3. **ProcessingStack**
   - EventBridge bus: `mvt-bus`
   - EventBridge rules: Schedule-based ingestion triggers
   - Lambda permissions for cross-service invocation

4. **RealtimeStack**
   - API Gateway WebSocket API endpoint: `wss://api-id.execute-api.us-east-1.amazonaws.com/prod`
   - Connection management table: `mvt-connections`
   - IAM roles for WebSocket Lambda handlers

5. **FrontendStack**
   - CloudFront distribution ID: (from outputs)
   - S3 bucket: `mvt-frontend-bucket`
   - ACM certificate: *.example.com (HTTPS enabled)
   - Origin access identity for S3

6. **MonitoringStack**
   - CloudWatch alarms (15+): Lambda errors, DynamoDB throttles, SQS backlog
   - SNS topic for alarms: `mvt-alarms`
   - Custom metrics: Vulnerability index, contagion score, sentiment delta

7. **EventsStack**
   - Additional event routing and transformation

### 15+ Lambda Functions (by category)

**Ingestion (6 functions):**
- `fred-poller`: FRED API → DynamoDB (hourly schedule)
- `finnhub-connector`: Finnhub WebSocket → Kinesis (real-time)
- `yfinance-streamer`: Yahoo Finance → S3 (5-min interval)
- `gdelt-querier`: GDELT v2 API → SNS (15-min interval)
- `worldbank-poller`: World Bank API → DynamoDB (daily)
- `trends-poller`: Google Trends → S3 (4-hour interval)

**Processing (5 functions):**
- `sentiment-aggregator`: GDELT + Twitter NLP → DynamoDB
- `contagion-modeler`: Cross-asset correlation → DynamoDB
- `inequality-scorer`: World Bank data → vulnerability metric
- `vulnerability-composite`: Synthesize final index → DynamoDB
- `cross-dashboard-router`: Distribute to outputs → SNS/WebSocket

**Output (2 functions):**
- `telegram-notifier`: Alert messages → Telegram API
- `dashboard-updater`: Real-time updates → WebSocket clients

**Realtime (2 functions):**
- `ws-connect`: Manage client connections → DynamoDB
- `ws-disconnect`: Cleanup disconnected clients → DynamoDB
- `ws-broadcast`: Distribute updates to all clients

**Memory**: 512 MB (standard), 1024 MB (processing-heavy)
**Timeout**: 60 seconds (standard), 300 seconds (batch processing)
**Concurrency**: Reserved = 10 per function (production)

### 4 DynamoDB Tables

1. **mvt-events** (Partition: eventId | Sort: timestamp)
   - TTL: 90 days | Encryption: AWS-managed

2. **mvt-alerts** (Partition: alertId | Sort: createdAt)
   - GSI: by severity, by country

3. **mvt-market-data** (Partition: assetId | Sort: timestamp)
   - TTL: 365 days | Compression: GZip for JSON

4. **mvt-conversation-state** (Partition: conversationId | Sort: messageId)
   - Used by Telegram bot for session tracking

### Other AWS Resources

- **EventBridge bus**: `mvt-bus` (central event routing)
- **SQS queues**: 2 main + 2 DLQ (max 14-day retention)
- **API Gateway WebSocket**: Endpoint for real-time client connections
- **CloudFront distribution**: DNS: `d2p9otbgwjwwuv.cloudfront.net`
- **S3 buckets**: 3 buckets (raw data, processed, logs)
- **IAM roles**: Lambda execution role, API Gateway role, EventBridge role
- **CloudWatch**: 15+ custom alarms + dashboards
- **Secrets Manager**: For API keys at runtime

---

## Section 6: GCP Infrastructure (Terraform)

**Project ID**: mvt-observer
**Region**: us-central1
**Terraform state**: GCS bucket (backend.tf)
**26 total resources** across 5 modules

### BigQuery Dataset & 4 Tables

**Dataset**: `mvt_analytics` (location: US-central1)

**Tables:**

1. **inequality_signals**
   - Partitioned by: date | Clustered by: source
   - Schema: date, source, metric, value, region, raw_json, ingested_at
   - TTL: 730 days (2 years)

2. **sentiment_events**
   - Partitioned by: date | Clustered by: event_type
   - Schema: date, event_type, severity, source, countries, raw_json, ingested_at
   - TTL: 365 days

3. **sovereign_indicators**
   - Partitioned by: date | Clustered by: country
   - Schema: date, country, indicator, value, risk_score, ingested_at
   - TTL: 730 days

4. **gdelt_events**
   - Partitioned by: date | Clustered by: event_code
   - Schema: date, event_code, event_root_code, actors, goldstein_scale, mentions, sources, articles, tone, geo_location
   - TTL: 1825 days (5 years, for historical analysis)

**Saved Queries** (2):
- `gdelt_extraction`: Extract GDELT conflict events (codes 14-20) with >5 mentions
- `daily_correlation`: Pearson correlation between inequality and sentiment (90-day window)

### 6 Pub/Sub Topics & Subscriptions

1. **mvt-signals**: Cross-cloud event relay from AWS EventBridge
2. **mvt-alerts**: High-priority alerts for Telegram
3. **mvt-analytics**: Events for BigQuery streaming
4. **mvt-gdelt**: GDELT extraction events
5. **mvt-firestore-sync**: Real-time Firestore updates
6. **mvt-dlq**: Dead-letter queue for failed messages (max 5 delivery attempts)

**Features**:
- Pull subscriptions with retry policies
- Message ordering enabled (FIFO)
- Dead-letter topics for each main topic
- Message storage in us-central1

### 3 Cloud Functions (Gen2, Python 3.12)

**Memory**: 256 MB | **Timeout**: 60 seconds | **Concurrency**: 0-5 instances

1. **signal-processor**
   - Trigger: mvt-signals Pub/Sub
   - Writes to: Firestore `dashboard_signals/{dashboard}/{signal_id}`
   - Updates: Latest signal doc per dashboard

2. **analytics-writer**
   - Trigger: mvt-analytics Pub/Sub
   - Routes: Events to appropriate BigQuery table
   - Validation: Schema validation before insert

3. **gdelt-extractor**
   - Trigger: Cloud Scheduler (every 15 minutes)
   - Queries: GDELT public dataset in BigQuery
   - Publishes: Results to mvt-signals topic

**IAM Service Account**: Custom role with:
- Pub/Sub Editor
- BigQuery Editor
- Firestore User
- Cloud Run Invoker

### Firestore Database

**Type**: Native mode | **Region**: nam5 (North America)

**Collections** (3):
1. **dashboard_signals**: Real-time events (nested by dashboard_id)
2. **dashboard_state**: UI configuration per dashboard
3. **processed_analytics**: Aggregated metrics

**Indexes** (2):
- `dashboard_signals`: (dashboard_id, timestamp DESC)
- `sentiment_events`: (event_type, date DESC)

### 2 GCS Buckets

1. **mvt-terraform-state**: Terraform state backend
2. **mvt-function-source**: Cloud Functions source code

### Firebase Hosting

**Site**: mvt-analytics-dashboard
**Features**:
- SPA rewrite rules: `/** → /index.html`
- Cache headers: HTML (300s), CSS/JS (1 year immutable)
- Security headers: CSP, X-Frame-Options, X-Content-Type-Options
- Auto HTTPS + HTTP/2

### Cloud Monitoring

**Dashboard**: MVT Observatory - GCP

**Metrics**:
- Cloud Function execution times + error rates
- Pub/Sub subscription backlog (unacked messages)
- BigQuery streaming insert errors

**Alert Policies** (3):
1. Cloud Function error rate > 5%
2. Pub/Sub dead-letter messages > 0
3. Firestore quota usage > 80%

**Notification Channel**: Email alerts to alerts@mvt-observatory.local

---

## Section 7: External Integrations

### Telegram Bot

**Name**: @mvt_observatory_bot
**ID**: 8632265408
**Token**: (stored in GitHub Secrets as TELEGRAM_BOT_TOKEN)

**Webhook URL**: `https://4a1rl4nacb.execute-api.us-east-1.amazonaws.com/prod/webhook`

**Handler**: `telegram-bot/lambda_function.py`
- Uses HTML parse_mode (no Markdown escaping issues)
- JIRA integration for epic creation
- Dashboard URL embedding

**Commands**:
- `/brainstorm [topic]`: Generate ideas using Claude
- `/create_epic [summary]`: Create JIRA epic
- `/kickoff_sprint`: Start sprint orchestration
- `/sprint_status`: Get board status
- `/approve_release`: Approve production deployment
- `/sprint_report`: Generate Confluence report
- `/help`: Show command help

**Inline Buttons**:
- ✅ Approve release
- ❌ Reject release
- 👀 Review (links to Confluence)

### JIRA

**URL**: https://themarketgraph.atlassian.net
**Project Key**: MVT
**API Token**: (stored in GitHub Secrets as JIRA_API_TOKEN)

**Integration Points**:
- Story loading for sprint execution
- Transition tracking (in-dev → in-test → approved → done)
- Deployment evidence attachment
- Comment updates with test results

### Confluence

**Space Key**: MVTCO
**Homepage ID**: 8356016
**API Token**: (same as JIRA)

**Generated Documents**:
- Sprint reports (auto-generated by docs_agent)
- Design documents (linked from epics)
- API documentation (OpenAPI → Confluence)
- Release notes (with breaking changes highlighted)

### Data APIs

All API keys stored in GitHub Secrets + AWS Secrets Manager:

1. **FRED**: Federal Reserve Economic Data (hourly)
   - API Key: `FRED_API_KEY`
   - Endpoint: https://api.stlouisfed.org/fred/
   - Data: Interest rates, unemployment, inflation

2. **Finnhub**: Stock market data (real-time WebSocket)
   - API Key: `FINNHUB_API_KEY`
   - Endpoint: wss://ws.finnhub.io
   - Data: Prices, volumes, sentiment

3. **GDELT**: Global events database (15-min updates)
   - Endpoint: BigQuery public dataset
   - Data: Conflict events, protests, geopolitical risk

4. **pytrends**: Google Trends (4-hour intervals)
   - No API key required
   - Data: Search trends, interest over time

5. **World Bank**: Development indicators (daily)
   - Endpoint: https://api.worldbank.org/
   - Data: GDP, inequality (Gini), development index

6. **yfinance**: Yahoo Finance (5-min intervals)
   - No API key required
   - Data: Historical OHLCV, dividends

---

## Section 8: GitHub Secrets (17 configured)

**Critical**: These must be set in repository Settings → Secrets and Variables → Actions

```
AWS_ACCESS_KEY_ID              # AWS programmatic access key
AWS_SECRET_ACCESS_KEY          # AWS secret access key
AWS_REGION                     # Default: us-east-1
AWS_ACCOUNT_ID                 # 12-digit AWS account ID
AWS_EVENT_RELAY_ENDPOINT       # EventBridge cross-cloud endpoint

GCP_PROJECT_ID                 # GCP project (mvt-observer)
GCP_SA_KEY                     # Service account JSON key (base64 encoded)
GCP_WORKLOAD_IDENTITY_PROVIDER # For keyless auth
GCP_TERRAFORM_BUCKET           # GCS bucket for state

GITHUB_TOKEN                   # Personal access token (for PR creation)
GITHUB_ORG                     # Repository organization
GITHUB_REPO                    # Repository name

TELEGRAM_BOT_TOKEN             # Telegram Bot API token
TELEGRAM_CHAT_ID               # Chat/channel ID for notifications

JIRA_BASE_URL                  # https://themarketgraph.atlassian.net
JIRA_EMAIL                     # Bot user email
JIRA_API_TOKEN                 # JIRA API token
JIRA_PROJECT_KEY               # MVT

FRED_API_KEY                   # Federal Reserve API key
FINNHUB_API_KEY                # Finnhub API key

DASHBOARD_URL                  # CloudFront distribution URL
CLOUDFRONT_DISTRIBUTION_ID     # For cache invalidation
S3_FRONTEND_BUCKET             # Frontend bucket name
```

---

## Section 9: CI/CD Pipeline

**4 GitHub Actions workflows** (in `.github/workflows/`):

### 1. `pr-checks.yml` — PR Validation

**Trigger**: `pull_request` to `main` or `develop`

**Jobs**:
- **Lint**: ESLint (TypeScript) + Flake8 (Python)
- **Type Check**: TypeScript + mypy
- **Unit Tests**: Jest (CDK) + pytest (handlers) with coverage
- **CDK Synth**: `cdk synth` generates CloudFormation
- **Terraform Validate**: `terraform fmt` + `terraform validate`
- **Security Scan**: Snyk, detect-secrets, bandit (Python)

**Result**:
- ✅ All checks pass → PR can be merged
- ❌ Any check fails → PR blocked, must fix and re-push

### 2. `deploy-staging.yml` — Staging Deployment

**Trigger**: Push to `main` (automatic)

**Jobs**:
1. **Test**: Run full test suite
2. **Build Artifacts**: CDK synth, Terraform plan
3. **Deploy to GCP**: `terraform apply` to staging project
4. **Deploy to AWS**: `cdk deploy --stage staging`
5. **Integration Tests**: Smoke tests against staging endpoints
6. **Notify**: JIRA comment + Telegram message

**Duration**: ~15 minutes
**Failure**: Automatic rollback, deployment stops

### 3. `deploy-canary.yml` — Canary Deployment

**Trigger**: `workflow_run` on `deploy-staging.yml` success

**Jobs**:
1. **Canary Config**:
   - Lambda alias: 10% traffic to new version (weighted routing)
   - Cloud Run: 10% traffic to new revision
   - Wait 5 minutes for stabilization

2. **Health Checks**:
   - CloudWatch metrics: Error rate, latency (p99), invocation count
   - Cloud Monitoring metrics: Same for GCP
   - Threshold check: Error rate > 1% → automatic rollback

3. **Result**:
   - ✅ Healthy → trigger `deploy-production.yml` workflow
   - ❌ Unhealthy → revert Lambda alias to previous version, stop

**Duration**: ~7 minutes

### 4. `deploy-production.yml` — Production Deployment

**Trigger**: Manual (`workflow_dispatch`) or automatic from canary

**Inputs**:
- `confirmation`: Must be "yes" to proceed
- Environment: Protected (requires approvers)

**Jobs**:
1. **Authorize**: Verify environment protection rules
2. **Deploy to AWS**: Promote Lambda alias to 100% traffic + `cdk deploy --stage production`
3. **Deploy to GCP**: `terraform apply` to production project + Cloud Run 100% traffic
4. **Smoke Tests**: Critical endpoint checks
5. **Notify Completion**:
   - JIRA story update with deployment evidence (build #, commit SHA)
   - GitHub Release with release notes
   - Telegram notification to channel

**Duration**: ~10 minutes
**Approval Gate**: Manual confirmation via Telegram or GitHub UI

---

## Section 10: How AI Agent Sprints Work with GitHub SDLC

**Master Orchestrator** (`agents/orchestrator.py`) coordinates the entire sprint:

### Sprint Execution Flow

1. **Load Sprint Stories** (Master Orchestrator):
   - Read JIRA board for sprint stories
   - Resolve dependencies (topological sort)
   - Create execution waves (max 3 concurrent stories)

2. **For Each Story** (Code Agent):
   - `a.` Create feature branch: `git checkout -b feature/MVT-{id}-{slug}`
   - `b.` Generate implementation code using Claude
   - `c.` Stage changes: `git add .`
   - `d.` Commit with message: `git commit -m "feat(scope): description"`
   - `e.` Push branch: `git push origin feature/MVT-{id}-{slug}`
   - `f.` Create PR via GitHub API (with body referencing JIRA story)
   - `g.` Wait for PR checks to pass (pr-checks.yml)
   - `h.` Report back: PR URL, files changed, any validation errors

3. **PR Checks Run Automatically** (GitHub Actions):
   - Lint, test, CDK synth, Terraform validate, security scan
   - If any fail: PR blocked, Code Agent must fix and re-push
   - If all pass: PR ready for merge

4. **Master Merges PR** (Master Orchestrator):
   - Use GitHub API to merge PR to `main`
   - This triggers automatic staging deployment

5. **Staging Deployment** (GitHub Actions):
   - `deploy-staging.yml` runs automatically
   - CDK deploy to staging + Terraform apply to staging GCP
   - Integration tests run against staging
   - Notify Telegram of result

6. **Test Agent Validates Staging** (Optional):
   - Run integration tests
   - Performance benchmarks
   - Security scan results
   - Report: Pass/fail status

7. **Canary Deployment** (GitHub Actions):
   - `deploy-canary.yml` runs automatically (if staging passed)
   - Deploy to production at 10% traffic
   - Monitor CloudWatch/Cloud Monitoring for 5 minutes
   - If healthy: trigger production workflow
   - If unhealthy: rollback automatically

8. **Deploy Agent Monitors** (Deploy Agent):
   - Watch GitHub workflow run status
   - Check canary health metrics
   - If unhealthy: trigger rollback by reverting PR merge
   - Report status back to JIRA + Telegram

9. **Production Approval** (Master Orchestrator):
   - Request PO approval via Telegram command button
   - Wait for `/approve_release` confirmation

10. **Production Deployment** (GitHub Actions):
    - `deploy-production.yml` runs on approval
    - Promote Lambda alias to 100% traffic
    - Terraform apply to production GCP
    - Run production smoke tests
    - Update JIRA story: "Deployed to production"
    - GitHub Release with notes
    - Telegram notification: Deployment complete

### Key Points

- **All code changes originate from GitHub**: No direct CDK/Terraform deployments
- **No manual CLI operations**: Everything is automated via GitHub Actions
- **Deployment is automatic**: Once PR merges, staging/canary/production happen automatically
- **Rollback is automatic**: Canary failure triggers immediate rollback
- **Approval gate is human-driven**: PO approves via Telegram before production
- **Full audit trail**: Every commit, PR, workflow run is tracked in Git

---

## Section 11: Testing

### CDK Tests (TypeScript)

```bash
cd cdk
npm install
npm test
# Runs Jest on stack and construct definitions
# Tests: resource creation, properties, permissions
```

### Handler Tests (Python)

```bash
cd cdk/lib/handlers
pip install -r requirements.txt
pytest
# Unit tests for Lambda function logic
# Tests: input validation, output format, error handling
```

### Terraform Validation

```bash
cd terraform
terraform init
terraform plan
# Validates: HCL syntax, variable types, resource definitions
# Does NOT apply changes
```

### Dashboard Frontend

```bash
# Open in browser
file:///path/to/mvt-trilogy/frontend/dist/index.html
# Tests: Chart rendering, data loading, responsiveness
```

### Integration Tests (CI/CD only)

Automated after merge to main:
- Deploy to staging environment
- Run smoke tests against live endpoints (HTTP 200 checks)
- Canary deployment at 10% traffic
- 5-minute health monitoring
- Production rollout with approval gate

---

## Section 12: Environment Variables

All runtime configuration comes from environment variables or secrets management.

### Local Development (.env file)

1. Copy `.env.example` to `.env`
2. Fill in your credentials (never commit this)
3. Source before running: `export $(cat .env | xargs)`

**File**: `.env` (git-ignored)
**Template**: `.env.example` (in repo)

### CI/CD (GitHub Secrets)

Referenced in workflows as `${{ secrets.SECRET_NAME }}`

### Runtime (Lambda/Cloud Functions)

- **AWS Lambda**: AWS Secrets Manager (referenced via environment variable)
- **Cloud Functions**: GCP Secret Manager (referenced via environment variable)
- **Local test**: Use `.env` file

### Secrets Management Best Practices

- ❌ **NEVER** commit `.env` with real values
- ✅ **DO** use GitHub Secrets for CI/CD
- ✅ **DO** use AWS Secrets Manager for Lambda
- ✅ **DO** use GCP Secret Manager for Cloud Functions
- ✅ **DO** rotate credentials regularly
- ✅ **DO** use service accounts (not user accounts) for automation

---

## Section 13: Code Guidelines

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
      removalPolicy: cdk.RemovalPolicy.RETAIN, // Data protection
    });
  }
}
```

### Lambda Handlers (Python)

```python
import json
import logging
from aws_lambda_powertools import Logger, Tracer

logger = Logger()
tracer = Tracer()

@logger.inject_lambda_context
@tracer.capture_lambda_handler
def lambda_handler(event, context):
    """Handler must be idempotent and handle failures gracefully."""
    try:
        result = process_data(event)
        return {
            'statusCode': 200,
            'body': json.dumps(result)
        }
    except Exception as e:
        logger.exception(f"Error: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
```

### Terraform (HCL)

```hcl
# Use descriptive variable names
variable "alert_threshold" {
  description = "Alert threshold in %"
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

### Python Agents (ai agents)

```python
"""Module docstring following Google style."""

import asyncio
from dataclasses import dataclass
from typing import Optional, Dict, List

@dataclass
class Result:
    """Result of agent execution."""
    status: str
    data: Optional[Dict] = None
    error: Optional[str] = None

class Agent:
    """Base agent class."""

    async def execute(self, input: Dict) -> Result:
        """Execute agent with given input.

        Args:
            input: Agent input parameters

        Returns:
            Result with status, data, and error fields
        """
        try:
            # Implementation
            pass
        except Exception as e:
            return Result(status="error", error=str(e))
```

---

## Section 14: Troubleshooting

### CDK Issues

**CDK synthesis fails**:
1. Run `npm install` in `cdk/` directory
2. Check TypeScript: `npx tsc --noEmit`
3. Verify AWS credentials: `aws sts get-caller-identity`

**Lambda function fails at runtime**:
1. Check CloudWatch logs: `aws logs tail /aws/lambda/<name> --follow`
2. Verify execution role has required permissions
3. Test locally: `sam local invoke`

### Terraform Issues

**Terraform plan shows unexpected changes**:
1. Run `terraform refresh` to sync state
2. Check for AWS/GCP console changes (conflicts)
3. Verify variable overrides in `terraform.tfvars`

**State is locked**:
1. Check: `terraform force-unlock <LOCK_ID>`
2. Find lock info: `gs://bucket/state.lock` (GCS)

### Deployment Issues

**PR checks fail locally**:
1. Run lint: `npm run lint` (TypeScript), `flake8` (Python)
2. Run tests: `npm test` (Jest), `pytest` (Python)
3. Check CDK: `cdk synth`

**Canary deployment fails**:
1. Check CloudWatch metrics: Error rate, latency
2. Check logs: CloudWatch Logs for Lambda
3. Rollback: Previous version automatically promoted

**Telegram bot not responding**:
1. Verify bot token: Correct format, not expired
2. Check webhook: URL is publicly accessible
3. Check DynamoDB: Conversation state table exists
4. Check IAM: Lambda role has Telegram + JIRA permissions

### Connection Issues

**Cannot reach JIRA**:
1. Verify URL: https://themarketgraph.atlassian.net
2. Check credentials: Email + API token valid
3. Check network: VPN, firewall rules

**Cannot reach GCP**:
1. Verify project: `gcloud config get-value project`
2. Check credentials: Service account key valid
3. Check permissions: Service account has required roles

---

## Section 15: Summary: The Three Rules

**1. All changes go through GitHub**
- Create branch → commit → PR → review → merge
- No manual CLI deployments
- No AWS/GCP console changes

**2. Infrastructure is code**
- Define everything in CDK (AWS) or Terraform (GCP)
- Never create resources manually
- All changes are version-controlled

**3. Secrets stay secret**
- Use GitHub Secrets for CI/CD
- Use Secrets Manager for runtime
- Never commit .env with real values
- Rotate credentials regularly

---

## Section 16: Quick Reference

**Get started in 5 minutes**:
```bash
# 1. Clone the repo
git clone https://github.com/vishalbk/mvt-trilogy.git
cd mvt-trilogy

# 2. Setup environment
cp .env.example .env
# Edit .env with your credentials

# 3. Install dependencies
npm install --prefix cdk
pip install -r agents/requirements.txt

# 4. Run tests
npm test --prefix cdk
pytest cdk/lib/handlers/

# 5. Create a feature branch
git checkout -b feature/MVT-XX-description

# 6. Make changes and commit
git add .
git commit -m "feat(scope): description"
git push origin feature/MVT-XX-description

# 7. Create PR in GitHub UI
```

**Check deployment status**:
```bash
# View GitHub Actions
gh run list

# View CloudWatch logs
aws logs tail /aws/lambda/my-function --follow

# View Terraform state
terraform state list
```

**Emergency rollback** (if needed):
```bash
# Revert last commit
git revert <commit-sha>
git push origin main
# GitHub Actions will automatically deploy the revert
```

---

**Last updated**: March 2026
**Maintained by**: MVT Development Team
**Questions?** Open an issue or contact the team via Telegram @mvt_observatory_bot

