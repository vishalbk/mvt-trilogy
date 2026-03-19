# MVT Trilogy Lambda Handlers - Complete Implementation Summary

All Lambda handler functions have been successfully built for the Macro Vulnerability Trilogy project.

## Directory Structure

```
handlers/
├── ingestion/           (6 handlers)
│   ├── fred-poller/
│   ├── trends-poller/
│   ├── finnhub-connector/
│   ├── gdelt-querier/
│   ├── worldbank-poller/
│   └── yfinance-streamer/
├── processing/          (5 handlers)
│   ├── inequality-scorer/
│   ├── sentiment-aggregator/
│   ├── contagion-modeler/
│   ├── vulnerability-composite/
│   └── cross-dashboard-router/
└── realtime/            (3 handlers)
    ├── ws-connect.ts
    ├── ws-disconnect.ts
    └── ws-broadcast.ts
```

## INGESTION HANDLERS (Python 3.12)

### 1. fred-poller
**File**: `ingestion/fred-poller/index.py`
- Polls Federal Reserve FRED API for economic indicators
- Series tracked: DRCCLACBS, TDSP, NFCIC, BOGZ1FL154022386Q
- Writes to DynamoDB: inequality_pulse dashboard
- Publishes: FREDDataUpdated event
- Uses: urllib.request (stdlib only)

### 2. trends-poller
**File**: `ingestion/trends-poller/index.py`
- Polls Google Trends via pytrends for financial distress keywords
- 12 keywords: "can't afford rent", "bankruptcy help", "debt relief", etc.
- Fetches: interest_over_time (past 12 months) + interest_by_region
- Writes to DynamoDB: inequality_pulse dashboard
- Publishes: TrendsDataUpdated event
- Dependencies: pytrends==4.9.2

### 3. finnhub-connector
**File**: `ingestion/finnhub-connector/index.py`
- Polls Finnhub API for news sentiment and market data
- Symbols: AAPL, MSFT, AMZN, GOOGL, NVDA, JPM, GS
- Computes aggregate sentiment: bullish_pct, bearish_pct, neutral_pct
- Writes to DynamoDB: sentiment_seismic dashboard
- Publishes: SentimentUpdated event
- Uses: urllib.request (stdlib only)

### 4. gdelt-querier
**File**: `ingestion/gdelt-querier/index.py`
- Queries GDELT data from GCP BigQuery via REST API
- Event codes: 14, 17, 18, 19, 20 (conflict, sanctions, threats)
- Writes to DynamoDB: sentiment_seismic and sovereign_dominoes dashboards
- Publishes: GDELTEventsUpdated event
- Uses: GCP service account (base64 encoded in GCP_SA_KEY env var)

### 5. worldbank-poller
**File**: `ingestion/worldbank-poller/index.py`
- Polls World Bank API for sovereign indicators
- Countries: ARG, TUR, EGY, PAK, NGA, BRA, ZAF, MEX, IDN, IND
- Indicators: FI.RES.TOTL.CD, DT.DOD.DECT.GN.ZS, NY.GDP.MKTP.KD.ZG
- Writes to DynamoDB: sovereign_dominoes dashboard
- Publishes: WorldBankUpdated event
- Uses: urllib.request (stdlib only)

### 6. yfinance-streamer
**File**: `ingestion/yfinance-streamer/index.py`
- Fetches real-time market data using yfinance
- Risk gauges: ^VIX, GLD, TLT, VWOB, EMB
- FX pairs: USDBRL=X, USDTRY=X, USDARS=X, USDEGP=X, USDPKR=X, USDNGN=X, USDZAR=X, USDMXN=X, USDIDR=X, USDINR=X
- Extracts: current price, daily change %, 30-day change %
- Writes to DynamoDB: sentiment_seismic (risk gauges) and sovereign_dominoes (FX/EM bonds)
- Publishes: PriceDataUpdated event
- Dependencies: yfinance==0.2.36

## PROCESSING HANDLERS (Python 3.12)

### 7. inequality-scorer
**File**: `processing/inequality-scorer/index.py`
- Computes composite inequality/distress score (0-100)
- Weights:
  - Credit card delinquency: 0.25
  - Google Trends distress: 0.25
  - Debt service ratio: 0.20
  - Financial conditions credit: 0.15
  - Trend velocity: 0.15
- Writes to: dashboardStateTable (inequality_pulse, composite_score panel)
- Publishes: InequalityScoreUpdated event

### 8. sentiment-aggregator
**File**: `processing/sentiment-aggregator/index.py`
- Aggregates sentiment signals into trigger probability (0-100%)
- Weights:
  - VIX (15-45 normalized): 0.30
  - Finnhub bearish %: 0.25
  - GDELT conflict count: 0.25
  - Sanctions activity: 0.20
- Writes to: dashboardStateTable (sentiment_seismic, trigger_probability panel)
- Publishes: SentimentScoreUpdated event

### 9. contagion-modeler
**File**: `processing/contagion-modeler/index.py`
- Models sovereign contagion cascades
- Per-country risk score (0-100):
  - Reserve adequacy: 0.30
  - Debt sustainability: 0.25
  - Currency pressure: 0.25
  - GDP trajectory: 0.20
- Computes regional contagion paths (correlation > 0.5)
- Writes to: dashboardStateTable (risk_scores and contagion_paths panels)
- Publishes: ContagionScoreUpdated event

