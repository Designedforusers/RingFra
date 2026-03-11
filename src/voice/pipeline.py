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
from src.voice.prompts import SYSTEM_PROMPT, get_tools_config, get_callback_prompt

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


def _build_user_context_prompt(user_context: dict) -> str:
    """Build a prompt section with user-specific context."""
    parts = ["## Your User Context\n"]
    
    # User info
    user = user_context.get("user", {})
    parts.append(f"**Caller:** {user.get('phone', 'Unknown')}")
    if user.get('email'):
        parts.append(f" ({user.get('email')})")
    parts.append("\n\n")
    
    # Connected repos
    repos = user_context.get("repos", [])
    if repos:
        parts.append("**Connected Repositories:**\n")
        for repo in repos:
            parts.append(f"- {repo.get('github_url')} (branch: {repo.get('default_branch', 'main')})\n")
        parts.append("\n")
    
    # Available credentials
    credentials = user_context.get("credentials", {})
    if credentials:
        parts.append("**Connected Services:**\n")
        for provider in credentials:
            parts.append(f"- {provider.title()}: Connected ✓\n")
        parts.append("\n")
    
    # Session memory
    memory = user_context.get("memory")
    if memory:
        if memory.get("summary"):
            parts.append("**Previous Conversation Context:**\n")
            parts.append(memory.get("summary"))
            parts.append("\n\n")
        
        if memory.get("preferences"):
            parts.append("**User Preferences:**\n")
            import json
            parts.append(json.dumps(memory.get("preferences"), indent=2))
            parts.append("\n")
    
    return "".join(parts)


async def run_pipeline(
    websocket: WebSocket,
    stream_sid: str,
    call_sid: str,
    call_type: str = "inbound",
    callback_context: dict | None = None,
    user_context: dict | None = None,
) -> None:
    """
    Run the Pipecat voice pipeline.

    This follows the official pipecat twilio-chatbot example pattern exactly.

    Args:
        websocket: The WebSocket connection from Twilio
        stream_sid: Twilio stream SID
        call_sid: Twilio call SID
        call_type: "inbound" or "outbound_*" for callback calls
        callback_context: Context for outbound callback calls
        user_context: Multi-tenant user context (credentials, repos, memory)
    """
    is_callback = call_type.startswith("outbound_")
    has_user = user_context is not None
    logger.info(f"Starting pipeline for stream_sid={stream_sid}, call_sid={call_sid}, type={call_type}, has_user={has_user}")
    
    # Set user context for code tools (multi-tenant)
    if user_context:
        from src.tools.code_tools import set_current_user_context
        set_current_user_context(user_context)

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

    # === Language Model (Claude Sonnet 4.6 - latest model) ===
    # Explicitly use claude-sonnet-4-6 for best performance
    model_name = settings.VOICE_MODEL
    logger.info(f"Using LLM model: {model_name}")
    llm = AnthropicLLMService(
        api_key=settings.ANTHROPIC_API_KEY,
        model=model_name,
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

    # Use callback prompt for outbound calls, otherwise standard prompt
    if is_callback and callback_context:
        system_prompt = get_callback_prompt(callback_context)
        logger.info(f"Using callback prompt with context: {callback_context.get('event_type', 'unknown')}")
    else:
        system_prompt = SYSTEM_PROMPT

    # Append user context if available (multi-tenant)
    if user_context:
        user_context_prompt = _build_user_context_prompt(user_context)
        system_prompt = system_prompt + "\n\n" + user_context_prompt
        logger.info("Appended user context to system prompt")

    # Start with system prompt only - greeting added in on_client_connected
    messages = [
        {"role": "system", "content": system_prompt},
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
            # Inject caller phone for tools that need it
            caller_phone = get_session_phone(stream_sid) if stream_sid else None

            # Tools that need caller phone for callbacks/notifications
            phone_tools = ["trigger_deploy", "schedule_callback", "set_reminder", "enable_monitoring"]
            if function_name in phone_tools and caller_phone:
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
        if is_callback:
            # For callbacks, prompt to deliver the update
            messages.append({
                "role": "system",
                "content": "Deliver the update from the context. Start with 'Hi, I'm calling back about...' and be concise."
            })
        else:
            # For inbound calls, greet normally
            messages.append({
                "role": "system",
                "content": "Greet the user briefly and ask how you can help with their Render infrastructure today."
            })
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        
        # Save session memory if we have user context
        if user_context and settings.DATABASE_URL:
            try:
                await _save_session_memory(user_context["user_id"], messages)
            except Exception as e:
                logger.error(f"Failed to save session memory: {e}")
        
        if stream_sid:
            clear_session_phone(stream_sid)
        await task.cancel()

    # === Run Pipeline ===
    runner = PipelineRunner(handle_sigint=False, force_gc=True)
    await runner.run(task)


async def _save_session_memory(user_id, messages: list) -> None:
    """
    Summarize the conversation and save to session memory.
    
    Uses Claude to generate a concise summary of what was discussed/done.
    """
    from anthropic import AsyncAnthropic
    from src.db.memory import update_session_memory
    
    # Filter to just user/assistant messages (skip system)
    conversation = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role in ["user", "assistant"] and content:
            conversation.append(f"{role.upper()}: {content}")
    
    if not conversation:
        logger.debug("No conversation to summarize")
        return
    
    transcript = "\n".join(conversation[-20:])  # Last 20 messages max
    
    # Generate summary with Claude
    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    
    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": f"""Summarize this voice agent conversation for future context. Be concise (2-4 sentences).

Include:
- What was worked on or discussed
- Any actions taken (deploys, fixes, etc.)
- Anything left incomplete
- User preferences learned

Conversation:
{transcript}

Summary:"""
            }]
        )
        
        summary = response.content[0].text.strip()
        
        # Save to database
        await update_session_memory(user_id, summary=summary)
        logger.info(f"Saved session memory for user {user_id}: {summary[:100]}...")
        
    except Exception as e:
        logger.error(f"Failed to generate session summary: {e}")
