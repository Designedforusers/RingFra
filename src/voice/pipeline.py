"""
Pipecat voice pipeline configuration.

Orchestrates:
- Speech-to-text (Deepgram)
- LLM processing (Claude)
- Text-to-speech (Cartesia)
- Voice activity detection (Silero)
- Tool execution

Uses Pipecat's built-in Twilio transport for proper audio handling.
Matches the official pipecat-examples/twilio-chatbot pattern exactly.
"""

import os

from fastapi import WebSocket
from loguru import logger

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

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


async def run_pipeline(
    websocket: WebSocket,
    stream_sid: str,
    call_sid: str,
) -> None:
    """
    Run the Pipecat voice pipeline.

    This follows the official pipecat twilio-chatbot example pattern exactly.

    Args:
        websocket: The WebSocket connection from Twilio
        stream_sid: Twilio stream SID
        call_sid: Twilio call SID
    """
    logger.info(f"Starting pipeline for stream_sid={stream_sid}, call_sid={call_sid}")

    # === Serializer with Twilio credentials for auto hang-up ===
    serializer = TwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        account_sid=settings.TWILIO_ACCOUNT_SID or "",
        auth_token=settings.TWILIO_AUTH_TOKEN or "",
    )

    # === Transport Layer (Twilio WebSocket) ===
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            vad_analyzer=SileroVADAnalyzer(),
            serializer=serializer,
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
    # Convert tool configs to FunctionSchema objects for ToolsSchema
    raw_tools = get_tools_config()
    function_schemas = []
    for tool in raw_tools:
        schema = FunctionSchema(
            name=tool["name"],
            description=tool.get("description", ""),
            properties=tool.get("input_schema", {}).get("properties", {}),
            required=tool.get("input_schema", {}).get("required", []),
        )
        function_schemas.append(schema)
    
    tools = ToolsSchema(standard_tools=function_schemas)

    # Start with system prompt only - greeting added in on_client_connected
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]
    context = LLMContext(messages, tools)
    context_aggregator = LLMContextAggregatorPair(context)

    # === Tool Handler (using FunctionCallParams API) ===
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
    for tool in raw_tools:
        tool_name = tool.get("name")
        if tool_name:
            llm.register_function(tool_name, handle_tool_call)

    # === Pipeline Assembly ===
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ]
    )

    # === Observers for Observability ===
    observers = [
        MetricsLogObserver(),
        TranscriptionLogObserver(),
        LLMLogObserver(),
    ]

    # === Task Runner ===
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=8000,  # Twilio uses 8kHz
            audio_out_sample_rate=8000,
            allow_interruptions=True,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        observers=observers,
    )

    # === Event Handlers (matching official example exactly) ===
    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected")
        # Add greeting prompt and kick off conversation
        messages.append({
            "role": "system",
            "content": "Greet the user briefly and ask how you can help with their Render infrastructure today."
        })
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        if stream_sid:
            clear_session_phone(stream_sid)
        await task.cancel()

    # === Run Pipeline ===
    runner = PipelineRunner(handle_sigint=False, force_gc=True)
    await runner.run(task)