### 10. vulnerability-composite
**File**: `processing/vulnerability-composite/index.py`
- Computes master vulnerability score from all three dashboards
- Weights:
  - Inequality distress: 0.35
  - Sentiment trigger probability: 0.35
  - Sovereign contagion risk: 0.30
- Writes to: dashboardStateTable (composite, vulnerability_score panel)
- Writes to: auditTable (historical tracking)
- Publishes: VulnerabilityCompositeUpdated event

### 11. cross-dashboard-router
**File**: `processing/cross-dashboard-router/index.py`
- Routes signals between dashboards based on threshold rules
- Threshold triggers:
  - Inequality > 70 → emit to sentiment_seismic
  - Trigger probability > 60 → emit to sovereign_dominoes
  - Contagion risk > 65 → emit to inequality_pulse (feedback loop)
  - VIX > 30 → emit to all dashboards
- Publishes: CrossDashboardSignal_* events

## REALTIME HANDLERS (Node.js 20 / TypeScript)

### 12. ws-connect.ts
**File**: `realtime/ws-connect.ts`
- WebSocket $connect handler
- Extracts connectionId from requestContext
- Reads queryStringParameters for subscribed dashboards (default: all)
- Writes to connectionsTable: connectionId, subscribedDashboards, connectedAt, ttl (24h)
- Returns: { statusCode: 200 }

### 13. ws-disconnect.ts
**File**: `realtime/ws-disconnect.ts`
- WebSocket $disconnect handler
- Extracts connectionId
- Deletes from connectionsTable
- Returns: { statusCode: 200 }

### 14. ws-broadcast.ts
**File**: `realtime/ws-broadcast.ts`
- DynamoDB Stream handler (triggered by dashboardStateTable changes)
- Processes INSERT/MODIFY events with NEW_AND_OLD_IMAGES
- Queries connectionsTable for subscribed clients
- Uses ApiGatewayManagementApi to post messages
- Handles stale connections (GoneException cleanup)
- Message format: { dashboard, panel, data, timestamp }

## Environment Variables Required

### All Handlers
- `AWS_REGION`: AWS region (e.g., us-east-1)

### Ingestion Handlers
- `SIGNALS_TABLE`: DynamoDB table for signal storage
- `EVENT_BUS_NAME`: EventBridge event bus name
- `FRED_API_KEY`: Federal Reserve API key
- `FINNHUB_API_KEY`: Finnhub API key
- `GCP_SA_KEY`: Base64-encoded GCP service account key (for GDELT)

### Processing Handlers
- `SIGNALS_TABLE`: DynamoDB signals table
- `DASHBOARD_STATE_TABLE`: DynamoDB dashboard state table
- `AUDIT_TABLE`: DynamoDB audit table (for vulnerability-composite)
- `EVENT_BUS_NAME`: EventBridge event bus name

### Realtime Handlers
- `CONNECTIONS_TABLE`: DynamoDB connections table
- `WEBSOCKET_ENDPOINT`: API Gateway WebSocket endpoint URL

## Dependencies Summary

### Python
- boto3==1.26.137 (all handlers)
- pytrends==4.9.2 (trends-poller)
- yfinance==0.2.36 (yfinance-streamer)

### TypeScript/Node.js
- @aws-sdk/client-dynamodb@^3.400.0
- @aws-sdk/client-apigatewaymanagementapi@^3.400.0
- @aws-sdk/util-dynamodb@^3.400.0
- aws-lambda@^1.0.7

## Key Implementation Details

### Error Handling
- All handlers include try/catch or try/except blocks
- Retry logic (3 attempts) for external API calls
- Stale connection cleanup in WebSocket broadcast handler
- Structured JSON logging for CloudWatch

### DynamoDB Operations
- Batch writes for efficiency (batch_size=25)
- TTL enabled (30 days for signals, 24h for connections)
- Partition keys: dashboard (GSI for queries)
- Sort keys: compound keys for multi-level organization

### EventBridge Publishing
- All processing handlers publish standardized events
- Source format: mvt.{layer}.{component} (e.g., mvt.ingestion.inequality)
- Detail includes timestamps and relevant metrics

### Scoring & Normalization
- Consistent 0-100 scale across all scores
- Weighted composite scoring with validation
- Specialized normalization for each data type (VIX, GDP, debt, etc.)
- Contagion paths computed via regional correlation clustering

## File Locations

All files are in: `/sessions/jolly-inspiring-goodall/mnt/WorldIsMyOyester/mvt-trilogy/cdk/lib/handlers/`

Python handlers:
- Each has its own `index.py` and `requirements.txt`

TypeScript handlers:
- Shared `package.json` and `tsconfig.json` in `realtime/` directory
- Individual handler files: `ws-connect.ts`, `ws-disconnect.ts`, `ws-broadcast.ts`
- Compile with: `npm run build` in realtime directory

## Deployment Considerations

1. **Python handlers**: Package each with requirements.txt for Lambda layer creation
2. **TypeScript handlers**: Compile with tsc, bundle dist/ folder with node_modules
3. **Environment variables**: Set via CloudFormation/CDK parameters
4. **IAM permissions**: Handlers need DynamoDB RW, EventBridge PutEvents, and service-specific API permissions
5. **VPC**: Optional for database access (DynamoDB in same account doesn't require VPC)

## Testing Recommendations

- Unit test scoring algorithms with known input/output
- Integration test EventBridge publishing with Lambda destinations
- Load test WebSocket broadcast with multiple concurrent connections
- Validate DynamoDB stream triggering with test records
- Monitor Lambda duration and memory allocation for optimization
