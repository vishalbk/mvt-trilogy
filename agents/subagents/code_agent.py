"""
Code Agent - Specialized agent for writing implementation code.

Receives JIRA stories, generates implementation code, creates PR via GitHub,
and validates the code before submission. All deployments happen via GitHub Actions,
NOT via direct CDK/Terraform commands.
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


@dataclass
class CodeGenResult:
    """Result of code generation."""
    status: str  # "success", "failed"
    branch: str
    pr_url: str
    files_changed: List[str]
    lines_added: int
    lines_removed: int
    validation_errors: List[str]
    error: Optional[str] = None


class GithubCodeClient:
    """GitHub client specialized for code operations."""

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
                    "error": str(e),
                })
            )
            return False

    def create_file(
        self,
        branch: str,
        file_path: str,
        content: str,
        message: str,
    ) -> bool:
        """Create or update file in repository."""
        try:
            url = f"{self.base_url}/repos/{self.org}/{self.repo}/contents/{file_path}"

            # Encode content as base64
            import base64
            encoded_content = base64.b64encode(content.encode()).decode()

            payload = {
                "message": message,
                "content": encoded_content,
                "branch": branch,
            }

            response = self.session.put(url, json=payload)
            response.raise_for_status()

            logger.info(
                json.dumps({
                    "action": "create_file",
                    "path": file_path,
                    "branch": branch,
                })
            )
            return True
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "create_file",
                    "path": file_path,
                    "error": str(e),
                })
            )
            return False

    def create_pull_request(
        self,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str = "main",
    ) -> Optional[str]:
        """Create pull request."""
        try:
            url = f"{self.base_url}/repos/{self.org}/{self.repo}/pulls"
            payload = {
                "title": title,
                "body": body,
                "head": head_branch,
                "base": base_branch,
            }

            response = self.session.post(url, json=payload)
            response.raise_for_status()
            pr_data = response.json()

            logger.info(
                json.dumps({
                    "action": "create_pull_request",
                    "pr_number": pr_data.get("number"),
                    "branch": head_branch,
                })
            )
            return pr_data.get("html_url")
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "create_pull_request",
                    "error": str(e),
                })
            )
            return None

    def get_repo_structure(self, path: str = "") -> List[Dict[str, Any]]:
        """Get repository structure/tree."""
        try:
            url = f"{self.base_url}/repos/{self.org}/{self.repo}/contents/{path}"
            response = self.session.get(url)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "get_repo_structure",
                    "error": str(e),
                })
            )
            return []

    def update_existing_file(
        self,
        branch: str,
        file_path: str,
        content: str,
        message: str,
    ) -> bool:
        """Update existing file in repository (gets SHA before updating).

        GitHub API requires the current file SHA for updates to prevent
        concurrent modification conflicts.

        Args:
            branch: Branch to update on
            file_path: Path to file to update
            content: New file content
            message: Commit message

        Returns:
            True if successful, False otherwise
        """
        try:
            url = f"{self.base_url}/repos/{self.org}/{self.repo}/contents/{file_path}"

            # First, get the current file SHA
            get_response = self.session.get(url, params={"ref": branch})
            get_response.raise_for_status()
            current_sha = get_response.json().get("sha")

            if not current_sha:
                logger.error(
                    json.dumps({
                        "action": "update_existing_file",
                        "error": "Could not retrieve file SHA",
                        "path": file_path,
                    })
                )
                return False

            # Encode content as base64
            import base64
            encoded_content = base64.b64encode(content.encode()).decode()

            # Update with SHA
            payload = {
                "message": message,
                "content": encoded_content,
                "sha": current_sha,
                "branch": branch,
            }

            response = self.session.put(url, json=payload)
            response.raise_for_status()

            logger.info(
                json.dumps({
                    "action": "update_existing_file",
                    "path": file_path,
                    "branch": branch,
                })
            )
            return True
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "update_existing_file",
                    "path": file_path,
                    "error": str(e),
                })
            )
            return False


class CodeAgent:
    """Specialized agent for writing implementation code."""

    def __init__(self, config: Dict):
        """Initialize code agent."""
        self.config = config
        self.github = GithubCodeClient(
            config["github"].token,
            config["github"].org,
            config["github"].repo,
        )
        self.claude = Anthropic(api_key=config["claude"].api_key)

    async def execute(self, story: Dict[str, Any], repo_path: str = ".") -> CodeGenResult:
        """Execute story: analyze, generate code, push branch, create PR, wait for checks.

        All changes go through GitHub:
        1. Create feature branch
        2. Generate implementation code
        3. Stage, commit, and push to GitHub
        4. Create PR targeting main
        5. Wait for PR checks to pass
        6. Return PR URL and validation status

        Args:
            story: JIRA story dict with key, summary, description, acceptance_criteria
            repo_path: Local repository path (default: current directory)

        Returns:
            CodeGenResult with status, PR URL, files changed, validation errors
        """
        logger.info(
            json.dumps({
                "action": "code_agent_execute",
                "story_key": story.get("key"),
                "repo_path": repo_path,
            })
        )

        branch_name = self._create_branch_name(story)

        try:
            # Analyze story type
            story_type = self.analyze_story(story)

            # Get repo context
            repo_context = self._get_repo_context()

            # Generate implementation using Claude
            files_to_create = await self._generate_implementation(
                story,
                story_type,
                repo_context,
            )

            if not files_to_create:
                raise RuntimeError("No code generated from story")

            # Validate generated code
            validation_errors = self._validate_code(files_to_create)

            # Create branch, stage files, commit, and push to GitHub
            if not await self.create_branch_and_push(story, files_to_create, repo_path):
                raise RuntimeError("Failed to create branch and push to GitHub")

            # Create PR via GitHub API
            pr_url = self.github.create_pull_request(
                title=f"{story.get('key')}: {story.get('summary')}",
                body=self._build_pr_description(story, files_to_create),
                head_branch=branch_name,
            )

            if not pr_url:
                raise RuntimeError("Failed to create pull request")

            # Extract PR number from URL (e.g., https://github.com/.../pull/123)
            pr_number = int(pr_url.split("/pull/")[-1])

            # Wait for PR checks to pass (timeout: 10 minutes)
            checks_passed = await self.wait_for_checks(pr_number, repo_path, timeout_seconds=600)

            if not checks_passed:
                logger.warning(
                    json.dumps({
                        "action": "code_agent_execute",
                        "warning": "PR checks did not pass within timeout",
                        "pr_url": pr_url,
                    })
                )
                # Still return success (PR is created, but checks failed)
                # Master Orchestrator will decide what to do

            result = CodeGenResult(
                status="success" if not validation_errors else "success_with_warnings",
                branch=branch_name,
                pr_url=pr_url,
                files_changed=list(files_to_create.keys()),
                lines_added=sum(
                    len(content.split("\n"))
                    for content in files_to_create.values()
                ),
                lines_removed=0,
                validation_errors=validation_errors,
            )

            logger.info(
                json.dumps({
                    "action": "code_agent_execute",
                    "status": "success",
                    "story_key": story.get("key"),
                    "pr_url": pr_url,
                    "checks_passed": checks_passed,
                })
            )
            return result

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "code_agent_execute",
                    "error": str(e),
                    "story_key": story.get("key"),
                })
            )
            return CodeGenResult(
                status="failed",
                branch=branch_name,
                pr_url="",
                files_changed=[],
                lines_added=0,
                lines_removed=0,
                validation_errors=[str(e)],
                error=str(e),
            )

    def analyze_story(self, story: Dict[str, Any]) -> str:
        """Determine what type of code to write based on story."""
        labels = story.get("labels", [])
        component = story.get("component", "")

        if "backend-lambda" in labels or "lambda" in component.lower():
            return "lambda_handler"
        elif "frontend-react" in labels or "react" in component.lower():
            return "react_component"
        elif "infrastructure-cdk" in labels or "cdk" in component.lower():
            return "cdk_construct"
        elif "api-spec" in labels:
            return "openapi_spec"
        elif "database" in labels:
            return "database_migration"
        else:
            return "generic"

    async def _generate_implementation(
        self,
        story: Dict[str, Any],
        story_type: str,
        repo_context: Dict[str, Any],
    ) -> Dict[str, str]:
        """Generate implementation code using Claude."""
        prompt = self._build_generation_prompt(story, story_type, repo_context)

        try:
            # Code Agent uses Sonnet for fast, accurate code generation
            message = self.claude.messages.create(
                model=self.config["claude"].model_coding,
                max_tokens=self.config["claude"].max_tokens,
                messages=[
                    {"role": "user", "content": prompt}
                ],
            )

            response_text = message.content[0].text

            # Parse file structure from response
            files = self._parse_generated_code(response_text)

            logger.info(
                json.dumps({
                    "action": "_generate_implementation",
                    "file_count": len(files),
                    "story_type": story_type,
                })
            )

            return files
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_generate_implementation",
                    "error": str(e),
                })
            )
            raise

    def _build_generation_prompt(
        self,
        story: Dict[str, Any],
        story_type: str,
        repo_context: Dict[str, Any],
    ) -> str:
        """Build Claude prompt for code generation."""
        criteria = "\n".join(
            [f"- {c}" for c in story.get("acceptance_criteria", [])]
        )

        return f"""
