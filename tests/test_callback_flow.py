"""
Tests for outbound callback flow.

Verifies:
1. TwiML generation for outbound calls
2. Greeting text matches task_type and success/failure
3. Twilio errors propagate correctly
"""

import html
import json
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


class TestInitiateCallback:
    """Test initiate_callback builds correct TwiML and calls Twilio."""

    @pytest.mark.asyncio
    async def test_builds_correct_twiml_for_success(self):
        """TwiML has <Stream>, callbackContext, callType params."""
        from src.callbacks.outbound import initiate_callback

        mock_call = MagicMock()
        mock_call.sid = "CA_test_123"

        mock_client = MagicMock()
        mock_client.calls.create = MagicMock(return_value=mock_call)

        context = {
            "task_type": "deploy",
            "summary": "Deployed to staging",
            "success": True,
            "status": "completed",
        }

        with patch("src.callbacks.outbound._get_twilio_client", return_value=mock_client):
            with patch("src.callbacks.outbound.settings") as mock_settings:
                mock_settings.TWILIO_PHONE_NUMBER = "+10000000000"
                mock_settings.APP_ENV = "development"
                mock_settings.HOST = "localhost"
                mock_settings.PORT = 8765

                sid = await initiate_callback("+1234567890", context, "task_complete")

        assert sid == "CA_test_123"

        # Check TwiML content
        call_kwargs = mock_client.calls.create.call_args[1]
        twiml = call_kwargs["twiml"]

        assert "<Response>" in twiml
        assert "<Stream" in twiml
        assert "callbackContext" in twiml
        assert "outbound_task_complete" in twiml
        assert "callerPhone" in twiml

        # Context should be JSON-escaped in TwiML
        context_json = json.dumps(context)
        escaped = html.escape(context_json)
        assert escaped in twiml

    @pytest.mark.asyncio
    async def test_greeting_task_complete_success(self):
        """Greeting matches task_type + success case."""
        from src.callbacks.outbound import initiate_callback

        mock_call = MagicMock()
        mock_call.sid = "CA_test"
        mock_client = MagicMock()
        mock_client.calls.create = MagicMock(return_value=mock_call)

        context = {"task_type": "deploy", "success": True}

        with patch("src.callbacks.outbound._get_twilio_client", return_value=mock_client):
            with patch("src.callbacks.outbound.settings") as mock_settings:
                mock_settings.TWILIO_PHONE_NUMBER = "+10000000000"
                mock_settings.APP_ENV = "development"
                mock_settings.HOST = "localhost"
                mock_settings.PORT = 8765
                await initiate_callback("+1234567890", context, "task_complete")

        twiml = mock_client.calls.create.call_args[1]["twiml"]
        assert "deploy" in twiml
        assert "finished successfully" in twiml

    @pytest.mark.asyncio
    async def test_greeting_task_complete_failure(self):
        """Greeting matches failure case."""
        from src.callbacks.outbound import initiate_callback

        mock_call = MagicMock()
        mock_call.sid = "CA_test"
        mock_client = MagicMock()
        mock_client.calls.create = MagicMock(return_value=mock_call)

        context = {"task_type": "fix_bug", "success": False}

        with patch("src.callbacks.outbound._get_twilio_client", return_value=mock_client):
            with patch("src.callbacks.outbound.settings") as mock_settings:
                mock_settings.TWILIO_PHONE_NUMBER = "+10000000000"
                mock_settings.APP_ENV = "development"
                mock_settings.HOST = "localhost"
                mock_settings.PORT = 8765
                await initiate_callback("+1234567890", context, "task_complete")

        twiml = mock_client.calls.create.call_args[1]["twiml"]
        assert "fix_bug" in twiml
        assert "ran into an issue" in twiml

    @pytest.mark.asyncio
    async def test_greeting_reminder_type(self):
        """Reminder callback type uses reminder greeting."""
        from src.callbacks.outbound import initiate_callback

        mock_call = MagicMock()
        mock_call.sid = "CA_test"
        mock_client = MagicMock()
        mock_client.calls.create = MagicMock(return_value=mock_call)

        with patch("src.callbacks.outbound._get_twilio_client", return_value=mock_client):
            with patch("src.callbacks.outbound.settings") as mock_settings:
                mock_settings.TWILIO_PHONE_NUMBER = "+10000000000"
                mock_settings.APP_ENV = "development"
                mock_settings.HOST = "localhost"
                mock_settings.PORT = 8765
                await initiate_callback("+1234567890", {"reminder": "Check deploy"}, "reminder")

        twiml = mock_client.calls.create.call_args[1]["twiml"]
        assert "quick reminder" in twiml

    @pytest.mark.asyncio
    async def test_twilio_error_raises(self):
        """Exception from Twilio propagated for worker to catch."""
        from src.callbacks.outbound import initiate_callback

        mock_client = MagicMock()
        mock_client.calls.create = MagicMock(side_effect=Exception("Twilio API error"))

        with patch("src.callbacks.outbound._get_twilio_client", return_value=mock_client):
            with patch("src.callbacks.outbound.settings") as mock_settings:
                mock_settings.TWILIO_PHONE_NUMBER = "+10000000000"
                mock_settings.APP_ENV = "development"
                mock_settings.HOST = "localhost"
                mock_settings.PORT = 8765

                with pytest.raises(Exception, match="Twilio API error"):
                    await initiate_callback("+1234567890", {}, "task_complete")


class TestSendSms:
    """Test SMS sending."""

    @pytest.mark.asyncio
    async def test_truncates_long_messages(self):
        """Messages over 1600 chars are truncated."""
        from src.callbacks.outbound import send_sms

        mock_msg = MagicMock()
        mock_msg.sid = "SM_test"
        mock_client = MagicMock()
        mock_client.messages.create = MagicMock(return_value=mock_msg)

        long_message = "x" * 2000

        with patch("src.callbacks.outbound._get_twilio_client", return_value=mock_client):
            with patch("src.callbacks.outbound.settings") as mock_settings:
                mock_settings.TWILIO_PHONE_NUMBER = "+10000000000"
                await send_sms("+1234567890", long_message)

        sent_body = mock_client.messages.create.call_args[1]["body"]
        assert len(sent_body) <= 1600
        assert sent_body.endswith("...")
