"""
Unit tests for Code Agent.

Tests agents/subagents/code_agent.py:
- _create_branch_name() generates proper branch names
- _parse_generated_code() extracts code from Claude responses
- _validate_code() checks for Python syntax errors
- analyze_story() determines story type from labels/components
"""

import pytest
import re
import sys
import os
from unittest import mock

# Import code agent module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../agents'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../agents/subagents'))

# Mock Anthropic and other imports before importing code_agent
with mock.patch.dict(sys.modules, {
    'anthropic': mock.MagicMock(),
    'requests': mock.MagicMock(),
}):
    from code_agent import CodeAgent


class TestCreateBranchName:
    """Test branch name generation."""

    def test_create_branch_name_basic(self):
        """Test basic branch name creation."""
        story = {
            "key": "MVT-101",
            "summary": "Fix login bug",
        }

        agent = CodeAgent(mock.MagicMock())
        branch_name = agent._create_branch_name(story)

        assert branch_name.startswith("feature/")
        assert "mvt-101" in branch_name
        assert "login" in branch_name

    def test_create_branch_name_slugifies_spaces(self):
        """Test that spaces are converted to hyphens."""
        story = {
            "key": "MVT-102",
            "summary": "Add user authentication",
        }

        agent = CodeAgent(mock.MagicMock())
        branch_name = agent._create_branch_name(story)

        assert "-" in branch_name
        assert " " not in branch_name

    def test_create_branch_name_removes_special_chars(self):
        """Test that special characters are removed."""
        story = {
            "key": "MVT-103",
            "summary": "Refactor API (v2.0) & optimize!",
        }

        agent = CodeAgent(mock.MagicMock())
        branch_name = agent._create_branch_name(story)

        # Should only contain lowercase letters, numbers, hyphens
        assert re.match(r"^feature/[a-z0-9-]+$", branch_name)

    def test_create_branch_name_lowercase(self):
        """Test that branch name is lowercase."""
        story = {
            "key": "MVT-104",
            "summary": "IMPLEMENT NEW FEATURE",
        }

        agent = CodeAgent(mock.MagicMock())
        branch_name = agent._create_branch_name(story)

        assert branch_name == branch_name.lower()

    def test_create_branch_name_limits_length(self):
        """Test that branch name is limited in length."""
        story = {
            "key": "MVT-105",
            "summary": "This is a very long story summary that goes on and on",
        }

        agent = CodeAgent(mock.MagicMock())
        branch_name = agent._create_branch_name(story)

        # Max 30 chars for slug + "feature/MVT-105-"
        assert len(branch_name) <= 50

    def test_create_branch_name_missing_summary(self):
        """Test branch name with missing summary."""
        story = {
            "key": "MVT-106",
            "summary": "",
        }

        agent = CodeAgent(mock.MagicMock())
        branch_name = agent._create_branch_name(story)

        assert branch_name.startswith("feature/")
        assert "mvt-106" in branch_name

    def test_create_branch_name_missing_key(self):
        """Test branch name with missing key."""
        story = {
            "summary": "Test feature",
        }

        agent = CodeAgent(mock.MagicMock())
        branch_name = agent._create_branch_name(story)

        assert branch_name.startswith("feature/")
        assert "unknown" in branch_name.lower()


