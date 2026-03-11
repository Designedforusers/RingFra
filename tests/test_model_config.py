"""
Tests for model configuration.

Ensures Sonnet 4.6 is configured everywhere after the upgrade.
No old model strings should remain in production code.
"""

import pytest
from unittest.mock import patch


class TestModelConfiguration:
    """Verify model is consistently set to claude-sonnet-4-6."""

    def test_voice_model_default_is_sonnet_46(self):
        """config.VOICE_MODEL defaults to claude-sonnet-4-6."""
        from src.config import Settings

        # Check the default in the Settings class definition
        assert Settings.model_fields["VOICE_MODEL"].default == "claude-sonnet-4-6"

    def test_sdk_options_include_model(self):
        """get_sdk_options returns options with correct model."""
        from src.agent.sdk_client import get_sdk_options

        with patch("src.agent.sdk_client.settings") as mock_settings:
            mock_settings.VOICE_MODEL = "claude-sonnet-4-6"
            mock_settings.RENDER_API_KEY = "test"
            mock_settings.GITHUB_TOKEN = "test"
            mock_settings.EXA_API_KEY = None
            mock_settings.MULTI_TENANT = False

            options = get_sdk_options()

        assert options.model == "claude-sonnet-4-6"

    def test_worker_model_is_sonnet_46(self):
        """Worker's headless SDK uses claude-sonnet-4-6."""
        # Read the source directly to verify the hardcoded model string
        import inspect
        from src.tasks.worker import execute_background_task

        source = inspect.getsource(execute_background_task)
        assert '"claude-sonnet-4-6"' in source

    def test_no_old_model_strings_in_source(self):
        """No references to old model strings in key source files."""
        import os

        old_models = [
            "claude-sonnet-4-5-20250929",
            "claude-sonnet-4-5",
            "claude-sonnet-3-5",
            "claude-3-5-sonnet",
            "claude-3-sonnet",
        ]

        src_dir = os.path.join(os.path.dirname(__file__), "..", "src")
        violations = []

        for root, _dirs, files in os.walk(src_dir):
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                filepath = os.path.join(root, fname)
                with open(filepath) as f:
                    content = f.read()
                for old_model in old_models:
                    if old_model in content:
                        violations.append(f"{filepath}: contains '{old_model}'")

        assert violations == [], f"Old model strings found:\n" + "\n".join(violations)
