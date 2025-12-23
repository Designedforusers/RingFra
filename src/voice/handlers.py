"""
Twilio webhook and WebSocket handlers.

Manages the connection between Twilio phone calls
and the Pipecat voice pipeline.

Follows the official pipecat twilio-chatbot example pattern.
"""

import time
import traceback

from fastapi import Request, WebSocket
from fastapi.responses import Response
from loguru import logger
from twilio.twiml.voice_response import Connect, Stream, VoiceResponse

from pipecat.runner.utils import parse_telephony_websocket

from src.config import settings

# Import both pipeline implementations
from src.voice.pipeline import run_pipeline as run_pipecat_pipeline, set_session_phone

# SDK pipeline is optional - only import if SDK is available
try:
    from src.voice.sdk_pipeline import run_sdk_pipeline
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
    run_sdk_pipeline = None

# Import Sentry for error capture if available
try:
    import sentry_sdk
    HAS_SENTRY = bool(settings.SENTRY_DSN)
except ImportError:
    HAS_SENTRY = False


async def _load_user_context(phone: str) -> dict | None:
    """
    Load user context from database for multi-tenant calls.
    
    Returns dict with:
    - user_id: UUID
    - user: User record
    - credentials: Dict of provider -> credentials
    - repos: List of connected repos
    - memory: Session memory (summary, preferences)
    """
    from src.db.users import get_user_by_phone, get_user_credentials, get_user_repos
    from src.db.memory import get_session_memory
    
    user = await get_user_by_phone(phone)
    if not user:
        return None
    
    user_id = user['id']
    
    # Load credentials
    credentials = {}
    for provider in ['render', 'github']:
        creds = await get_user_credentials(user_id, provider)
        if creds:
            credentials[provider] = creds
    
    # Load repos
    repos = await get_user_repos(user_id)
    
    # Load session memory
    memory = await get_session_memory(user_id)
    
    return {
        "user_id": user_id,
        "user": user,
        "credentials": credentials,
        "repos": repos,
        "memory": memory,
    }


async def handle_incoming_call(request: Request) -> Response:
    """
    Handle incoming Twilio call webhook.

    Returns TwiML that:
    1. Checks if user exists
    2. If new user: sends SMS signup link and plays message
    3. If existing user: connects to voice pipeline
    """
    # Get form data from Twilio
    form_data = await request.form()
    caller_phone = form_data.get("From", "")
    call_sid = form_data.get("CallSid", "")

    # Get the host from the request for WebSocket URL
    host = request.headers.get("host", "localhost:8765")
    protocol = "wss" if "https" in str(request.url) or host.endswith(".onrender.com") else "ws"
    ws_url = f"{protocol}://{host}/twilio/media-stream"

    logger.info(f"Incoming call from {caller_phone}, connecting to WebSocket: {ws_url}")

    response = VoiceResponse()

    # Check if user exists (for multi-tenant mode)
    user_exists = True
    if settings.DATABASE_URL and caller_phone:
        try:
            from src.db.users import get_user_by_phone
            user = await get_user_by_phone(caller_phone)
            user_exists = user is not None
        except Exception as e:
            logger.error(f"Failed to check user: {e}")
            user_exists = True  # Fail open
    
    if not user_exists:
        # New user - send SMS with signup link and play message
        logger.info(f"Unknown caller {caller_phone} - sending signup SMS")
        await _send_signup_sms(caller_phone)
        
        response.say(
            "Hi! I don't recognize this number yet. "
            "I just sent you a text message with a link to set up your account. "
            "Once you connect your GitHub and Render, you can call back and I'll be ready to help. "
            "Goodbye!",
            voice="Polly.Matthew",
        )
        response.hangup()
        return Response(content=str(response), media_type="application/xml")

    # Existing user - connect to voice pipeline
    response.say(
        "Connecting you to the Render infrastructure assistant.",
        voice="Polly.Matthew",
    )

    # Connect to WebSocket for bidirectional streaming
    connect = Connect()
    stream = Stream(url=ws_url)
    stream.parameter(name="track", value="both_tracks")
    # Pass caller phone to the stream so we can use it for notifications
    stream.parameter(name="callerPhone", value=caller_phone)
    stream.parameter(name="callSid", value=call_sid)
    connect.append(stream)
    response.append(connect)

    # Keep connection alive
    response.pause(length=3600)  # 1 hour max

    return Response(content=str(response), media_type="application/xml")


