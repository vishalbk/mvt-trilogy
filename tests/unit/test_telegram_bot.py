"""
Unit tests for Telegram bot Lambda handler.

Tests the deployed handler at telegram-bot/lambda_function.py:
- HTML escaping with esc()
- lambda_handler() routing for commands
- Mock urllib API calls to Telegram and JIRA
- Callback query handling
"""

import json
import os
import pytest
from unittest import mock
from urllib.error import HTTPError
from io import BytesIO

# Import the handler module
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../telegram-bot'))

import lambda_function as handler


class TestEscFunction:
    """Test HTML escaping utility."""

    def test_esc_escapes_ampersand(self):
        """Test escaping of & character."""
        assert handler.esc("A & B") == "A &amp; B"

    def test_esc_escapes_less_than(self):
        """Test escaping of < character."""
        assert handler.esc("A < B") == "A &lt; B"

    def test_esc_escapes_greater_than(self):
        """Test escaping of > character."""
        assert handler.esc("A > B") == "A &gt; B"

    def test_esc_escapes_multiple_chars(self):
        """Test escaping of multiple special characters."""
        result = handler.esc("A & B < C > D")
        assert result == "A &amp; B &lt; C &gt; D"

    def test_esc_converts_to_string(self):
        """Test that esc converts non-string input to string."""
        assert handler.esc(123) == "123"
        assert handler.esc(None) == "None"

    def test_esc_empty_string(self):
        """Test escaping of empty string."""
        assert handler.esc("") == ""

    def test_esc_no_special_chars(self):
        """Test string without special characters."""
        assert handler.esc("hello world") == "hello world"


class TestTelegramApi:
    """Test Telegram API communication."""

    @mock.patch('urllib.request.urlopen')
    def test_telegram_api_success(self, mock_urlopen):
        """Test successful Telegram API call."""
        # Mock response
        mock_response = mock.MagicMock()
        mock_response.read.return_value = b'{"ok": true, "result": {"message_id": 123}}'
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        result = handler.telegram_api("sendMessage", {"chat_id": 123, "text": "Hello"})

        assert result["ok"] is True
        assert result["result"]["message_id"] == 123

    @mock.patch('urllib.request.urlopen')
    def test_telegram_api_http_error(self, mock_urlopen):
        """Test Telegram API HTTP error handling."""
        error = HTTPError("url", 403, "Forbidden", {}, BytesIO(b"Forbidden"))
        mock_urlopen.side_effect = error

        result = handler.telegram_api("sendMessage", {})

        assert result["ok"] is False
        assert "error" in result

    @mock.patch('urllib.request.urlopen')
    def test_telegram_api_generic_exception(self, mock_urlopen):
        """Test Telegram API generic exception handling."""
        mock_urlopen.side_effect = Exception("Connection timeout")

        result = handler.telegram_api("sendMessage", {})

        assert result["ok"] is False
        assert "error" in result


class TestJiraApi:
    """Test JIRA API communication via urllib mocking."""

    @mock.patch('lambda_function.JIRA_BASE_URL', "https://jira.example.com")
    @mock.patch('urllib.request.urlopen')
    def test_jira_api_get_success(self, mock_urlopen):
        """Test successful JIRA GET request."""
        mock_response = mock.MagicMock()
        mock_response.read.return_value = b'{"key": "MVT-123", "fields": {"summary": "Test"}}'
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        result = handler.jira_api("GET", "issue/MVT-123")

        assert result["key"] == "MVT-123"
        assert result["fields"]["summary"] == "Test"

    @mock.patch('lambda_function.JIRA_BASE_URL', "https://jira.example.com")
    @mock.patch('urllib.request.urlopen')
    def test_jira_api_post_success(self, mock_urlopen):
        """Test successful JIRA POST request."""
        mock_response = mock.MagicMock()
        mock_response.read.return_value = b'{"key": "MVT-124"}'
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        data = {"fields": {"summary": "New issue"}}
        result = handler.jira_api("POST", "issue", data)

        assert result["key"] == "MVT-124"

    @mock.patch('lambda_function.JIRA_BASE_URL', "https://jira.example.com")
    @mock.patch('urllib.request.urlopen')
    def test_jira_api_http_error(self, mock_urlopen):
        """Test JIRA API HTTP error handling."""
        error = HTTPError("url", 401, "Unauthorized", {}, BytesIO(b"Invalid credentials"))
        mock_urlopen.side_effect = error

        result = handler.jira_api("GET", "issue/MVT-999")

        assert "error" in result

    @mock.patch('lambda_function.JIRA_BASE_URL', "https://jira.example.com")
    @mock.patch('urllib.request.urlopen')
    def test_jira_api_exception(self, mock_urlopen):
        """Test JIRA API exception handling."""
        mock_urlopen.side_effect = Exception("Network error")

        result = handler.jira_api("GET", "issue/MVT-123")

        assert "error" in result