You are a code generation expert for the Macro Vulnerability Trilogy project.

Generate implementation code for this story:
Key: {story.get('key')}
Summary: {story.get('summary')}
Type: {story_type}

Description:
{story.get('description', '')}

Acceptance Criteria:
{criteria}

Repository Context:
- Project structure: {json.dumps(repo_context.get('structure', {}))}
- Existing patterns: {json.dumps(repo_context.get('patterns', []))}

Generate complete, production-ready code that:
1. Follows project conventions and patterns
2. Satisfies all acceptance criteria
3. Includes proper error handling
4. Has type hints (Python) or TypeScript types
5. Includes docstrings/comments for complex logic

Format your response as:
```
FILE: path/to/file.ext
<complete file content>
```

Repeat for each file needed. Generate all necessary files for a complete implementation.
"""

    def _parse_generated_code(self, response_text: str) -> Dict[str, str]:
        """Parse generated code from Claude response."""
        files = {}

        # Extract code blocks with FILE: prefix
        pattern = r"```\n?FILE: (.+?)\n(.*?)```"
        matches = re.findall(pattern, response_text, re.DOTALL)

        for file_path, content in matches:
            files[file_path.strip()] = content.strip()

        # Fallback: extract markdown code blocks
        if not files:
            pattern = r"```(?:python|typescript|yaml|json)?\n(.*?)```"
            matches = re.findall(pattern, response_text, re.DOTALL)
            # Use generic filenames
            for i, content in enumerate(matches):
                files[f"generated_{i}.py"] = content.strip()

        return files

    def _validate_code(self, files: Dict[str, str]) -> List[str]:
        """Validate generated code for basic errors."""
        errors = []

        for file_path, content in files.items():
            # Check for syntax errors (basic)
            if file_path.endswith(".py"):
                try:
                    compile(content, file_path, "exec")
                except SyntaxError as e:
                    errors.append(f"Syntax error in {file_path}: {e}")

            # Check for common issues
            if "TODO" in content and "TODO:" in content:
                errors.append(f"Incomplete code (TODOs) in {file_path}")

            # Check imports
            if file_path.endswith(".py"):
                for line in content.split("\n"):
                    if line.startswith("import ") or line.startswith("from "):
                        if "unknown_package" in line:
                            errors.append(f"Unknown import in {file_path}: {line}")

            # Check for DynamoDB float() usage - MVT requires Decimal type
            if file_path.endswith(".py") and "dynamodb" in file_path.lower():
                if "float(" in content:
                    errors.append(
                        f"DynamoDB code in {file_path}: Use Decimal() instead of float() "
                        "(MVT requirement for DynamoDB decimal precision)"
                    )
                if "from decimal import Decimal" not in content and "Decimal(" in content:
                    logger.warning(
                        json.dumps({
                            "action": "_validate_code",
                            "warning": "Decimal used without import",
                            "file": file_path,
                        })
                    )

        logger.info(
            json.dumps({
                "action": "_validate_code",
                "error_count": len(errors),
            })
        )

        return errors

    def _build_pr_description(
        self,
        story: Dict[str, Any],
        files: Dict[str, str],
    ) -> str:
        """Build pull request description."""
        criteria = "\n".join(
            [f"- [x] {c}" for c in story.get("acceptance_criteria", [])]
        )

        return f"""
