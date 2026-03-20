"""
Test Agent - Specialized agent for testing and security validation.

Runs test suites, performs security scanning, posts test results,
and tracks testing coverage.
"""

import asyncio
import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from datetime import datetime

import requests
from anthropic import Anthropic

from config import load_config


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


class GitHubTestClient:
    """GitHub client specialized for test workflow operations."""

    def __init__(self, token: str, org: str, repo: str):
        """Initialize GitHub test client."""
        self.token = token
        self.org = org
        self.repo = repo
        self.base_url = "https://api.github.com"
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        })

    def trigger_test_workflow(
        self,
        branch: str,
        workflow_name: str = "pr-checks.yml",
    ) -> Optional[str]:
        """Trigger a test workflow via GitHub Actions API.

        Args:
            branch: Branch to run tests on
            workflow_name: Workflow file name (default: pr-checks.yml)

        Returns:
            Workflow run ID if successful, None otherwise
        """
        try:
            url = (
                f"{self.base_url}/repos/{self.org}/{self.repo}/actions/workflows/"
                f"{workflow_name}/dispatches"
            )
            payload = {
                "ref": branch,
                "inputs": {},
            }
            response = self.session.post(url, json=payload)
            response.raise_for_status()

            logger.info(
                json.dumps({
                    "action": "trigger_test_workflow",
                    "workflow": workflow_name,
                    "branch": branch,
                })
            )
            return "workflow_triggered"  # GitHub returns 204, so we return a marker

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "trigger_test_workflow",
                    "workflow": workflow_name,
                    "error": str(e),
                })
            )
            return None

    def get_workflow_run_status(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Get test workflow run status.

        Args:
            run_id: Workflow run ID

        Returns:
            Dict with status and conclusion, None on error
        """
        try:
            url = f"{self.base_url}/repos/{self.org}/{self.repo}/actions/runs/{run_id}"
            response = self.session.get(url)
            response.raise_for_status()
            data = response.json()

            return {
                "status": data.get("status"),  # queued, in_progress, completed
                "conclusion": data.get("conclusion"),  # success, failure, neutral, cancelled
                "run_id": data.get("id"),
                "name": data.get("name"),
            }
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "get_workflow_run_status",
                    "error": str(e),
                })
            )
            return None

    def get_workflow_run_logs(self, run_id: str) -> Optional[str]:
        """Download and parse workflow run logs.

        Args:
            run_id: Workflow run ID

        Returns:
            Combined logs from all jobs, None on error
        """
        try:
            # Get all jobs in the run
            jobs_url = f"{self.base_url}/repos/{self.org}/{self.repo}/actions/runs/{run_id}/jobs"
            jobs_response = self.session.get(jobs_url)
            jobs_response.raise_for_status()
            jobs = jobs_response.json().get("jobs", [])

            all_logs = []

            # Get logs for each job
            for job in jobs:
                job_id = job.get("id")
                job_name = job.get("name")
                logs_url = f"{self.base_url}/repos/{self.org}/{self.repo}/actions/jobs/{job_id}/logs"

                logs_response = self.session.get(logs_url)
                if logs_response.status_code == 200:
                    logs_text = logs_response.text
                    all_logs.append(f"=== {job_name} ===\n{logs_text}")

            return "\n\n".join(all_logs) if all_logs else None

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "get_workflow_run_logs",
                    "error": str(e),
                })
            )
            return None

    def get_latest_workflow_run(
        self,
        workflow_name: str,
        branch: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get the latest run of a workflow.

        Args:
            workflow_name: Workflow file name
            branch: Optional branch filter

        Returns:
            Latest workflow run data, None on error
        """
        try:
            url = (
                f"{self.base_url}/repos/{self.org}/{self.repo}/actions/workflows/"
                f"{workflow_name}/runs"
            )
            params = {"per_page": 1}
            if branch:
                params["head"] = branch

            response = self.session.get(url, params=params)
            response.raise_for_status()
            runs = response.json().get("workflow_runs", [])

            return runs[0] if runs else None

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "get_latest_workflow_run",
                    "error": str(e),
                })
            )
            return None


@dataclass
class TestResult:
    """Result of test execution."""
    status: str  # "passed", "failed"
    total_tests: int
    passed: int
    failed: int
    skipped: int
    coverage_percent: float
    security_issues: List[str]
    report_url: str
    duration: float


