"""
Pytest configuration and fixtures.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_settings():
    """Mock application settings."""
    with patch("src.config.settings") as mock:
        mock.TWILIO_ACCOUNT_SID = "test_sid"
        mock.TWILIO_AUTH_TOKEN = "test_token"
        mock.TWILIO_PHONE_NUMBER = "+1234567890"
        mock.ANTHROPIC_API_KEY = "test_anthropic_key"
        mock.DEEPGRAM_API_KEY = "test_deepgram_key"
        mock.CARTESIA_API_KEY = "test_cartesia_key"
        mock.RENDER_API_KEY = "test_render_key"
        mock.GITHUB_TOKEN = "test_github_token"
        mock.GITHUB_REPO_URL = "https://github.com/test/repo.git"
        mock.APP_ENV = "test"
        mock.LOG_LEVEL = "DEBUG"
        mock.TARGET_REPO_PATH = "/tmp/test-repo"
        mock.HOST = "0.0.0.0"
        mock.PORT = 8765
        mock.VOICE_MODEL = "claude-sonnet-4-5-20250929"
        mock.STT_MODEL = "nova-2"
        mock.TTS_VOICE = "test-voice"
        yield mock


@pytest.fixture
def mock_render_api():
    """Mock Render API responses."""
    return AsyncMock(
        return_value=[
            {
                "service": {
                    "id": "srv-123",
                    "name": "demo-api",
                    "type": "web_service",
                    "suspended": "not_suspended",
                    "numInstances": 1,
                }
            },
            {
                "service": {
                    "id": "srv-456",
                    "name": "demo-worker",
                    "type": "background_worker",
                    "suspended": "not_suspended",
                    "numInstances": 1,
                }
            },
        ]
    )


@pytest.fixture
def mock_claude_code():
    """Mock Claude Code CLI execution."""

    async def mock_run(*args, **kwargs):
        return "Mock response from Claude Code"

    return mock_run
