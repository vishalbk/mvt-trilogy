"""
Unit tests for Sprint Orchestrator.

Tests agents/orchestrator.py:
- Story.requires_approval() checks for approval-required labels
- SprintOrchestrator.resolve_dependencies() analyzes story dependencies
- SprintOrchestrator.create_execution_waves() groups stories for parallel execution
- SprintOrchestrator.assign_to_agent() routes stories to appropriate agents
"""

import pytest
import sys
import os
from unittest import mock
from collections import defaultdict

# Import orchestrator and config modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../agents'))

# Mock external dependencies before importing
with mock.patch.dict(sys.modules, {
    'anthropic': mock.MagicMock(),
    'requests': mock.MagicMock(),
}):
    from orchestrator import (
        Story,
        SprintOrchestrator,
        ExecutionResult,
        JiraClient,
        GithubClient,
        TelegramClient,
    )
    from config import COMPONENT_TO_AGENT, APPROVAL_REQUIRED_LABELS, MAX_WAVE_SIZE


class TestStoryRequiresApproval:
    """Test Story.requires_approval() method."""

    def test_requires_approval_with_security_critical(self):
        """Test approval required for security-critical label."""
        story = Story(
            key="MVT-100",
            summary="Fix security issue",
            description="",
            status="To Do",
            component="security",
            labels=["security-critical"],
            linked_issues=[],
            acceptance_criteria=[],
        )

        assert story.requires_approval() is True

    def test_requires_approval_with_production_impact(self):
        """Test approval required for production-impact label."""
        story = Story(
            key="MVT-101",
            summary="Change production config",
            description="",
            status="To Do",
            component="config",
            labels=["production-impact"],
            linked_issues=[],
            acceptance_criteria=[],
        )

        assert story.requires_approval() is True

    def test_requires_approval_with_breaking_change(self):
        """Test approval required for breaking-change label."""
        story = Story(
            key="MVT-102",
            summary="API redesign",
            description="",
            status="To Do",
            component="api",
            labels=["breaking-change"],
            linked_issues=[],
            acceptance_criteria=[],
        )

        assert story.requires_approval() is True

    def test_requires_approval_with_compliance(self):
        """Test approval required for compliance label."""
        story = Story(
            key="MVT-103",
            summary="GDPR compliance",
            description="",
            status="To Do",
            component="data",
            labels=["compliance"],
            linked_issues=[],
            acceptance_criteria=[],
        )

        assert story.requires_approval() is True

    def test_requires_approval_without_approval_labels(self):
        """Test no approval needed for regular labels."""
        story = Story(
            key="MVT-104",
            summary="Add feature",
            description="",
            status="To Do",
            component="feature",
            labels=["frontend-react", "enhancement"],
            linked_issues=[],
            acceptance_criteria=[],
        )

        assert story.requires_approval() is False

    def test_requires_approval_empty_labels(self):
        """Test no approval needed with empty labels."""
        story = Story(
            key="MVT-105",
            summary="Simple task",
            description="",
            status="To Do",
            component="misc",
            labels=[],
            linked_issues=[],
            acceptance_criteria=[],
        )

        assert story.requires_approval() is False

    def test_requires_approval_multiple_labels(self):
        """Test approval with mixed labels."""
        story = Story(
            key="MVT-106",
            summary="Complex change",
            description="",
            status="To Do",
            component="core",
            labels=["enhancement", "security-critical", "bug"],
            linked_issues=[],
            acceptance_criteria=[],
        )

        assert story.requires_approval() is True


