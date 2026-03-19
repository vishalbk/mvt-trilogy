# MVT Sprint Automation - Technical Architecture

## Overview

The MVT Sprint Automation system implements a sophisticated orchestration engine that automates the complete software development lifecycle for JIRA sprints. It uses specialized subagents to handle different domains (code, testing, deployment, documentation) and coordinates their execution through a master orchestrator.

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Telegram Bot Interface                       │
│                   (Lambda: handler.py)                          │
│  Commands: /brainstorm, /create-epic, /kickoff-sprint, etc.   │
└──────────────────────┬──────────────────────────────────────────┘
                       │ HTTP/WebSocket
┌──────────────────────▼──────────────────────────────────────────┐
│           Master Orchestrator (orchestrator.py)                 │
│                                                                  │
│  ┌─ SprintOrchestrator                                         │
│  │  - load_sprint() → reads JIRA                               │
│  │  - resolve_dependencies() → topological sort                │
│  │  - create_execution_waves() → parallel grouping             │
│  │  - execute_wave() → asyncio concurrent execution            │
│  │  - request_approval() → Telegram gate                       │
│  │  - deploy_to_production() → GitHub Actions                  │
│  │  - generate_sprint_report() → Confluence                    │
│  └─ Main loop: sprint → deps → waves → execute → approve → deploy → report
└──────┬────────┬───────────┬────────────┬───────────┬──────────┘
       │        │           │            │           │
     ┌─▼─┐  ┌──▼──┐  ┌─────▼──┐  ┌────▼────┐  ┌───▼────┐
     │   │  │     │  │        │  │         │  │        │
     │   │  │     │  │        │  │         │  │        │
     │   │  │     │  │        │  │         │  │        │
     └───┘  └─────┘  └────────┘  └─────────┘  └────────┘
      Code   Test    Deploy        Docs       External
      Agent  Agent   Agent         Agent      Systems
```

## Component Details

### 1. Master Orchestrator (`agents/orchestrator.py`)

**Purpose:** Coordinates entire sprint execution pipeline

**Key Classes:**

#### SprintOrchestrator
```python
class SprintOrchestrator:
    def __init__(self, config: Dict)
    async def load_sprint(self, sprint_id: str) -> List[Story]
    def resolve_dependencies(self, stories: List[Story]) -> Dict[str, Set[str]]
    def create_execution_waves(self, stories: List[Story]) -> List[List[Story]]
    def assign_to_agent(self, story: Story) -> str
    async def execute_wave(self, wave: List[Story]) -> List[ExecutionResult]
    async def execute_story(self, story: Story) -> ExecutionResult
    async def request_approval(self, stories: List[Story]) -> str
    async def deploy_to_production(self, stories: List[Story]) -> bool
    async def generate_sprint_report(self, sprint_id: str) -> str
    async def run(self, sprint_id: str) -> bool
```

#### Data Models
```python
@dataclass
class Story:
    key: str
    summary: str
    description: str
    status: str
    component: str
    labels: List[str]
    linked_issues: List[str]
    acceptance_criteria: List[str]
    assignee: Optional[str]
    story_points: int

    def requires_approval(self) -> bool

@dataclass
class ExecutionResult:
    story_key: str
    agent_type: str
    status: str  # "success", "failed", "blocked"
    output: Dict[str, Any]
    error: Optional[str]
    duration: float
```

#### Integration Clients
- `JiraClient` - REST API v3 with auth & retry logic
- `GithubClient` - GitHub API for branch/workflow management
- `TelegramClient` - Telegram Bot API for notifications

**Workflow:**
```
1. Load Sprint (JIRA API)
   ↓
2. Resolve Dependencies (Graph analysis)
   ↓
3. Create Execution Waves (Grouping by dependency)
   ↓
4. Execute Each Wave (Parallel subagent execution)
   ├─ Story → Assign Agent (label-based routing)
   ├─ Call Claude API with story context
   ├─ Transition JIRA story status
   └─ Collect ExecutionResult
   ↓
5. Check for Failures (Abort if any failed)
   ↓
6. Request Approval (If security-critical)
   ├─ Send Telegram message with buttons
   ├─ Wait for callback
   └─ Continue on approval
   ↓
