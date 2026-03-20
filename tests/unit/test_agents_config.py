"""
Unit tests for agents configuration module.

Tests agents/config.py load_config() with environment variables.
"""

import pytest
import os
from unittest import mock

# Import config module
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../agents'))

from config import (
    load_config,
    JiraConfig,
    GitHubConfig,
    TelegramConfig,
    ClaudeConfig,
    DynamoDBConfig,
    COMPONENT_TO_AGENT,
    STORY_COMPLEXITY,
    APPROVAL_REQUIRED_LABELS,
    MAX_WAVE_SIZE,
    CANARY_TRAFFIC_PERCENT,
    CANARY_DURATION_MINUTES,
)


class TestLoadConfigDefaults:
    """Test load_config() with default environment."""

    def test_load_config_returns_dict(self):
        """Test that load_config returns a dictionary."""
        config = load_config()

        assert isinstance(config, dict)
        assert "jira" in config
        assert "github" in config
        assert "telegram" in config
        assert "claude" in config
        assert "dynamodb" in config

    def test_load_config_jira_section(self):
        """Test JIRA section in config."""
        config = load_config()

        assert isinstance(config["jira"], JiraConfig)
        assert config["jira"].project_key == "MVT"
        assert config["jira"].transition_ids is not None

    def test_load_config_github_section(self):
        """Test GitHub section in config."""
        config = load_config()

        assert isinstance(config["github"], GitHubConfig)
        assert config["github"].base_url == "https://api.github.com"
        assert config["github"].default_branch == "main"

    def test_load_config_telegram_section(self):
        """Test Telegram section in config."""
        config = load_config()

        assert isinstance(config["telegram"], TelegramConfig)
        assert config["telegram"].base_url == "https://api.telegram.org"

    def test_load_config_claude_section(self):
        """Test Claude section in config."""
        config = load_config()

        assert isinstance(config["claude"], ClaudeConfig)
        assert config["claude"].model == "claude-sonnet-4-6"
        assert config["claude"].model_reasoning == "claude-opus-4-6"
        assert config["claude"].model_coding == "claude-sonnet-4-6"

    def test_load_config_dynamodb_section(self):
        """Test DynamoDB section in config."""
        config = load_config()

        assert isinstance(config["dynamodb"], DynamoDBConfig)
        assert config["dynamodb"].table_name == "mvt-conversations"
        assert config["dynamodb"].region == "us-east-1"


class TestLoadConfigEnvironmentVariables:
    """Test load_config() reads from environment variables."""

    @mock.patch.dict(os.environ, {
        "JIRA_BASE_URL": "https://custom.jira.com",
        "JIRA_EMAIL": "user@example.com",
        "JIRA_API_TOKEN": "token123",
        "JIRA_PROJECT_KEY": "CUSTOM",
        "JIRA_BOARD_ID": "99",
    })
    def test_load_config_jira_env_vars(self):
        """Test JIRA config reads from env vars."""
        config = load_config()

        assert config["jira"].base_url == "https://custom.jira.com"
        assert config["jira"].email == "user@example.com"
        assert config["jira"].api_token == "token123"
        assert config["jira"].project_key == "CUSTOM"
        assert config["jira"].board_id == "99"

    @mock.patch.dict(os.environ, {
        "GITHUB_TOKEN": "ghp_test123",
        "GITHUB_ORG": "myorg",
        "GITHUB_REPO": "myrepo",
    })
    def test_load_config_github_env_vars(self):
        """Test GitHub config reads from env vars."""
        config = load_config()

        assert config["github"].token == "ghp_test123"
        assert config["github"].org == "myorg"
        assert config["github"].repo == "myrepo"

    @mock.patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "bot_token_123",
        "TELEGRAM_CHAT_ID": "chat_123",
    })
    def test_load_config_telegram_env_vars(self):
        """Test Telegram config reads from env vars."""
        config = load_config()

        assert config["telegram"].bot_token == "bot_token_123"
        assert config["telegram"].chat_id == "chat_123"

    @mock.patch.dict(os.environ, {
        "ANTHROPIC_API_KEY": "sk_test123",
        "CLAUDE_MODEL": "claude-opus-4-6",
        "CLAUDE_MODEL_REASONING": "claude-opus-4-6",
        "CLAUDE_MODEL_CODING": "claude-sonnet-4-6",
    })
    def test_load_config_claude_env_vars(self):
        """Test Claude config reads from env vars."""
        config = load_config()

        assert config["claude"].api_key == "sk_test123"
        assert config["claude"].model == "claude-opus-4-6"
        assert config["claude"].model_reasoning == "claude-opus-4-6"
        assert config["claude"].model_coding == "claude-sonnet-4-6"

    @mock.patch.dict(os.environ, {
        "CONVERSATION_TABLE": "custom-table",
    })
    def test_load_config_dynamodb_env_vars(self):
        """Test DynamoDB config reads from env vars."""
        config = load_config()

        assert config["dynamodb"].table_name == "custom-table"


