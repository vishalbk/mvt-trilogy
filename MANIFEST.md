# MVT Trilogy - Complete Infrastructure Manifest

## Project Overview
Macro Vulnerability Trilogy - A cross-cloud (AWS + GCP) data ingestion, processing, and analytics platform for global macro vulnerability signals.

**Base Path:** `/sessions/jolly-inspiring-goodall/mnt/WorldIsMyOyester/mvt-trilogy/`

---

## TERRAFORM INFRASTRUCTURE (21 files)

### 1. Core Terraform Configuration (4 files)
| File | Purpose | Key Components |
|------|---------|-----------------|
| `terraform/main.tf` | Provider & module orchestration | Google Cloud provider, all module calls, required_providers |
| `terraform/variables.tf` | Input variables | project_id, region, environment, aws_event_relay_endpoint |
| `terraform/outputs.tf` | Output values | All resource IDs and URLs |
| `terraform/backend.tf` | State management | GCS backend config (or local for dev) |

### 2. Pub/Sub Module (1 file)
**Location:** `terraform/modules/pubsub/main.tf`

**Creates:**
- 3 Pub/Sub topics:
  - `mvt-signals` - Cross-cloud event relay from AWS EventBridge
  - `mvt-alerts` - High-priority alerts for Telegram
  - `mvt-analytics` - BigQuery-bound analytics events
- Pull subscriptions for each topic
- Dead-letter topics with retry policies (max 5 attempts)
- Message storage policies (US-central1 region)

**Outputs:**
- signals_topic_name
- alerts_topic_name
- analytics_topic_name

### 3. BigQuery Module (9 files)
**Location:** `terraform/modules/bigquery/`

**Files:**
1. `main.tf` - Dataset creation, 4 tables, 2 saved queries, IAM setup
2. `schemas/inequality_signals.json` - 7 fields for inequality metrics
3. `schemas/sentiment_events.json` - 7 fields for event data
4. `schemas/sovereign_indicators.json` - 6 fields for country indicators
5. `schemas/gdelt_events.json` - 14 fields for GDELT geopolitical events
6. `queries/gdelt_extraction.sql` - Extract conflict events from GDELT
7. `queries/daily_correlation.sql` - 90-day inequality-sentiment correlation

**Dataset:** `mvt_analytics`

**Tables:**
| Table | Partition | Cluster | Records |
|-------|-----------|---------|---------|
| inequality_signals | date | source | Economic inequality metrics |
| sentiment_events | date | event_type | Protest/riot/strike/boycott |
| sovereign_indicators | date | country | Fragility/risk metrics |
| gdelt_events | date | event_code | Geopolitical conflict events |

**Queries:**
- `gdelt_extraction` - Extract 1000 conflict events (root codes 14-20) with >5 mentions
- `daily_correlation` - Pearson correlation coefficient between inequality and sentiment

### 4. Cloud Functions Module (4 files)
**Location:** `terraform/modules/functions/`

**Main File:** `main.tf`
- 3 Cloud Functions (Gen2, Python 3.12)
- Service account with 5 IAM roles
- Cloud Storage bucket for source code
- Cloud Scheduler trigger (every 15 minutes)

**Function 1: signal-processor** (`src/signal-processor/main.py`)
- **Trigger:** Pub/Sub message on `mvt-signals` topic
- **Source:** AWS EventBridge relay events
- **Action:** Parse JSON, write to Firestore
- **Structure:** `dashboard_signals/{dashboard_id}/{signal_id}`
- **Side Effect:** Update `_latest` document with newest signal
- **Memory:** 256MB | **Timeout:** 60s | **Instances:** 0-5

**Function 2: analytics-writer** (`src/analytics-writer/main.py`)
- **Trigger:** Pub/Sub message on `mvt-analytics` topic
- **Source:** Various data sources
- **Action:** Route to BigQuery table based on event_type
- **Tables Supported:**
  - inequality_signals
  - sentiment_events
  - sovereign_indicators
  - gdelt_events
- **Memory:** 256MB | **Timeout:** 60s | **Instances:** 0-5

