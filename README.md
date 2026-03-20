# MVT Observatory — Macro Vulnerability Trilogy

Multi-cloud FinTech platform monitoring global macro-economic vulnerability through 4 real-time KPI dashboards, AI-powered SDLC agents, and cross-cloud analytics.

**Live Dashboard**: https://d2p9otbgwjwwuv.cloudfront.net

## Architecture

```
┌─────────────────────────────── AWS (us-east-1) ───────────────────────────────┐
│                                                                                │
│  Ingestion (6 Lambdas)          Processing (5 Lambdas)         Frontend        │
│  ┌──────────────────┐           ┌────────────────────┐    ┌──────────────┐    │
│  │ finnhub-connector│──┐        │ sentiment-aggregator│    │ S3 + CloudFr │    │
│  │ fred-poller      │  ├──DDB──►│ inequality-scorer   │──►│ 5-tab dashbd │    │
│  │ gdelt-querier    │  │signals │ contagion-modeler   │    │ WebSocket RT │    │
│  │ trends-poller    │  │        │ vulnerability-comp   │    └──────────────┘    │
│  │ worldbank-poller │  │        │ dashboard-router     │         ▲              │
│  │ yfinance-streamer│──┘        └────────┬─────────────┘         │              │
│  └──────────────────┘                    │                       │              │
│                                   DDB dashboard-state ───► DDB Streams         │
│                                          │                 ws-broadcast         │
│  SRE (5 Lambdas)                         │                                     │
│  ┌──────────────────┐            ┌───────▼──────────┐                          │
│  │ cost-tracker     │            │ alert-manager     │──► Telegram Bot          │
│  │ health-check     │            │ history-writer    │                          │
│  │ cost-anomaly     │            │ history-api       │                          │
│  │ perf-baseline    │            │ event-relay ──────┼──► GCP Pub/Sub           │
│  │ capacity-planner │            │ analytics-relay   │                          │
│  └──────────────────┘            └──────────────────┘                          │
│                                                                                │
│  SDLC Agents                     Telegram Bot                                  │
│  ┌──────────────────┐           ┌──────────────────┐                           │
│  │ Orchestrator     │           │ /kickoff_sprint   │                           │
│  │ Code Agent       │◄──────►  │ /sprint_status    │                           │
│  │ Test Agent       │  Claude  │ /approve_release  │                           │
│  │ Deploy Agent     │   API    │ /sprint_report    │                           │
│  │ Docs Agent       │           │ /brainstorm       │                           │
│  └──────────────────┘           └──────────────────┘                           │
└────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────── GCP (mvt-observer) ────────────────────────────────────┐
│  Pub/Sub Topics: signals, alerts, analytics (each with DLQ)                    │
│  Cloud Functions: signal-processor, analytics-writer, gdelt-extractor          │
│  BigQuery: mvt_analytics (4 tables + daily_summary view)                       │
│  Firestore: Real-time state sync                                               │
│  Firebase Hosting: GCP dashboard mirror                                        │
└────────────────────────────────────────────────────────────────────────────────┘
```

## Dashboard Tabs

| Tab | KPIs | Data Source |
|-----|------|-------------|
| Overview | Distress Index, Trigger Probability, Contagion Risk, VIX Level | All pollers → composite |
| Inequality Pulse | CPI YoY, Fed Rate, 10Y Treasury, M2 Supply, Unemployment | FRED API |
| Sentiment Seismic | Market Sentiment, VIX, SPY, QQQ, Sector Scores | Finnhub + Yahoo Finance |
| Sovereign Dominoes | Countries at Risk, Cascade Probability, Risk Rankings | World Bank API |
| SRE Observatory | Health Score, Daily Cost, Error Rate, P50/P90/P99, Capacity | CloudWatch metrics |

## Quick Start

```bash
# Clone
git clone https://github.com/vishalbk/mvt-trilogy.git
cd mvt-trilogy

# Install Python dependencies
pip install -r requirements-dev.txt

# Run unit tests (139 tests)
pytest tests/unit/ -v

# Run smoke tests (requires network)
pytest tests/smoke/test_e2e_smoke.py -v

# Deploy frontend
aws s3 sync frontend/ s3://mvt-frontend-572664774559-us-east-1/ --delete
aws cloudfront create-invalidation --distribution-id E2JPNGN2TCNNV8 --paths "/*"
```

## Project Structure

