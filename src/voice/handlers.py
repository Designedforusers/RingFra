"""
Twilio webhook and WebSocket handlers.

Manages the connection between Twilio phone calls
and the Pipecat voice pipeline.
"""

from fastapi import Request, WebSocket
from fastapi.responses import Response
from loguru import logger
from twilio.twiml.voice_response import Connect, Stream, VoiceResponse

from src.voice.pipeline import create_voice_pipeline


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

    Creates and runs the Pipecat voice pipeline for the duration
    of the call. Pipecat's FastAPIWebsocketTransport handles
    the WebSocket lifecycle internally.
    """
    logger.info("Creating Pipecat voice pipeline for Twilio stream")

    try:
        # Create and run the voice pipeline
        # Pipecat handles WebSocket acceptance and communication
        pipeline_task = await create_voice_pipeline(websocket)

        # Run the pipeline until the call ends
        await pipeline_task

        logger.info("Call ended normally")

    except Exception as e:
        logger.error(f"Error in media stream: {e}")
        raise
    finally:
        logger.info("Pipeline terminated")
