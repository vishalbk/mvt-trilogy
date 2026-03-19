# Macro Vulnerability Trilogy (MVT) - Sprint Automation System

Production-grade sprint automation platform for the Macro Vulnerability Trilogy project. Orchestrates story execution across specialized subagents with JIRA integration, GitHub workflows, and Telegram command interface.

## Architecture

```
Master Orchestrator (agents/orchestrator.py)
├── Code Agent (agents/subagents/code_agent.py)
│   └── Generates implementation code, creates PRs, validates syntax
├── Test Agent (agents/subagents/test_agent.py)
│   └── Runs tests, performs security scans, generates reports
├── Deploy Agent (agents/subagents/deploy_agent.py)
│   └── CDK deployment, staging validation, canary deployment, rollback
├── Docs Agent (agents/subagents/docs_agent.py)
│   └── Generates Confluence pages, release notes, API documentation
└── Telegram Bot (telegram-bot/handler.py)
    └── Command interface for sprint management and approval gates
```

## Components

### Master Orchestrator (`agents/orchestrator.py`)

Coordinates entire sprint execution with dependency resolution and parallel execution waves.

**Key Classes:**
- `SprintOrchestrator` - Main orchestration engine
- `JiraClient` - JIRA REST API integration
- `GithubClient` - GitHub REST API integration
- `TelegramClient` - Telegram Bot API integration

**Core Methods:**
```python
# Load and execute sprint
await orchestrator.load_sprint(sprint_id)
await orchestrator.run(sprint_id)

# Dependency resolution
dependencies = orchestrator.resolve_dependencies(stories)

# Execution waves (parallel execution groups)
waves = orchestrator.create_execution_waves(stories)

# Approval gate
request_id = await orchestrator.request_approval(stories)

# Production deployment
await orchestrator.deploy_to_production(stories)

# Reporting
report_url = await orchestrator.generate_sprint_report(sprint_id)
```

**Workflow:**
1. Load sprint stories from JIRA
2. Resolve story dependencies (topological sort)
3. Group stories into execution waves (max 3 per wave)
4. Execute each wave concurrently with appropriate subagent
5. Request approval for security-critical stories
6. Deploy to production via GitHub Actions
7. Generate Confluence sprint report

### Code Agent (`agents/subagents/code_agent.py`)

Generates implementation code from JIRA stories.

**Methods:**
```python
result = await agent.execute(story)
# Returns: CodeGenResult with branch, pr_url, files_changed, validation_errors
```

**Features:**
- Analyzes story type (Lambda, React, CDK, API spec, database)
- Generates production-ready code using Claude
- Creates feature branch: `feature/MVT-{id}-{slug}`
- Creates pull request with auto-linked story
- Validates code (syntax, imports, completeness)

**Supported Code Types:**
- AWS Lambda handlers (Python)
- React components (TypeScript)
- CDK constructs (Python)
- OpenAPI specifications
- Database migrations
- Configuration files

### Test Agent (`agents/subagents/test_agent.py`)

Runs test suites, performs security scanning, tracks coverage.

**Methods:**
```python
result = await agent.execute(pr_ref, repo_path)
# Returns: TestResult with passed, failed, coverage, security_issues
```

**Features:**
- Runs pytest (Python) or jest (JavaScript)
- Security scanning:
  - Hardcoded secrets detection
  - SQL/command injection patterns
  - Claude-powered vulnerability analysis
- Coverage report generation
- Posts results to PR

**Security Checks:**
- Regex patterns for common vulnerabilities
- AI-powered code analysis via Claude
- Secret detection (API keys, tokens, passwords)
- Injection vulnerability patterns

### Deploy Agent (`agents/subagents/deploy_agent.py`)

Handles deployment with canary rollout and automatic rollback.

**Methods:**
```python
result = await agent.execute(merged_branch, repo_path)
# Returns: DeployResult with staging_url, production_url, canary_metrics
```

