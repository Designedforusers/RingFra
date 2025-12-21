"""
Tests for tool implementations.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from src.tools import execute_tool


class TestCodeTools:
    """Tests for code operation tools using Claude Agent SDK."""

    @pytest.mark.asyncio
    async def test_analyze_code(self):
        """Test code analysis with mocked agent."""
        mock_messages = [
            {"type": "result", "subtype": "success", "result": "Found auth module in src/auth.py"}
        ]

        async def mock_query(*args, **kwargs):
            for msg in mock_messages:
                yield msg

        with patch("src.tools.code_tools.query", mock_query):
            from src.tools.code_tools import analyze_code
            result = await analyze_code("authentication flow")
            assert "auth" in result.lower() or "Found" in result

    @pytest.mark.asyncio
    async def test_fix_bug(self):
        """Test bug fixing with mocked agent."""
        mock_messages = [
            {"type": "result", "subtype": "success", "result": "Fixed null check in login.py"}
        ]

        async def mock_query(*args, **kwargs):
            for msg in mock_messages:
                yield msg

        with patch("src.tools.code_tools.query", mock_query):
            from src.tools.code_tools import fix_bug
            result = await fix_bug("login crashes on empty email")
            assert result


class TestRenderToolsMCP:
    """Tests for Render tools that use MCP (via Claude Agent SDK)."""

    @pytest.mark.asyncio
    async def test_list_services(self):
        """Test listing services via Render MCP."""
        mock_messages = [
            {"type": "result", "subtype": "success", "result": "You have 3 services: api, frontend, database"}
        ]

        async def mock_query(*args, **kwargs):
            for msg in mock_messages:
                yield msg

        with patch("src.tools.render_tools.query", mock_query):
            from src.tools.render_tools import list_services
            result = await list_services()
            assert "service" in result.lower()

    @pytest.mark.asyncio
    async def test_get_logs(self):
        """Test getting logs via Render MCP."""
        mock_messages = [
            {"type": "result", "subtype": "success", "result": "No errors in the last 50 logs"}
        ]

        async def mock_query(*args, **kwargs):
            for msg in mock_messages:
                yield msg

        with patch("src.tools.render_tools.query", mock_query):
            from src.tools.render_tools import get_logs
            result = await get_logs("api-service")
            assert result

    @pytest.mark.asyncio
    async def test_get_metrics(self):
        """Test getting metrics via Render MCP."""
        mock_messages = [
            {"type": "result", "subtype": "success", "result": "CPU at 45%, memory at 60%"}
        ]

        async def mock_query(*args, **kwargs):
            for msg in mock_messages:
                yield msg

        with patch("src.tools.render_tools.query", mock_query):
            from src.tools.render_tools import get_metrics
            result = await get_metrics("api-service")
            assert "cpu" in result.lower() or "%" in result


class TestRenderToolsAPI:
    """Tests for Render tools that use direct API (scale, deploy, rollback)."""

    @pytest.mark.asyncio
    async def test_scale_service_success(self):
        """Test scaling a service via direct API."""
        mock_service = {
            "id": "srv-123",
            "name": "api",
            "numInstances": 1
        }

        async def mock_get_service(name):
            return mock_service

        async def mock_api(method, endpoint, data=None):
            return {}

        with patch("src.tools.render_tools.get_service_by_name", mock_get_service):
            with patch("src.tools.render_tools.render_api", mock_api):
                from src.tools.render_tools import scale_service
                result = await scale_service("api", 3)
                assert "scaled" in result.lower()
                assert "1" in result and "3" in result

    @pytest.mark.asyncio
    async def test_scale_service_not_found(self):
        """Test scaling when service doesn't exist."""
        async def mock_get_service(name):
            return None

        with patch("src.tools.render_tools.get_service_by_name", mock_get_service):
            from src.tools.render_tools import scale_service
            result = await scale_service("nonexistent", 2)
            assert "couldn't find" in result.lower()

    @pytest.mark.asyncio
    async def test_trigger_deploy_success(self):
        """Test triggering a deployment via direct API."""
        mock_service = {"id": "srv-123", "name": "api"}

        async def mock_get_service(name):
            return mock_service

        async def mock_api(method, endpoint, data=None):
            return {"id": "dep-123"}

        with patch("src.tools.render_tools.get_service_by_name", mock_get_service):
            with patch("src.tools.render_tools.render_api", mock_api):
                from src.tools.render_tools import trigger_deploy
                result = await trigger_deploy("api")
                assert "triggered" in result.lower()

    @pytest.mark.asyncio
    async def test_trigger_deploy_with_notification(self):
        """Test triggering a deployment with SMS notification tracking."""
        mock_service = {"id": "srv-123", "name": "api"}

        async def mock_get_service(name):
            return mock_service

        async def mock_api(method, endpoint, data=None):
            return {"id": "dep-456"}

        with patch("src.tools.render_tools.get_service_by_name", mock_get_service):
            with patch("src.tools.render_tools.render_api", mock_api):
                with patch("src.tools.render_tools.track_deploy") as mock_track:
                    from src.tools.render_tools import trigger_deploy
                    result = await trigger_deploy("api", caller_phone="+14155551234")

                    assert "text you" in result.lower() or "triggered" in result.lower()
                    mock_track.assert_called_once_with("dep-456", "api", "+14155551234")

    @pytest.mark.asyncio
    async def test_rollback_deploy_no_previous(self):
        """Test rollback when there's no previous deploy."""
        mock_service = {"id": "srv-123", "name": "api"}

        async def mock_get_service(name):
            return mock_service

        async def mock_api(method, endpoint, data=None):
            if "deploys" in endpoint and method == "GET":
                return [{"deploy": {"id": "dep-1", "status": "live"}}]  # Only one deploy
            return {}

        with patch("src.tools.render_tools.get_service_by_name", mock_get_service):
            with patch("src.tools.render_tools.render_api", mock_api):
                from src.tools.render_tools import rollback_deploy
                result = await rollback_deploy("api")
                assert "no previous" in result.lower()


class TestToolExecution:
    """Tests for tool execution dispatcher."""

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self):
        """Test executing an unknown tool."""
        result = await execute_tool("unknown_tool", {})
        assert "don't recognize" in result.lower()

    @pytest.mark.asyncio
    async def test_execute_tool_error_handling(self):
        """Test tool execution with error."""

        async def failing_query(*args, **kwargs):
            raise Exception("Connection failed")
            yield

        with patch("src.tools.render_tools.query", failing_query):
            result = await execute_tool("list_services", {})
            assert "error" in result.lower() or "couldn't" in result.lower()
