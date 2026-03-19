"""
Telegram Bot Lambda Handler
Handles Telegram webhook for MVT project sprint automation commands.

Commands:
  /brainstorm [topic] - Ideate and create draft Confluence page
  /create-epic [summary] - Create JIRA epic with child stories
  /kickoff-sprint - Create sprint and trigger orchestrator
  /sprint-status - Get current sprint board status
  /approve-release [callback_data] - Trigger production deployment
  /sprint-report - Generate Confluence report
"""

import json
import logging
from typing import Any, Dict, Optional
from datetime import datetime

import requests
from anthropic import Anthropic

import config


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


class TelegramBotHandler:
    """Telegram Bot webhook handler."""

    def __init__(self):
        """Initialize bot handler."""
        self.bot_token = config.TELEGRAM_BOT_TOKEN
        self.chat_id = config.TELEGRAM_CHAT_ID
        self.jira_base_url = config.JIRA_BASE_URL
        self.jira_email = config.JIRA_EMAIL
        self.jira_api_token = config.JIRA_API_TOKEN
        self.claude = Anthropic(api_key=config.CLAUDE_API_KEY)

    async def handle_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Handle incoming Telegram message."""
        try:
            chat_id = message["chat"]["id"]
            text = message.get("text", "")
            message_id = message.get("message_id")

            logger.info(
                json.dumps({
                    "action": "handle_message",
                    "chat_id": chat_id,
                    "text": text[:50],
                })
            )

            # Parse command
            if text.startswith("/"):
                response = await self._handle_command(chat_id, text)
            else:
                # Natural language fallback
                response = await self._handle_natural_language(chat_id, text)

            return {
                "status": "success",
                "response": response,
            }

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "handle_message",
                    "error": str(e),
                })
            )
            return {
                "status": "error",
                "error": str(e),
            }

    async def handle_callback(self, callback: Dict[str, Any]) -> Dict[str, Any]:
        """Handle Telegram inline button callback."""
        try:
            callback_query_id = callback["id"]
            chat_id = callback["from"]["id"]
            callback_data = callback.get("data", "")

            logger.info(
                json.dumps({
                    "action": "handle_callback",
                    "callback_data": callback_data,
                })
            )

            # Parse callback data
            parts = callback_data.split(":")
            action = parts[0]
            request_id = parts[1] if len(parts) > 1 else ""

            if action == "approve":
                response = await self._approve_release(chat_id, request_id)
            elif action == "reject":
                response = await self._reject_release(chat_id, request_id)
            elif action == "review":
                response = self._send_review_link(chat_id)
            else:
                response = "Unknown action"

            # Answer callback query
            self._answer_callback_query(callback_query_id, response)

            return {
                "status": "success",
                "response": response,
            }

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "handle_callback",
                    "error": str(e),
                })
            )
            return {
                "status": "error",
                "error": str(e),
            }

    async def _handle_command(self, chat_id: str, text: str) -> str:
        """Handle slash command."""
        command_parts = text.split(maxsplit=1)
        command = command_parts[0].lower()
        args = command_parts[1] if len(command_parts) > 1 else ""

        if command == "/brainstorm":
            return await self._brainstorm(chat_id, args)
        elif command == "/create-epic":
            return await self._create_epic(chat_id, args)
        elif command == "/kickoff-sprint":
            return await self._kickoff_sprint(chat_id)
        elif command == "/sprint-status":
            return await self._sprint_status(chat_id)
        elif command == "/approve-release":
            return await self._approve_release(chat_id, args)
        elif command == "/sprint-report":
            return await self._sprint_report(chat_id)
        elif command == "/help":
            return self._help_text()
        else:
            return f"Unknown command: {command}. Use /help for available commands."

    async def _handle_natural_language(self, chat_id: str, text: str) -> str:
        """Handle natural language input with Claude."""
        try:
            prompt = f"""
The user sent this message in the MVT Telegram bot chat:
"{text}"

Based on the message, determine if they want to:
1. Brainstorm ideas about a topic
2. Create a new epic/story
3. Check sprint status
4. Approve a release
5. Generate reports
6. Just have a conversation about MVT

If it's one of the above, respond with a helpful message and ask for clarification if needed.
If it's just conversation, respond naturally and keep it brief.

Keep responses under 100 words.
"""

            message = self.claude.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=500,
                messages=[
                    {"role": "user", "content": prompt}
                ],
            )

            response = message.content[0].text
            self._send_message(chat_id, response)
            return response

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_handle_natural_language",
                    "error": str(e),
                })
            )
            return "I encountered an error processing your message. Please try again."

    async def _brainstorm(self, chat_id: str, topic: str) -> str:
        """Brainstorm ideas using Claude."""
        try:
            if not topic:
                return "Please provide a topic: /brainstorm [topic]"

            prompt = f"""