**Deployment Process:**
1. Validate CloudFormation/CDK
2. Deploy to staging environment
3. Run integration tests against staging
4. Deploy canary to production (10% traffic by default)
5. Monitor canary metrics for 5 minutes
6. Auto-rollback if error rate exceeds 1%
7. Promote to 100% if healthy

**Monitoring:**
- Error rate threshold: 1% (configurable)
- Latency monitoring (p99)
- CloudWatch metrics integration
- Auto-rollback on failure

### Docs Agent (`agents/subagents/docs_agent.py`)

Generates Confluence documentation.

**Methods:**
```python
# Sprint report
result = await agent.generate_sprint_report(sprint_id, stories, results)

# Design documents
result = await agent.generate_design_doc(story_key, summary, criteria)

# API documentation
result = await agent.generate_api_documentation(openapi_spec)

# Release notes
result = await agent.generate_release_notes(version, stories, breaking_changes)
```

**Features:**
- Confluence REST API v2 integration
- Claude-powered content generation
- Proper wiki markup formatting
- Story linking
- Automatic page creation

### Telegram Bot (`telegram-bot/handler.py`)

AWS Lambda webhook handler for Telegram Bot API.

**Commands:**
```
/brainstorm [topic]      - Generate ideas, create draft page
/create-epic [summary]   - Create JIRA epic with stories
/kickoff-sprint          - Start sprint and run orchestrator
/sprint-status           - Get current board status
/approve-release         - Approve production deployment
/sprint-report           - Generate Confluence report
/help                    - Show available commands
```

**Inline Buttons:**
- ✅ Approve release
- ❌ Reject release
- 👀 Review (link to Confluence)

**Features:**
- Command parsing and routing
- Natural language fallback (via Claude)
- Approval gate with callbacks
- Conversation state in DynamoDB
- Story ideation with Claude
- JIRA integration for epic creation

## Configuration

### Environment Variables

```bash
# Telegram Bot
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_CHAT_ID="..."

# JIRA
export JIRA_BASE_URL="https://jira.example.com"
export JIRA_EMAIL="bot@example.com"
export JIRA_API_TOKEN="..."
export JIRA_PROJECT_KEY="MVT"
export JIRA_BOARD_ID="1"

# Confluence
export CONFLUENCE_URL="https://confluence.example.com"
export CONFLUENCE_EMAIL="bot@example.com"
export CONFLUENCE_TOKEN="..."

# GitHub
export GITHUB_TOKEN="..."
export GITHUB_ORG="your-org"
export GITHUB_REPO="your-repo"

# Claude
export ANTHROPIC_API_KEY="..."
export CLAUDE_MODEL="claude-3-5-sonnet-20241022"

# DynamoDB (for conversation state)
export CONVERSATION_TABLE="mvt-conversations"
export AWS_REGION="us-east-1"
```

### Configuration File (`agents/config.py`)

Centralized configuration with dataclasses:
- `JiraConfig` - JIRA connection and workflow IDs
- `GitHubConfig` - Repository details
- `TelegramConfig` - Bot credentials
- `ClaudeConfig` - Model and token limits
- `DynamoDBConfig` - State storage

**Component-to-Agent Routing:**
```python
COMPONENT_TO_AGENT = {
    "backend-lambda": "code",
    "infrastructure-cdk": "architect",
    "frontend-react": "code",
    "api-spec": "architect",
    "database": "architect",
    "security": "test",
    "docs": "docs",
    "deployment": "deploy",
}
```

**Canary Settings:**
```python
CANARY_TRAFFIC_PERCENT = 10          # Start with 10% new version
CANARY_DURATION_MINUTES = 5          # Monitor for 5 minutes
CANARY_ERROR_THRESHOLD = 1.0         # Rollback if error rate > 1%
```

## Installation

```bash
# Install dependencies
pip install -r agents/requirements.txt
pip install -r telegram-bot/requirements.txt

# Or combined
pip install -r agents/requirements.txt -r telegram-bot/requirements.txt
```