async def _send_signup_sms(phone: str) -> None:
    """Send SMS with signup link to new caller."""
    from twilio.rest import Client
    from urllib.parse import quote
    
    signup_url = f"{settings.APP_BASE_URL}/signup?phone={quote(phone)}"
    message = f"Welcome to Voice Agent! Set up your account here: {signup_url}"
    
    try:
        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        client.messages.create(
            to=phone,
            from_=settings.TWILIO_PHONE_NUMBER,
            body=message,
        )
        logger.info(f"Sent signup SMS to {phone}")
    except Exception as e:
        logger.error(f"Failed to send signup SMS: {e}")


async def handle_media_stream(websocket: WebSocket):
    """
    Handle WebSocket connection for Twilio media streams.

    Uses parse_telephony_websocket() to extract call data,
    then runs the Pipecat voice pipeline directly.
    
    Follows the official pipecat twilio-chatbot example pattern.
    """
    call_start = time.time()
    logger.info("=" * 60)
    logger.info("MEDIA STREAM: Starting new Twilio WebSocket connection")
    logger.info("=" * 60)

    # Accept the WebSocket connection first
    await websocket.accept()
    logger.info("MEDIA STREAM: WebSocket connection accepted")

    try:
        # Parse Twilio WebSocket messages to get call data
        # This handles the Connected/Start messages from Twilio
        logger.info("MEDIA STREAM: Parsing telephony websocket...")
        _, call_data = await parse_telephony_websocket(websocket)
        
        stream_sid = call_data.get("stream_id", "")
        call_sid = call_data.get("call_id", "")
        custom_params = call_data.get("body", {})
        
        logger.info(f"MEDIA STREAM: stream_sid={stream_sid}, call_sid={call_sid}")
        logger.info(f"MEDIA STREAM: custom_params={custom_params}")
        
        # Store caller phone for notifications if passed via custom params
        caller_phone = custom_params.get("callerPhone", "")
        if caller_phone and stream_sid:
            set_session_phone(stream_sid, caller_phone)
            logger.info(f"MEDIA STREAM: Stored caller phone {caller_phone} for session")

        # Look up user by phone number (multi-tenant)
        user_context = None
        if caller_phone and settings.DATABASE_URL:
            try:
                user_context = await _load_user_context(caller_phone)
                if user_context:
                    logger.info(f"MEDIA STREAM: Loaded context for user {user_context.get('user_id')}")
                else:
                    logger.info(f"MEDIA STREAM: No existing user for {caller_phone}")
            except Exception as e:
                logger.error(f"MEDIA STREAM: Failed to load user context: {e}")

        # Check for callback context (outbound calls)
        call_type = custom_params.get("callType", "inbound")
        callback_context = None
        if custom_params.get("callbackContext"):
            import json
            try:
                callback_context = json.loads(custom_params.get("callbackContext", "{}"))
                logger.info(f"MEDIA STREAM: Callback context: {callback_context}")
            except json.JSONDecodeError:
                logger.warning("MEDIA STREAM: Failed to parse callback context")

        # Run the voice pipeline (blocking until call ends)
        # Use SDK pipeline for full Claude Code capabilities (if available)
        use_sdk = settings.USE_SDK_PIPELINE and SDK_AVAILABLE
        logger.info(f"MEDIA STREAM: Running {'SDK' if use_sdk else 'Pipecat'} voice pipeline (type={call_type})...")
        
        if use_sdk and run_sdk_pipeline:
            await run_sdk_pipeline(
                websocket,
                stream_sid,
                call_sid,
                call_type=call_type,
                callback_context=callback_context,
                user_context=user_context,
            )
        else:
            if settings.USE_SDK_PIPELINE and not SDK_AVAILABLE:
                logger.warning("SDK pipeline requested but claude-agent-sdk not installed, using Pipecat")
            await run_pipecat_pipeline(
                websocket,
                stream_sid,
                call_sid,
                call_type=call_type,
                callback_context=callback_context,
                user_context=user_context,
            )

        call_duration = time.time() - call_start
        logger.info(f"MEDIA STREAM: Call ended normally after {call_duration:.1f}s")

    except Exception as e:
        call_duration = time.time() - call_start
        logger.error(f"MEDIA STREAM ERROR after {call_duration:.1f}s: {type(e).__name__}: {e}")
        logger.error(f"MEDIA STREAM TRACEBACK:\n{traceback.format_exc()}")

        # Report to Sentry if available
        if HAS_SENTRY:
            sentry_sdk.capture_exception(e)

        raise
    finally:
        call_duration = time.time() - call_start
        logger.info(f"MEDIA STREAM: Pipeline terminated (total: {call_duration:.1f}s)")
        logger.info("=" * 60)
