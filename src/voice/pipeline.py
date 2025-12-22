"""
Pipecat voice pipeline configuration.

Orchestrates:
- Speech-to-text (Deepgram)
- LLM processing (Claude)
- Text-to-speech (Cartesia)
- Voice activity detection (Silero)
- Tool execution

Uses Pipecat's built-in Twilio transport for proper audio handling.
"""

import asyncio

from fastapi import WebSocket
from loguru import logger

from pipecat.frames.frames import LLMMessagesFrame, EndFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketTransport,
    FastAPIWebsocketParams,
)
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.serializers.twilio import TwilioFrameSerializer

from src.config import settings
from src.tools import execute_tool
from src.voice.prompts import SYSTEM_PROMPT, get_tools_config


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
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
            vad_audio_passthrough=True,
        ),
        serializer=TwilioFrameSerializer(
            stream_sid="",  # Will be set by Twilio
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

    # === Tool Handler ===
    async def handle_tool_call(
        function_name: str,
        tool_call_id: str,
        arguments: dict,
        llm: AnthropicLLMService,
        context: OpenAILLMContext,
        result_callback,
    ):
        """Handle tool calls from the LLM."""
        logger.info(f"Tool call: {function_name} with args: {arguments}")

        try:
            # Inject caller phone for deploy-related tools
            if function_name == "trigger_deploy" and stream_sid:
                caller_phone = get_session_phone(stream_sid)
                if caller_phone:
                    arguments["caller_phone"] = caller_phone

            result = await execute_tool(function_name, arguments)
            await result_callback(result)
        except Exception as e:
            logger.error(f"Tool execution error: {e}")
            await result_callback(f"Error executing {function_name}: {str(e)}")

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

    # === Task Runner ===
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,
            enable_metrics=True,
        ),
    )

    # === Event Handlers ===
    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Twilio client connected")

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Twilio client disconnected")
        if stream_sid:
            clear_session_phone(stream_sid)

    @transport.event_handler("on_call_state_updated")
    async def on_call_state_updated(transport, state):
        nonlocal stream_sid
        if hasattr(state, "stream_sid"):
            stream_sid = state.stream_sid
            logger.info(f"Stream SID: {stream_sid}")

        # Extract caller phone from custom parameters if available
        if hasattr(state, "custom_parameters"):
            caller_phone = state.custom_parameters.get("callerPhone")
            if caller_phone and stream_sid:
                set_session_phone(stream_sid, caller_phone)
                logger.info(f"Caller phone captured: {caller_phone}")

    # === Welcome Message ===
    # Queue initial greeting after pipeline starts
    async def send_welcome():
        await asyncio.sleep(0.5)  # Brief delay for connection
        initial_message = LLMMessagesFrame(
            [
                {
                    "role": "user",
                    "content": "Greet the user briefly and ask how you can help with their Render infrastructure today.",
                }
            ]
        )
        await task.queue_frame(initial_message)

    # === Run Pipeline ===
    runner = PipelineRunner()

    async def run_pipeline():
        # Start welcome message task
        welcome_task = asyncio.create_task(send_welcome())

        try:
            await runner.run(task)
        finally:
            welcome_task.cancel()
            if stream_sid:
                clear_session_phone(stream_sid)

    pipeline_task = asyncio.create_task(run_pipeline())

    return pipeline_task