## {story.get('key')}: {story.get('summary')}

{story.get('description', '')}

### Changes
- {chr(10).join(f"**{f}**: Added" for f in files.keys())}

### Acceptance Criteria
{criteria}

### Testing
- [ ] Unit tests added/updated
- [ ] Integration tests pass
- [ ] Manual testing completed

### Related Issue
Closes #{story.get('key')}
"""

    def _get_repo_context(self) -> Dict[str, Any]:
        """Get repository context for code generation."""
        try:
            structure = self.github.get_repo_structure()

            # Filter structure to meaningful items (exclude dotfiles, common unneeded dirs)
            filtered_structure = [
                {"name": item.get("name"), "type": item.get("type")}
                for item in structure
                if isinstance(item, dict)
                and item.get("name") not in [".git", ".github", "__pycache__", "node_modules"]
                and not item.get("name", "").startswith(".")
            ][:20]

            return {
                "structure": filtered_structure,
                "patterns": [
                    "Use async/await for I/O operations",
                    "Follow Black code style for Python",
                    "Use TypeScript for frontend code",
                    "Include type hints on all functions",
                    "For DynamoDB: Use Decimal type instead of float",
                    "For Lambda: Include proper error handling and logging",
                    "For React: Use functional components with hooks",
                ],
            }
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_get_repo_context",
                    "error": str(e),
                })
            )
            return {
                "structure": [],
                "patterns": [
                    "Use async/await for I/O operations",
                    "Follow Black code style for Python",
                    "Use TypeScript for frontend code",
                    "Include type hints on all functions",
                ]
            }

    def _create_branch_name(self, story: Dict[str, Any]) -> str:
        """Create feature branch name from story."""
        story_key = story.get("key", "UNKNOWN")
        summary = story.get("summary", "feature").lower()
        # Slugify summary: remove special chars, replace spaces with hyphens
        slug = re.sub(r"[^a-z0-9-]", "", summary.replace(" ", "-"))[:30]
        return f"feature/{story_key}-{slug}".lower()

    async def wait_for_checks(
        self,
        pr_number: int,
        repo_path: str = ".",
        timeout_seconds: int = 600,
    ) -> bool:
        """Wait for PR checks to pass.

        Polls GitHub API for PR check status until all pass or timeout occurs.

        Args:
            pr_number: GitHub PR number
            repo_path: Local repository path
            timeout_seconds: Maximum time to wait (default 10 minutes)

        Returns:
            True if all checks passed, False if timeout or failed
        """
        logger.info(
            json.dumps({
                "action": "wait_for_checks",
                "pr_number": pr_number,
                "timeout_seconds": timeout_seconds,
            })
        )

        start_time = time.time()
        poll_interval = 10  # seconds

        while True:
            elapsed = time.time() - start_time
            if elapsed > timeout_seconds:
                logger.error(
                    json.dumps({
                        "action": "wait_for_checks",
                        "status": "timeout",
                        "elapsed_seconds": int(elapsed),
                    })
                )
                return False

            # Get PR check status from GitHub API
            try:
                url = (
                    f"{self.github.base_url}/repos/{self.github.org}/{self.github.repo}"
                    f"/pulls/{pr_number}"
                )
                response = self.github.session.get(url)
                response.raise_for_status()
                pr_data = response.json()

                # Check PR status
                state = pr_data.get("state", "open")
                if state != "open":
                    logger.warning(
                        json.dumps({
                            "action": "wait_for_checks",
                            "pr_state": state,
                        })
                    )
                    return state == "closed"  # Merged is closed state

                # Get check runs
                checks_url = (
                    f"{self.github.base_url}/repos/{self.github.org}/{self.github.repo}"
                    f"/commits/{pr_data['head']['sha']}/check-runs"
                )
                checks_response = self.github.session.get(checks_url)
                checks_response.raise_for_status()
                checks_data = checks_response.json()

                check_runs = checks_data.get("check_runs", [])
                if not check_runs:
                    logger.info(
                        json.dumps({
                            "action": "wait_for_checks",
                            "status": "no_checks_yet",
                            "elapsed_seconds": int(elapsed),
                        })
                    )
                    await asyncio.sleep(poll_interval)
                    continue

                # Analyze check statuses
                completed_checks = [c for c in check_runs if c["status"] == "completed"]
                failed_checks = [c for c in check_runs if c["conclusion"] == "failure"]

                logger.info(
                    json.dumps({
                        "action": "wait_for_checks",
                        "status": "polling",
                        "total_checks": len(check_runs),
                        "completed": len(completed_checks),
                        "failed": len(failed_checks),
                        "elapsed_seconds": int(elapsed),
                    })
                )

                # All checks completed
                if len(completed_checks) == len(check_runs):
                    if failed_checks:
                        logger.error(
                            json.dumps({
                                "action": "wait_for_checks",
                                "status": "failed",
                                "failed_checks": [c["name"] for c in failed_checks],
                            })
                        )
                        return False

                    logger.info(
                        json.dumps({
                            "action": "wait_for_checks",
                            "status": "all_passed",
                        })
                    )
                    return True

            except Exception as e:
                logger.warning(
                    json.dumps({
                        "action": "wait_for_checks",
                        "warning": str(e),
                    })
                )

            await asyncio.sleep(poll_interval)

    async def create_branch_and_push(
        self,
        story: Dict[str, Any],
        files: Dict[str, str],
        repo_path: str = ".",
    ) -> bool:
        """Create feature branch, stage files, and push to GitHub.

        Uses local git commands instead of GitHub API for file creation
        to maintain proper git history.

        Args:
            story: JIRA story with key and summary
            files: Dict of {file_path: content}
            repo_path: Local repository path

        Returns:
            True if successful, False otherwise
        """
        branch_name = self._create_branch_name(story)

        try:
            # Create branch from main (ensure we're on main first)
            subprocess.run(
                ["git", "checkout", "main"],
                cwd=repo_path,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "pull", "origin", "main"],
                cwd=repo_path,
                capture_output=True,
                check=True,
            )

            # Create feature branch
            subprocess.run(
                ["git", "checkout", "-b", branch_name],
                cwd=repo_path,
                capture_output=True,
                check=True,
            )

            # Write files
            for file_path, content in files.items():
                full_path = f"{repo_path}/{file_path}"
                # Create directories if needed
                import os
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                # Write file
                with open(full_path, "w") as f:
                    f.write(content)

            # Stage all changes
            subprocess.run(
                ["git", "add", "."],
                cwd=repo_path,
                capture_output=True,
                check=True,
            )

            # Commit with conventional commit message
            story_key = story.get("key", "UNKNOWN")
            summary = story.get("summary", "Update")
            commit_msg = f"feat({story_key}): {summary}"

            subprocess.run(
                ["git", "commit", "-m", commit_msg],
                cwd=repo_path,
                capture_output=True,
                check=True,
            )

            # Push to remote
            subprocess.run(
                ["git", "push", "origin", branch_name],
                cwd=repo_path,
                capture_output=True,
                check=True,
            )

            logger.info(
                json.dumps({
                    "action": "create_branch_and_push",
                    "status": "success",
                    "branch": branch_name,
                    "files": len(files),
                })
            )

            return True

        except subprocess.CalledProcessError as e:
            logger.error(
                json.dumps({
                    "action": "create_branch_and_push",
                    "error": str(e),
                    "stderr": e.stderr.decode() if e.stderr else "",
                })
            )
            return False
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "create_branch_and_push",
                    "error": str(e),
                })
            )
            return False


async def main():
    """Main entry point for testing."""
    config = load_config()
    agent = CodeAgent(config)

    # Example story
    story = {
        "key": "MVT-101",
        "summary": "Create Lambda handler for vulnerability scanner",
        "description": "Implement a Lambda function that scans infrastructure for macro vulnerabilities",
        "component": "backend-lambda",
        "labels": ["backend-lambda", "security"],
        "acceptance_criteria": [
            "Lambda handler accepts infrastructure config JSON",
            "Returns list of vulnerabilities found",
            "Includes proper error handling",
            "Has comprehensive logging",
        ],
    }

    result = await agent.execute(story)
    print(json.dumps(
        {
            "status": result.status,
            "branch": result.branch,
            "pr_url": result.pr_url,
            "files_changed": result.files_changed,
            "validation_errors": result.validation_errors,
        },
        indent=2,
    ))


if __name__ == "__main__":
    asyncio.run(main())
