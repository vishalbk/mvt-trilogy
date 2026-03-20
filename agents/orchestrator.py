"""
MVT Master Orchestrator Agent
Coordinates sprint execution, manages dependencies, assigns stories to subagents,
and handles approval flows and deployments.

Architecture:
  Master Orchestrator
    ├── Architect Agent (CDK stacks, Terraform modules, API specs)
    ├── Code Agent x3 (Lambda handlers, React components, configs)
    ├── Test Agent (unit tests, integration tests, security scans)
    ├── Deploy Agent (CDK deploy, TF apply, canary, blue-green)
    └── Docs Agent (Confluence pages, sprint reports, release notes)
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple
from collections import defaultdict, deque

import requests
from anthropic import Anthropic

from config import (
    load_config,
    COMPONENT_TO_AGENT,
    MAX_WAVE_SIZE,
    APPROVAL_REQUIRED_LABELS,
)


# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format=json.dumps({
        "timestamp": "%(asctime)s",
        "level": "%(levelname)s",
        "message": "%(message)s",
    }),
)
logger = logging.getLogger(__name__)


@dataclass
class Story:
    """Represents a JIRA story."""
    key: str
    summary: str
    description: str
    status: str
    component: str
    labels: List[str]
    linked_issues: List[str]  # IDs of blocking/blocked issues
    acceptance_criteria: List[str]
    assignee: Optional[str] = None
    story_points: int = 0

    def requires_approval(self) -> bool:
        """Check if story requires approval gate."""
        return any(label in APPROVAL_REQUIRED_LABELS for label in self.labels)


@dataclass
class ExecutionResult:
    """Result of story execution."""
    story_key: str
    agent_type: str
    status: str  # "success", "failed", "blocked"
    output: Dict[str, Any]
    error: Optional[str] = None
    duration: float = 0.0


class JiraClient:
    """JIRA REST API client with auth and rate limiting."""

    def __init__(self, base_url: str, email: str, api_token: str):
        """Initialize JIRA client."""
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.api_token = api_token
        self.session = requests.Session()
        self.session.auth = (email, api_token)
        self.session.headers.update({"Content-Type": "application/json"})

    def get_sprint_issues(self, sprint_id: str) -> List[Story]:
        """Fetch all issues in a sprint with IN SPRINT status."""
        try:
            url = f"{self.base_url}/rest/api/3/search"
            jql = f'sprint = {sprint_id} AND status in ("IN DEV", "IN TEST", "APPROVED")'
            params = {"jql": jql, "maxResults": 100, "expand": "changelog"}

            response = self._request("GET", url, params=params)
            stories = []

            for issue in response.get("issues", []):
                story = self._parse_issue(issue)
                stories.append(story)

            logger.info(
                json.dumps({
                    "action": "get_sprint_issues",
                    "sprint_id": sprint_id,
                    "count": len(stories),
                })
            )
            return stories
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "get_sprint_issues",
                    "error": str(e),
                })
            )
            raise

    def _parse_issue(self, issue: Dict) -> Story:
        """Parse JIRA issue response into Story object."""
        fields = issue.get("fields", {})
        return Story(
            key=issue["key"],
            summary=fields.get("summary", ""),
            description=fields.get("description", ""),
            status=fields.get("status", {}).get("name", ""),
            component=fields.get("components", [{}])[0].get("name", ""),
            labels=fields.get("labels", []),
            linked_issues=self._get_linked_issues(issue),
            acceptance_criteria=self._parse_acceptance_criteria(fields),
            assignee=fields.get("assignee", {}).get("displayName"),
            story_points=fields.get("customfield_10000", 0) or 0,
        )

    def _get_linked_issues(self, issue: Dict) -> List[str]:
        """Extract linked issue keys from issue links."""
        linked = []
        for link in issue.get("fields", {}).get("issuelinks", []):
            if link.get("type", {}).get("name") in ["blocks", "is blocked by"]:
                linked_key = link.get("outwardIssue", {}).get("key") or \
                             link.get("inwardIssue", {}).get("key")
                if linked_key:
                    linked.append(linked_key)
        return linked

    def _parse_acceptance_criteria(self, fields: Dict) -> List[str]:
        """Extract acceptance criteria from description."""
        description = fields.get("description", "")
        criteria = []
        if description:
            for line in description.split("\n"):
                if line.strip().startswith("- "):
                    criteria.append(line.strip()[2:])
        return criteria

    def transition_issue(self, issue_key: str, transition_id: str, comment: str = "") -> bool:
        """Transition issue to new status."""
        try:
            url = f"{self.base_url}/rest/api/3/issue/{issue_key}/transitions"
            payload = {
                "transition": {"id": transition_id},
            }
            if comment:
                payload["update"] = {
                    "comment": [{"add": {"body": comment}}]
                }

            self._request("POST", url, json=payload)
            logger.info(
                json.dumps({
                    "action": "transition_issue",
                    "issue": issue_key,
                    "transition": transition_id,
                })
            )
            return True
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "transition_issue",
                    "issue": issue_key,
                    "error": str(e),
                })
            )
            return False

    def add_comment(self, issue_key: str, comment: str) -> bool:
        """Add comment to issue."""
        try:
            url = f"{self.base_url}/rest/api/3/issue/{issue_key}/comment"
            payload = {
                "body": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": comment}
                            ]
                        }
                    ]
                }
            }

            self._request("POST", url, json=payload)
            return True
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "add_comment",
                    "issue": issue_key,
                    "error": str(e),
                })
            )
            return False

    def _request(self, method: str, url: str, **kwargs) -> Dict:
        """Make HTTP request with retry logic."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self.session.request(method, url, **kwargs)
                response.raise_for_status()
                return response.json() if response.text else {}
            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning(
                        json.dumps({
                            "action": "_request",
                            "status": "timeout",
                            "attempt": attempt + 1,
                            "retry_in": wait_time,
                        })
                    )
                    time.sleep(wait_time)
                else:
                    raise
            except requests.exceptions.HTTPError as e:
                if response.status_code == 429:  # Rate limited
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt
                        logger.warning(
                            json.dumps({
                                "action": "_request",
                                "status": "rate_limited",
                                "retry_in": wait_time,
                            })
                        )
                        time.sleep(wait_time)
                    else:
                        raise
                else:
                    raise