class TestAgent:
    """Specialized agent for testing and validation."""

    def __init__(self, config: Dict):
        """Initialize test agent."""
        self.config = config
        self.claude = Anthropic(api_key=config["claude"].api_key)
        self.github = GitHubTestClient(
            config["github"].token,
            config["github"].org,
            config["github"].repo,
        )

    async def execute(
        self,
        pr_ref: str,
        repo_path: str,
        use_github_actions: bool = True,
    ) -> TestResult:
        """Execute test suite for PR.

        Can use either GitHub Actions workflow (preferred) or local subprocess tests.

        Args:
            pr_ref: PR reference (branch name or PR number)
            repo_path: Local repository path
            use_github_actions: If True, use GitHub Actions; if False, run locally

        Returns:
            TestResult with test status and metrics
        """
        logger.info(
            json.dumps({
                "action": "test_agent_execute",
                "pr_ref": pr_ref,
                "use_github_actions": use_github_actions,
            })
        )

        try:
            start_time = datetime.now()

            test_results = {}
            security_issues = []
            coverage = 0.0
            report_url = ""

            if use_github_actions:
                # Trigger GitHub Actions workflow
                test_results, security_issues, coverage, report_url = (
                    await self._run_tests_via_github_actions(pr_ref)
                )
            else:
                # Fall back to local test execution
                test_results = self._run_tests(repo_path)
                security_issues = await self._run_security_scan(repo_path)
                coverage = self._get_coverage(repo_path)
                report_url = f"https://example.com/test-reports/{pr_ref}"

            duration = (datetime.now() - start_time).total_seconds()

            result = TestResult(
                status="passed" if test_results.get("failed", 0) == 0 else "failed",
                total_tests=test_results.get("total", 0),
                passed=test_results.get("passed", 0),
                failed=test_results.get("failed", 0),
                skipped=test_results.get("skipped", 0),
                coverage_percent=coverage,
                security_issues=security_issues,
                report_url=report_url,
                duration=duration,
            )

            logger.info(
                json.dumps({
                    "action": "test_agent_execute",
                    "status": result.status,
                    "passed": result.passed,
                    "failed": result.failed,
                    "coverage": result.coverage_percent,
                })
            )

            return result

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "test_agent_execute",
                    "error": str(e),
                })
            )
            return TestResult(
                status="failed",
                total_tests=0,
                passed=0,
                failed=1,
                skipped=0,
                coverage_percent=0.0,
                security_issues=[str(e)],
                report_url="",
                duration=0.0,
            )

    async def _run_tests_via_github_actions(
        self,
        branch: str,
        workflow_name: str = "pr-checks.yml",
        timeout_seconds: int = 900,
    ) -> tuple[Dict[str, int], List[str], float, str]:
        """Run tests via GitHub Actions workflow.

        Triggers the workflow and monitors until completion.

        Args:
            branch: Branch to run tests on
            workflow_name: Workflow file name
            timeout_seconds: Max time to wait for workflow

        Returns:
            Tuple of (test_results, security_issues, coverage, report_url)
        """
        logger.info(
            json.dumps({
                "action": "_run_tests_via_github_actions",
                "branch": branch,
                "workflow": workflow_name,
            })
        )

        # Trigger workflow
        trigger_result = self.github.trigger_test_workflow(
            branch=branch,
            workflow_name=workflow_name,
        )

        if not trigger_result:
            logger.error(
                json.dumps({
                    "action": "_run_tests_via_github_actions",
                    "error": "Failed to trigger workflow",
                })
            )
            return {}, [], 0.0, ""

        # Wait for latest run to appear and complete
        start_time = time.time()
        poll_interval = 10  # seconds

        while True:
            elapsed = time.time() - start_time
            if elapsed > timeout_seconds:
                logger.error(
                    json.dumps({
                        "action": "_run_tests_via_github_actions",
                        "status": "timeout",
                    })
                )
                return {}, [], 0.0, ""

            run = self.github.get_latest_workflow_run(
                workflow_name=workflow_name,
                branch=branch,
            )

            if not run:
                await asyncio.sleep(poll_interval)
                continue

            run_id = run.get("id")
            status = run.get("status")
            conclusion = run.get("conclusion")

            logger.info(
                json.dumps({
                    "action": "_run_tests_via_github_actions",
                    "status": status,
                    "conclusion": conclusion,
                    "elapsed_seconds": int(elapsed),
                })
            )

            # Wait for completion
            if status != "completed":
                await asyncio.sleep(poll_interval)
                continue

            # Workflow completed - parse results from logs
            logs = self.github.get_workflow_run_logs(run_id)
            test_results = self._parse_github_actions_logs(logs or "")
            security_issues = self._extract_security_issues_from_logs(logs or "")
            coverage = self._extract_coverage_from_logs(logs or "")

            report_url = f"https://github.com/{self.github.org}/{self.github.repo}/actions/runs/{run_id}"

            logger.info(
                json.dumps({
                    "action": "_run_tests_via_github_actions",
                    "status": "completed",
                    "conclusion": conclusion,
                    "passed": test_results.get("passed", 0),
                    "failed": test_results.get("failed", 0),
                })
            )

            return test_results, security_issues, coverage, report_url

    def _parse_github_actions_logs(self, logs: str) -> Dict[str, int]:
        """Parse test results from GitHub Actions logs.

        Args:
            logs: Combined logs from workflow jobs

        Returns:
            Dict with total, passed, failed, skipped counts
        """
        results = {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
        }

        try:
            # Try to parse pytest output
            pytest_match = re.search(
                r"(\d+) passed|(\d+) failed|(\d+) skipped",
                logs,
            )
            if pytest_match:
                passed = int(pytest_match.group(1)) if pytest_match.group(1) else 0
                failed = int(pytest_match.group(2)) if pytest_match.group(2) else 0
                skipped = int(pytest_match.group(3)) if pytest_match.group(3) else 0
                return {
                    "total": passed + failed + skipped,
                    "passed": passed,
                    "failed": failed,
                    "skipped": skipped,
                }

            # Try to parse jest output
            jest_match = re.search(r"Tests:\s+(\d+) passed,\s+(\d+) total", logs)
            if jest_match:
                passed = int(jest_match.group(1))
                total = int(jest_match.group(2))
                return {
                    "total": total,
                    "passed": passed,
                    "failed": total - passed,
                    "skipped": 0,
                }
        except Exception as e:
            logger.warning(
                json.dumps({
                    "action": "_parse_github_actions_logs",
                    "error": str(e),
                })
            )

        return results

    def _extract_security_issues_from_logs(self, logs: str) -> List[str]:
        """Extract security issue summaries from workflow logs.

        Args:
            logs: Workflow logs

        Returns:
            List of security issue strings
        """
        issues = []

        try:
            # Look for common security scanner outputs
            if "bandit" in logs.lower():
                bandit_matches = re.findall(
                    r"Issue: (.*?) \(Severity: (\w+)\)",
                    logs,
                )
                for issue, severity in bandit_matches[:5]:
                    issues.append(f"{severity.upper()}: {issue}")

            if "safety" in logs.lower():
                safety_matches = re.findall(
                    r"(\d+) known security vulnerability",
                    logs,
                )
                if safety_matches:
                    issues.append(f"Found {safety_matches[0]} known security vulnerabilities")

        except Exception as e:
            logger.warning(
                json.dumps({
                    "action": "_extract_security_issues_from_logs",
                    "error": str(e),
                })
            )

        return issues[:5]  # Limit to 5 issues

    def _extract_coverage_from_logs(self, logs: str) -> float:
        """Extract test coverage percentage from logs.

        Args:
            logs: Workflow logs

        Returns:
            Coverage percentage (0-100)
        """
        try:
            # Look for coverage.py output
            match = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", logs)
            if match:
                return float(match.group(1))

            # Look for nyc/Istanbul output
            match = re.search(r"Statements\s+:\s+(\d+\.?\d*)%", logs)
            if match:
                return float(match.group(1))

            # Look for generic coverage output
            match = re.search(r"(\d+\.?\d*)%\s+coverage", logs)
            if match:
                return float(match.group(1))

        except Exception as e:
            logger.warning(
                json.dumps({
                    "action": "_extract_coverage_from_logs",
                    "error": str(e),
                })
            )

        return 0.0

    def _run_tests(self, repo_path: str) -> Dict[str, int]:
        """Run test suites (pytest, jest, etc)."""
        results = {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
        }

        try:
            # Try pytest first (Python)
            try:
                output = subprocess.run(
                    ["pytest", "--tb=short", "--json-report", "--json-report-file=report.json"],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )

                # Parse pytest output
                if output.returncode == 0 or "passed" in output.stdout:
                    results = self._parse_pytest_output(output.stdout)
                else:
                    logger.warning(
                        json.dumps({
                            "action": "_run_tests",
                            "tool": "pytest",
                            "stderr": output.stderr[:500],
                        })
                    )

            except FileNotFoundError:
                logger.info(
                    json.dumps({
                        "action": "_run_tests",
                        "status": "pytest_not_found",
                    })
                )

            # Try jest if pytest not available (JavaScript)
            try:
                output = subprocess.run(
                    ["npm", "test", "--", "--json", "--outputFile=test-results.json"],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )

                if output.returncode == 0 or "pass" in output.stdout:
                    results = self._parse_jest_output(output.stdout)

            except FileNotFoundError:
                logger.info(
                    json.dumps({
                        "action": "_run_tests",
                        "status": "jest_not_found",
                    })
                )

            logger.info(
                json.dumps({
                    "action": "_run_tests",
                    "total": results["total"],
                    "passed": results["passed"],
                    "failed": results["failed"],
                })
            )

            return results

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_run_tests",
                    "error": str(e),
                })
            )
            return results

    def _parse_pytest_output(self, output: str) -> Dict[str, int]:
        """Parse pytest output."""
        # Look for pytest summary line: "passed, failed, skipped"
        match = re.search(
            r"(\d+) passed|(\d+) failed|(\d+) skipped",
            output,
        )

        passed = int(match.group(1)) if match else 0
        failed = int(match.group(2)) if match else 0
        skipped = int(match.group(3)) if match else 0

        return {
            "total": passed + failed + skipped,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
        }

    def _parse_jest_output(self, output: str) -> Dict[str, int]:
        """Parse jest output."""
        # Look for jest summary
        match = re.search(r"Tests:\s+(\d+) passed,\s+(\d+) total", output)

        if match:
            passed = int(match.group(1))
            total = int(match.group(2))
            return {
                "total": total,
                "passed": passed,
                "failed": total - passed,
                "skipped": 0,
            }

        return {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
        }

    async def _run_security_scan(self, repo_path: str) -> List[str]:
        """Run security scanning for common vulnerabilities."""
        issues = []

        try:
            # Scan for hardcoded secrets
            secret_issues = self._scan_for_secrets(repo_path)
            issues.extend(secret_issues)

            # Scan for SQL injection patterns
            injection_issues = self._scan_for_injection(repo_path)
            issues.extend(injection_issues)

            # Use Claude for deeper analysis
            claude_issues = await self._claude_security_analysis(repo_path)
            issues.extend(claude_issues)

            logger.info(
                json.dumps({
                    "action": "_run_security_scan",
                    "issue_count": len(issues),
                })
            )

            return issues

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_run_security_scan",
                    "error": str(e),
                })
            )
            return [str(e)]

    def _scan_for_secrets(self, repo_path: str) -> List[str]:
        """Scan for hardcoded secrets."""
        issues = []
        secret_patterns = [
            r"password\s*=\s*['\"][\w\-!@#$%^&*()]+['\"]",
            r"api[_-]?key\s*=\s*['\"][\w\-]+['\"]",
            r"secret\s*=\s*['\"][\w\-]+['\"]",
            r"token\s*=\s*['\"][\w\-]+['\"]",
        ]

        try:
            # Scan Python files
            result = subprocess.run(
                ["grep", "-r", "-E", "|".join(secret_patterns), repo_path],
                capture_output=True,
                text=True,
            )

            for line in result.stdout.split("\n"):
                if line:
                    issues.append(f"Potential hardcoded secret found: {line[:100]}")

        except Exception as e:
            logger.warning(
                json.dumps({
                    "action": "_scan_for_secrets",
                    "error": str(e),
                })
            )

        return issues[:5]  # Limit to 5 issues

    def _scan_for_injection(self, repo_path: str) -> List[str]:
        """Scan for SQL injection and command injection patterns."""
        issues = []
        injection_patterns = [
            r"f['\"].*\+.*['\"]",  # f-strings with concatenation
            r"\.format\(.*\+.*\)",  # .format() with concatenation
            r"execute\(['\"]SELECT.*\+",  # SQL concatenation
            r"subprocess\..*shell=True",  # Dangerous subprocess usage
        ]

        try:
            result = subprocess.run(
                ["grep", "-r", "-E", "|".join(injection_patterns), repo_path],
                capture_output=True,
                text=True,
            )

            for line in result.stdout.split("\n"):
                if line:
                    issues.append(f"Potential injection vulnerability: {line[:100]}")

        except Exception as e:
            logger.warning(
                json.dumps({
                    "action": "_scan_for_injection",
                    "error": str(e),
                })
            )

        return issues[:5]  # Limit to 5 issues

    async def _claude_security_analysis(self, repo_path: str) -> List[str]:
        """Use Claude for deeper security analysis."""
        try:
            # Read sample files for analysis
            sample_code = self._read_sample_files(repo_path, limit=3)

            if not sample_code:
                return []

            prompt = f"""
You are a security expert reviewing code for vulnerabilities.

Analyze this code for security issues:

{sample_code}

Identify:
1. Authentication/Authorization issues
2. Input validation problems
3. Cryptographic weaknesses
4. Insecure dependencies
5. Configuration security

Return a JSON array of issues with format:
[
  {{"severity": "high|medium|low", "type": "...", "description": "..."}}
]

Return only valid JSON, no other text.
"""

            # Test Agent uses Sonnet for security analysis
            message = self.claude.messages.create(
                model=self.config["claude"].model_coding,
                max_tokens=1024,
                messages=[
                    {"role": "user", "content": prompt}
                ],
            )

            response_text = message.content[0].text
            # Extract JSON
            json_match = re.search(r"\[.*\]", response_text, re.DOTALL)
            if json_match:
                issues_data = json.loads(json_match.group())
                return [
                    f"{i['severity'].upper()}: {i['type']} - {i['description']}"
                    for i in issues_data[:5]
                ]

            return []

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_claude_security_analysis",
                    "error": str(e),
                })
            )
            return []

    def _read_sample_files(self, repo_path: str, limit: int = 3) -> str:
        """Read sample code files for analysis."""
        try:
            result = subprocess.run(
                ["find", repo_path, "-name", "*.py", "-o", "-name", "*.ts"],
                capture_output=True,
                text=True,
            )

            files = result.stdout.split("\n")[:limit]
            content = []

            for file_path in files:
                if not file_path or not file_path.endswith((".py", ".ts")):
                    continue

                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()[:50]  # First 50 lines
                        content.append(f"# {file_path}\n{''.join(lines)}")
                except Exception:
                    pass

            return "\n\n".join(content)

        except Exception as e:
            logger.warning(
                json.dumps({
                    "action": "_read_sample_files",
                    "error": str(e),
                })
            )
            return ""

    def _get_coverage(self, repo_path: str) -> float:
        """Get test coverage percentage."""
        try:
            # Try coverage.py (Python)
            result = subprocess.run(
                ["coverage", "report"],
                cwd=repo_path,
                capture_output=True,
                text=True,
            )

            # Parse coverage output
            match = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", result.stdout)
            if match:
                return float(match.group(1))

        except Exception:
            pass

        try:
            # Try nyc (JavaScript)
            result = subprocess.run(
                ["npm", "run", "coverage"],
                cwd=repo_path,
                capture_output=True,
                text=True,
            )

            match = re.search(r"Statements\s+:\s+(\d+\.?\d*)%", result.stdout)
            if match:
                return float(match.group(1))

        except Exception:
            pass

        # Default to 0
        return 0.0


async def main():
    """Main entry point for testing."""
    config = load_config()
    agent = TestAgent(config)

    result = await agent.execute("mvt-101-feature", "/repo")
    print(json.dumps(
        {
            "status": result.status,
            "passed": result.passed,
            "failed": result.failed,
            "coverage": result.coverage_percent,
            "security_issues": result.security_issues,
        },
        indent=2,
    ))


if __name__ == "__main__":
    asyncio.run(main())
