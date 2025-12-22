"""
Pipecat voice pipeline configuration.

Orchestrates:
- Speech-to-text (Deepgram)
- LLM processing (Claude)
- Text-to-speech (Cartesia)
- Voice activity detection (Silero)
- Tool execution

Uses Pipecat's built-in Twilio transport for proper audio handling.
Includes comprehensive observability via Pipecat observers.
"""

import asyncio
import os

from fastapi import WebSocket
from loguru import logger

from pipecat.frames.frames import EndFrame, LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketTransport,
    FastAPIWebsocketParams,
)
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.serializers.twilio import TwilioFrameSerializer

# Pipecat Observers for comprehensive logging
from pipecat.observers.loggers.metrics_log_observer import MetricsLogObserver
from pipecat.observers.loggers.transcription_log_observer import TranscriptionLogObserver
from pipecat.observers.loggers.llm_log_observer import LLMLogObserver

from src.config import settings
from src.tools import execute_tool
from src.voice.prompts import SYSTEM_PROMPT, get_tools_config

# Initialize Sentry if configured
if settings.SENTRY_DSN:
    import sentry_sdk
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
        environment=settings.APP_ENV,
    )
    logger.info("Sentry initialized for error tracking")


# Store caller phone per session for notifications
_session_phones: dict[str, str] = {}


def set_session_phone(stream_sid: str, phone: str) -> None:
    """Store caller phone for a session."""
    _session_phones[stream_sid] = phone


def get_session_phone(stream_sid: str) -> str | None:
    """Get caller phone for a session."""
    return _session_phones.get(stream_sid)


def clear_session_phone(stream_sid: str) -> None:
    """Clear caller phone for a session."""
    _session_phones.pop(stream_sid, None)


async def create_voice_pipeline(websocket: WebSocket) -> asyncio.Task:
    """
    Create and configure the Pipecat voice pipeline.

    Uses Pipecat's built-in FastAPIWebsocketTransport with TwilioFrameSerializer
    for proper Twilio media stream handling.

    Args:
        websocket: The WebSocket connection from Twilio

    Returns:
        asyncio.Task: The running pipeline task
    """
    # === Transport Layer (Twilio WebSocket) ===
    # In pipecat 0.0.98+, serializer goes inside FastAPIWebsocketParams
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            vad_analyzer=SileroVADAnalyzer(),
            serializer=TwilioFrameSerializer(
                stream_sid="",  # Will be set by Twilio
                params=TwilioFrameSerializer.InputParams(auto_hang_up=False),
            ),
        ),
    )

    # === Speech-to-Text (Deepgram) ===
    stt = DeepgramSTTService(
        api_key=settings.DEEPGRAM_API_KEY,
        model=settings.STT_MODEL,
    )

    # === Language Model (Claude) ===
    llm = AnthropicLLMService(
        api_key=settings.ANTHROPIC_API_KEY,
        model=settings.VOICE_MODEL,
    )

    # === Text-to-Speech (Cartesia) ===
    tts = CartesiaTTSService(
        api_key=settings.CARTESIA_API_KEY,
        voice_id=settings.TTS_VOICE,
    )

    # === Conversation Context ===
    tools_config = get_tools_config()

    context = OpenAILLMContext(
        messages=[{"role": "system", "content": SYSTEM_PROMPT}],
        tools=tools_config,
    )
    context_aggregator = llm.create_context_aggregator(context)

    # Track stream SID for caller phone lookup
    stream_sid: str | None = None

    # === Tool Handler (using new FunctionCallParams API) ===
    async def handle_tool_call(params: FunctionCallParams):
        """Handle tool calls from the LLM."""
        function_name = params.function_name
        arguments = params.arguments

        logger.info(f"Tool call: {function_name} with args: {arguments}")

        try:
            # Inject caller phone for deploy-related tools
            if function_name == "trigger_deploy" and stream_sid:
                caller_phone = get_session_phone(stream_sid)
                if caller_phone:
                    arguments["caller_phone"] = caller_phone

            result = await execute_tool(function_name, arguments)
            await params.result_callback(result)
        except Exception as e:
            logger.error(f"Tool execution error: {e}")
            await params.result_callback(f"Error executing {function_name}: {str(e)}")

    # Register tool handlers for each tool
    for tool in tools_config:
        tool_name = tool.get("name")
        if tool_name:
            llm.register_function(tool_name, handle_tool_call)

    # === Pipeline Assembly ===
    pipeline = Pipeline(
        [
            transport.input(),           # Audio from Twilio
            stt,                          # Speech to text
            context_aggregator.user(),    # Add user message to context
            llm,                          # Process with Claude
            tts,                          # Text to speech
            transport.output(),           # Audio to Twilio
            context_aggregator.assistant(),  # Add assistant message to context
        ]
    )

    # === Observers for Full Observability ===
    observers = [
        MetricsLogObserver(),  # TTFB, processing times, token usage
        TranscriptionLogObserver(),  # What user said (STT output)
        LLMLogObserver(),  # LLM requests and responses
    ]
    logger.info("Pipeline observers initialized: metrics, transcription, LLM")

    # === Task Runner ===
    # Note: observers passed directly to PipelineTask, not in PipelineParams
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,
            enable_metrics=True,
        ),
        observers=observers,
    )

    # === Event Handlers ===
    # Note: FastAPIWebsocketTransport only supports on_client_connected, 
    # on_client_disconnected, and on_session_timeout
    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("PIPELINE EVENT: Twilio client connected")

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("PIPELINE EVENT: Twilio client disconnected")
        if stream_sid:
            clear_session_phone(stream_sid)

    # === Welcome Message ===
    # Queue initial greeting after pipeline starts
    async def send_welcome():
        logger.info("WELCOME: Waiting 0.5s before sending greeting...")
        await asyncio.sleep(0.5)  # Brief delay for connection
        logger.info("WELCOME: Queueing initial greeting message to LLM")
        
        # Add the greeting prompt to context messages and trigger LLM with LLMRunFrame
        context.messages.append({
            "role": "user",
            "content": "Greet the user briefly and ask how you can help with their Render infrastructure today.",
        })
        await task.queue_frame(LLMRunFrame())
        logger.info("WELCOME: Initial greeting queued successfully")

    # === Run Pipeline ===
    runner = PipelineRunner()

    async def run_pipeline():
        logger.info("RUNNER: Starting pipeline runner...")
        # Start welcome message task
        welcome_task = asyncio.create_task(send_welcome())

        try:
            logger.info("RUNNER: Calling runner.run(task)...")
            await runner.run(task)
            logger.info("RUNNER: runner.run(task) completed normally")
        except Exception as e:
            logger.error(f"RUNNER ERROR: {type(e).__name__}: {e}")
            raise
        finally:
            welcome_task.cancel()
            if stream_sid:
                clear_session_phone(stream_sid)
            logger.info("RUNNER: Pipeline runner cleanup complete")

    logger.info("PIPELINE: Creating asyncio task for run_pipeline")
    pipeline_task = asyncio.create_task(run_pipeline())

    return pipeline_task