class GithubClient:
    """GitHub REST API client."""

    def __init__(self, token: str, org: str, repo: str):
        """Initialize GitHub client."""
        self.token = token
        self.org = org
        self.repo = repo
        self.base_url = "https://api.github.com"
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        })

    def create_branch(self, branch_name: str, from_branch: str = "main") -> bool:
        """Create new branch from base branch."""
        try:
            # Get SHA of base branch
            url = f"{self.base_url}/repos/{self.org}/{self.repo}/git/refs/heads/{from_branch}"
            response = self.session.get(url)
            response.raise_for_status()
            base_sha = response.json()["object"]["sha"]

            # Create new branch
            create_url = f"{self.base_url}/repos/{self.org}/{self.repo}/git/refs"
            payload = {
                "ref": f"refs/heads/{branch_name}",
                "sha": base_sha,
            }
            response = self.session.post(create_url, json=payload)
            response.raise_for_status()

            logger.info(
                json.dumps({
                    "action": "create_branch",
                    "branch": branch_name,
                })
            )
            return True
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "create_branch",
                    "branch": branch_name,
                    "error": str(e),
                })
            )
            return False

    def trigger_workflow(self, workflow_name: str, ref: str = "main", **inputs) -> bool:
        """Trigger GitHub Actions workflow."""
        try:
            url = f"{self.base_url}/repos/{self.org}/{self.repo}/actions/workflows/{workflow_name}/dispatches"
            payload = {
                "ref": ref,
                "inputs": inputs,
            }
            response = self.session.post(url, json=payload)
            response.raise_for_status()

            logger.info(
                json.dumps({
                    "action": "trigger_workflow",
                    "workflow": workflow_name,
                })
            )
            return True
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "trigger_workflow",
                    "workflow": workflow_name,
                    "error": str(e),
                })
            )
            return False


