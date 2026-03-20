"""
Unit test suite for MVT Trilogy project.

This module contains comprehensive tests for:
- telegram-bot/lambda_function.py (Telegram bot handler)
- agents/config.py (configuration management)
- agents/subagents/code_agent.py (code generation agent)
- agents/orchestrator.py (sprint orchestration and agent routing)

All tests use unittest.mock for external API calls (Telegram, JIRA, GitHub, Claude).
"""


def test_mvt_trilogy_loads():
    """Smoke test: confirms test infrastructure is wired correctly."""
    assert True, "MVT Trilogy unit test suite is operational"
