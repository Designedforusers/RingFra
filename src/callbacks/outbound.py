"""
Twilio outbound call and SMS functionality.

Enables proactive communication:
- Callback calls when tasks complete
- SMS for non-urgent notifications
- Voice alerts for critical issues
"""

import html
import json
from typing import Any

from loguru import logger
from twilio.rest import Client

from src.config import settings


def _get_twilio_client() -> Client:
    """Get Twilio REST client."""
    return Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


def _get_websocket_url() -> str:
    """Get the WebSocket URL for media streams."""
    # In production, use the Render URL
    if settings.APP_ENV == "production":
        return "wss://render-voice-agent-eyyr.onrender.com/twilio/media-stream"
    return f"ws://{settings.HOST}:{settings.PORT}/twilio/media-stream"


async def initiate_callback(
    phone: str,
    context: dict[str, Any],
    callback_type: str = "task_complete",
) -> str:
    """
    Start an outbound call with context for the agent.

    The agent will greet the user and deliver the update,
    then continue the conversation if the user has questions.

    Args:
        phone: Phone number to call (E.164 format)
        context: Context dict to pass to the agent
        callback_type: Type of callback (task_complete, alert, reminder)

    Returns:
        Call SID
    """
    client = _get_twilio_client()
    ws_url = _get_websocket_url()

    # Serialize context for TwiML parameter
    context_json = json.dumps(context)
    escaped_context = html.escape(context_json)

    # Build immediate greeting based on callback type
    if callback_type == "reminder":
        immediate_greeting = "Hey, quick reminder for you."
    elif callback_type == "alert":
        immediate_greeting = "Hey, I need to tell you something."
    else:
        # task_complete - provide context about what finished
        task_type = context.get("task_type", "task")
        success = context.get("success", True)
        if success:
            immediate_greeting = f"Hey, that {task_type} you asked me to run finished successfully."
        else:
            immediate_greeting = f"Hey, that {task_type} you asked me to run ran into an issue. Let me explain."

    # Build TwiML for outbound call with immediate audio
    twiml = f"""
    <Response>
        <Say voice="Polly.Matthew">{immediate_greeting}</Say>
        <Connect>
            <Stream url="{ws_url}">
                <Parameter name="callbackContext" value="{escaped_context}" />
                <Parameter name="callType" value="outbound_{callback_type}" />
                <Parameter name="callerPhone" value="{phone}" />
            </Stream>
        </Connect>
        <Pause length="3600" />
    </Response>
    """

    logger.info(f"Initiating callback to {phone}, type={callback_type}")
    logger.debug(f"Callback context: {context}")

    try:
        call = client.calls.create(
            to=phone,
            from_=settings.TWILIO_PHONE_NUMBER,
            twiml=twiml.strip(),
        )
        logger.info(f"Outbound call initiated: {call.sid}")
        return call.sid
    except Exception as e:
        logger.error(f"Failed to initiate callback: {e}")
        raise


async def send_sms(phone: str, message: str) -> str:
    """
    Send an SMS notification.

    Use for non-urgent updates that don't require a voice call.

    Args:
        phone: Phone number (E.164 format)
        message: Message content (max 1600 chars)

    Returns:
        Message SID
    """
    client = _get_twilio_client()

    # Truncate if too long
    if len(message) > 1600:
        message = message[:1597] + "..."

    logger.info(f"Sending SMS to {phone}: {message[:50]}...")

    try:
        msg = client.messages.create(
            to=phone,
            from_=settings.TWILIO_PHONE_NUMBER,
            body=message,
        )
        logger.info(f"SMS sent: {msg.sid}")
        return msg.sid
    except Exception as e:
        logger.error(f"Failed to send SMS: {e}")
        raise


async def send_callback_sms(phone: str, context: dict[str, Any]) -> str:
    """
    Send an SMS summary of a completed task.

    Used as fallback if voice call fails or for quick updates.

    Args:
        phone: Phone number
        context: Task completion context

    Returns:
        Message SID
    """
    task_type = context.get("task_type", "task")
    status = context.get("status", "completed")
    summary = context.get("summary", "Task finished.")

    message = f"[Render Agent] {task_type} {status}: {summary}"

    return await send_sms(phone, message)