**Function 3: gdelt-extractor** (`src/gdelt-extractor/main.py`)
- **Trigger:** Cloud Scheduler HTTP POST (every 15 minutes)
- **Source:** GDELT public BigQuery dataset
- **Action:** Extract conflict events, publish to `mvt-signals`
- **Query:** Event root codes 14-20 (conflict/protests), >5 mentions, 1000 limit
- **Relay:** Publishes to AWS EventBridge for cross-cloud aggregation
- **Memory:** 256MB | **Timeout:** 60s | **Instances:** 0-5

**IAM Roles:**
- roles/pubsub.editor
- roles/bigquery.dataEditor
- roles/bigquery.user
- roles/datastore.user
- roles/run.invoker

### 5. Firestore Module (1 file)
**Location:** `terraform/modules/firestore/main.tf`

**Database:** Native mode, nam5 region

**Collections:**
1. `dashboard_signals` - Real-time signal events by dashboard
2. `dashboard_state` - UI state and configuration
3. `processed_analytics` - Aggregated metrics

**Indexes:**
- dashboard_signals: (dashboard_id ASC, timestamp DESC)
- processed_analytics: (event_type ASC, date DESC)

**AppEngine:** Required for Firestore initialization

### 6. Firebase Hosting Module (1 file)
**Location:** `terraform/modules/hosting/main.tf`

**Site:** `mvt-analytics-dashboard`

**Routing:**
- SPA rewrite: `/**` → `/index.html`
- Regex: `^(?!.*\..*)(?!.*(__).*)[^\.]+$`

**Cache Headers:**
- HTML: `Cache-Control: public, max-age=300, must-revalidate`
- Assets (CSS/JS/images): `Cache-Control: public, max-age=31536000, immutable`

**Security Headers:**
- X-Content-Type-Options: nosniff
- X-Frame-Options: DENY
- X-XSS-Protection: 1; mode=block
- Referrer-Policy: strict-origin-when-cross-origin
- Content-Security-Policy: Custom for Firebase + Google APIs

**Redirects:**
- `/old-dashboard/**` → `/dashboard` (301)

### 7. Monitoring Module (1 file)
**Location:** `terraform/modules/monitoring/main.tf`

**Dashboard:** "MVT Observatory - GCP"

**Panels:**
1. Cloud Function error rate (execution_times ALIGN_RATE)
2. Pub/Sub backlog (num_unacked_messages ALIGN_MAX)
3. BigQuery insert errors (num_failed_insert_requests ALIGN_RATE)

**Alert Policies:**
1. **Function Errors** - threshold: 5%, duration: 300s
2. **Dead-Letter Messages** - threshold: >0, duration: 60s
3. **Firestore Quota** - threshold: >80%, duration: 300s

**Notification Channel:** Email to alerts@mvt-observatory.local

### 8. Environment Configuration (1 file)
**Location:** `environments/dev.tfvars`

```hcl
project_id    = "mvt-trilogy-dev"
region        = "us-central1"
environment   = "dev"
aws_event_relay_endpoint = "https://events.us-east-1.amazonaws.com/"
```

---

## GITHUB ACTIONS CI/CD (4 files)

**Location:** `.github/workflows/`

### 1. PR Checks (`pr-checks.yml`)
**Trigger:** `on: [pull_request]` to main/develop

**Jobs:**
| Job | Tools | Purpose |
|-----|-------|---------|
| lint | ESLint, Flake8 | Code style validation |
| type-check | TypeScript, mypy | Static type checking |
| unit-tests | Jest, pytest | Unit test execution |
| validate-templates | Terraform, CDK | Infrastructure validation |
| security-scan | detect-secrets, bandit | Security vulnerability detection |

**Artifacts:** Coverage reports, security reports

### 2. Deploy to Staging (`deploy-staging.yml`)
**Trigger:** `on: [push]` to main

**Concurrency:** Single deployment (cancel-in-progress: false)

**Jobs:**
1. **test**
   - Unit tests + build artifacts
   - Success required for deployment

