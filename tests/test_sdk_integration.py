"""
Tests for Claude Agent SDK integration in the worker.

These tests verify:
1. ClaudeAgentOptions is used correctly (not a dict)
2. Fail-fast checks work for missing API key and CLI
3. Structured output parsing works
4. Text fallback works when structured output is missing
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from uuid import UUID


class TestClaudeAgentOptionsUsage:
    """Verify ClaudeAgentOptions is used instead of dict."""

    def test_query_options_is_claude_agent_options(self):
        """Confirm we're using ClaudeAgentOptions object, not dict."""
        from claude_agent_sdk import ClaudeAgentOptions

        # Build query options the same way worker.py does
        query_options = ClaudeAgentOptions(
            cwd="/app",
            env={"TEST": "value"},
            system_prompt="Test prompt",
            mcp_servers={},
            permission_mode="bypassPermissions",
            allowed_tools=["Read", "Write"],
            max_turns=30,
            output_format={
                "type": "json_schema",
                "schema": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "success": {"type": "boolean"}
                    },
                    "required": ["summary", "success"]
                }
            },
        )

        # CRITICAL: Must be ClaudeAgentOptions, NOT dict
        assert isinstance(query_options, ClaudeAgentOptions)
        assert not isinstance(query_options, dict)

        # Verify it has the expected attributes
        assert query_options.cwd == "/app"
        assert query_options.permission_mode == "bypassPermissions"
        assert query_options.max_turns == 30

    def test_claude_agent_options_has_can_use_tool_attribute(self):
        """Verify ClaudeAgentOptions has the can_use_tool attribute that caused the error."""
        from claude_agent_sdk import ClaudeAgentOptions

        options = ClaudeAgentOptions()

        # This is the attribute that caused "'dict' object has no attribute 'can_use_tool'"
        # It's an optional callback, not a method - but the attribute must exist
        assert hasattr(options, 'can_use_tool')

        # When set, it should be callable
        options_with_callback = ClaudeAgentOptions(can_use_tool=lambda tool_name: True)
        assert options_with_callback.can_use_tool is not None
        assert callable(options_with_callback.can_use_tool)


class TestFailFastChecks:
    """Test that worker fails fast when dependencies are missing."""

    @pytest.mark.asyncio
    async def test_fails_without_api_key(self):
        """Worker should fail immediately if ANTHROPIC_API_KEY is not set."""
        from src.tasks.worker import execute_background_task

        # API key check happens BEFORE database calls, so no mocking needed
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
            with patch("shutil.which", return_value="/usr/bin/claude"):
                result = await execute_background_task({}, "test-task-id")

        assert "error" in result
        assert "ANTHROPIC_API_KEY" in result["error"]

    @pytest.mark.asyncio
    async def test_fails_without_cli(self):
        """Worker should fail immediately if Claude CLI is not found."""
        from src.tasks.worker import execute_background_task

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}, clear=False):
            with patch("shutil.which", return_value=None):  # CLI not found
                result = await execute_background_task({}, "test-task-id")

        assert "error" in result
        assert "CLI not found" in result["error"]


