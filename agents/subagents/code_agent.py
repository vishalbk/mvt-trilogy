"""
Code Agent - Specialized agent for writing implementation code.

Receives JIRA stories, generates implementation code, creates PR,
and validates the code before submission.
"""

import asyncio
import json
import logging
import re
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

    async def execute(self, story: Dict[str, Any]) -> CodeGenResult:
        """Execute story: analyze, generate code, create PR."""
        logger.info(
            json.dumps({
                "action": "code_agent_execute",
                "story_key": story.get("key"),
            })
        )

        try:
            # Analyze story
            story_type = self.analyze_story(story)

            # Create feature branch
            branch_name = self._create_branch_name(story)
            if not self.github.create_branch(branch_name):
                raise RuntimeError(f"Failed to create branch {branch_name}")

            # Get repo context
            repo_context = self._get_repo_context()

            # Generate implementation
            files_to_create = await self._generate_implementation(
                story,
                story_type,
                repo_context,
            )

            # Create files in branch
            for file_path, content in files_to_create.items():
                message = f"feat({branch_name}): {story.get('summary', 'Update')}"
                if not self.github.create_file(branch_name, file_path, content, message):
                    raise RuntimeError(f"Failed to create file {file_path}")

            # Validate generated code
            validation_errors = self._validate_code(files_to_create)

            # Create pull request
            pr_url = self.github.create_pull_request(
                title=f"{story.get('key')}: {story.get('summary')}",
                body=self._build_pr_description(story, files_to_create),
                head_branch=branch_name,
            )

            if not pr_url:
                raise RuntimeError("Failed to create pull request")

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
                branch="",
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
            message = self.claude.messages.create(
                model=self.config["claude"].model,
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
            return {
                "structure": [
                    {"name": item.get("name"), "type": item.get("type")}
                    for item in structure[:20]  # Limit output
                ],
                "patterns": [
                    "Use async/await for I/O operations",
                    "Follow Black code style for Python",
                    "Use TypeScript for frontend code",
                    "Include type hints on all functions",
                ],
            }
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_get_repo_context",
                    "error": str(e),
                })
            )
            return {"structure": [], "patterns": []}

    def _create_branch_name(self, story: Dict[str, Any]) -> str:
        """Create feature branch name from story."""
        story_key = story.get("key", "UNKNOWN")
        summary = story.get("summary", "feature").lower()
        # Slugify summary: remove special chars, replace spaces with hyphens
        slug = re.sub(r"[^a-z0-9-]", "", summary.replace(" ", "-"))[:30]
        return f"feature/{story_key}-{slug}".lower()


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