2. **deploy-gcp-staging**
   - `terraform init` with staging state bucket
   - `terraform plan/apply` to staging
   - Outputs: Pub/Sub topics, BigQuery dataset, Hosting URL

3. **deploy-cdk-staging**
   - AWS CDK synth + deploy
   - Deploys to AWS staging environment

4. **integration-tests**
   - pytest against staging endpoints
   - Uses outputs from GCP deployment

5. **notify-results**
   - JIRA comment if successful
   - Telegram message with status

### 3. Deploy Canary (`deploy-canary.yml`)
**Trigger:** `on: [workflow_run]` after successful staging

**Canary Configuration:**
- Traffic: 10% to new version
- Duration: 5 minutes (300 seconds)

**Jobs:**
1. **canary-checks**
   - AWS Lambda alias: 10% routing config
   - Cloud Run: 10% traffic to latest revision
   - Wait 300 seconds for stabilization
   - Check CloudWatch metrics (error rate, latency)
   - Check Cloud Monitoring metrics
   - Conditional rollback if unhealthy
   - Auto-trigger production if healthy

**Metrics Monitored:**
- Lambda error rate
- Lambda duration (latency)
- Cloud Run error rate
- Cloud Run latency

**Actions on Health Check Failure:**
- Rollback Lambda alias (0% new traffic)
- Rollback Cloud Run traffic
- Telegram notification
- Do not trigger production

### 4. Deploy Production (`deploy-production.yml`)
**Trigger:** `on: [workflow_dispatch]` with manual confirmation

**Environment Protection:** GitHub production environment rules required

**Confirmation:** Input parameter `confirm=yes` required

**Jobs:**
1. **authorize**
   - Verify environment protection
   - Check for explicit confirmation

2. **deploy-aws-production**
   - Lambda alias: promote to 100% traffic
   - AWS CDK deploy with ENVIRONMENT=production

3. **deploy-gcp-production**
   - Terraform plan/apply to production
   - Cloud Run traffic: set to 100%

4. **smoke-tests**
   - pytest tests/smoke/ against production URLs
   - Validates critical functionality

5. **notify-completion**
   - Determine deployment status
   - JIRA story comment with evidence
   - Telegram notification (rocket emoji)
   - GitHub Release creation
   - Release notes with deployment info

**GitHub Release Includes:**
- tag_name: release-{run_id}
- release_name: Production Release {run_id}
- body: RELEASE_NOTES.md with service list, monitoring links

---

## CLOUD FUNCTION SOURCE CODE (3 files)

### Signal Processor (`terraform/modules/functions/src/signal-processor/main.py`)
**Lines:** ~70 | **Size:** 4KB

**Handler:** `process_signal(cloud_event)`

**Functionality:**
1. Decode Pub/Sub Base64 message
2. Parse JSON signal data
3. Extract: dashboard_id, signal_id, event_type, severity
4. Write to Firestore: `dashboard_signals/{dashboard}/{signal_id}`
5. Update: `dashboard_signals/{dashboard}/_latest` with newest data
6. Add: processed_at timestamp

**Error Handling:**
- JSONDecodeError → 400 Bad Request
- Missing signal_id → 400 Bad Request
- Firestore errors → 500 Server Error

**Response:**
```json
{
  "status": "success|error",
  "signal_id": "...",
  "message": "..."
}
```

### Analytics Writer (`terraform/modules/functions/src/analytics-writer/main.py`)
**Lines:** ~120 | **Size:** 8KB

**Handler:** `write_analytics(cloud_event)`

**Functionality:**
1. Decode Pub/Sub message
2. Route by event_type to appropriate BigQuery table:
   - `inequality_signal` → `mvt_analytics.inequality_signals`
   - `sentiment_event` → `mvt_analytics.sentiment_events`
   - `sovereign_indicator` → `mvt_analytics.sovereign_indicators`
   - `gdelt_event` → `mvt_analytics.gdelt_events`
3. Build row object with typed fields
4. Perform BigQuery streaming insert
5. Return status

**Type Conversions:**
- Numeric fields: FLOAT64/INT64
- Dates: DATE type
- Nullable fields: Null handling per schema
- ARRAY fields: REPEATED mode

