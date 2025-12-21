"""
Notification system for deploy status updates.

- Tracks pending deploys with caller phone numbers
- Receives Render webhook when deploy completes
- Sends SMS via Twilio to notify caller
"""

import asyncio
from datetime import datetime, timedelta
from typing import Optional

from loguru import logger
from twilio.rest import Client as TwilioClient

from src.config import settings


# In-memory store for pending deploys (use Redis in production)
# Format: {deploy_id: {"phone": "+1234567890", "service": "api", "timestamp": datetime}}
_pending_deploys: dict[str, dict] = {}


def get_twilio_client() -> TwilioClient:
    """Get Twilio client for sending SMS."""
    return TwilioClient(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


def track_deploy(deploy_id: str, service_name: str, caller_phone: str) -> None:
    """
    Track a deploy for notification when complete.

    Args:
        deploy_id: The Render deploy ID
        service_name: Name of the service being deployed
        caller_phone: Phone number to notify
    """
    _pending_deploys[deploy_id] = {
        "phone": caller_phone,
        "service": service_name,
        "timestamp": datetime.utcnow(),
    }
    logger.info(f"Tracking deploy {deploy_id} for {service_name}, will notify {caller_phone}")

    # Clean up old entries (older than 1 hour)
    cleanup_old_deploys()


def cleanup_old_deploys() -> None:
    """Remove deploy entries older than 1 hour."""
    cutoff = datetime.utcnow() - timedelta(hours=1)
    old_keys = [k for k, v in _pending_deploys.items() if v["timestamp"] < cutoff]
    for key in old_keys:
        del _pending_deploys[key]
        logger.debug(f"Cleaned up old deploy tracking: {key}")


def get_pending_deploy(deploy_id: str) -> Optional[dict]:
    """Get pending deploy info if exists."""
    return _pending_deploys.get(deploy_id)


def remove_pending_deploy(deploy_id: str) -> Optional[dict]:
    """Remove and return pending deploy info."""
    return _pending_deploys.pop(deploy_id, None)


async def send_deploy_notification(
    phone_number: str,
    service_name: str,
    status: str,
    deploy_url: Optional[str] = None,
) -> bool:
    """
    Send SMS notification about deploy status.

    Args:
        phone_number: Phone to send SMS to
        service_name: Name of the deployed service
        status: Deploy status (live, failed, etc.)
        deploy_url: Optional URL to the deploy

    Returns:
        bool: True if SMS sent successfully
    """
    try:
        client = get_twilio_client()

        if status == "live":
            message_body = f"✅ {service_name} is now live! Your changes have been deployed successfully."
        elif status == "build_failed":
            message_body = f"❌ {service_name} deploy failed during build. Check the logs for details."
        elif status == "update_failed":
            message_body = f"❌ {service_name} deploy failed during update. The previous version is still running."
        else:
            message_body = f"ℹ️ {service_name} deploy status: {status}"

        if deploy_url:
            message_body += f"\n\nView: {deploy_url}"

        # Send SMS
        message = client.messages.create(
            body=message_body,
            from_=settings.TWILIO_PHONE_NUMBER,
            to=phone_number,
        )

        logger.info(f"Sent deploy notification to {phone_number}: {message.sid}")
        return True

    except Exception as e:
        logger.error(f"Failed to send deploy notification: {e}")
        return False


async def handle_render_webhook(payload: dict) -> dict:
    """
    Handle incoming Render deploy webhook.

    Render webhook payload includes:
    - type: "deploy"
    - data.id: deploy ID
    - data.status: "created", "build_in_progress", "update_in_progress", "live", "build_failed", etc.
    - data.service.name: service name

    Args:
        payload: Webhook payload from Render

    Returns:
        dict: Response status
    """
    try:
        event_type = payload.get("type")
        data = payload.get("data", {})

        if event_type != "deploy":
            logger.debug(f"Ignoring non-deploy webhook: {event_type}")
            return {"status": "ignored", "reason": "not a deploy event"}

        deploy_id = data.get("id")
        status = data.get("status")
        service_name = data.get("service", {}).get("name", "Unknown service")

        logger.info(f"Received deploy webhook: {deploy_id} - {status}")

        # Check if this is a terminal status
        terminal_statuses = ["live", "build_failed", "update_failed", "canceled"]

        if status not in terminal_statuses:
            logger.debug(f"Deploy {deploy_id} still in progress: {status}")
            return {"status": "acknowledged", "deploy_status": status}

        # Check if we're tracking this deploy
        pending = remove_pending_deploy(deploy_id)

        if not pending:
            # Might be a deploy we didn't trigger, or tracking expired
            logger.debug(f"No pending notification for deploy {deploy_id}")
            return {"status": "no_notification_needed"}

        # Send notification
        deploy_url = f"https://dashboard.render.com/web/{data.get('serviceId')}/deploys/{deploy_id}"

        await send_deploy_notification(
            phone_number=pending["phone"],
            service_name=service_name,
            status=status,
            deploy_url=deploy_url,
        )

        return {"status": "notified", "phone": pending["phone"], "deploy_status": status}

    except Exception as e:
        logger.error(f"Error handling Render webhook: {e}")
        return {"status": "error", "message": str(e)}