## Usage

### Running the Orchestrator

```python
import asyncio
from agents.orchestrator import SprintOrchestrator
from agents.config import load_config

async def main():
    config = load_config()
    orchestrator = SprintOrchestrator(config)

    # Run sprint execution
    success = await orchestrator.run(sprint_id="1")
    return 0 if success else 1

if __name__ == "__main__":
    exit(asyncio.run(main()))
```

### Telegram Bot Webhook

Deploy `telegram-bot/handler.py` as AWS Lambda:

```bash
# Package for Lambda
zip lambda.zip telegram-bot/handler.py telegram-bot/config.py

# Deploy to Lambda
aws lambda create-function \
  --function-name mvt-telegram-bot \
  --runtime python3.11 \
  --role arn:aws:iam::ACCOUNT:role/lambda-role \
  --handler handler.lambda_handler \
  --zip-file fileb://lambda.zip \
  --environment Variables='{...}'

# Register webhook
curl -X POST \
  https://api.telegram.org/bot{BOT_TOKEN}/setWebhook \
  -d url=https://{LAMBDA_API_ENDPOINT}/
```

### Manual Test

```bash
# Test orchestrator
python agents/orchestrator.py

# Test code agent
python agents/subagents/code_agent.py

# Test test agent
python agents/subagents/test_agent.py

# Test deploy agent
python agents/subagents/deploy_agent.py

# Test telegram bot
python telegram-bot/handler.py
```

## Logging

Structured JSON logging for CloudWatch/ELK:

```json
{
  "timestamp": "2025-03-18T10:30:45.123456",
  "level": "INFO",
  "message": {
    "action": "load_sprint",
    "sprint_id": "1",
    "story_count": 12
  }
}
```

All components use structured logging with:
- Standardized timestamp format
- JSON payload for log aggregation
- Action/context in each log
- Error details for debugging

## Error Handling

**Retry Logic:**
- HTTP requests: 3 retries with exponential backoff
- Rate limit handling (429 status)
- Timeout handling (60-600 seconds per operation)

**Validation:**
- JIRA API response validation
- GitHub PR creation verification
- Code syntax checking
- Test result parsing

**Rollback Support:**
- Automatic canary rollback on errors
- GitHub branch cleanup on failure
- Issue transition rollback

## Performance

**Concurrency:**
- Parallel wave execution (up to 3 stories per wave)
- Async/await throughout
- CloudWatch metrics polling

**Timeouts:**
- Code generation: 60 seconds
- Test execution: 300 seconds (5 minutes)
- Deployment: 600 seconds (10 minutes)
- Canary monitoring: 5 minutes

**Rate Limiting:**
- JIRA: 60 requests/60s
- GitHub: 60 requests/60s
- Claude: 50 requests/60s

## Testing

```bash
# Unit tests (mocked integrations)
pytest tests/

# Integration tests (real JIRA/GitHub)
pytest tests/integration/ -v

# Load testing
locust -f tests/load/locustfile.py
```

## Troubleshooting

**Orchestrator fails to load sprint:**
- Check JIRA credentials and board ID
- Verify project key matches
- Check network connectivity

**Subagent execution fails:**
- Check agent assignment logic in `assign_to_agent()`
- Verify Claude API key and rate limits
- Review component labels in story

**Deployment rollback triggered:**
- Check CloudWatch metrics
- Review error rate threshold
- Check Lambda function logs

**Telegram bot not responding:**
- Verify bot token is correct
- Check webhook is registered
- Review DynamoDB table exists
- Check IAM permissions

## Contributing

1. Follow Python style guide (Black, isort)
2. Add type hints to all functions
3. Include docstrings (Google style)
4. Write tests for new agents
5. Update config.py for new settings

## License

Proprietary - Macro Vulnerability Trilogy Project

## Support

For issues or questions, open an issue or contact the MVT team.