**Error Handling:**
- JSONDecodeError → 400
- ValueError (type conversion) → 400
- BigQuery errors → 500

### GDELT Extractor (`terraform/modules/functions/src/gdelt-extractor/main.py`)
**Lines:** ~110 | **Size:** 4KB

**Handler:** `extract_gdelt(request)`

**Functionality:**
1. Query GDELT BigQuery public dataset
2. Filter by:
   - EventRootCode IN ('14','17','18','19','20') - conflict/protest codes
   - NumMentions > 5
   - Last 24 hours
3. Limit to 1000 results
4. For each result:
   - Extract 13 fields
   - Convert types (dates, floats, ints)
   - Add extracted_at timestamp
5. Publish each to `mvt-signals` Pub/Sub
6. Return count and bytes processed

**GDELT Conflict Codes:**
- 14: Protest
- 17: Attempt to assassinate
- 18: Assassinate
- 19: Abduction/forced disappearance
- 20: Torture/rape/sexual abuse

**Response:**
```json
{
  "status": "success|error",
  "events_extracted": 42,
  "bytes_processed": 123456
}
```

---

## BIGQUERY SCHEMAS (4 files)

### inequality_signals.json
```json
[
  { "name": "date", "type": "DATE", "mode": "REQUIRED" },
  { "name": "source", "type": "STRING", "mode": "REQUIRED" },
  { "name": "metric", "type": "STRING", "mode": "REQUIRED" },
  { "name": "value", "type": "FLOAT64", "mode": "REQUIRED" },
  { "name": "region", "type": "STRING", "mode": "NULLABLE" },
  { "name": "raw_json", "type": "JSON", "mode": "NULLABLE" },
  { "name": "ingested_at", "type": "TIMESTAMP", "mode": "REQUIRED" }
]
```

### sentiment_events.json
```json
[
  { "name": "date", "type": "DATE", "mode": "REQUIRED" },
  { "name": "event_type", "type": "STRING", "mode": "REQUIRED" },
  { "name": "severity", "type": "FLOAT64", "mode": "REQUIRED" },
  { "name": "source", "type": "STRING", "mode": "REQUIRED" },
  { "name": "countries", "type": "STRING", "mode": "REPEATED" },
  { "name": "raw_json", "type": "JSON", "mode": "NULLABLE" },
  { "name": "ingested_at", "type": "TIMESTAMP", "mode": "REQUIRED" }
]
```

### sovereign_indicators.json
```json
[
  { "name": "date", "type": "DATE", "mode": "REQUIRED" },
  { "name": "country", "type": "STRING", "mode": "REQUIRED" },
  { "name": "indicator", "type": "STRING", "mode": "REQUIRED" },
  { "name": "value", "type": "FLOAT64", "mode": "REQUIRED" },
  { "name": "risk_score", "type": "FLOAT64", "mode": "REQUIRED" },
  { "name": "ingested_at", "type": "TIMESTAMP", "mode": "REQUIRED" }
]
```

### gdelt_events.json
```json
[
  { "name": "date", "type": "DATE", "mode": "REQUIRED" },
  { "name": "event_code", "type": "STRING", "mode": "REQUIRED" },
  { "name": "event_root_code", "type": "STRING", "mode": "REQUIRED" },
  { "name": "actor1_country", "type": "STRING", "mode": "NULLABLE" },
  { "name": "actor2_country", "type": "STRING", "mode": "NULLABLE" },
  { "name": "goldstein_scale", "type": "FLOAT64", "mode": "NULLABLE" },
  { "name": "num_mentions", "type": "INT64", "mode": "REQUIRED" },
  { "name": "num_sources", "type": "INT64", "mode": "REQUIRED" },
  { "name": "num_articles", "type": "INT64", "mode": "REQUIRED" },
  { "name": "avg_tone", "type": "FLOAT64", "mode": "NULLABLE" },
  { "name": "action_geo_country", "type": "STRING", "mode": "NULLABLE" },
  { "name": "action_geo_lat", "type": "FLOAT64", "mode": "NULLABLE" },
  { "name": "action_geo_long", "type": "FLOAT64", "mode": "NULLABLE" },
  { "name": "ingested_at", "type": "TIMESTAMP", "mode": "REQUIRED" }
]
```

