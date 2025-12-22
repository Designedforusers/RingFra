"""
Tests for voice pipeline.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.voice.prompts import SYSTEM_PROMPT, get_tools_config


class TestPrompts:
    """Tests for system prompts and tool configs."""

    def test_system_prompt_exists(self):
        """Test that system prompt is defined."""
        assert SYSTEM_PROMPT
        assert len(SYSTEM_PROMPT) > 100

    def test_system_prompt_has_key_sections(self):
        """Test that system prompt has required sections."""
        assert "Infrastructure Tools" in SYSTEM_PROMPT
        assert "Code Tools" in SYSTEM_PROMPT
        assert "Response Guidelines" in SYSTEM_PROMPT

    def test_tools_config_structure(self):
        """Test that tools config has correct structure."""
        tools = get_tools_config()

        assert isinstance(tools, list)
        assert len(tools) > 0

        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            assert "type" in tool["input_schema"]
            assert tool["input_schema"]["type"] == "object"

    def test_tools_config_has_required_tools(self):
        """Test that required tools are defined."""
        tools = get_tools_config()
        tool_names = [t["name"] for t in tools]

        required_tools = [
            "list_services",
            "get_logs",
            "get_metrics",
            "scale_service",
            "trigger_deploy",
            "rollback_deploy",
            "analyze_code",
            "fix_bug",
            "get_status",
        ]

        for required in required_tools:
            assert required in tool_names, f"Missing required tool: {required}"


class TestHandlers:
    """Tests for Twilio handlers."""

    @pytest.mark.asyncio
    async def test_incoming_call_returns_twiml(self):
        """Test that incoming call returns valid TwiML."""
        from fastapi import Request
        from src.voice.handlers import handle_incoming_call

        # Create mock request with form data
        mock_form = AsyncMock(return_value={
            "From": "+14155551234",
            "CallSid": "CA123456789",
        })

        mock_request = MagicMock(spec=Request)
        mock_request.headers = {"host": "test.onrender.com"}
        mock_request.url = MagicMock()
        mock_request.url.__str__ = lambda self: "https://test.onrender.com/twilio/incoming"
        mock_request.form = mock_form

        response = await handle_incoming_call(mock_request)

        assert response.media_type == "application/xml"
        content = response.body.decode()
        assert "<Response>" in content
        assert "<Say" in content
        assert "<Connect>" in content
        assert "<Stream" in content
        # Check caller phone is passed as parameter
        assert "callerPhone" in content
        assert "+14155551234" in content