```
mvt-trilogy/
├── .github/workflows/          # CI/CD pipelines
├── agents/                     # SDLC AI agent system
│   ├── orchestrator.py         # Master orchestrator (900+ lines)
│   ├── config.py               # Centralized configuration
│   └── subagents/              # Specialized agents
│       ├── code_agent.py       # Code generation + PR creation
│       ├── test_agent.py       # Test execution + coverage
│       ├── deploy_agent.py     # Deployment + canary rollback
│       └── docs_agent.py       # Confluence documentation
├── cdk/                        # AWS CDK infrastructure
│   ├── lib/stacks/             # 7 CDK stacks
│   └── lib/handlers/           # Lambda function code
│       ├── ingestion/          # 6 data pollers
│       ├── processing/         # 8 processing functions
│       ├── relay/              # Cross-cloud relay (AWS→GCP)
│       └── sre/                # 5 SRE/observability functions
├── frontend/                   # Dashboard (S3/CloudFront)
│   └── index.html              # Single-page app with 5 tabs
├── telegram-bot/               # Telegram bot Lambda
│   └── lambda_function.py      # Deployed handler with JIRA+Claude
├── terraform/                  # GCP infrastructure
│   └── modules/                # Pub/Sub, BigQuery, Functions, etc.
└── tests/                      # Test suite (139+ tests)
    ├── unit/                   # Unit tests (mocked)
    ├── integration/            # Integration tests
    └── smoke/                  # End-to-end smoke tests
```

## AWS Resources

| Service | Resource | Purpose |
|---------|----------|---------|
| Lambda | 21 functions (mvt-* prefix) | Data pipeline + processing + SRE |
| DynamoDB | 4 tables | Signals, dashboard-state, connections, history |
| API Gateway | WebSocket API | Real-time dashboard updates |
| EventBridge | 6 schedule rules | Poller triggers |
| S3 | Frontend bucket | Static dashboard hosting |
| CloudFront | Distribution E2JPNGN2TCNNV8 | CDN for dashboard |
| CloudWatch | MVT-Observatory dashboard | System monitoring |
| SNS | mvt-alerts topic | Alert notifications |

## GCP Resources (Terraform)

| Service | Resource | Purpose |
|---------|----------|---------|
| Pub/Sub | 3 topics + 3 DLQs | Cross-cloud event relay |
| Cloud Functions | 3 Gen 2 functions | Signal processing, analytics, GDELT |
| BigQuery | mvt_analytics dataset | Historical analytics (4 tables) |
| Firestore | mvt-observer DB | Real-time state sync |
| Cloud Scheduler | GDELT extraction | 15-min extraction trigger |

## Data Pipeline

```
Pollers (6) → DynamoDB signals → EventBridge → Processing Lambdas (5)
→ DynamoDB dashboard-state → DynamoDB Streams → ws-broadcast
→ WebSocket API Gateway → Dashboard (browser)
                        → event-relay → GCP Pub/Sub → Cloud Functions → BigQuery
```

**Key constraint**: All DynamoDB numeric values MUST use `Decimal(str(value))`, never Python `float`.

## Sprint History

| Sprint | Theme | Stories | Key Deliverables |
|--------|-------|---------|------------------|
| 1 | Live Data | 22 PRs | End-to-end data pipeline, 4 dashboard tabs, WebSocket real-time |
| 2 | Smart Agents | 8 stories | SDLC agents wired to real JIRA/GitHub/Claude APIs, 112 unit tests |
| 3 | Cross-Cloud | 7 stories | AWS→GCP relay, BigQuery pipeline, alerts, history, sector sentiment |
| 4 | SRE Observatory | 7 stories | Tab 5, cost tracking, health checks, anomaly detection, capacity planning |
| 5 | Magic Demo | 6 stories | Integration tests, dashboard polish, documentation |

**Progress**: 43 of 51 stories DONE, 4 PARTIAL, 4 NOT STARTED

## Testing

```bash
# All unit tests (139 passing)
pytest tests/unit/ -v

# Smoke tests (26 pass without AWS creds, 35 with)
pytest tests/smoke/test_e2e_smoke.py -v

# Integration tests (requires AWS credentials)
pytest tests/integration/ -v
```

## Deployment (via CloudShell)

```bash
# Lambda deployment pattern
cd ~/mvt-trilogy-fresh
git pull origin main
cd cdk/lib/handlers/<category>/<function-name>
zip -j /tmp/<function-name>.zip index.py
aws lambda update-function-code --function-name mvt-<function-name> \
  --zip-file fileb:///tmp/<function-name>.zip --region us-east-1

# Frontend deployment
aws s3 sync frontend/ s3://mvt-frontend-572664774559-us-east-1/ --delete
aws cloudfront create-invalidation --distribution-id E2JPNGN2TCNNV8 --paths "/*"

# SRE Lambdas (all at once)
bash cdk/lib/handlers/sre/deploy-all.sh
```

## Configuration

Environment variables are set per Lambda in the AWS Console. Key variables:

- `DASHBOARD_STATE_TABLE`: `mvt-dashboard-state`
- `SIGNALS_TABLE`: `mvt-signals`
- `EVENT_BUS_NAME`: `mvt-event-bus`
- `TELEGRAM_BOT_TOKEN`: Bot token for alerts
- `TELEGRAM_CHAT_ID`: Chat ID for notifications
- `GCP_PROJECT_ID`: `mvt-observer`
- `GCP_SA_KEY_JSON`: Service account key (for cross-cloud relay)

## License

Proprietary — Macro Vulnerability Trilogy Project