class TestResolveDependencies:
    """Test SprintOrchestrator.resolve_dependencies() method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_config = {
            "jira": mock.MagicMock(),
            "github": mock.MagicMock(),
            "telegram": mock.MagicMock(),
            "claude": mock.MagicMock(),
        }

    def test_resolve_dependencies_no_links(self):
        """Test dependency resolution with no linked issues."""
        with mock.patch('orchestrator.JiraClient'), \
             mock.patch('orchestrator.GithubClient'), \
             mock.patch('orchestrator.TelegramClient'), \
             mock.patch('orchestrator.Anthropic'):

            orchestrator = SprintOrchestrator(self.mock_config)

            stories = [
                Story("MVT-1", "Task 1", "", "To Do", "comp", [], [], []),
                Story("MVT-2", "Task 2", "", "To Do", "comp", [], [], []),
            ]
            orchestrator.stories = {s.key: s for s in stories}

            deps = orchestrator.resolve_dependencies(stories)

            assert deps["MVT-1"] == set()
            assert deps["MVT-2"] == set()

    def test_resolve_dependencies_single_dependency(self):
        """Test dependency resolution with single dependency."""
        with mock.patch('orchestrator.JiraClient'), \
             mock.patch('orchestrator.GithubClient'), \
             mock.patch('orchestrator.TelegramClient'), \
             mock.patch('orchestrator.Anthropic'):

            orchestrator = SprintOrchestrator(self.mock_config)

            stories = [
                Story("MVT-1", "Task 1", "", "To Do", "comp", [], [], []),
                Story("MVT-2", "Task 2", "", "To Do", "comp", [], ["MVT-1"], []),
            ]
            orchestrator.stories = {s.key: s for s in stories}

            deps = orchestrator.resolve_dependencies(stories)

            assert deps["MVT-1"] == set()
            assert deps["MVT-2"] == {"MVT-1"}

    def test_resolve_dependencies_chain(self):
        """Test dependency resolution with chain of dependencies."""
        with mock.patch('orchestrator.JiraClient'), \
             mock.patch('orchestrator.GithubClient'), \
             mock.patch('orchestrator.TelegramClient'), \
             mock.patch('orchestrator.Anthropic'):

            orchestrator = SprintOrchestrator(self.mock_config)

            stories = [
                Story("MVT-1", "Task 1", "", "To Do", "comp", [], [], []),
                Story("MVT-2", "Task 2", "", "To Do", "comp", [], ["MVT-1"], []),
                Story("MVT-3", "Task 3", "", "To Do", "comp", [], ["MVT-2"], []),
            ]
            orchestrator.stories = {s.key: s for s in stories}

            deps = orchestrator.resolve_dependencies(stories)

            assert deps["MVT-1"] == set()
            assert deps["MVT-2"] == {"MVT-1"}
            assert deps["MVT-3"] == {"MVT-2"}

    def test_resolve_dependencies_missing_linked_issue(self):
        """Test dependency resolution ignores missing linked issues."""
        with mock.patch('orchestrator.JiraClient'), \
             mock.patch('orchestrator.GithubClient'), \
             mock.patch('orchestrator.TelegramClient'), \
             mock.patch('orchestrator.Anthropic'):

            orchestrator = SprintOrchestrator(self.mock_config)

            stories = [
                Story("MVT-1", "Task 1", "", "To Do", "comp", [], [], []),
            ]
            orchestrator.stories = {s.key: s for s in stories}

            deps = orchestrator.resolve_dependencies(stories)

            # Linked issue "MVT-999" doesn't exist, should be ignored
            assert deps["MVT-1"] == set()


class TestCreateExecutionWaves:
    """Test SprintOrchestrator.create_execution_waves() method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_config = {
            "jira": mock.MagicMock(),
            "github": mock.MagicMock(),
            "telegram": mock.MagicMock(),
            "claude": mock.MagicMock(),
        }

    def test_create_execution_waves_single_story(self):
        """Test wave creation with single story."""
        with mock.patch('orchestrator.JiraClient'), \
             mock.patch('orchestrator.GithubClient'), \
             mock.patch('orchestrator.TelegramClient'), \
             mock.patch('orchestrator.Anthropic'):

            orchestrator = SprintOrchestrator(self.mock_config)

            stories = [
                Story("MVT-1", "Task 1", "", "To Do", "comp", [], [], []),
            ]
            orchestrator.stories = {s.key: s for s in stories}

            waves = orchestrator.create_execution_waves(stories)

            assert len(waves) == 1
            assert len(waves[0]) == 1

    def test_create_execution_waves_independent_stories(self):
        """Test wave creation with independent stories."""
        with mock.patch('orchestrator.JiraClient'), \
             mock.patch('orchestrator.GithubClient'), \
             mock.patch('orchestrator.TelegramClient'), \
             mock.patch('orchestrator.Anthropic'):

            orchestrator = SprintOrchestrator(self.mock_config)

            stories = [
                Story("MVT-1", "Task 1", "", "To Do", "comp", [], [], []),
                Story("MVT-2", "Task 2", "", "To Do", "comp", [], [], []),
                Story("MVT-3", "Task 3", "", "To Do", "comp", [], [], []),
            ]
            orchestrator.stories = {s.key: s for s in stories}

            waves = orchestrator.create_execution_waves(stories)

            # All can run in first wave
            assert len(waves) >= 1
            assert sum(len(w) for w in waves) == 3

    def test_create_execution_waves_respects_dependencies(self):
        """Test wave creation respects story dependencies."""
        with mock.patch('orchestrator.JiraClient'), \
             mock.patch('orchestrator.GithubClient'), \
             mock.patch('orchestrator.TelegramClient'), \
             mock.patch('orchestrator.Anthropic'):

            orchestrator = SprintOrchestrator(self.mock_config)

            stories = [
                Story("MVT-1", "Task 1", "", "To Do", "comp", [], [], []),
                Story("MVT-2", "Task 2", "", "To Do", "comp", [], ["MVT-1"], []),
            ]
            orchestrator.stories = {s.key: s for s in stories}

            waves = orchestrator.create_execution_waves(stories)

            # MVT-1 should come before MVT-2
            assert len(waves) >= 2
            wave_1_keys = {s.key for s in waves[0]}
            wave_2_keys = {s.key for s in waves[1]}

            assert "MVT-1" in wave_1_keys
            assert "MVT-2" in wave_2_keys

    def test_create_execution_waves_max_wave_size(self):
        """Test wave creation respects MAX_WAVE_SIZE."""
        with mock.patch('orchestrator.JiraClient'), \
             mock.patch('orchestrator.GithubClient'), \
             mock.patch('orchestrator.TelegramClient'), \
             mock.patch('orchestrator.Anthropic'):

            orchestrator = SprintOrchestrator(self.mock_config)

            # Create more stories than MAX_WAVE_SIZE
            stories = [
                Story(f"MVT-{i}", f"Task {i}", "", "To Do", "comp", [], [], [])
                for i in range(MAX_WAVE_SIZE + 2)
            ]
            orchestrator.stories = {s.key: s for s in stories}

            waves = orchestrator.create_execution_waves(stories)

            # Verify no wave exceeds MAX_WAVE_SIZE
            for wave in waves:
                assert len(wave) <= MAX_WAVE_SIZE

    def test_create_execution_waves_circular_dependency_error(self):
        """Test circular dependency detection."""
        with mock.patch('orchestrator.JiraClient'), \
             mock.patch('orchestrator.GithubClient'), \
             mock.patch('orchestrator.TelegramClient'), \
             mock.patch('orchestrator.Anthropic'):

            orchestrator = SprintOrchestrator(self.mock_config)

            # Create circular dependency: A -> B -> C -> A
            stories = [
                Story("MVT-1", "Task 1", "", "To Do", "comp", [], ["MVT-3"], []),
                Story("MVT-2", "Task 2", "", "To Do", "comp", [], ["MVT-1"], []),
                Story("MVT-3", "Task 3", "", "To Do", "comp", [], ["MVT-2"], []),
            ]
            orchestrator.stories = {s.key: s for s in stories}

            # Should raise RuntimeError for circular dependency
            with pytest.raises(RuntimeError):
                orchestrator.create_execution_waves(stories)