Brainstorm creative ideas for: {topic}

Generate 5-7 actionable ideas that could be turned into JIRA stories.
Format as numbered list with brief descriptions.
"""

            message = self.claude.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=1024,
                messages=[
                    {"role": "user", "content": prompt}
                ],
            )

            ideas = message.content[0].text

            # Create draft Confluence page
            page_url = self._create_confluence_page(
                f"Brainstorm: {topic}",
                ideas,
            )

            response = f"✨ Brainstorming ideas for: {topic}\n\n{ideas}\n\n📄 Draft page: {page_url}"
            self._send_message(chat_id, response)

            logger.info(
                json.dumps({
                    "action": "_brainstorm",
                    "topic": topic,
                    "page_url": page_url,
                })
            )

            return response

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_brainstorm",
                    "error": str(e),
                })
            )
            return f"Error brainstorming: {str(e)}"

    async def _create_epic(self, chat_id: str, summary: str) -> str:
        """Create JIRA epic with child stories."""
        try:
            if not summary:
                return "Please provide a summary: /create-epic [summary]"

            # Use Claude to generate child stories
            prompt = f"""
Create a breakdown of JIRA stories for this epic:
Epic: {summary}

Generate 3-5 child stories with:
- Title (50 chars max)
- Description (2-3 sentences)
- Acceptance criteria (2-3 bullets)
- Story points estimate (3, 5, 8)

Format as JSON array of objects with keys: title, description, acceptance_criteria, story_points

Return only valid JSON, no other text.
"""

            message = self.claude.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=2048,
                messages=[
                    {"role": "user", "content": prompt}
                ],
            )

            stories_json = message.content[0].text
            # Parse and create JIRA epic
            epic_key = self._create_jira_epic(summary, stories_json)

            response = f"✅ Created epic: {epic_key}"
            self._send_message(chat_id, response)

            logger.info(
                json.dumps({
                    "action": "_create_epic",
                    "epic_key": epic_key,
                })
            )

            return response

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_create_epic",
                    "error": str(e),
                })
            )
            return f"Error creating epic: {str(e)}"

    async def _kickoff_sprint(self, chat_id: str) -> str:
        """Create JIRA sprint and trigger orchestrator."""
        try:
            # Create sprint
            sprint_id = self._create_jira_sprint()

            # Move stories to sprint
            story_count = self._move_stories_to_sprint(sprint_id)

            # Trigger orchestrator
            self._trigger_orchestrator(sprint_id)

            response = f"🚀 Sprint {sprint_id} kicked off with {story_count} stories. Orchestrator running..."
            self._send_message(chat_id, response)

            logger.info(
                json.dumps({
                    "action": "_kickoff_sprint",
                    "sprint_id": sprint_id,
                    "story_count": story_count,
                })
            )

            return response

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_kickoff_sprint",
                    "error": str(e),
                })
            )
            return f"Error kicking off sprint: {str(e)}"

    async def _sprint_status(self, chat_id: str) -> str:
        """Get current sprint board status."""
        try:
            # Query JIRA for current sprint
            status_report = self._query_jira_sprint_status()

            response = f"📊 Sprint Status\n\n{status_report}"
            self._send_message(chat_id, response)

            logger.info(
                json.dumps({
                    "action": "_sprint_status",
                    "status": "success",
                })
            )

            return response

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_sprint_status",
                    "error": str(e),
                })
            )
            return f"Error getting sprint status: {str(e)}"

    async def _approve_release(self, chat_id: str, request_id: str) -> str:
        """Approve and deploy to production."""
        try:
            # Trigger GitHub Actions deployment
            success = self._trigger_github_deployment()

            if success:
                response = "✅ Release approved! Deploying to production..."
            else:
                response = "❌ Failed to trigger deployment"

            self._send_message(chat_id, response)

            logger.info(
                json.dumps({
                    "action": "_approve_release",
                    "request_id": request_id,
                    "status": "approved" if success else "failed",
                })
            )

            return response

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_approve_release",
                    "error": str(e),
                })
            )
            return f"Error approving release: {str(e)}"

    async def _reject_release(self, chat_id: str, request_id: str) -> str:
        """Reject release approval."""
        response = "❌ Release rejected"
        self._send_message(chat_id, response)

        logger.info(
            json.dumps({
                "action": "_reject_release",
                "request_id": request_id,
            })
        )

        return response

    async def _sprint_report(self, chat_id: str) -> str:
        """Generate and post sprint report."""
        try:
            # Trigger Docs Agent
            report_url = self._trigger_docs_agent()

            response = f"📄 Sprint report generated: {report_url}"
            self._send_message(chat_id, response)

            logger.info(
                json.dumps({
                    "action": "_sprint_report",
                    "url": report_url,
                })
            )

            return response

        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_sprint_report",
                    "error": str(e),
                })
            )
            return f"Error generating report: {str(e)}"

    def _send_message(self, chat_id: str, text: str) -> bool:
        """Send Telegram message."""
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
            }

            response = requests.post(url, json=payload)
            response.raise_for_status()

            return True
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_send_message",
                    "error": str(e),
                })
            )
            return False

    def _answer_callback_query(self, callback_query_id: str, text: str) -> bool:
        """Answer Telegram callback query."""
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/answerCallbackQuery"
            payload = {
                "callback_query_id": callback_query_id,
                "text": text,
            }

            response = requests.post(url, json=payload)
            response.raise_for_status()

            return True
        except Exception as e:
            logger.error(
                json.dumps({
                    "action": "_answer_callback_query",
                    "error": str(e),
                })
            )
            return False

    def _send_review_link(self, chat_id: str) -> str:
        """Send review/approval link."""
        review_url = "https://confluence.example.com/sprint-report"
        self._send_message(chat_id, f"📋 Review: {review_url}")
        return "Review link sent"

    def _create_confluence_page(self, title: str, content: str) -> str:
        """Create Confluence page (mock)."""
        page_id = f"page_{int(datetime.now().timestamp())}"
        return f"https://confluence.example.com/spaces/MVT/pages/{page_id}"

    def _create_jira_epic(self, summary: str, stories_json: str) -> str:
        """Create JIRA epic (mock)."""
        epic_id = f"MVT-{int(datetime.now().timestamp()) % 10000}"
        return epic_id

    def _create_jira_sprint(self) -> str:
        """Create JIRA sprint (mock)."""
        sprint_id = f"sprint_{int(datetime.now().timestamp())}"
        return sprint_id

    def _move_stories_to_sprint(self, sprint_id: str) -> int:
        """Move stories to sprint (mock)."""
        return 5  # Moved 5 stories

    def _trigger_orchestrator(self, sprint_id: str) -> bool:
        """Trigger orchestrator Lambda (mock)."""
        logger.info(
            json.dumps({
                "action": "_trigger_orchestrator",
                "sprint_id": sprint_id,
            })
        )
        return True

    def _query_jira_sprint_status(self) -> str:
        """Query JIRA for sprint status (mock)."""
        return """Total Stories: 12
