"""
Telegram Bot Configuration
Environment variables for bot, JIRA, and Claude integration.
"""

import os

# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# JIRA Configuration
JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "https://jira.example.com")
JIRA_EMAIL = os.getenv("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "MVT")
JIRA_BOARD_ID = os.getenv("JIRA_BOARD_ID", "1")

# Confluence Configuration
CONFLUENCE_URL = os.getenv("CONFLUENCE_URL", "https://confluence.example.com")
CONFLUENCE_EMAIL = os.getenv("CONFLUENCE_EMAIL", "")
CONFLUENCE_TOKEN = os.getenv("CONFLUENCE_TOKEN", "")

# GitHub Configuration
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_ORG = os.getenv("GITHUB_ORG", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")

# Claude Configuration
# Telegram bot uses Opus for brainstorming (reasoning-heavy)
# and Sonnet for structured tasks (epic/story generation)
CLAUDE_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-6")  # Default: Opus for brainstorming
CLAUDE_MODEL_CODING = os.getenv("CLAUDE_MODEL_CODING", "claude-sonnet-4-6")  # Sonnet for structured output

# DynamoDB Configuration
CONVERSATION_TABLE = os.getenv("CONVERSATION_TABLE", "mvt-conversations")
DYNAMODB_REGION = os.getenv("AWS_REGION", "us-east-1")