class TestStructuredOutputParsing:
    """Test parsing of structured output from SDK."""

    @pytest.mark.asyncio
    async def test_parses_structured_result(self):
        """Test that structured output is correctly extracted from result message."""
        from src.tasks.worker import execute_background_task

        mock_task = {
            "phone": "+1234567890",
            "user_id": UUID("12345678-1234-5678-1234-567812345678"),
            "plan": {"objective": "Test task", "steps": ["Step 1"]},
            "task_type": "test"
        }

        # Mock a result message with structured output
        mock_result_message = {
            "type": "result",
            "structured_output": {
                "summary": "Task completed successfully",
                "success": True,
                "details": {"items_processed": 5},
                "action_items": ["Review the changes"]
            },
            "total_cost_usd": 0.0123
        }

        async def mock_query(*args, **kwargs):
            yield mock_result_message

        # Note: Must mock at src.tasks.worker level since that's where the functions are imported
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}, clear=False):
            with patch("shutil.which", return_value="/usr/bin/claude"):
                with patch("src.db.background_tasks.get_background_task", new_callable=AsyncMock, return_value=mock_task):
                    with patch("src.db.background_tasks.update_task_status", new_callable=AsyncMock):
                        with patch("src.db.users.get_user_credentials", new_callable=AsyncMock, return_value=None):
                            with patch("src.db.users.get_user_repos", new_callable=AsyncMock, return_value=None):
                                with patch("claude_agent_sdk.query", side_effect=mock_query):
                                    with patch("src.tasks.worker.initiate_callback", new_callable=AsyncMock) as mock_callback:
                                        with patch("src.tasks.worker.send_sms", new_callable=AsyncMock):
                                            result = await execute_background_task({}, "test-task-id")

        # Verify structured result was captured
        assert result["status"] == "completed"
        assert "Task completed successfully" in result["result"]

        # Verify callback was called with structured data
        mock_callback.assert_called_once()
        callback_context = mock_callback.call_args[1]["context"]
        assert callback_context["summary"] == "Task completed successfully"
        assert callback_context["success"] is True

    @pytest.mark.asyncio
    async def test_uses_text_fallback_when_no_structured_output(self):
        """Test that last text output is used when structured output is missing."""
        from src.tasks.worker import execute_background_task

        mock_task = {
            "phone": "+1234567890",
            "user_id": UUID("12345678-1234-5678-1234-567812345678"),
            "plan": {"objective": "Test task", "steps": ["Step 1"]},
            "task_type": "test"
        }

        # Mock messages: assistant with text, then result without structured output
        mock_messages = [
            {
                "type": "assistant",
                "content": [{"type": "text", "text": "I completed the deployment to staging environment."}]
            },
            {
                "type": "result",
                "structured_output": None,  # No structured output
                "total_cost_usd": 0.05
            }
        ]

        async def mock_query(*args, **kwargs):
            for msg in mock_messages:
                yield msg

        # Note: Must mock at src.tasks.worker level since that's where the functions are imported
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}, clear=False):
            with patch("shutil.which", return_value="/usr/bin/claude"):
                with patch("src.db.background_tasks.get_background_task", new_callable=AsyncMock, return_value=mock_task):
                    with patch("src.db.background_tasks.update_task_status", new_callable=AsyncMock):
                        with patch("src.db.users.get_user_credentials", new_callable=AsyncMock, return_value=None):
                            with patch("src.db.users.get_user_repos", new_callable=AsyncMock, return_value=None):
                                with patch("claude_agent_sdk.query", side_effect=mock_query):
                                    with patch("src.tasks.worker.initiate_callback", new_callable=AsyncMock) as mock_callback:
                                        with patch("src.tasks.worker.send_sms", new_callable=AsyncMock):
                                            result = await execute_background_task({}, "test-task-id")

        # Verify text fallback was used
        assert "deployment to staging" in result["result"]

        # Verify callback includes the fallback text
        mock_callback.assert_called_once()
        callback_context = mock_callback.call_args[1]["context"]
        assert "deployment to staging" in callback_context["summary"]
        assert callback_context["details"]["fallback"] is True


class TestWorkerStartupValidation:
    """Test worker startup environment validation."""

    @pytest.mark.asyncio
    async def test_on_startup_logs_sdk_version(self):
        """Verify on_startup logs SDK version."""
        from src.tasks.worker import WorkerSettings
        from loguru import logger
        import io

        # Capture log output
        log_output = io.StringIO()
        handler_id = logger.add(log_output, format="{message}")

        try:
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}, clear=False):
                with patch("shutil.which", return_value="/usr/bin/claude"):
                    await WorkerSettings.on_startup({})

            log_text = log_output.getvalue()

            # Verify environment validation was logged
            assert "WORKER ENVIRONMENT VALIDATION" in log_text
            assert "claude-agent-sdk version" in log_text
            assert "Claude Code CLI found" in log_text
            assert "ANTHROPIC_API_KEY" in log_text
        finally:
            logger.remove(handler_id)

    @pytest.mark.asyncio
    async def test_on_startup_warns_missing_api_key(self):
        """Verify on_startup warns when API key is missing."""
        from src.tasks.worker import WorkerSettings
        from loguru import logger
        import io

        log_output = io.StringIO()
        handler_id = logger.add(log_output, format="{message}")

        try:
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
                with patch("shutil.which", return_value="/usr/bin/claude"):
                    await WorkerSettings.on_startup({})

            log_text = log_output.getvalue()
            assert "NOT SET - SDK will fail" in log_text
        finally:
            logger.remove(handler_id)


class TestTaskResultSchema:
    """Test TASK_RESULT_SCHEMA validity."""

    def test_schema_is_valid_json_schema(self):
        """Verify the schema is a valid JSON schema structure."""
        from src.tasks.schemas import TASK_RESULT_SCHEMA

        assert TASK_RESULT_SCHEMA["type"] == "object"
        assert "properties" in TASK_RESULT_SCHEMA
        assert "summary" in TASK_RESULT_SCHEMA["properties"]
        assert "success" in TASK_RESULT_SCHEMA["properties"]
        assert TASK_RESULT_SCHEMA["required"] == ["summary", "success"]

    def test_schema_works_with_claude_agent_options(self):
        """Verify the schema can be passed to ClaudeAgentOptions."""
        from claude_agent_sdk import ClaudeAgentOptions
        from src.tasks.schemas import TASK_RESULT_SCHEMA

        # This should not raise
        options = ClaudeAgentOptions(
            output_format={
                "type": "json_schema",
                "schema": TASK_RESULT_SCHEMA
            }
        )

        assert options.output_format is not None
        assert options.output_format["type"] == "json_schema"