class TestParseGeneratedCode:
    """Test code extraction from Claude responses."""

    def test_parse_generated_code_file_prefix(self):
        """Test parsing code blocks with FILE: prefix."""
        response = """
Here's the implementation:

```
FILE: src/handler.py
def handler():
    return {"status": "ok"}
```

And another file:

```
FILE: tests/test_handler.py
def test_handler():
    assert True
```
"""

        agent = CodeAgent(mock.MagicMock())
        files = agent._parse_generated_code(response)

        assert "src/handler.py" in files
        assert "tests/test_handler.py" in files
        assert "def handler()" in files["src/handler.py"]
        assert "def test_handler()" in files["tests/test_handler.py"]

    def test_parse_generated_code_strips_content(self):
        """Test that parsed content is stripped of whitespace."""
        response = """
```
FILE: app.py

  def main():
      pass

```
"""

        agent = CodeAgent(mock.MagicMock())
        files = agent._parse_generated_code(response)

        content = files["app.py"]
        assert not content.startswith(" ")
        assert not content.endswith(" ")

    def test_parse_generated_code_markdown_fallback(self):
        """Test fallback to plain markdown code blocks."""
        response = """
```python
def test():
    return True
```

```python
def another():
    return False
```
"""

        agent = CodeAgent(mock.MagicMock())
        files = agent._parse_generated_code(response)

        # Fallback generates generic filenames
        assert "generated_0.py" in files or len(files) > 0

    def test_parse_generated_code_no_code(self):
        """Test parsing response with no code blocks."""
        response = "Sorry, I couldn't generate code for this request."

        agent = CodeAgent(mock.MagicMock())
        files = agent._parse_generated_code(response)

        assert files == {}

    def test_parse_generated_code_multiple_files(self):
        """Test parsing multiple file blocks."""
        response = """
```
FILE: module.py
import os

class Module:
    pass
```

```
FILE: config.py
CONFIG = {"debug": True}
```

```
FILE: requirements.txt
requests==2.28.0
```
"""

        agent = CodeAgent(mock.MagicMock())
        files = agent._parse_generated_code(response)

        assert len(files) == 3
        assert "module.py" in files
        assert "config.py" in files
        assert "requirements.txt" in files


class TestValidateCode:
    """Test code validation."""

    def test_validate_code_valid_python(self):
        """Test validation of valid Python code."""
        files = {
            "app.py": "def hello():\n    return 'world'",
        }

        agent = CodeAgent(mock.MagicMock())
        errors = agent._validate_code(files)

        assert errors == []

    def test_validate_code_syntax_error(self):
        """Test detection of Python syntax errors."""
        files = {
            "app.py": "def hello(\n    return 'world'",  # Missing closing paren
        }

        agent = CodeAgent(mock.MagicMock())
        errors = agent._validate_code(files)

        assert len(errors) > 0
        assert "Syntax error" in errors[0]

    def test_validate_code_incomplete_with_todo(self):
        """Test detection of incomplete code (TODOs)."""
        files = {
            "app.py": "def process():\n    # TODO: implement this\n    pass",
        }

        agent = CodeAgent(mock.MagicMock())
        errors = agent._validate_code(files)

        assert len(errors) > 0
        assert "TODO" in errors[0] or "Incomplete" in errors[0]

    def test_validate_code_unknown_import(self):
        """Test detection of unknown imports."""
        files = {
            "app.py": "import unknown_package\n\ndef main():\n    pass",
        }

        agent = CodeAgent(mock.MagicMock())
        errors = agent._validate_code(files)

        assert len(errors) > 0
        assert "unknown_package" in errors[0]

    def test_validate_code_non_python_file(self):
        """Test validation skips non-Python files."""
        files = {
            "config.json": '{"invalid json}',
            "script.sh": "echo hello",
        }

        agent = CodeAgent(mock.MagicMock())
        errors = agent._validate_code(files)

        # Should not validate non-Python files
        assert len(errors) == 0

    def test_validate_code_multiple_files(self):
        """Test validation of multiple files."""
        files = {
            "good.py": "def valid():\n    pass",
            "bad.py": "def bad(\n    pass",  # Syntax error
        }

        agent = CodeAgent(mock.MagicMock())
        errors = agent._validate_code(files)

        assert len(errors) == 1
        assert "bad.py" in errors[0]

    def test_validate_code_complex_valid_python(self):
        """Test validation of complex but valid Python."""
        code = """
import asyncio
from typing import Dict, List, Optional

class DataProcessor:
    def __init__(self, config: Dict):
        self.config = config

    async def process(self, data: List[str]) -> Optional[Dict]:
        try:
            result = await self._validate(data)
            return result
        except Exception as e:
            logger.error(f"Error: {e}")
            return None

    async def _validate(self, data):
        return {"status": "ok"}
"""

        files = {"processor.py": code}

        agent = CodeAgent(mock.MagicMock())
        errors = agent._validate_code(files)

        assert errors == []


