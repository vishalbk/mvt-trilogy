# MVT Trilogy Lambda Handlers

Complete implementation of all 14 Lambda handler functions for the Macro Vulnerability Trilogy project.

## Quick Overview

| Layer | Count | Type | Language |
|-------|-------|------|----------|
| **Ingestion** | 6 | Data polling/API calls | Python 3.12 |
| **Processing** | 5 | Scoring & aggregation | Python 3.12 |
| **Realtime** | 3 | WebSocket updates | TypeScript (Node.js 20) |

## Handler List

### Ingestion (Data Sources)
1. **fred-poller** - Federal Reserve economic indicators
2. **trends-poller** - Google Trends financial distress keywords
3. **finnhub-connector** - Market sentiment & news
4. **gdelt-querier** - Global event data & geopolitical events
5. **worldbank-poller** - Sovereign economic indicators
6. **yfinance-streamer** - Real-time market prices & FX rates

### Processing (Analytics)
7. **inequality-scorer** - Consumer distress composite score
8. **sentiment-aggregator** - Market trigger probability
9. **contagion-modeler** - Sovereign risk & cascade modeling
10. **vulnerability-composite** - Master vulnerability index
11. **cross-dashboard-router** - Signal routing between dashboards

### Realtime (WebSocket)
12. **ws-connect** - Client connection handler
13. **ws-disconnect** - Client disconnection handler
14. **ws-broadcast** - Dashboard state change broadcaster

## File Structure

```
handlers/
├── ingestion/
│   ├── fred-poller/
│   │   ├── index.py
│   │   └── requirements.txt
│   ├── trends-poller/
│   ├── finnhub-connector/
│   ├── gdelt-querier/
│   ├── worldbank-poller/
│   └── yfinance-streamer/
├── processing/
│   ├── inequality-scorer/
│   ├── sentiment-aggregator/
│   ├── contagion-modeler/
│   ├── vulnerability-composite/
│   └── cross-dashboard-router/
├── realtime/
│   ├── ws-connect.ts
│   ├── ws-disconnect.ts
│   ├── ws-broadcast.ts
│   ├── package.json
│   └── tsconfig.json
├── HANDLERS_SUMMARY.md    (Detailed documentation)
└── README.md              (This file)
```

## Key Features

### Consistent Architecture
- Standard error handling with retries
- Structured JSON logging for CloudWatch
- Environment variable configuration
- DynamoDB for state management
- EventBridge for event publishing

### Three Dashboard System
- **inequality_pulse**: Consumer financial stress signals
- **sentiment_seismic**: Market sentiment & volatility indicators
- **sovereign_dominoes**: Geopolitical & sovereign risk

### Composite Scoring
- Normalized 0-100 scales
- Weighted multi-factor models
- Cross-dashboard feedback loops
- Audit trail for historical analysis

## Deployment

### Python Handlers
Each Python handler requires:
1. `index.py` - Handler code
2. `requirements.txt` - Dependencies
3. Lambda runtime: Python 3.12
4. Handler name: `index.lambda_handler`

### TypeScript Handlers
WebSocket handlers in `realtime/`:
1. Compile with TypeScript compiler
2. Bundle with node_modules
3. Lambda runtime: Node.js 20
4. Handler names vary (see package.json for build output)

### Environment Variables
See HANDLERS_SUMMARY.md for complete list. Key tables:
- `SIGNALS_TABLE` - Signal ingestion storage
- `DASHBOARD_STATE_TABLE` - Current scores & states
- `AUDIT_TABLE` - Historical records
- `CONNECTIONS_TABLE` - Active WebSocket clients
- `EVENT_BUS_NAME` - EventBridge bus

## API Keys Required
- `FRED_API_KEY` - Federal Reserve API
- `FINNHUB_API_KEY` - Finnhub market data
- `GCP_SA_KEY` - Google Cloud service account (base64)

## Documentation
See **HANDLERS_SUMMARY.md** for:
- Detailed handler descriptions
- Weighted scoring algorithms
- Threshold rules
- Event formats
- Complete environment variable reference

## Build Commands

### Python (if needed)
```bash
# Install dependencies
pip install -r ingestion/fred-poller/requirements.txt
```

### TypeScript
```bash
cd realtime/
npm install
npm run build
```

## Testing Notes
- All handlers include error handling and validation
- DynamoDB operations use batch writes for efficiency
- External API calls implement 3-attempt retries
- WebSocket broadcast handles stale connections gracefully
- Scores validated to 0-100 range

---

**Created**: 2024
**Language**: Python 3.12 + TypeScript (Node.js 20)
**AWS Services**: Lambda, DynamoDB, EventBridge, API Gateway WebSocket
