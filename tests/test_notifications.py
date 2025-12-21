"""
Tests for the notification system.
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from src.notifications import (
    track_deploy,
    get_pending_deploy,
    remove_pending_deploy,
    handle_render_webhook,
    send_deploy_notification,
    cleanup_old_deploys,
    _pending_deploys,
)


class TestDeployTracking:
    """Tests for deploy tracking."""

    def setup_method(self):
        """Clear pending deploys before each test."""
        _pending_deploys.clear()

    def test_track_deploy(self):
        """Test tracking a deploy."""
        track_deploy("dep-123", "api-service", "+14155551234")

        pending = get_pending_deploy("dep-123")
        assert pending is not None
        assert pending["phone"] == "+14155551234"
        assert pending["service"] == "api-service"
        assert "timestamp" in pending

    def test_get_pending_deploy_not_found(self):
        """Test getting a non-existent deploy."""
        pending = get_pending_deploy("nonexistent")
        assert pending is None

    def test_remove_pending_deploy(self):
        """Test removing a tracked deploy."""
        track_deploy("dep-456", "frontend", "+14155551234")

        removed = remove_pending_deploy("dep-456")
        assert removed is not None
        assert removed["service"] == "frontend"

        # Should be gone now
        assert get_pending_deploy("dep-456") is None


class TestRenderWebhook:
    """Tests for Render webhook handling."""

    def setup_method(self):
        """Clear pending deploys before each test."""
        _pending_deploys.clear()

    @pytest.mark.asyncio
    async def test_ignore_non_deploy_event(self):
        """Test that non-deploy events are ignored."""
        payload = {"type": "service", "data": {"id": "srv-123"}}

        result = await handle_render_webhook(payload)

        assert result["status"] == "ignored"
        assert "not a deploy" in result["reason"]

    @pytest.mark.asyncio
    async def test_acknowledge_in_progress_deploy(self):
        """Test that in-progress deploys are acknowledged but not notified."""
        payload = {
            "type": "deploy",
            "data": {
                "id": "dep-789",
                "status": "build_in_progress",
                "service": {"name": "api"},
            },
        }

        result = await handle_render_webhook(payload)

        assert result["status"] == "acknowledged"
        assert result["deploy_status"] == "build_in_progress"

    @pytest.mark.asyncio
    async def test_no_notification_for_untracked_deploy(self):
        """Test that untracked deploys don't trigger notifications."""
        payload = {
            "type": "deploy",
            "data": {
                "id": "dep-untracked",
                "status": "live",
                "service": {"name": "api"},
            },
        }

        result = await handle_render_webhook(payload)

        assert result["status"] == "no_notification_needed"

    @pytest.mark.asyncio
    async def test_send_notification_on_live(self):
        """Test that live deploys trigger SMS notification."""
        # Track the deploy first
        track_deploy("dep-live", "api-service", "+14155551234")

        payload = {
            "type": "deploy",
            "data": {
                "id": "dep-live",
                "status": "live",
                "serviceId": "srv-123",
                "service": {"name": "api-service"},
            },
        }

        with patch("src.notifications.send_deploy_notification") as mock_send:
            mock_send.return_value = True

            result = await handle_render_webhook(payload)

            assert result["status"] == "notified"
            assert result["phone"] == "+14155551234"
            assert result["deploy_status"] == "live"

            mock_send.assert_called_once()
            call_args = mock_send.call_args
            assert call_args.kwargs["phone_number"] == "+14155551234"
            assert call_args.kwargs["status"] == "live"

    @pytest.mark.asyncio
    async def test_send_notification_on_failure(self):
        """Test that failed deploys trigger SMS notification."""
        track_deploy("dep-fail", "frontend", "+14155559999")

        payload = {
            "type": "deploy",
            "data": {
                "id": "dep-fail",
                "status": "build_failed",
                "serviceId": "srv-456",
                "service": {"name": "frontend"},
            },
        }

        with patch("src.notifications.send_deploy_notification") as mock_send:
            mock_send.return_value = True

            result = await handle_render_webhook(payload)

            assert result["status"] == "notified"
            assert result["deploy_status"] == "build_failed"


class TestSMSNotification:
    """Tests for SMS notification sending."""

    @pytest.mark.asyncio
    async def test_send_success_notification(self):
        """Test sending a success notification."""
        mock_client = MagicMock()
        mock_message = MagicMock()
        mock_message.sid = "SM123"
        mock_client.messages.create.return_value = mock_message

        with patch("src.notifications.get_twilio_client", return_value=mock_client):
            result = await send_deploy_notification(
                phone_number="+14155551234",
                service_name="api",
                status="live",
                deploy_url="https://example.com",
            )

            assert result is True
            mock_client.messages.create.assert_called_once()
            call_kwargs = mock_client.messages.create.call_args.kwargs
            assert "is now live" in call_kwargs["body"]
            assert "+14155551234" == call_kwargs["to"]

    @pytest.mark.asyncio
    async def test_send_failure_notification(self):
        """Test sending a failure notification."""
        mock_client = MagicMock()
        mock_message = MagicMock()
        mock_message.sid = "SM456"
        mock_client.messages.create.return_value = mock_message

        with patch("src.notifications.get_twilio_client", return_value=mock_client):
            result = await send_deploy_notification(
                phone_number="+14155551234",
                service_name="api",
                status="build_failed",
            )

            assert result is True
            call_kwargs = mock_client.messages.create.call_args.kwargs
            assert "failed" in call_kwargs["body"]

    @pytest.mark.asyncio
    async def test_send_notification_error_handling(self):
        """Test error handling when SMS fails."""
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("Twilio error")

        with patch("src.notifications.get_twilio_client", return_value=mock_client):
            result = await send_deploy_notification(
                phone_number="+14155551234",
                service_name="api",
                status="live",
            )

            assert result is False