class TelegramClient:
    """Telegram Bot API client."""

    def __init__(self, bot_token: str, chat_id: str):
        """Initialize Telegram client."""
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = "https://api.telegram.org"

    def send_message(self, text: str, reply_markup: Optional[Dict] = None) -> bool:
        """Send message to Telegram chat."""
        try:
            url = f"{self.base_url}/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
            }
            if reply_markup:
                payload["reply_markup"] = reply_markup

            response = requests.post(url, json=payload)
            response.raise_for_status()

            logger.info(
                json.dumps({
                    "action": "send_message",
                    "chat_id": self.chat_id,
                })
            )
            return True
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "send_message",
                    "error": str(e),
                })
            )
            return False

    def send_approval_request(
        self,
        stories: List[Story],
        request_id: str,
    ) -> bool:
        """Send approval request with inline buttons."""
        try:
            story_text = "\n".join(
                [f"• <b>{s.key}</b>: {s.summary}" for s in stories]
            )
            text = (
                f"<b>Sprint Approval Required</b>\n\n"
                f"Stories ready for approval:\n{story_text}\n\n"
                f"Review and approve to proceed to production."
            )

            reply_markup = {
                "inline_keyboard": [
                    [
                        {
                            "text": "✅ Approve",
                            "callback_data": f"approve:{request_id}",
                        },
                        {
                            "text": "❌ Reject",
                            "callback_data": f"reject:{request_id}",
                        },
                    ],
                    [
                        {
                            "text": "👀 Review",
                            "url": "https://confluence.example.com/sprint-report",
                        },
                    ],
                ]
            }

            return self.send_message(text, reply_markup)
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "send_approval_request",
                    "error": str(e),
                })
            )
            return False


