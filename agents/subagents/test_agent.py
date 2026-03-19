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

    async def execute(
        self,
        pr_ref: str,
        repo_path: str,
    ) -> TestResult:
        """Execute test suite for PR."""
        logger.info(
            json.dumps({
                "action": "test_agent_execute",
                "pr_ref": pr_ref,
            })
        )

        try:
            start_time = datetime.now()

            # Run test suites
            test_results = self._run_tests(repo_path)

            # Run security scans
            security_issues = await self._run_security_scan(repo_path)

            # Generate coverage report
            coverage = self._get_coverage(repo_path)

            duration = (datetime.now() - start_time).total_seconds()

            result = TestResult(
                status="passed" if test_results["failed"] == 0 else "failed",
                total_tests=test_results["total"],
                passed=test_results["passed"],
                failed=test_results["failed"],
                skipped=test_results["skipped"],
                coverage_percent=coverage,
                security_issues=security_issues,
                report_url=f"https://example.com/test-reports/{pr_ref}",
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