class TestAnalyzeStory:
    """Test story type analysis."""

    def test_analyze_story_backend_lambda(self):
        """Test detection of backend Lambda stories."""
        story = {
            "labels": ["backend-lambda"],
            "component": "api",
        }

        agent = CodeAgent(mock.MagicMock())
        story_type = agent.analyze_story(story)

        assert story_type == "lambda_handler"

    def test_analyze_story_frontend_react(self):
        """Test detection of frontend React stories."""
        story = {
            "labels": ["frontend-react"],
            "component": "ui",
        }

        agent = CodeAgent(mock.MagicMock())
        story_type = agent.analyze_story(story)

        assert story_type == "react_component"

    def test_analyze_story_infrastructure_cdk(self):
        """Test detection of infrastructure CDK stories."""
        story = {
            "labels": ["infrastructure-cdk"],
            "component": "infra",
        }

        agent = CodeAgent(mock.MagicMock())
        story_type = agent.analyze_story(story)

        assert story_type == "cdk_construct"

    def test_analyze_story_api_spec(self):
        """Test detection of API spec stories."""
        story = {
            "labels": ["api-spec"],
            "component": "api",
        }

        agent = CodeAgent(mock.MagicMock())
        story_type = agent.analyze_story(story)

        assert story_type == "openapi_spec"

    def test_analyze_story_database(self):
        """Test detection of database stories."""
        story = {
            "labels": ["database"],
            "component": "persistence",
        }

        agent = CodeAgent(mock.MagicMock())
        story_type = agent.analyze_story(story)

        assert story_type == "database_migration"

    def test_analyze_story_component_over_label(self):
        """Test story type detection from component."""
        story = {
            "labels": [],
            "component": "lambda",
        }

        agent = CodeAgent(mock.MagicMock())
        story_type = agent.analyze_story(story)

        assert story_type == "lambda_handler"

    def test_analyze_story_component_react(self):
        """Test story type detection from component (react)."""
        story = {
            "labels": [],
            "component": "React Components",
        }

        agent = CodeAgent(mock.MagicMock())
        story_type = agent.analyze_story(story)

        assert story_type == "react_component"

    def test_analyze_story_no_label_or_component(self):
        """Test default story type."""
        story = {
            "labels": [],
            "component": "",
        }

        agent = CodeAgent(mock.MagicMock())
        story_type = agent.analyze_story(story)

        assert story_type == "generic"

    def test_analyze_story_label_priority(self):
        """Test that labels take priority over component."""
        story = {
            "labels": ["backend-lambda"],
            "component": "React Component",  # Conflicting component
        }

        agent = CodeAgent(mock.MagicMock())
        story_type = agent.analyze_story(story)

        assert story_type == "lambda_handler"  # Label takes priority

    def test_analyze_story_missing_labels_field(self):
        """Test story without labels field."""
        story = {
            "component": "lambda-handler",
        }

        agent = CodeAgent(mock.MagicMock())
        story_type = agent.analyze_story(story)

        assert story_type == "lambda_handler"


class TestCodeAgentInit:
    """Test CodeAgent initialization."""

    def test_code_agent_init(self):
        """Test CodeAgent initialization."""
        mock_config = {
            "github": mock.MagicMock(token="token", org="org", repo="repo"),
            "claude": mock.MagicMock(api_key="key"),
        }

        with mock.patch('subagents.code_agent.Anthropic'):
            agent = CodeAgent(mock_config)

            assert agent.config == mock_config
            assert agent.github is not None

    def test_code_agent_github_client_init(self):
        """Test GitHub client is initialized."""
        mock_config = {
            "github": mock.MagicMock(token="test_token", org="test_org", repo="test_repo"),
            "claude": mock.MagicMock(api_key="key"),
        }

        with mock.patch('subagents.code_agent.Anthropic'):
            agent = CodeAgent(mock_config)

            assert agent.github.token == "test_token"
            assert agent.github.org == "test_org"
            assert agent.github.repo == "test_repo"
