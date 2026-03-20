# MVT Observatory — Live Demo Walkthrough

**Duration**: 5–7 minutes
**Audience**: Technical stakeholders, engineering leadership
**Dashboard URL**: https://d2p9otbgwjwwuv.cloudfront.net

---

## Pre-Demo Checklist

- [ ] Open dashboard in Chrome (full-screen recommended)
- [ ] Verify WebSocket connection (green "Connected" banner at top)
- [ ] Have AWS Console open in second tab (Lambda, DynamoDB, CloudWatch)
- [ ] Have Telegram bot chat open on phone or desktop
- [ ] Ensure all 6 pollers ran within last 15 minutes (check EventBridge)

---

## Act 1: The Big Picture (1 min)

**Open**: Overview tab (default landing page)

> "Welcome to the MVT Observatory — a multi-cloud FinTech platform that monitors global macro-economic vulnerability in real time. What you're seeing is a live dashboard fed by 6 data pollers pulling from FRED, Finnhub, Yahoo Finance, World Bank, and GDELT — all processed through 21 AWS Lambda functions."

**Point out the 4 headline KPIs**:

- **Distress Index** — composite score (0–100) aggregating all macro signals
- **Trigger Probability** — likelihood of a systemic event within 30 days
- **Contagion Risk** — cross-border financial contagion probability
- **VIX Level** — real-time CBOE Volatility Index

> "These numbers update in real time via WebSocket push — no polling from the browser. The sparkline charts below each KPI show the trend over the last 24 hours."

---

## Act 2: Deep-Dive Tabs (2 min)

### Tab 2: Inequality Pulse

**Click**: Inequality tab

> "This tab tracks domestic economic inequality signals from the Federal Reserve's FRED API. We monitor CPI year-over-year change, the Fed Funds rate, 10-year Treasury yield, M2 money supply growth, and unemployment — all indicators that historically precede economic distress."

**Highlight**: The gauge charts and trend direction arrows.

### Tab 3: Sentiment Seismic

**Click**: Sentiment tab

> "Here we aggregate market sentiment from Finnhub news sentiment and Yahoo Finance price data. The system scores sentiment across 11 S&P sectors and tracks SPY and QQQ as market proxies. When sentiment diverges sharply from price action, that's a leading indicator."

### Tab 4: Sovereign Dominoes

**Click**: Sovereign tab

> "This is our contagion model. Using World Bank data — external debt ratios, reserves, current account balances — we rank countries by sovereign risk and model cascade probability. If Argentina defaults, what happens to Brazil? That's what the domino chain visualizes."

---

## Act 3: SRE Observatory (1 min)

**Click**: SRE Observatory tab

> "Tab 5 is our operational command center. The health score at the top is a weighted composite: Lambda health (30%), DynamoDB (25%), data freshness (25%), WebSocket (10%), and CloudFront (10%)."

**Walk through sections**:

- **Component Health Badges** — green/yellow/red for each AWS service
- **Cost Breakdown** — daily Lambda cost by category (ingestion, processing, realtime, cross-cloud, SRE)
- **Performance Baselines** — P50/P90/P99 latency per function, with outlier flags
- **Capacity Planning** — Lambda concurrency utilization meter, DynamoDB storage, cost projections

> "The cost anomaly detector runs hourly and alerts via Telegram if daily spend exceeds $5, error rates spike above 5%, or DynamoDB throttles exceed threshold."

---

## Act 4: Real-Time Architecture (1 min)

**Switch to**: AWS Console → DynamoDB → mvt-signals table

> "Under the hood, each poller writes signals to DynamoDB with `Decimal(str(value))` precision — never floats, because DynamoDB will reject them. Five processing Lambdas consume these signals via EventBridge, compute composite scores, and write to the dashboard-state table."

**Show**: DynamoDB Streams → ws-broadcast Lambda

> "DynamoDB Streams trigger the ws-broadcast Lambda, which pushes updates to all connected browsers via API Gateway WebSocket. The dashboard you saw updates without any browser-side polling."

---

## Act 5: Cross-Cloud Pipeline (45 sec)

**Show**: Architecture diagram or AWS Console → Lambda → mvt-event-relay

> "Every signal and alert is also relayed to Google Cloud Platform via the event-relay Lambda. It publishes to GCP Pub/Sub topics, which trigger Cloud Functions that write to BigQuery for historical analytics and Firestore for real-time state sync. This gives us a fully redundant cross-cloud architecture."

**Mention**: 3 Pub/Sub topics (signals, alerts, analytics), each with dead-letter queues.

---

## Act 6: SDLC Agents & Telegram Bot (45 sec)

**Show**: Telegram bot on phone/desktop

> "The platform includes an AI-powered SDLC system. Through Telegram, you can run sprint commands:"

- `/kickoff_sprint` — Claude AI plans sprint stories from the JIRA backlog
- `/sprint_status` — real-time progress across all stories
- `/approve_release` — triggers the deploy agent for canary deployment
- `/brainstorm` — AI-assisted technical brainstorming

> "Behind the scenes, an orchestrator coordinates four sub-agents: Code Agent (generates PRs), Test Agent (runs coverage), Deploy Agent (canary rollback), and Docs Agent (Confluence updates). All powered by Claude API."

---

## Closing (30 sec)

> "To summarize: MVT Observatory processes 6 real-time data feeds through 21 Lambda functions across AWS and GCP, renders 5 dashboard tabs with WebSocket push, monitors its own health with SRE tooling, and manages its own development lifecycle through AI agents. The entire platform was built across 5 sprints with 51 stories — 43 complete, 4 partial, 4 planned for Sprint 6."

**Questions?**

---

## Appendix: Key Metrics for Q&A

| Metric | Value |
|--------|-------|
| Lambda functions | 21 (AWS) + 3 (GCP) |
| DynamoDB tables | 4 |
| Pub/Sub topics | 3 + 3 DLQs |
| BigQuery tables | 4 + 1 view |
| Unit tests | 139 passing |
| Smoke tests | 35 (26 without AWS creds) |
| Dashboard tabs | 5 |
| Data sources | 6 (FRED, Finnhub, Yahoo, WorldBank, GDELT, Google Trends) |
| WebSocket latency | <100ms broadcast |
| Estimated daily cost | <$2.00 (Lambda + DynamoDB on-demand) |