7. Deploy to Production (GitHub Actions)
   ├─ Trigger workflow
   ├─ Monitor deployment
   └─ Update JIRA
   ↓
8. Generate Sprint Report (Confluence)
   └─ Post analytics and results
```

### 2. Code Agent (`agents/subagents/code_agent.py`)

**Purpose:** Generate production-ready code from JIRA stories

**Key Class:**
```python
class CodeAgent:
    async def execute(self, story: Dict) -> CodeGenResult
    def analyze_story(self, story: Dict) -> str
    async def _generate_implementation(self, story, story_type, context) -> Dict[str, str]
    def _validate_code(self, files: Dict) -> List[str]
    def _build_pr_description(self, story, files) -> str
```

**Story Type Detection:**
- `lambda_handler` → AWS Lambda (Python)
- `react_component` → React UI (TypeScript)
- `cdk_construct` → CDK infrastructure (Python)
- `openapi_spec` → API specification (YAML)
- `database_migration` → Database schema
- `generic` → Other files

**Process:**
```
Story Input
  ↓
Analyze Type (label-based classification)
  ↓
Create Feature Branch (feature/MVT-{id}-{slug})
  ↓
Get Repo Context (file structure, patterns)
  ↓
Generate Code (Claude API)
  ├─ Build prompt with story details
  ├─ Call Claude with context
  ├─ Parse generated files from response
  └─ Extract code from markdown blocks
  ↓
Validate Code (syntax, imports, completeness)
  ├─ Python: compile() check
  ├─ Check for TODO markers
  └─ Validate imports
  ↓
Create Files (GitHub API)
  ├─ Encode content as base64
  ├─ Create/update via REST API
  └─ Use feature branch
  ↓
Create Pull Request (with story reference)
  ├─ Generate description from story
  ├─ Include acceptance criteria checklist
  └─ Link to JIRA issue
  ↓
CodeGenResult Output (branch, pr_url, files, validation_errors)
```

### 3. Test Agent (`agents/subagents/test_agent.py`)

**Purpose:** Run tests, security scans, and coverage analysis

**Key Class:**
```python
class TestAgent:
    async def execute(self, pr_ref: str, repo_path: str) -> TestResult
    def _run_tests(self, repo_path: str) -> Dict[str, int]
    async def _run_security_scan(self, repo_path: str) -> List[str]
    def _scan_for_secrets(self, repo_path: str) -> List[str]
    def _scan_for_injection(self, repo_path: str) -> List[str]
    async def _claude_security_analysis(self, repo_path: str) -> List[str]
    def _get_coverage(self, repo_path: str) -> float
```

**Test Execution:**
```
PR Reference Input
  ↓
Run Test Suites
  ├─ Try pytest (Python) with --json-report
  ├─ Try npm test (JavaScript) with --json
  └─ Parse test output (passed, failed, skipped)
  ↓
Run Security Scans
  ├─ Regex pattern scanning
  │  ├─ Hardcoded secrets (passwords, API keys)
  │  └─ Injection patterns (SQL, command)
  ├─ Claude vulnerability analysis
  │  └─ Authentication, input validation, crypto
  └─ Aggregate results
  ↓
Get Coverage Metrics
  ├─ coverage report (Python)
  ├─ nyc report (JavaScript)
  └─ Extract percentage
  ↓
Generate Test Report
  ├─ Summary statistics
  ├─ Failed test list
  ├─ Security issues
  └─ Coverage percentage
  ↓