class TestLambdaHandlerRouting:
    """Test lambda_handler command routing."""

    @mock.patch('lambda_function.send')
    def test_handle_start_command(self, mock_send):
        """Test /start command routing."""
        event = {
            "body": json.dumps({
                "message": {
                    "chat": {"id": 123},
                    "text": "/start"
                }
            })
        }

        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 200
        mock_send.assert_called_once()
        # Verify welcome message was sent
        call_args = mock_send.call_args[0]
        assert "Welcome" in call_args[1] or "welcome" in call_args[1].lower()

    @mock.patch('lambda_function.send')
    def test_handle_brainstorm_command(self, mock_send):
        """Test /brainstorm command routing."""
        event = {
            "body": json.dumps({
                "message": {
                    "chat": {"id": 123},
                    "text": "/brainstorm new features"
                }
            })
        }

        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 200

    @mock.patch('lambda_function.send')
    def test_handle_sprint_status_command(self, mock_send):
        """Test /sprint_status command routing."""
        event = {
            "body": json.dumps({
                "message": {
                    "chat": {"id": 123},
                    "text": "/sprint_status"
                }
            })
        }

        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 200

    @mock.patch('lambda_function.send')
    def test_handle_create_epic_command(self, mock_send):
        """Test /create_epic command routing."""
        event = {
            "body": json.dumps({
                "message": {
                    "chat": {"id": 123},
                    "text": "/create_epic Dashboard redesign"
                }
            })
        }

        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 200

    @mock.patch('lambda_function.send')
    def test_handle_unknown_command(self, mock_send):
        """Test unknown command handling."""
        event = {
            "body": json.dumps({
                "message": {
                    "chat": {"id": 123},
                    "text": "/unknown_command"
                }
            })
        }

        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 200
        mock_send.assert_called_once()

    def test_lambda_handler_invalid_json(self):
        """Test lambda_handler with invalid JSON."""
        event = {"body": "invalid json"}

        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 400

    def test_lambda_handler_no_message(self):
        """Test lambda_handler without message."""
        event = {
            "body": json.dumps({})
        }

        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 200

    def test_lambda_handler_empty_chat_id(self):
        """Test lambda_handler with empty chat_id."""
        event = {
            "body": json.dumps({
                "message": {
                    "chat": {"id": None},
                    "text": "/start"
                }
            })
        }

        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 200

    def test_lambda_handler_empty_text(self):
        """Test lambda_handler with empty text."""
        event = {
            "body": json.dumps({
                "message": {
                    "chat": {"id": 123},
                    "text": ""
                }
            })
        }

        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 200


class TestCallbackQueryHandling:
    """Test callback query handling for inline buttons."""

    @mock.patch('lambda_function.jira_api')
    @mock.patch('lambda_function.telegram_api')
    @mock.patch('lambda_function.send')
    def test_callback_approve_release(self, mock_send, mock_api, mock_jira):
        """Test approve_release callback."""
        # Mock JIRA API responses
        mock_jira.return_value = {
            "issues": [
                {
                    "key": "MVT-1",
                    "fields": {"summary": "Test story"},
                }
            ]
        }

        event = {
            "body": json.dumps({
                "callback_query": {
                    "id": "query_123",
                    "data": "approve_release",
                    "message": {
                        "chat": {"id": 123}
                    }
                }
            })
        }

        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 200
        # Verify answerCallbackQuery was called
        mock_api.assert_called_once()

    @mock.patch('lambda_function.telegram_api')
    @mock.patch('lambda_function.send')
    def test_callback_hold_release(self, mock_send, mock_api):
        """Test hold_release callback."""
        event = {
            "body": json.dumps({
                "callback_query": {
                    "id": "query_124",
                    "data": "hold_release",
                    "message": {
                        "chat": {"id": 123}
                    }
                }
            })
        }

        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 200


class TestSendFunction:
    """Test send message helper function."""

    @mock.patch('lambda_function.telegram_api')
    def test_send_basic_message(self, mock_api):
        """Test sending basic message."""
        mock_api.return_value = {"ok": True}

        handler.send(123, "Hello World")

        mock_api.assert_called_once()
        call_args = mock_api.call_args[0]
        assert call_args[0] == "sendMessage"
        assert call_args[1]["chat_id"] == 123
        assert call_args[1]["text"] == "Hello World"
        assert call_args[1]["parse_mode"] == "HTML"

    @mock.patch('lambda_function.telegram_api')
    def test_send_with_reply_markup(self, mock_api):
        """Test sending message with reply markup."""
        mock_api.return_value = {"ok": True}

        markup = {"inline_keyboard": [[{"text": "Button", "callback_data": "btn"}]]}
        handler.send(123, "Click", reply_markup=markup)

        call_args = mock_api.call_args[0]
        assert call_args[1]["reply_markup"] == markup
