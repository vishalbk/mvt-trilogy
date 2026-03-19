"""
MVT Configuration Module
Centralized configuration for JIRA, GitHub, Telegram, and Claude integrations.
"""

import os
from dataclasses import dataclass
from typing import Dict, List


@dataclass
class JiraConfig:
    """JIRA integration configuration."""
    base_url: str
    email: str
    api_token: str
    project_key: str
    board_id: str

    # Workflow transition IDs (customize per instance)
    transition_ids: Dict[str, str] = None

    def __post_init__(self):
        if self.transition_ids is None:
            self.transition_ids = {
                "in_dev": "11",
                "in_test": "21",
                "approved": "31",
                "done": "41",
            }


@dataclass
class GitHubConfig:
    """GitHub integration configuration."""
    token: str
    org: str
    repo: str
    default_branch: str = "main"
    base_url: str = "https://api.github.com"


@dataclass
class TelegramConfig:
    """Telegram bot configuration."""
    bot_token: str
    chat_id: str
    base_url: str = "https://api.telegram.org"


@dataclass
class ClaudeConfig:
    """Claude API configuration."""
    api_key: str
    model: str = "claude-3-5-sonnet-20241022"
    max_tokens: int = 4096
    temperature: float = 0.7


@dataclass
class DynamoDBConfig:
    """DynamoDB configuration for conversation state."""
    table_name: str
    region: str = "us-east-1"


# Component labels to agent type mapping
COMPONENT_TO_AGENT: Dict[str, str] = {
    "backend-lambda": "code",
    "infrastructure-cdk": "architect",
    "frontend-react": "code",
    "api-spec": "architect",
    "database": "architect",
    "security": "test",
    "docs": "docs",
    "deployment": "deploy",
}

# Story complexity to estimation mapping
STORY_COMPLEXITY: Dict[str, int] = {
    "simple": 3,
    "moderate": 5,
    "complex": 8,
    "epic": 13,
}

# Approval required for stories with these labels
APPROVAL_REQUIRED_LABELS = [
    "security-critical",
    "production-impact",
    "breaking-change",
    "compliance",
]

# Parallel execution wave size
MAX_WAVE_SIZE = 3

# Canary deployment settings
CANARY_TRAFFIC_PERCENT = 10
CANARY_DURATION_MINUTES = 5
CANARY_ERROR_THRESHOLD = 1.0  # percent

# Rate limiting
JIRA_RATE_LIMIT_REQUESTS = 60
JIRA_RATE_LIMIT_PERIOD = 60  # seconds

GITHUB_RATE_LIMIT_REQUESTS = 60
GITHUB_RATE_LIMIT_PERIOD = 60  # seconds

CLAUDE_RATE_LIMIT_REQUESTS = 50
CLAUDE_RATE_LIMIT_PERIOD = 60  # seconds


def load_config() -> Dict:
    """Load configuration from environment variables."""
    return {
        "jira": JiraConfig(
            base_url=os.getenv("JIRA_BASE_URL", "https://jira.example.com"),
            email=os.getenv("JIRA_EMAIL", ""),
            api_token=os.getenv("JIRA_API_TOKEN", ""),
            project_key=os.getenv("JIRA_PROJECT_KEY", "MVT"),
            board_id=os.getenv("JIRA_BOARD_ID", "1"),
        ),
        "github": GitHubConfig(
            token=os.getenv("GITHUB_TOKEN", ""),
            org=os.getenv("GITHUB_ORG", ""),
            repo=os.getenv("GITHUB_REPO", ""),
        ),
        "telegram": TelegramConfig(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        ),
        "claude": ClaudeConfig(
            api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            model=os.getenv("CLAUDE_MODEL", "claude-3-5-sonnet-20241022"),
        ),
        "dynamodb": DynamoDBConfig(
            table_name=os.getenv("CONVERSATION_TABLE", "mvt-conversations"),
        ),
    }