---

## BIGQUERY QUERIES (2 files)

### gdelt_extraction.sql
Extracts conflict events from GDELT with >5 mentions, ordered by prominence.

### daily_correlation.sql
Computes Pearson correlation between inequality distress and sentiment triggers over 90 days.

---

## DOCUMENTATION (2 files)

### INFRASTRUCTURE_SUMMARY.md
Complete reference covering:
- All Terraform modules
- BigQuery tables and schemas
- Cloud Functions specifications
- GitHub Actions workflow details
- Environment configuration
- Required secrets
- Deployment flow diagram

### DEPLOYMENT_GUIDE.md
Step-by-step instructions for:
- Prerequisites and setup
- Terraform initialization
- Development deployment
- Cloud Function packaging
- GitHub secret configuration
- Local testing
- Staging/canary/production promotion
- Monitoring and debugging
- Troubleshooting common issues
- Cleanup procedures

---

## SUMMARY STATISTICS

| Category | Count | Details |
|----------|-------|---------|
| **Terraform Files** | 16 | 4 core + 7 modules + 1 env + 4 outputs/vars/backend |
| **GitHub Workflows** | 4 | PR checks, staging, canary, production |
| **BigQuery Schemas** | 4 | 7-14 fields each, partitioned + clustered |
| **SQL Queries** | 2 | Extraction + correlation analysis |
| **Cloud Functions** | 3 | Python 3.12, Gen2, Pub/Sub + Scheduler triggered |
| **Pub/Sub Topics** | 3 | Signals, alerts, analytics (each with DLQ) |
| **Firestore Collections** | 3 | Signals, state, processed_analytics |
| **Services Total** | 15+ | Pub/Sub, BigQuery, Firestore, Cloud Functions, Scheduler, Cloud Run, Hosting, Monitoring |

---

## DEPLOYMENT ARCHITECTURE

```
GitHub PR
    ↓
pr-checks.yml (lint/test/validate)
    ↓ [merge to main]
    ↓
deploy-staging.yml
├─ Terraform → GCP (staging)
│  ├─ Pub/Sub topics + subscriptions
│  ├─ BigQuery dataset + tables
│  ├─ Cloud Functions (3x)
│  ├─ Firestore database
│  ├─ Firebase Hosting
│  └─ Cloud Monitoring dashboard
├─ AWS CDK → Lambda + EventBridge
└─ Integration tests
    ↓ [on success]
    ↓
deploy-canary.yml
├─ Lambda alias: 10% traffic
├─ Cloud Run: 10% traffic
├─ Wait 5 minutes
├─ Monitor metrics
└─ Health check → rollback OR trigger prod
    ↓ [if healthy]
    ↓
deploy-production.yml (manual dispatch)
├─ Lambda alias: 100% traffic
├─ Terraform → GCP (production)
├─ Cloud Run: 100% traffic
├─ Smoke tests
└─ GitHub Release + Telegram notification
```

---

## KEY DESIGN DECISIONS

✅ **Pub/Sub as central event hub** - Decouples data sources from sinks
✅ **Partitioned BigQuery tables** - Optimizes query performance and costs
✅ **Firestore for real-time state** - Dashboard can subscribe to changes
✅ **Firebase Hosting for SPA** - Zero-downtime deployments, global CDN
✅ **Cloud Scheduler for GDELT** - Serverless recurring extraction
✅ **Canary before production** - Automatic health checks prevent bad deployments
✅ **GitHub Actions for everything** - Single source of truth for deployments
✅ **JIRA + Telegram integration** - Team visibility and notification

---

**Total Lines of Infrastructure Code:** ~2,500+ lines
**Total Configuration Files:** 25
**Estimated Deployment Time:** 15-20 minutes (first time), 5 minutes (subsequent)
**Monthly GCP Estimate:** $50-200 (depending on volume)