class SprintOrchestrator:
    """Master orchestrator for sprint execution."""

    def __init__(self, config: Dict):
        """Initialize orchestrator with configuration."""
        self.config = config
        self.jira = JiraClient(
            config["jira"].base_url,
            config["jira"].email,
            config["jira"].api_token,
        )
        self.github = GithubClient(
            config["github"].token,
            config["github"].org,
            config["github"].repo,
        )
        self.telegram = TelegramClient(
            config["telegram"].bot_token,
            config["telegram"].chat_id,
        )
        self.claude = Anthropic(api_key=config["claude"].api_key)

        # Execution state
        self.stories: Dict[str, Story] = {}
        self.completed_stories: List[Story] = []
        self.execution_results: Dict[str, ExecutionResult] = {}
        self.blocked_stories: Set[str] = set()

    def load_sprint(self, sprint_id: str) -> List[Story]:
        """Load stories from JIRA sprint."""
        logger.info(
            json.dumps({
                "action": "load_sprint",
                "sprint_id": sprint_id,
            })
        )

        stories = self.jira.get_sprint_issues(sprint_id)
        self.stories = {s.key: s for s in stories}
        return stories

    def resolve_dependencies(self, stories: List[Story]) -> Dict[str, Set[str]]:
        """Resolve story dependencies using topological analysis."""
        dependencies = defaultdict(set)

        for story in stories:
            for linked_key in story.linked_issues:
                if linked_key in self.stories:
                    dependencies[story.key].add(linked_key)

        logger.info(
            json.dumps({
                "action": "resolve_dependencies",
                "story_count": len(stories),
                "dependency_count": len(dependencies),
            })
        )

        return dependencies

    def create_execution_waves(self, stories: List[Story]) -> List[List[Story]]:
        """Group stories into parallel execution waves."""
        dependencies = self.resolve_dependencies(stories)

        # Topological sort using Kahn's algorithm
        in_degree = defaultdict(int)
        adj_list = defaultdict(list)

        for story in stories:
            if story.key not in in_degree:
                in_degree[story.key] = 0

        for story_key, deps in dependencies.items():
            in_degree[story_key] = len(deps)
            for dep in deps:
                adj_list[dep].append(story_key)

        # Process waves
        waves = []
        processed = set()

        while processed < set(s.key for s in stories):
            # Find stories with no pending dependencies
            current_wave = [
                s for s in stories
                if s.key not in processed and in_degree[s.key] == 0
            ]

            if not current_wave:
                # Deadlock detection
                logger.error(
                    json.dumps({
                        "action": "create_execution_waves",
                        "status": "deadlock_detected",
                        "unprocessed": list(set(s.key for s in stories) - processed),
                    })
                )
                raise RuntimeError("Circular dependency detected in stories")

            # Split large waves
            for i in range(0, len(current_wave), MAX_WAVE_SIZE):
                wave = current_wave[i : i + MAX_WAVE_SIZE]
                waves.append(wave)
                processed.update(s.key for s in wave)

            # Update in-degrees
            for wave in waves[-len(current_wave) // MAX_WAVE_SIZE if len(current_wave) % MAX_WAVE_SIZE == 0 else -((len(current_wave) // MAX_WAVE_SIZE) + 1):]:
                for story in wave:
                    for dependent in adj_list[story.key]:
                        in_degree[dependent] -= 1

        logger.info(
            json.dumps({
                "action": "create_execution_waves",
                "wave_count": len(waves),
                "wave_sizes": [len(w) for w in waves],
            })
        )

        return waves

    def assign_to_agent(self, story: Story) -> str:
        """Route story to appropriate subagent based on component label."""
        for label in story.labels:
            if label in COMPONENT_TO_AGENT:
                agent = COMPONENT_TO_AGENT[label]
                logger.info(
                    json.dumps({
                        "action": "assign_to_agent",
                        "story": story.key,
                        "agent": agent,
                        "label": label,
                    })
                )
                return agent

        # Default to code agent
        agent = COMPONENT_TO_AGENT.get(story.component, "code")
        logger.info(
            json.dumps({
                "action": "assign_to_agent",
                "story": story.key,
                "agent": agent,
                "component": story.component,
            })
        )
        return agent

    async def execute_wave(self, wave: List[Story]) -> List[ExecutionResult]:
        """Execute all stories in a wave concurrently."""
        logger.info(
            json.dumps({
                "action": "execute_wave",
                "story_count": len(wave),
            })
        )

        tasks = [
            self.execute_story(story)
            for story in wave
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle exceptions
        processed_results = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(
                    json.dumps({
                        "action": "execute_wave",
                        "error": str(result),
                    })
                )
            else:
                processed_results.append(result)

        return processed_results

    async def execute_story(self, story: Story) -> ExecutionResult:
        """Execute story through full lifecycle using real subagents."""
        start_time = datetime.now()
        agent_type = self.assign_to_agent(story)

        try:
            # Transition to IN_DEV
            self.jira.transition_issue(
                story.key,
                self.config["jira"].transition_ids["in_dev"],
                f"Starting execution with {agent_type} agent",
            )

            # Dispatch to real subagent
            story_dict = {
                "key": story.key,
                "summary": story.summary,
                "description": story.description,
                "component": story.component,
                "labels": story.labels,
                "acceptance_criteria": story.acceptance_criteria,
            }

            result = {}
            if agent_type == "code":
                from subagents.code_agent import CodeAgent
                agent = CodeAgent(self.config)
                code_result = await agent.execute(story_dict)
                result = {
                    "status": code_result.status,
                    "pr_url": code_result.pr_url,
                    "branch": code_result.branch,
                    "files_changed": code_result.files_changed,
                }
            elif agent_type == "test":
                from subagents.test_agent import TestAgent
                agent = TestAgent(self.config)
                test_result = await agent.execute(story.key, ".")
                result = {
                    "status": test_result.status,
                    "passed": test_result.passed,
                    "failed": test_result.failed,
                    "coverage": test_result.coverage_percent,
                }
            elif agent_type == "deploy":
                from subagents.deploy_agent import DeployAgent
                agent = DeployAgent(self.config)
                deploy_result = await agent.execute(pr_number=0)
                result = {
                    "status": deploy_result.status,
                    "staging_url": deploy_result.staging_url,
                }
            elif agent_type == "docs":
                from subagents.docs_agent import DocsAgent
                agent = DocsAgent(self.config)
                docs_result = await agent.generate_design_doc(
                    story.key, story.summary, story.acceptance_criteria
                )
                result = {
                    "status": docs_result.status,
                    "page_url": docs_result.page_url,
                }
            else:
                # Fallback to Claude for unknown agent types
                prompt = self._build_execution_prompt(story, agent_type)
                result = await self._call_claude_agent(prompt, agent_type)

            # Transition to IN_TEST
            self.jira.transition_issue(
                story.key,
                self.config["jira"].transition_ids["in_test"],
                "Completed implementation, moving to testing",
            )

            duration = (datetime.now() - start_time).total_seconds()
            exec_result = ExecutionResult(
                story_key=story.key,
                agent_type=agent_type,
                status="success",
                output=result,
                duration=duration,
            )

            self.execution_results[story.key] = exec_result
            return exec_result

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "execute_story",
                    "story": story.key,
                    "error": str(e),
                })
            )
            duration = (datetime.now() - start_time).total_seconds()
            return ExecutionResult(
                story_key=story.key,
                agent_type=agent_type,
                status="failed",
                output={},
                error=str(e),
                duration=duration,
            )

    def _build_execution_prompt(self, story: Story, agent_type: str) -> str:
        """Build Claude prompt for story execution."""
        return f"""
You are a {agent_type} agent for the Macro Vulnerability Trilogy project.

Execute the following story:
Key: {story.key}
Summary: {story.summary}
Description: {story.description}

Acceptance Criteria:
{chr(10).join(f"- {criterion}" for criterion in story.acceptance_criteria)}

Your task:
1. Analyze the acceptance criteria
2. Plan the implementation approach
3. Generate code or documentation as needed
4. Validate the deliverable
5. Return a JSON object with: {{"status": "success", "files_changed": [...], "pr_url": "...", "details": {{...}}}}

Be thorough and ensure all acceptance criteria are met.
"""

    async def _call_claude_agent(self, prompt: str, agent_type: str) -> Dict[str, Any]:
        """Call Claude API for story execution.

        Uses Opus for orchestration/planning tasks, Sonnet for code generation.
        """
        try:
            # Orchestrator uses Opus for reasoning-heavy tasks
            model = self.config["claude"].model_reasoning
            message = self.claude.messages.create(
                model=model,
                max_tokens=self.config["claude"].max_tokens,
                messages=[
                    {"role": "user", "content": prompt}
                ],
            )

            # Parse response
            response_text = message.content[0].text
            # Extract JSON from markdown code blocks if present
            if "```json" in response_text:
                json_str = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                json_str = response_text.split("```")[1].split("```")[0].strip()
            else:
                json_str = response_text

            result = json.loads(json_str)
            return result
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_call_claude_agent",
                    "error": str(e),
                })
            )
            raise

    async def request_approval(self, stories: List[Story]) -> str:
        """Send approval request via Telegram."""
        request_id = f"approval_{datetime.now().timestamp()}"

        self.telegram.send_approval_request(stories, request_id)
        logger.info(
            json.dumps({
                "action": "request_approval",
                "request_id": request_id,
                "story_count": len(stories),
            })
        )

        return request_id

    async def deploy_to_production(self, stories: List[Story]) -> bool:
        """Trigger GitHub Actions deployment workflow."""
        try:
            # Merge PR and create release
            workflow_inputs = {
                "stories": ",".join(s.key for s in stories),
                "environment": "production",
            }

            success = self.github.trigger_workflow(
                "deploy.yml",
                **workflow_inputs,
            )

            if success:
                for story in stories:
                    self.jira.transition_issue(
                        story.key,
                        self.config["jira"].transition_ids["done"],
                        "Deployed to production",
                    )
                self.completed_stories.extend(stories)

            logger.info(
                json.dumps({
                    "action": "deploy_to_production",
                    "status": "success" if success else "failed",
                    "story_count": len(stories),
                })
            )
            return success
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "deploy_to_production",
                    "error": str(e),
                })
            )
            return False

    async def generate_sprint_report(self, sprint_id: str) -> str:
        """Generate sprint report and post to Confluence."""
        try:
            from subagents.docs_agent import DocsAgent
            docs_agent = DocsAgent(self.config)
            stories_data = [
                {"key": s.key, "summary": s.summary, "status": s.status}
                for s in self.stories.values()
            ]
            results_data = {
                key: {"status": r.status, "duration": r.duration, "agent": r.agent_type}
                for key, r in self.execution_results.items()
            }
            report = await docs_agent.generate_sprint_report(sprint_id, stories_data, results_data)

            confluence_url = report.page_url if report.status == "success" else ""

            logger.info(
                json.dumps({
                    "action": "generate_sprint_report",
                    "sprint_id": sprint_id,
                    "url": confluence_url,
                })
            )

            return confluence_url
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "generate_sprint_report",
                    "error": str(e),
                })
            )
            return ""

    def _build_sprint_report(self, sprint_id: str) -> str:
        """Build sprint report content."""
        completed = len(self.completed_stories)
        total = len(self.stories)
        completion_rate = (completed / total * 100) if total > 0 else 0

        results_summary = "\n".join([
            f"- {r.story_key}: {r.status} ({r.duration:.2f}s)"
            for r in self.execution_results.values()
        ])

        return f"""
# Sprint {sprint_id} Report

## Summary
- Total Stories: {total}
- Completed: {completed}
- Completion Rate: {completion_rate:.1f}%
- Execution Time: {sum(r.duration for r in self.execution_results.values()):.2f}s

## Execution Results
{results_summary}

## Completed Stories
{chr(10).join(f"- {s.key}: {s.summary}" for s in self.completed_stories)}
"""

    async def run(self, sprint_id: str) -> bool:
        """Main orchestration loop."""
        logger.info(
            json.dumps({
                "action": "orchestrator_start",
                "sprint_id": sprint_id,
            })
        )

        try:
            # Load sprint
            stories = self.load_sprint(sprint_id)
            if not stories:
                logger.warning(
                    json.dumps({
                        "action": "run",
                        "status": "no_stories",
                    })
                )
                return False

            # Create execution waves
            waves = self.create_execution_waves(stories)

            # Execute waves
            for i, wave in enumerate(waves):
                logger.info(
                    json.dumps({
                        "action": "execute_wave",
                        "wave": i + 1,
                        "total_waves": len(waves),
                    })
                )
                results = await self.execute_wave(wave)

                # Check for failures
                failed = [r for r in results if r.status == "failed"]
                if failed:
                    logger.error(
                        json.dumps({
                            "action": "run",
                            "status": "wave_failed",
                            "failed_stories": [r.story_key for r in failed],
                        })
                    )
                    return False

            # Request approval
            approval_required = [
                s for s in self.completed_stories
                if s.requires_approval()
            ]
            if approval_required:
                await self.request_approval(approval_required)
                # In production, wait for approval callback
                logger.info(
                    json.dumps({
                        "action": "run",
                        "status": "awaiting_approval",
                    })
                )
                return True

            # Deploy to production
            deploy_success = await self.deploy_to_production(self.completed_stories)

            # Generate report
            report_url = await self.generate_sprint_report(sprint_id)

            self.telegram.send_message(
                f"<b>Sprint {sprint_id} Complete</b>\n\n"
                f"✅ All stories deployed to production\n"
                f"📊 Report: {report_url}"
            )

            logger.info(
                json.dumps({
                    "action": "orchestrator_complete",
                    "sprint_id": sprint_id,
                    "status": "success",
                })
            )
            return deploy_success

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "run",
                    "error": str(e),
                })
            )
            return False


async def main():
    """Main entry point."""
    config = load_config()
    orchestrator = SprintOrchestrator(config)

    # Run orchestrator for sprint
    sprint_id = "1"  # Read from env or command line
    success = await orchestrator.run(sprint_id)

    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
