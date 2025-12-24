"""
Voice pipeline using Claude Agent SDK as the brain.

This replaces Pipecat's AnthropicLLMService with a persistent
ClaudeSDKClient session that maintains conversation context
and has access to all Claude Code tools + Render MCP.

Architecture:
    Twilio WebSocket
         ↓
    Deepgram Flux STT (speech → text with AI turn detection)
         ↓
    ClaudeSDKClient (persistent session)
    - Code tools (Read, Write, Edit, Bash, etc.)
    - Render MCP (logs, metrics, deploy, etc.)
    - Proactive tools (callbacks, SMS, reminders)
         ↓
    Cartesia TTS (text → speech)
         ↓
    Twilio WebSocket
"""

import asyncio
from pathlib import Path

from fastapi import WebSocket
from loguru import logger

from pipecat.frames.frames import (
    Frame,
    TextFrame,
    TranscriptionFrame,
    EndFrame,
    LLMFullResponseEndFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat.observers.loggers.transcription_log_observer import TranscriptionLogObserver

from src.config import settings
from src.agent.sdk_client import VoiceAgentSession


class SDKBridgeProcessor(FrameProcessor):
    """
    Bridges Pipecat frames to Claude Agent SDK.
    
    Receives TranscriptionFrames from STT, sends to SDK,
    and emits TextFrames from SDK responses for TTS.
    """
    
    # Phrases that signal the user wants to end the call
    GOODBYE_PHRASES = [
        "bye", "goodbye", "good bye", "see you", "see ya",
        "talk to you later", "take care", "hang up",
        "end the call", "end call", "disconnect",
        "thanks bye", "thank you bye", "thanks goodbye",
    ]
    
    def __init__(self, session: VoiceAgentSession, end_call_callback=None):
        super().__init__()
        self.session = session
        self._processing = False
        self._end_call_callback = end_call_callback
    
    def _is_goodbye(self, text: str) -> bool:
        """Check if user is saying goodbye."""
        text_lower = text.lower().strip()
        # Check exact matches and phrases
        for phrase in self.GOODBYE_PHRASES:
            if phrase in text_lower:
                return True
        return False
    
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process incoming frames."""
        await super().process_frame(frame, direction)
        
        if isinstance(frame, TranscriptionFrame):
            # User finished speaking - process with SDK
            if frame.text and not self._processing:
                self._processing = True
                try:
                    await self._process_user_input(frame.text)
                finally:
                    self._processing = False
        else:
            # Pass through other frames
            await self.push_frame(frame, direction)
    
    async def _process_user_input(self, text: str):
        """Send user input to SDK and stream response to TTS."""
        logger.info(f"User said: {text}")
        
        # Check for goodbye
        if self._is_goodbye(text):
            logger.info("User said goodbye - ending call")
            await self.push_frame(TextFrame(text="Goodbye! Talk to you later."))
            await self.push_frame(LLMFullResponseEndFrame())
            # Trigger end of call
            if self._end_call_callback:
                await self._end_call_callback()
            await self.push_frame(EndFrame())
            return
        
        try:
            async for response_text in self.session.query(text):
                if response_text:
                    # Send TextFrame to TTS - Pipecat will handle conversion
                    await self.push_frame(TextFrame(text=response_text))
                    logger.debug(f"SDK response chunk: {response_text[:50]}...")
            
            # Signal end of response
            await self.push_frame(LLMFullResponseEndFrame())
            
        except Exception as e:
            logger.error(f"SDK query error: {e}")
            await self.push_frame(TextFrame(text="I encountered an error. Please try again."))
            await self.push_frame(LLMFullResponseEndFrame())


async def run_sdk_pipeline(
    websocket: WebSocket,
    stream_sid: str,
    call_sid: str,
    call_type: str = "inbound",
    callback_context: dict | None = None,
    user_context: dict | None = None,
) -> None:
    """
    Run the voice pipeline with Claude Agent SDK as the brain.
    
    Args:
        websocket: Twilio WebSocket connection
        stream_sid: Twilio stream SID
        call_sid: Twilio call SID  
        call_type: "inbound" or "outbound_*"
        callback_context: Context for callback calls
        user_context: User's credentials, repos, memory
    """
    logger.info(f"Starting SDK pipeline: stream_sid={stream_sid}, call_type={call_type}")
    
    # Determine working directory from user's repo
    cwd = None
    if user_context and user_context.get("repos"):
        repos = user_context["repos"]
        if repos and repos[0].get("local_path"):
            cwd = Path(repos[0]["local_path"])
    
    # Get caller phone for proactive features
    caller_phone = None
    if user_context and user_context.get("user"):
        caller_phone = user_context["user"].get("phone")
    
    # === Claude Agent SDK Session (persistent for entire call) ===
    session = VoiceAgentSession(
        user_context=user_context,
        cwd=cwd,
        caller_phone=caller_phone,
    )
    
    # === Twilio Transport ===
    serializer = TwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        account_sid=settings.TWILIO_ACCOUNT_SID or "",
        auth_token=settings.TWILIO_AUTH_TOKEN or "",
    )
    
    # No VAD analyzer needed - Deepgram Flux handles turn detection with AI
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=serializer,
        ),
    )
    
    # === Speech-to-Text (Deepgram Flux - AI-powered turn detection) ===
    # Flux uses semantic understanding to detect end-of-turn, not just silence
    # This prevents mid-sentence splits from pauses
    stt = DeepgramFluxSTTService(
        api_key=settings.DEEPGRAM_API_KEY,
        model="flux-general-en",
        params=DeepgramFluxSTTService.InputParams(
            eot_threshold=0.75,       # Slightly higher = wait for more certainty user is done
            eot_timeout_ms=6000,      # 6 sec max silence before forcing end-of-turn
            keyterm=["solhedge", "render", "deploy", "github", "commit", "push", "merge"],
        ),
    )
    
    # === Text-to-Speech (Cartesia) ===
    tts = CartesiaTTSService(
        api_key=settings.CARTESIA_API_KEY,
        voice_id=settings.TTS_VOICE,
    )
    
    # === SDK Bridge (connects Pipecat to Claude Agent SDK) ===
    sdk_bridge = SDKBridgeProcessor(session=session)
    
    # === Pipeline ===
    # SDK bridge outputs TextFrames which TTS converts to audio
    pipeline = Pipeline([
        transport.input(),
        stt,
        sdk_bridge,
        tts,
        transport.output(),
    ])
    
    # === Observers ===
    observers = [
        TranscriptionLogObserver(),
    ]
    
    # === Task ===
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=8000,  # Twilio uses 8kHz
            audio_out_sample_rate=8000,
            allow_interruptions=True,
            enable_metrics=True,
        ),
        observers=observers,
    )
    
    # === Event Handlers ===
    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected - starting SDK session")
        await session.connect()
        
        # Send initial greeting
        if call_type.startswith("outbound_") and callback_context:
            greeting = f"Hi, I'm calling back about {callback_context.get('task_description', 'your task')}."
        else:
            greeting = "Hey, I'm your on-call engineer. What can I help you with?"
        
        await sdk_bridge.push_frame(TextFrame(text=greeting))
    
    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected - cleaning up SDK session")
        await session.disconnect()
        
        # Save session memory if we have user context
        if user_context and settings.DATABASE_URL:
            try:
                await _save_session_memory(session, user_context)
            except Exception as e:
                logger.error(f"Failed to save session memory: {e}")
        
        await task.cancel()
    
    # === Run ===
    runner = PipelineRunner(handle_sigint=False, force_gc=True)
    await runner.run(task)


async def _save_session_memory(session: VoiceAgentSession, user_context: dict) -> None:
    """Save conversation summary to session memory."""
    # The SDK maintains conversation history internally
    # We can ask it to summarize the conversation
    try:
        summary_response = []
        async for text in session.query(
            "Summarize our conversation in 2-3 sentences for future context. "
            "Include what was worked on, actions taken, and anything left incomplete."
        ):
            summary_response.append(text)
        
        summary = " ".join(summary_response)
        
        if summary:
            from src.db.memory import update_session_memory
            await update_session_memory(user_context["user_id"], summary=summary)
            logger.info(f"Saved session memory: {summary[:100]}...")
    except Exception as e:
        logger.error(f"Failed to generate session summary: {e}")
