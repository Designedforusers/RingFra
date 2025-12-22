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
from src.voice.pipeline import run_pipeline, set_session_phone

# Import Sentry for error capture if available
try:
    import sentry_sdk
    HAS_SENTRY = bool(settings.SENTRY_DSN)
except ImportError:
    HAS_SENTRY = False


async def handle_incoming_call(request: Request) -> Response:
    """
    Handle incoming Twilio call webhook.

    Returns TwiML that:
    1. Says a brief greeting
    2. Connects to WebSocket for streaming with caller info
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

    # Brief greeting while connecting
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

        # Run the voice pipeline (blocking until call ends)
        logger.info("MEDIA STREAM: Running Pipecat voice pipeline...")
        await run_pipeline(websocket, stream_sid, call_sid)

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