TestResult Output (passed, failed, coverage, security_issues, report_url)
```

**Security Patterns:**
```python
# Hardcoded secrets
password\s*=\s*['"]\w-!@#$%^&*()+['"']
api[_-]?key\s*=\s*['"]\w-+['"']
secret\s*=\s*['"]\w-+['"']

# Injection vulnerabilities
f['"'].*\+.*['"']                    # f-strings with concatenation
\.format\(.*\+.*\)                   # .format() with concatenation
execute\(['"']SELECT.*\+             # SQL concatenation
subprocess\..*shell=True             # Unsafe subprocess
```

### 4. Deploy Agent (`agents/subagents/deploy_agent.py`)

**Purpose:** Orchestrate deployment with canary validation and rollback

**Key Classes:**
```python
class CloudWatchClient:
    def get_metrics(self, namespace: str, metric_name: str, duration: int) -> Dict

class DeployAgent:
    async def execute(self, merged_branch: str, repo_path: str) -> DeployResult
    def _validate_cdk(self, repo_path: str) -> bool
    def _get_cdk_diff(self, repo_path: str) -> str
    async def _deploy_to_staging(self, repo_path: str) -> str
    async def _run_integration_tests(self, endpoint: str) -> bool
    async def _deploy_canary(self, repo_path: str) -> tuple[bool, Dict]
    def _monitor_canary(self, initial_metrics: Dict) -> bool
    async def _promote_canary(self, repo_path: str) -> str
    async def _rollback(self, repo_path: str, deployment_id: str) -> bool
```

**Deployment Strategy (Blue-Green with Canary):**
```
Input: Merged branch
  ↓
1. Validate Infrastructure
   ├─ cdk synth (CloudFormation validation)
   ├─ cdk diff (preview changes)
   └─ Abort if validation fails
  ↓
2. Deploy to Staging
   ├─ cdk deploy --stage staging
   ├─ Get staging endpoint
   └─ Return staging_url
  ↓
3. Integration Test Staging
   ├─ Smoke test (GET /health)
   ├─ Check status code 200
   └─ Abort if tests fail
  ↓
4. Deploy Canary to Production
   ├─ cdk deploy --stage production
   ├─ Configure Lambda alias weighted routing
   ├─ Route 10% traffic to new version
   └─ Return deployment_id
  ↓
5. Monitor Canary (5 minutes)
   ├─ Poll CloudWatch metrics (1-minute intervals)
   ├─ Check error rate < 1%
   ├─ Check latency p99 < 500ms
   ├─ Check no throttles
   └─ Rollback immediately if threshold exceeded
  ↓
6. Promote Canary to 100%
   ├─ Update Lambda alias to 100% new version
   ├─ Verify promotion
   └─ Return production_url
  ↓
DeployResult Output (status, staging_url, production_url, canary_metrics, duration)
```

**Monitoring Metrics:**
```
- Error rate: 0% - 1% (threshold)
- Latency p99: target < 500ms
- Invocations: tracking only
- Throttles: 0 allowed
- Duration: 5-minute window
```

**Rollback Trigger:**
- Error rate > 1%
- Lambda timeout
- Canary monitoring failure
- Integration test failure at any stage

### 5. Docs Agent (`agents/subagents/docs_agent.py`)

**Purpose:** Generate Confluence documentation automatically

**Key Class:**
```python
class DocsAgent:
    async def generate_sprint_report(self, sprint_id, stories, results) -> DocsResult
    async def generate_design_doc(self, story_key, summary, criteria) -> DocsResult
    async def generate_api_documentation(self, api_spec: Dict) -> DocsResult
    async def generate_release_notes(self, version, stories, breaking_changes) -> DocsResult
```

**Document Types:**

1. **Sprint Report**
   - Executive summary
   - Metrics (velocity, completion rate)
   - Completed stories list
   - Issues and blockers
   - Next sprint recommendations

2. **Design Documents**
   - Problem statement
   - Proposed solution
   - Architecture/design diagrams
   - API/interface changes
   - Data model
   - Security considerations
   - Testing strategy
   - Rollout plan

3. **API Documentation**
   - Overview and authentication
   - Resource endpoints
   - Request/response examples
   - Error handling
   - Rate limits
   - Code samples

4. **Release Notes**
   - Release highlights
   - New features
   - Bug fixes
   - Breaking changes
   - Migration guide
   - Known issues
   - Upgrade instructions

**Process:**
```
Documentation Request
  ↓
Generate Content (Claude API)
  ├─ Build prompt with context
  ├─ Call Claude with max_tokens=4096
  └─ Get wiki markup response
  ↓
Create Confluence Page
  ├─ Get space ID from space key
  ├─ Create page via REST API v2
  ├─ Include parent ID if available
  └─ Get page URL
  ↓
DocsResult Output (page_id, page_url, page_title)
```

### 6. Telegram Bot (`telegram-bot/handler.py`)

**Purpose:** Provide command interface for sprint management

**Key Class:**
```python
class TelegramBotHandler:
    async def handle_message(self, message: Dict) -> Dict
    async def handle_callback(self, callback: Dict) -> Dict
    async def _handle_command(self, chat_id: str, text: str) -> str
    async def _handle_natural_language(self, chat_id: str, text: str) -> str
```

**Commands:**

```
/brainstorm [topic]
├─ Generate 5-7 ideas using Claude
├─ Create draft Confluence page
└─ Send ideas to chat

/create-epic [summary]
├─ Generate child stories using Claude
├─ Create JIRA epic
├─ Create child stories from generated list
└─ Send epic key to chat

/kickoff-sprint
├─ Create JIRA sprint
├─ Move backlog items to sprint
├─ Trigger orchestrator Lambda
└─ Send confirmation to chat

/sprint-status
├─ Query JIRA board
├─ Aggregate status (total, completed, in-progress, to-do)
├─ Format status message
└─ Send to chat

/approve-release [callback_data]
├─ Trigger GitHub Actions deploy workflow
├─ Update JIRA issues to DONE
└─ Send confirmation

/sprint-report
├─ Trigger Docs Agent
├─ Wait for Confluence page creation
├─ Send link to chat

/help
└─ Show available commands and descriptions
```

**Inline Buttons (Callbacks):**
```
✅ Approve    → callback_data: "approve:{request_id}"
❌ Reject     → callback_data: "reject:{request_id}"
👀 Review     → Direct URL link to Confluence
```

**Natural Language Processing:**
- Falls back to Claude for unrecognized input
- Analyzes intent (brainstorm, create, status, approve, etc.)
- Asks for clarification if needed
- Keeps responses under 100 words

**Lambda Integration:**
```python
def lambda_handler(event: Dict, context: Any) -> Dict:
    # Parse Telegram update
    # Route to handle_message or handle_callback
    # Return response with status 200
```

## Data Flow

### Sprint Execution Flow
```
User: /kickoff-sprint
  ↓
Telegram Bot
  ├─ Create JIRA sprint
  ├─ Move stories to sprint
  └─ Trigger orchestrator
  ↓
Master Orchestrator
  ├─ Load sprint from JIRA
  ├─ Resolve dependencies
  ├─ Create execution waves
  └─ Execute each wave
     ├─ For each story:
     │  ├─ Assign to agent (Code/Test/Deploy/Docs)
     │  ├─ Call Claude API
     │  ├─ Transition JIRA status
     │  └─ Collect result
     ├─ Code Agent → PR
     ├─ Test Agent → PR comments
     ├─ Deploy Agent → staging + canary
     └─ Docs Agent → Confluence pages
  ├─ Request approval (if needed)
  ├─ Deploy to production
  ├─ Generate report
  └─ Send completion notification
  ↓
Telegram Bot (completion message)
  └─ User sees sprint report link
```

### Story Lifecycle
```
Story Status in JIRA: TO DO
  ↓ (orchestrator.execute_story)
Status: IN DEV
  ├─ Code Agent generates implementation
  ├─ GitHub PR created
  └─ Code pushed to feature branch
  ↓
Status: IN TEST
  ├─ Test Agent runs tests
  ├─ Security scan performed
  └─ Coverage report generated
  ↓
Status: APPROVED
  ├─ If requires_approval: request Telegram confirmation
  └─ Wait for approval
  ↓
Status: DONE
  ├─ Merge PR
  ├─ Deploy Agent runs deployment
  ├─ Canary validated
  └─ Promoted to production
  ↓
Story Complete (in report)
```

## Configuration Management

### Environment Variable Hierarchy
```
1. Environment (runtime)
   ↓ (if not set)
2. .env file (development)
   ↓ (if not set)
3. config.py defaults
   ↓ (fallback)
4. Hardcoded defaults
```

### Configuration Files
- `agents/config.py` - Shared agent configuration
- `telegram-bot/config.py` - Bot-specific configuration
- `.env` (not in repo) - Local development secrets

### Component-to-Agent Routing
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

## Error Handling and Resilience

### Retry Strategy
```
Request → Try 1
  ├─ Success → Return
  ├─ Timeout → Wait 1s, Try 2
  │  ├─ Success → Return
  │  └─ Timeout → Wait 2s, Try 3
  │     ├─ Success → Return
  │     └─ Timeout → Raise TimeoutError
  └─ Rate limit (429) → Exponential backoff → Retry
```

### Circuit Breaker Pattern
```
Closed State (normal)
  ├─ Request succeeds → Stay closed
  └─ Failure threshold exceeded → Open
     ↓
Open State (fail-fast)
  ├─ Requests fail immediately
  └─ After timeout → Half-open
     ↓
Half-Open State (testing recovery)
  ├─ Allow single request
  ├─ Success → Close
  └─ Failure → Open (restart timeout)
```

### Validation Checkpoints
1. JIRA API response validation (schema)
2. GitHub PR creation verification
3. Code syntax checking (compile)
4. Test result parsing
5. Deployment status confirmation

## Performance Characteristics

### Concurrency Model
- Parallel wave execution (asyncio)
- Max 3 stories per wave (configurable)
- Subagent execution is async
- Telegram commands are fire-and-forget (callback later)

### Timeouts
```
Code generation: 60s
Test execution: 300s (5min)
Deployment: 600s (10min)
Canary monitoring: 5min fixed
Code validation: immediate
JIRA queries: 30s
GitHub API: 30s
Telegram: 10s
```

### Rate Limits
```
JIRA: 60 req/min
GitHub: 60 req/min
Claude API: 50 req/min
Telegram: No limit
```

## Security

### Authentication
- JIRA: Basic auth (email + API token)
- GitHub: OAuth token
- Telegram: Bot token (in webhook body)
- Confluence: Basic auth (email + token)
- Claude: API key (in Authorization header)

### Data Protection
- Secrets not logged (redacted in logs)
- No credentials in error messages
- HTTPS for all external calls
- IAM roles for AWS services (Lambda)
- DynamoDB encryption at rest

### Secret Scanning
- Regex patterns for common secrets
- Claude vulnerability analysis
- Pre-commit hooks (future)
- Dependency scanning (future)

## Monitoring and Observability

### Structured Logging
```json
{
  "timestamp": "2025-03-18T10:30:45.123Z",
  "level": "INFO",
  "action": "execute_story",
  "story_key": "MVT-101",
  "agent_type": "code",
  "status": "success",
  "duration": 45.2
}
```

### Metrics
- Story execution duration
- Wave execution duration
- Test coverage percentage
- Deployment metrics (error rate, latency)
- Approval time
- Sprint completion rate

### CloudWatch Integration
- Lambda logs
- Custom metrics (story duration, wave size)
- Error rate monitoring
- Performance dashboards

### Alerts
- Orchestrator failure
- Wave execution timeout
- Test failures
- Deployment rollback
- Approval timeout

## Deployment

### Local Development
```bash
pip install -r agents/requirements.txt
python agents/orchestrator.py
```

### AWS Lambda (Telegram Bot)
```bash
zip -r lambda.zip telegram-bot/
aws lambda update-function-code --function-name mvt-telegram-bot --zip-file fileb://lambda.zip
```

### CI/CD Integration
- GitHub Actions for PR testing
- CodePipeline for deployment
- CloudFormation for infrastructure
- CDK for infrastructure as code

## Future Enhancements

1. **Architect Agent** - Infrastructure design and CDK generation
2. **Enhanced Claude Integration** - Vision models for diagram understanding
3. **Multi-sprint Orchestration** - Cross-sprint dependency tracking
4. **Analytics Dashboard** - Velocity trends, team metrics
5. **Cost Optimization** - AWS cost tracking and optimization
6. **Team Collaboration** - PR review routing, mention management
7. **Learning Loop** - Feedback on generated code quality

## References

- [JIRA REST API v3](https://developer.atlassian.com/cloud/jira/rest/v3)
- [GitHub REST API](https://docs.github.com/en/rest)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [Confluence REST API v2](https://developer.atlassian.com/cloud/confluence/rest/v2)
- [AWS CDK](https://docs.aws.amazon.com/cdk)
- [Claude API](https://docs.anthropic.com)