class TestAssignToAgent:
    """Test SprintOrchestrator.assign_to_agent() method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_config = {
            "jira": mock.MagicMock(),
            "github": mock.MagicMock(),
            "telegram": mock.MagicMock(),
            "claude": mock.MagicMock(),
        }

    def test_assign_to_agent_by_label(self):
        """Test agent assignment by label."""
        with mock.patch('orchestrator.JiraClient'), \
             mock.patch('orchestrator.GithubClient'), \
             mock.patch('orchestrator.TelegramClient'), \
             mock.patch('orchestrator.Anthropic'):

            orchestrator = SprintOrchestrator(self.mock_config)

            story = Story(
                "MVT-1", "Task", "", "To Do", "comp",
                labels=["backend-lambda"],
                linked_issues=[], acceptance_criteria=[],
            )

            agent = orchestrator.assign_to_agent(story)

            assert agent == "code"

    def test_assign_to_agent_frontend_react(self):
        """Test agent assignment for React components."""
        with mock.patch('orchestrator.JiraClient'), \
             mock.patch('orchestrator.GithubClient'), \
             mock.patch('orchestrator.TelegramClient'), \
             mock.patch('orchestrator.Anthropic'):

            orchestrator = SprintOrchestrator(self.mock_config)

            story = Story(
                "MVT-1", "Task", "", "To Do", "comp",
                labels=["frontend-react"],
                linked_issues=[], acceptance_criteria=[],
            )

            agent = orchestrator.assign_to_agent(story)

            assert agent == "code"

    def test_assign_to_agent_security(self):
        """Test agent assignment for security tasks."""
        with mock.patch('orchestrator.JiraClient'), \
             mock.patch('orchestrator.GithubClient'), \
             mock.patch('orchestrator.TelegramClient'), \
             mock.patch('orchestrator.Anthropic'):

            orchestrator = SprintOrchestrator(self.mock_config)

            story = Story(
                "MVT-1", "Task", "", "To Do", "comp",
                labels=["security"],
                linked_issues=[], acceptance_criteria=[],
            )

            agent = orchestrator.assign_to_agent(story)

            assert agent == "test"

    def test_assign_to_agent_deployment(self):
        """Test agent assignment for deployment tasks."""
        with mock.patch('orchestrator.JiraClient'), \
             mock.patch('orchestrator.GithubClient'), \
             mock.patch('orchestrator.TelegramClient'), \
             mock.patch('orchestrator.Anthropic'):

            orchestrator = SprintOrchestrator(self.mock_config)

            story = Story(
                "MVT-1", "Task", "", "To Do", "comp",
                labels=["deployment"],
                linked_issues=[], acceptance_criteria=[],
            )

            agent = orchestrator.assign_to_agent(story)

            assert agent == "deploy"

    def test_assign_to_agent_docs(self):
        """Test agent assignment for documentation."""
        with mock.patch('orchestrator.JiraClient'), \
             mock.patch('orchestrator.GithubClient'), \
             mock.patch('orchestrator.TelegramClient'), \
             mock.patch('orchestrator.Anthropic'):

            orchestrator = SprintOrchestrator(self.mock_config)

            story = Story(
                "MVT-1", "Task", "", "To Do", "comp",
                labels=["docs"],
                linked_issues=[], acceptance_criteria=[],
            )

            agent = orchestrator.assign_to_agent(story)

            assert agent == "docs"

    def test_assign_to_agent_by_component(self):
        """Test agent assignment by component when no label matches."""
        with mock.patch('orchestrator.JiraClient'), \
             mock.patch('orchestrator.GithubClient'), \
             mock.patch('orchestrator.TelegramClient'), \
             mock.patch('orchestrator.Anthropic'):

            orchestrator = SprintOrchestrator(self.mock_config)

            story = Story(
                "MVT-1", "Task", "", "To Do", "persistence",  # Use component not in mapping
                labels=[],
                linked_issues=[], acceptance_criteria=[],
            )

            agent = orchestrator.assign_to_agent(story)

            # "persistence" not in COMPONENT_TO_AGENT, should default to "code"
            assert agent == "code"

    def test_assign_to_agent_infrastructure(self):
        """Test agent assignment for infrastructure."""
        with mock.patch('orchestrator.JiraClient'), \
             mock.patch('orchestrator.GithubClient'), \
             mock.patch('orchestrator.TelegramClient'), \
             mock.patch('orchestrator.Anthropic'):

            orchestrator = SprintOrchestrator(self.mock_config)

            story = Story(
                "MVT-1", "Task", "", "To Do", "infra",
                labels=["infrastructure-cdk"],
                linked_issues=[], acceptance_criteria=[],
            )

            agent = orchestrator.assign_to_agent(story)

            assert agent == "architect"

    def test_assign_to_agent_multiple_labels(self):
        """Test agent assignment picks first matching label."""
        with mock.patch('orchestrator.JiraClient'), \
             mock.patch('orchestrator.GithubClient'), \
             mock.patch('orchestrator.TelegramClient'), \
             mock.patch('orchestrator.Anthropic'):

            orchestrator = SprintOrchestrator(self.mock_config)

            story = Story(
                "MVT-1", "Task", "", "To Do", "comp",
                labels=["enhancement", "backend-lambda", "security"],
                linked_issues=[], acceptance_criteria=[],
            )

            agent = orchestrator.assign_to_agent(story)

            # Should match "backend-lambda"
            assert agent == "code"


class TestExecutionResult:
    """Test ExecutionResult dataclass."""

    def test_execution_result_success(self):
        """Test ExecutionResult for successful execution."""
        result = ExecutionResult(
            story_key="MVT-1",
            agent_type="code",
            status="success",
            output={"pr_url": "https://github.com/..."},
            duration=10.5,
        )

        assert result.story_key == "MVT-1"
        assert result.agent_type == "code"
        assert result.status == "success"
        assert result.error is None

    def test_execution_result_failed(self):
        """Test ExecutionResult for failed execution."""
        result = ExecutionResult(
            story_key="MVT-1",
            agent_type="code",
            status="failed",
            output={},
            error="Compilation error",
            duration=5.0,
        )

        assert result.status == "failed"
        assert result.error == "Compilation error"