Completed: 8 (67%)
In Progress: 3
To Do: 1
Blockers: None"""

    def _trigger_github_deployment(self) -> bool:
        """Trigger GitHub Actions deployment (mock)."""
        logger.info(
            json.dumps({
                "action": "_trigger_github_deployment",
            })
        )
        return True

    def _trigger_docs_agent(self) -> str:
        """Trigger Docs Agent (mock)."""
        return "https://confluence.example.com/sprint-report"

    def _help_text(self) -> str:
        """Get help text."""
        return """
*MVT Bot Commands*

/brainstorm [topic] - Ideate and create draft page
/create-epic [summary] - Create JIRA epic with stories
/kickoff-sprint - Start new sprint
/sprint-status - Current sprint board status
/approve-release - Approve production deployment
/sprint-report - Generate sprint report

Or just chat naturally - I'll try to help!
"""


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """AWS Lambda handler for Telegram webhook."""
    try:
        logger.info(
            json.dumps({
                "action": "lambda_handler",
                "body": event.get("body", "")[:100],
            })
        )

        # Parse Telegram update
        body = json.loads(event.get("body", "{}"))

        handler = TelegramBotHandler()

        # Handle message
        if "message" in body:
            result = handler.handle_message(body["message"])
        # Handle callback query
        elif "callback_query" in body:
            result = handler.handle_callback(body["callback_query"])
        else:
            result = {"status": "unknown_update"}

        return {
            "statusCode": 200,
            "body": json.dumps(result),
        }

    except Exception as e:
        logger.error(
            json.dumps({
                "action": "lambda_handler",
                "error": str(e),
            })
        )
        return {
            "statusCode": 500,
            "body": json.dumps({"status": "error", "error": str(e)}),
        }


if __name__ == "__main__":
    # Test locally
    test_event = {
        "body": json.dumps({
            "message": {
                "chat": {"id": "12345"},
                "text": "/brainstorm security vulnerabilities",
                "message_id": 1,
            }
        })
    }

    result = lambda_handler(test_event, None)
    print(json.dumps(json.loads(result["body"]), indent=2))
