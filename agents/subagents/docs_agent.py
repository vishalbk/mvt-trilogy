"""
Docs Agent - Specialized agent for documentation generation.

Generates Confluence pages for sprint reports, design docs, API docs,
and release notes. Uses Confluence REST API v2.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

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
class DocsResult:
    """Result of documentation generation."""
    status: str  # "success", "failed"
    page_id: str
    page_url: str
    page_title: str
    error: Optional[str] = None


class ConfluenceClient:
    """Confluence REST API v2 client."""

    def __init__(self, base_url: str, email: str, api_token: str):
        """Initialize Confluence client."""
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.api_token = api_token
        self.session = requests.Session()
        self.session.auth = (email, api_token)
        self.session.headers.update({"Content-Type": "application/json"})

    def create_page(
        self,
        space_id: str,
        title: str,
        body: str,
        content_format: str = "storage",
        parent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Create a new Confluence page."""
        try:
            url = f"{self.base_url}/api/v2/pages"

            payload = {
                "spaceId": space_id,
                "title": title,
                "body": {
                    "representation": content_format,
                    "value": body,
                },
            }

            if parent_id:
                payload["parentId"] = parent_id

            response = self.session.post(url, json=payload)
            response.raise_for_status()

            page_data = response.json()

            logger.info(
                json.dumps({
                    "action": "create_page",
                    "page_id": page_data.get("id"),
                    "title": title,
                })
            )

            return page_data

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "create_page",
                    "error": str(e),
                })
            )
            return None

    def update_page(
        self,
        page_id: str,
        title: str,
        body: str,
        version: int,
        content_format: str = "storage",
    ) -> bool:
        """Update an existing Confluence page."""
        try:
            url = f"{self.base_url}/api/v2/pages/{page_id}"

            payload = {
                "title": title,
                "body": {
                    "representation": content_format,
                    "value": body,
                },
                "version": {
                    "number": version + 1,
                },
            }

            response = self.session.put(url, json=payload)
            response.raise_for_status()

            logger.info(
                json.dumps({
                    "action": "update_page",
                    "page_id": page_id,
                })
            )
            return True

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "update_page",
                    "page_id": page_id,
                    "error": str(e),
                })
            )
            return False

    def get_space_id(self, space_key: str) -> Optional[str]:
        """Get space ID from space key."""
        try:
            url = f"{self.base_url}/api/v2/spaces"
            params = {"keys": space_key, "limit": 1}

            response = self.session.get(url, params=params)
            response.raise_for_status()

            data = response.json()
            if data.get("results"):
                return data["results"][0]["id"]

            return None

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "get_space_id",
                    "space_key": space_key,
                    "error": str(e),
                })
            )
            return None