class TestJiraConfigDefaults:
    """Test JiraConfig dataclass defaults."""

    def test_jira_config_transition_ids_default(self):
        """Test JIRA config has default transition IDs."""
        jira = JiraConfig(
            base_url="https://jira.example.com",
            email="test@example.com",
            api_token="token",
            project_key="TEST",
            board_id="1",
        )

        assert jira.transition_ids is not None
        assert jira.transition_ids["in_dev"] == "11"
        assert jira.transition_ids["in_test"] == "21"
        assert jira.transition_ids["approved"] == "31"
        assert jira.transition_ids["done"] == "41"

    def test_jira_config_custom_transition_ids(self):
        """Test JIRA config accepts custom transition IDs."""
        custom_ids = {"in_dev": "1", "in_test": "2"}
        jira = JiraConfig(
            base_url="https://jira.example.com",
            email="test@example.com",
            api_token="token",
            project_key="TEST",
            board_id="1",
            transition_ids=custom_ids,
        )

        assert jira.transition_ids == custom_ids


class TestComponentToAgent:
    """Test COMPONENT_TO_AGENT mapping."""

    def test_component_to_agent_has_entries(self):
        """Test COMPONENT_TO_AGENT has expected entries."""
        assert "backend-lambda" in COMPONENT_TO_AGENT
        assert "frontend-react" in COMPONENT_TO_AGENT
        assert "infrastructure-cdk" in COMPONENT_TO_AGENT
        assert "api-spec" in COMPONENT_TO_AGENT
        assert "database" in COMPONENT_TO_AGENT
        assert "security" in COMPONENT_TO_AGENT
        assert "docs" in COMPONENT_TO_AGENT
        assert "deployment" in COMPONENT_TO_AGENT

    def test_component_to_agent_values(self):
        """Test COMPONENT_TO_AGENT contains valid agent types."""
        valid_agents = {"code", "architect", "test", "docs", "deploy"}

        for agent in COMPONENT_TO_AGENT.values():
            assert agent in valid_agents

    def test_component_to_agent_is_dict(self):
        """Test COMPONENT_TO_AGENT is a dictionary."""
        assert isinstance(COMPONENT_TO_AGENT, dict)


class TestStoryComplexity:
    """Test STORY_COMPLEXITY mapping."""

    def test_story_complexity_has_entries(self):
        """Test STORY_COMPLEXITY has expected complexity levels."""
        assert "simple" in STORY_COMPLEXITY
        assert "moderate" in STORY_COMPLEXITY
        assert "complex" in STORY_COMPLEXITY
        assert "epic" in STORY_COMPLEXITY

    def test_story_complexity_point_values(self):
        """Test STORY_COMPLEXITY has ascending point values."""
        assert STORY_COMPLEXITY["simple"] < STORY_COMPLEXITY["moderate"]
        assert STORY_COMPLEXITY["moderate"] < STORY_COMPLEXITY["complex"]
        assert STORY_COMPLEXITY["complex"] < STORY_COMPLEXITY["epic"]

    def test_story_complexity_is_dict(self):
        """Test STORY_COMPLEXITY is a dictionary."""
        assert isinstance(STORY_COMPLEXITY, dict)


class TestApprovalRequiredLabels:
    """Test APPROVAL_REQUIRED_LABELS configuration."""

    def test_approval_required_labels_is_list(self):
        """Test APPROVAL_REQUIRED_LABELS is a list."""
        assert isinstance(APPROVAL_REQUIRED_LABELS, list)

    def test_approval_required_labels_has_entries(self):
        """Test APPROVAL_REQUIRED_LABELS contains expected labels."""
        assert "security-critical" in APPROVAL_REQUIRED_LABELS
        assert "production-impact" in APPROVAL_REQUIRED_LABELS
        assert "breaking-change" in APPROVAL_REQUIRED_LABELS
        assert "compliance" in APPROVAL_REQUIRED_LABELS

    def test_approval_required_labels_all_strings(self):
        """Test all approval labels are strings."""
        assert all(isinstance(label, str) for label in APPROVAL_REQUIRED_LABELS)


class TestConfigConstants:
    """Test configuration constants."""

    def test_max_wave_size(self):
        """Test MAX_WAVE_SIZE is positive integer."""
        assert isinstance(MAX_WAVE_SIZE, int)
        assert MAX_WAVE_SIZE > 0

    def test_canary_traffic_percent(self):
        """Test CANARY_TRAFFIC_PERCENT is valid percentage."""
        assert isinstance(CANARY_TRAFFIC_PERCENT, (int, float))
        assert 0 < CANARY_TRAFFIC_PERCENT <= 100

    def test_canary_duration_minutes(self):
        """Test CANARY_DURATION_MINUTES is positive integer."""
        assert isinstance(CANARY_DURATION_MINUTES, int)
        assert CANARY_DURATION_MINUTES > 0


class TestClaudeConfig:
    """Test ClaudeConfig dataclass."""

    def test_claude_config_defaults(self):
        """Test ClaudeConfig default values."""
        claude = ClaudeConfig(api_key="test_key")

        assert claude.model == "claude-sonnet-4-6"
        assert claude.model_reasoning == "claude-opus-4-6"
        assert claude.model_coding == "claude-sonnet-4-6"
        assert claude.max_tokens == 8192
        assert claude.temperature == 0.7

    def test_claude_config_custom_values(self):
        """Test ClaudeConfig with custom values."""
        claude = ClaudeConfig(
            api_key="test_key",
            model="claude-opus-4-6",
            max_tokens=4096,
            temperature=0.5,
        )

        assert claude.model == "claude-opus-4-6"
        assert claude.max_tokens == 4096
        assert claude.temperature == 0.5
