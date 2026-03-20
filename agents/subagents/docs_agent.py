"""
Docs Agent - Specialized agent for documentation generation.

Generates Confluence pages for sprint reports, design docs, API docs,
and release notes. Uses Confluence REST API v2.
"""

import json
import logging
import base64
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

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

    def create_page_v1(
        self,
        space_key: str,
        title: str,
        body_html: str,
        parent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Create a new Confluence page using REST API v1."""
        try:
            url = f"{self.base_url}/rest/api/content"

            payload = {
                "type": "page",
                "space": {"key": space_key},
                "title": title,
                "body": {
                    "storage": {
                        "value": body_html,
                        "representation": "storage",
                    }
                },
            }

            if parent_id:
                payload["ancestors"] = [{"id": parent_id}]

            response = self.session.post(url, json=payload)
            response.raise_for_status()

            page_data = response.json()

            logger.info(
                json.dumps({
                    "action": "create_page_v1",
                    "page_id": page_data.get("id"),
                    "title": title,
                })
            )

            return page_data

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "create_page_v1",
                    "error": str(e),
                })
            )
            return None

    def search_pages(self, space_key: str, title: str) -> Optional[List[Dict[str, Any]]]:
        """Search for pages in a space by title using CQL."""
        try:
            url = f"{self.base_url}/rest/api/content/search"
            cql = f'space="{space_key}" AND title="{title}"'
            params = {"cql": cql, "limit": 10}

            response = self.session.get(url, params=params)
            response.raise_for_status()

            data = response.json()
            return data.get("results", [])

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "search_pages",
                    "space_key": space_key,
                    "title": title,
                    "error": str(e),
                })
            )
            return None


class DocsAgent:
    """Specialized agent for documentation generation."""

    def __init__(self, config: Dict):
        """Initialize docs agent."""
        self.config = config

        # Use JIRA config credentials for Confluence
        jira_config = config["jira"]
        confluence_base_url = jira_config.base_url.rstrip("/") + "/wiki"

        self.confluence = ConfluenceClient(
            base_url=confluence_base_url,
            email=jira_config.email,
            api_token=jira_config.api_token,
        )
        self.claude = Anthropic(api_key=config["claude"].api_key)
        self.jira_base_url = jira_config.base_url.rstrip("/")

    async def generate_sprint_report(
        self,
        sprint_id: str,
        stories: List[Dict[str, Any]] = None,
        results: Dict[str, Any] = None,
    ) -> DocsResult:
        """Generate sprint report and post to Confluence."""
        try:
            # Fetch sprint data from JIRA if not provided
            if stories is None or results is None:
                sprint_data = self._fetch_sprint_data_from_jira(sprint_id)
                stories = sprint_data.get("stories", [])
                results = sprint_data.get("results", {})

            # Generate report content using Claude
            content = await self._generate_report_content(sprint_id, stories, results)

            # Create Confluence page using v1 API
            page_data = self.confluence.create_page_v1(
                space_key="MVT",
                title=f"Sprint {sprint_id} Report",
                body_html=content,
            )

            if not page_data:
                raise RuntimeError("Failed to create Confluence page")

            page_url = f"{self.jira_base_url}/wiki/spaces/MVT/pages/{page_data['id']}"

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

            # Create Confluence page using v1 API
            page_data = self.confluence.create_page_v1(
                space_key="MVT",
                title=f"{story_key}: Design Doc",
                body_html=content,
            )

            if not page_data:
                raise RuntimeError("Failed to create Confluence page")

            page_url = f"{self.jira_base_url}/wiki/spaces/MVT/pages/{page_data['id']}"

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

            # Create Confluence page using v1 API
            page_data = self.confluence.create_page_v1(
                space_key="MVT",
                title="API Documentation",
                body_html=content,
            )

            if not page_data:
                raise RuntimeError("Failed to create Confluence page")

            page_url = f"{self.jira_base_url}/wiki/spaces/MVT/pages/{page_data['id']}"

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

            # Create Confluence page using v1 API
            page_data = self.confluence.create_page_v1(
                space_key="MVT",
                title=f"Release Notes v{version}",
                body_html=content,
            )

            if not page_data:
                raise RuntimeError("Failed to create Confluence page")

            page_url = f"{self.jira_base_url}/wiki/spaces/MVT/pages/{page_data['id']}"

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
        stories_text = chr(10).join(
            f"- {s.get('key')}: {s.get('summary')} (Status: {s.get('status', 'Unknown')}, Points: {s.get('points', '-')})"
            for s in stories
        )

        prompt = f"""
Generate a Confluence storage format sprint report for:
Sprint: {sprint_id}
Total Stories: {len(stories)}

Stories:
{stories_text}

Results:
{json.dumps(results, indent=2)}

Include:
1. Executive summary (use <ac:structured-macro ac:name="panel"> for a highlighted summary panel)
2. Metrics (velocity, completion rate) - format as a table using <table> tags
3. Completed stories list (with status lozenges using <ac:structured-macro ac:name="status">)
4. Issues and blockers (highlighted with an info panel)
5. Next sprint recommendations

Use Confluence Storage Format (XHTML) with:
- <ac:structured-macro> tags for panels
- <table> tags for metrics
- <ac:structured-macro ac:name="status"> for status indicators
- Proper XHTML formatting for lists and headings

Return only Confluence storage format XHTML, no markdown or wiki markup.
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
Generate a Confluence Storage Format design document for:
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

Use Confluence Storage Format (XHTML) with:
- <ac:structured-macro> tags for callouts and panels
- <table> tags for structured data
- Proper headings with h1, h2, h3
- Lists using <ul> and <li>

Return only Confluence storage format XHTML, no markdown or wiki markup.
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
Generate a Confluence Storage Format API documentation from this OpenAPI spec:

{json.dumps(api_spec, indent=2)}

Include:
1. API overview
2. Authentication
3. Endpoints (grouped by resource)
4. Request/Response examples
5. Error handling
6. Rate limits
7. Code samples

Use Confluence Storage Format (XHTML) with:
- <ac:structured-macro> tags for code examples and callouts
- <table> tags for endpoint listings
- <pre> tags for code examples
- Proper semantic XHTML structure

Return only Confluence storage format XHTML, no markdown or wiki markup.
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
Generate Confluence Storage Format release notes for v{version}:

Features/Stories:
{stories_text}

Breaking Changes:
{breaking_text}

Migration Guide:
{migration_guide}

Include:
1. Release highlights (use <ac:structured-macro ac:name="panel"> for a banner)
2. New features
3. Bug fixes
4. Breaking changes (highlighted with warning panel)
5. Migration guide
6. Known issues
7. Upgrade instructions

Use Confluence Storage Format (XHTML) with:
- <ac:structured-macro> for highlight panels and warnings
- Proper heading hierarchy (h1, h2, h3)
- Lists for features and issues
- Callout boxes for important information

Return only Confluence storage format XHTML, no markdown or wiki markup.
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

    def _fetch_sprint_data_from_jira(self, sprint_id: str) -> Dict[str, Any]:
        """Fetch sprint data and issues from JIRA REST API."""
        try:
            jira_config = self.config["jira"]
            auth = (jira_config.email, jira_config.api_token)
            headers = {"Content-Type": "application/json"}

            # Get sprint info
            sprint_url = f"{self.jira_base_url}/rest/api/3/sprints/{sprint_id}"
            sprint_response = requests.get(sprint_url, auth=auth, headers=headers)
            sprint_response.raise_for_status()
            sprint_info = sprint_response.json()

            # Query issues in the sprint
            jql = f'sprint = {sprint_id} ORDER BY status DESC'
            issues_url = f"{self.jira_base_url}/rest/api/3/search"
            params = {
                "jql": jql,
                "maxResults": 100,
                "fields": ["key", "summary", "status", "customfield_10016", "assignee"],
            }

            issues_response = requests.get(issues_url, auth=auth, headers=headers, params=params)
            issues_response.raise_for_status()
            issues_data = issues_response.json()

            # Parse issues into structured format
            stories = []
            completed_count = 0
            total_points = 0

            for issue in issues_data.get("issues", []):
                story = {
                    "key": issue["key"],
                    "summary": issue["fields"]["summary"],
                    "status": issue["fields"]["status"]["name"],
                    "points": issue["fields"].get("customfield_10016") or 0,  # Story points field
                    "assignee": issue["fields"].get("assignee", {}).get("displayName", "Unassigned"),
                }
                stories.append(story)

                if story["status"].lower() in ["done", "closed"]:
                    completed_count += 1
                total_points += story["points"] if story["points"] else 0

            results = {
                "sprint_name": sprint_info.get("name", f"Sprint {sprint_id}"),
                "completed_stories": completed_count,
                "total_stories": len(stories),
                "total_points": total_points,
                "completion_rate": round((completed_count / len(stories) * 100) if stories else 0, 1),
            }

            logger.info(
                json.dumps({
                    "action": "_fetch_sprint_data_from_jira",
                    "sprint_id": sprint_id,
                    "stories_count": len(stories),
                })
            )

            return {
                "stories": stories,
                "results": results,
            }

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_fetch_sprint_data_from_jira",
                    "sprint_id": sprint_id,
                    "error": str(e),
                })
            )
            return {"stories": [], "results": {}}


async def main():
    """Main entry point for testing."""
    config = load_config()
    agent = DocsAgent(config)

    # Test sprint report - fetches data from real JIRA
    result = await agent.generate_sprint_report(
        sprint_id="1",
    )

    print(json.dumps(
        {
            "status": result.status,
            "page_id": result.page_id,
            "page_url": result.page_url,
            "error": result.error,
        },
        indent=2,
    ))


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