class DocsAgent:
    """Specialized agent for documentation generation."""

    def __init__(self, config: Dict):
        """Initialize docs agent."""
        self.config = config
        self.confluence = ConfluenceClient(
            base_url=config.get("confluence_url", "https://confluence.example.com"),
            email=config.get("confluence_email", ""),
            api_token=config.get("confluence_token", ""),
        )
        self.claude = Anthropic(api_key=config["claude"].api_key)

    async def generate_sprint_report(
        self,
        sprint_id: str,
        stories: List[Dict[str, Any]],
        results: Dict[str, Any],
    ) -> DocsResult:
        """Generate sprint report and post to Confluence."""
        try:
            # Generate report content using Claude
            content = await self._generate_report_content(sprint_id, stories, results)

            # Create Confluence page
            space_id = self.confluence.get_space_id("MVT")
            if not space_id:
                raise RuntimeError("Could not find MVT space")

            page_data = self.confluence.create_page(
                space_id=space_id,
                title=f"Sprint {sprint_id} Report",
                body=content,
                content_format="storage",
            )

            if not page_data:
                raise RuntimeError("Failed to create Confluence page")

            page_url = f"{self.config.get('confluence_url')}/wiki/spaces/MVT/pages/{page_data['id']}"

            result = DocsResult(
                status="success",
                page_id=page_data.get("id", ""),
                page_url=page_url,
                page_title=f"Sprint {sprint_id} Report",
            )

            logger.info(
                json.dumps({
                    "action": "generate_sprint_report",
                    "page_id": page_data.get("id"),
                    "url": page_url,
                })
            )

            return result

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "generate_sprint_report",
                    "error": str(e),
                })
            )
            return DocsResult(
                status="failed",
                page_id="",
                page_url="",
                page_title="",
                error=str(e),
            )

    async def generate_design_doc(
        self,
        story_key: str,
        story_summary: str,
        acceptance_criteria: List[str],
    ) -> DocsResult:
        """Generate design document for a story."""
        try:
            # Generate design doc using Claude
            content = await self._generate_design_doc_content(
                story_key,
                story_summary,
                acceptance_criteria,
            )

            # Create Confluence page
            space_id = self.confluence.get_space_id("MVT")
            if not space_id:
                raise RuntimeError("Could not find MVT space")

            page_data = self.confluence.create_page(
                space_id=space_id,
                title=f"{story_key}: Design Doc",
                body=content,
                content_format="storage",
            )

            if not page_data:
                raise RuntimeError("Failed to create Confluence page")

            page_url = f"{self.config.get('confluence_url')}/wiki/spaces/MVT/pages/{page_data['id']}"

            result = DocsResult(
                status="success",
                page_id=page_data.get("id", ""),
                page_url=page_url,
                page_title=f"{story_key}: Design Doc",
            )

            logger.info(
                json.dumps({
                    "action": "generate_design_doc",
                    "page_id": page_data.get("id"),
                    "story": story_key,
                })
            )

            return result

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "generate_design_doc",
                    "error": str(e),
                })
            )
            return DocsResult(
                status="failed",
                page_id="",
                page_url="",
                page_title="",
                error=str(e),
            )

    async def generate_api_documentation(
        self,
        api_spec: Dict[str, Any],
    ) -> DocsResult:
        """Generate API documentation from OpenAPI spec."""
        try:
            # Generate API docs using Claude
            content = await self._generate_api_docs_content(api_spec)

            # Create Confluence page
            space_id = self.confluence.get_space_id("MVT")
            if not space_id:
                raise RuntimeError("Could not find MVT space")

            page_data = self.confluence.create_page(
                space_id=space_id,
                title="API Documentation",
                body=content,
                content_format="storage",
            )

            if not page_data:
                raise RuntimeError("Failed to create Confluence page")

            page_url = f"{self.config.get('confluence_url')}/wiki/spaces/MVT/pages/{page_data['id']}"

            result = DocsResult(
                status="success",
                page_id=page_data.get("id", ""),
                page_url=page_url,
                page_title="API Documentation",
            )

            logger.info(
                json.dumps({
                    "action": "generate_api_documentation",
                    "page_id": page_data.get("id"),
                })
            )

            return result

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "generate_api_documentation",
                    "error": str(e),
                })
            )
            return DocsResult(
                status="failed",
                page_id="",
                page_url="",
                page_title="",
                error=str(e),
            )

    async def generate_release_notes(
        self,
        version: str,
        stories: List[Dict[str, Any]],
        breaking_changes: List[str] = None,
        migration_guide: str = "",
    ) -> DocsResult:
        """Generate release notes."""
        try:
            # Generate release notes using Claude
            content = await self._generate_release_notes_content(
                version,
                stories,
                breaking_changes or [],
                migration_guide,
            )

            # Create Confluence page
            space_id = self.confluence.get_space_id("MVT")
            if not space_id:
                raise RuntimeError("Could not find MVT space")

            page_data = self.confluence.create_page(
                space_id=space_id,
                title=f"Release Notes v{version}",
                body=content,
                content_format="storage",
            )

            if not page_data:
                raise RuntimeError("Failed to create Confluence page")

            page_url = f"{self.config.get('confluence_url')}/wiki/spaces/MVT/pages/{page_data['id']}"

            result = DocsResult(
                status="success",
                page_id=page_data.get("id", ""),
                page_url=page_url,
                page_title=f"Release Notes v{version}",
            )

            logger.info(
                json.dumps({
                    "action": "generate_release_notes",
                    "page_id": page_data.get("id"),
                    "version": version,
                })
            )

            return result

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "generate_release_notes",
                    "error": str(e),
                })
            )
            return DocsResult(
                status="failed",
                page_id="",
                page_url="",
                page_title="",
                error=str(e),
            )

    async def _generate_report_content(
        self,
        sprint_id: str,
        stories: List[Dict[str, Any]],
        results: Dict[str, Any],
    ) -> str:
        """Generate sprint report content using Claude."""
        prompt = f"""
Generate a Confluence wiki markup formatted sprint report for:
Sprint: {sprint_id}
Total Stories: {len(stories)}

Stories:
{chr(10).join(f"- {s.get('key')}: {s.get('summary')}" for s in stories)}

Results:
{json.dumps(results, indent=2)}

Include:
1. Executive summary
2. Metrics (velocity, completion rate)
3. Completed stories list
4. Issues and blockers
5. Next sprint recommendations

Return only wiki markup, no markdown.
"""

        try:
            message = self.claude.messages.create(
                model=self.config["claude"].model_reasoning,  # Docs Agent uses Opus for rich documentation
                max_tokens=4096,
                messages=[
                    {"role": "user", "content": prompt}
                ],
            )

            return message.content[0].text
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_generate_report_content",
                    "error": str(e),
                })
            )
            raise

    async def _generate_design_doc_content(
        self,
        story_key: str,
        story_summary: str,
        acceptance_criteria: List[str],
    ) -> str:
        """Generate design doc content using Claude."""
        criteria = "\n".join(f"- {c}" for c in acceptance_criteria)

        prompt = f"""
Generate a Confluence wiki markup formatted design document for:
Story: {story_key}
Summary: {story_summary}

Acceptance Criteria:
{criteria}

Include:
1. Problem statement
2. Proposed solution
3. Architecture/Design
4. API/Interface changes
5. Data model (if applicable)
6. Security considerations
7. Testing strategy
8. Rollout plan

Return only wiki markup, no markdown.
"""

        try:
            message = self.claude.messages.create(
                model=self.config["claude"].model_reasoning,  # Docs Agent uses Opus for rich documentation
                max_tokens=4096,
                messages=[
                    {"role": "user", "content": prompt}
                ],
            )

            return message.content[0].text
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_generate_design_doc_content",
                    "error": str(e),
                })
            )
            raise

    async def _generate_api_docs_content(
        self,
        api_spec: Dict[str, Any],
    ) -> str:
        """Generate API documentation from OpenAPI spec."""
        prompt = f"""
Generate a Confluence wiki markup formatted API documentation from this OpenAPI spec:

{json.dumps(api_spec, indent=2)}

Include:
1. API overview
2. Authentication
3. Endpoints (grouped by resource)
4. Request/Response examples
5. Error handling
6. Rate limits
7. Code samples

Return only wiki markup, no markdown.
"""

        try:
            message = self.claude.messages.create(
                model=self.config["claude"].model_reasoning,  # Docs Agent uses Opus for rich documentation
                max_tokens=4096,
                messages=[
                    {"role": "user", "content": prompt}
                ],
            )

            return message.content[0].text
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_generate_api_docs_content",
                    "error": str(e),
                })
            )
            raise

    async def _generate_release_notes_content(
        self,
        version: str,
        stories: List[Dict[str, Any]],
        breaking_changes: List[str],
        migration_guide: str,
    ) -> str:
        """Generate release notes content."""
        stories_text = "\n".join(
            f"- {s.get('key')}: {s.get('summary')}"
            for s in stories
        )
        breaking_text = "\n".join(f"- {bc}" for bc in breaking_changes)

        prompt = f"""
Generate Confluence wiki markup formatted release notes for v{version}:

Features/Stories:
{stories_text}

Breaking Changes:
{breaking_text}

Migration Guide:
{migration_guide}

Include:
1. Release highlights
2. New features
3. Bug fixes
4. Breaking changes
5. Migration guide
6. Known issues
7. Upgrade instructions

Return only wiki markup, no markdown.
"""

        try:
            message = self.claude.messages.create(
                model=self.config["claude"].model_reasoning,  # Docs Agent uses Opus for rich documentation
                max_tokens=4096,
                messages=[
                    {"role": "user", "content": prompt}
                ],
            )

            return message.content[0].text
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_generate_release_notes_content",
                    "error": str(e),
                })
            )
            raise


async def main():
    """Main entry point for testing."""
    config = load_config()
    config["confluence_url"] = "https://confluence.example.com"
    config["confluence_email"] = "bot@example.com"
    config["confluence_token"] = "token"

    agent = DocsAgent(config)

    # Test sprint report
    result = await agent.generate_sprint_report(
        sprint_id="1",
        stories=[
            {"key": "MVT-1", "summary": "Implement scanner"},
            {"key": "MVT-2", "summary": "Add API endpoint"},
        ],
        results={"completed": 2, "failed": 0},
    )

    print(json.dumps(
        {
            "status": result.status,
            "page_id": result.page_id,
            "page_url": result.page_url,
        },
        indent=2,
    ))


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
