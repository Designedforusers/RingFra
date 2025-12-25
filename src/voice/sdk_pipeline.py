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
import random
from pathlib import Path

from fastapi import WebSocket
from loguru import logger

from pipecat.frames.frames import (
    Frame,
    TextFrame,
    TranscriptionFrame,
    EndFrame,
    LLMFullResponseEndFrame,
    StartInterruptionFrame,
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
from src.db.zep_memory import ZepSession


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
    
    # Context-aware filler phrases to eliminate awkward silence
    THINKING_FILLERS = {
        "lookup": [
            "Let me check that...", "Looking into it...", "One sec...", "Pulling that up...",
            "Checking now...", "Let me see what's going on...", "Looking at that...",
        ],
        "action": [
            "On it...", "Working on that now...", "Let me do that...", "Sure thing...",
            "Doing that now...", "Got it, working on it...", "Alright, let me handle that...",
        ],
        "complex": [
            "Hmm, let me think...", "Good question...", "Let me figure this out...",
            "That's interesting, let me dig into it...", "Let me work through that...",
        ],
        "default": [
            "Let me see...", "One moment...", "Sure...", "Okay...",
            "Alright...", "Got it...", "Let me look...",
        ],
    }
    
    # Streaming fillers for long operations (10+ seconds)
    LONG_OPERATION_FILLERS = [
        "Still working on it...",
        "Almost there...",
        "Bear with me...",
        "This is taking a bit longer...",
        "Still on it...",
        "Hang tight...",
        "Just a little longer...",
        "Making progress...",
        "Working through it...",
    ]
    
    def __init__(
        self,
        session: VoiceAgentSession,
        zep_session: ZepSession | None = None,
        end_call_callback=None,
        is_callback: bool = False,
    ):
        super().__init__()
        self.session = session
        self.zep_session = zep_session
        self._processing = False
        self._end_call_callback = end_call_callback
        self._session_ready = asyncio.Event()  # Set when SDK session is connected
        self._is_callback = is_callback  # Skip greeting for callbacks (Twilio already greeted)
        self._long_op_task: asyncio.Task | None = None  # For streaming fillers
        self._current_query_task: asyncio.Task | None = None  # Track current SDK query for interruption
        self._last_user_message: str | None = None  # Track for Zep persistence
    
    def mark_session_ready(self):
        """Called when SDK session is connected and ready."""
        self._session_ready.set()
    
    def _is_goodbye(self, text: str) -> bool:
        """Check if user is saying goodbye."""
        text_lower = text.lower().strip()
        # Check exact matches and phrases
        for phrase in self.GOODBYE_PHRASES:
            if phrase in text_lower:
                return True
        return False
    
    def _get_contextual_filler(self, text: str) -> str:
        """Pick a filler phrase based on the type of query."""
        text_lower = text.lower()
        
        if any(w in text_lower for w in ["check", "look", "what", "show", "status", "logs", "metrics"]):
            category = "lookup"
        elif any(w in text_lower for w in ["fix", "deploy", "run", "create", "delete", "scale", "restart"]):
            category = "action"
        elif any(w in text_lower for w in ["why", "how", "explain", "help me understand", "what do you think"]):
            category = "complex"
        else:
            category = "default"
        
        return random.choice(self.THINKING_FILLERS[category])
    
    async def _stream_long_operation_fillers(self):
        """Send periodic fillers for operations taking 10+ seconds."""
        try:
            await asyncio.sleep(10)  # Wait 10 seconds before first long-op filler
            # Shuffle to add variety across calls
            fillers = self.LONG_OPERATION_FILLERS.copy()
            random.shuffle(fillers)
            filler_index = 0
            while True:
                filler = fillers[filler_index % len(fillers)]
                await self.push_frame(TextFrame(text=filler))
                logger.debug(f"Sent long-op filler: {filler}")
                filler_index += 1
                await asyncio.sleep(8)  # Every 8 seconds after that
        except asyncio.CancelledError:
            pass  # Task cancelled when SDK responds
    
    def _cancel_long_op_filler(self):
        """Cancel the long operation filler task."""
        if self._long_op_task and not self._long_op_task.done():
            self._long_op_task.cancel()
            self._long_op_task = None
    
    async def _handle_interruption(self):
        """Handle user interruption - stop SDK execution."""
        logger.info("User interrupted - stopping SDK execution")
        
        # Cancel long-op fillers
        self._cancel_long_op_filler()
        
        # Cancel the current query task if running
        if self._current_query_task and not self._current_query_task.done():
            self._current_query_task.cancel()
            self._current_query_task = None
        
        # Tell SDK to interrupt (stops tool execution, etc.)
        if self._session_ready.is_set():
            try:
                await self.session.interrupt()
                logger.info("SDK interrupt sent successfully")
            except Exception as e:
                logger.warning(f"SDK interrupt failed (may already be idle): {e}")
        
        # Reset processing flag so next transcription can be processed
        self._processing = False
    
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process incoming frames."""
        await super().process_frame(frame, direction)
        
        if isinstance(frame, StartInterruptionFrame):
            # User is interrupting (barge-in) - stop Claude immediately
            await self._handle_interruption()
            # Still pass the frame through so Pipecat can stop TTS
            await self.push_frame(frame, direction)
        elif isinstance(frame, TranscriptionFrame):
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
        self._last_user_message = text  # Track for Zep persistence
        
        # Check for goodbye first - compress before ending
        if self._is_goodbye(text):
            logger.info("User said goodbye - compressing session and ending call")
            
            # Tell user we're saving (sets expectation for brief pause)
            await self.push_frame(TextFrame(text="Got it! Saving our conversation..."))
            
            # Compress NOW while session is still fully active
            # This takes 2-5 seconds but user knows to wait
            try:
                summary = await self.session.compress_and_save_memory()
                if summary:
                    logger.info(f"Compression complete: {len(summary)} chars saved")
                    await self.push_frame(TextFrame(text="Done! Talk to you later."))
                else:
                    # No user_id or compression failed - still say goodbye gracefully
                    logger.debug("No compression performed (no user_id or failed)")
                    await self.push_frame(TextFrame(text="Talk to you later!"))
            except Exception as e:
                logger.error(f"Compression error during goodbye: {e}")
                await self.push_frame(TextFrame(text="Talk to you later!"))
            
            await self.push_frame(LLMFullResponseEndFrame())
            
            # Trigger end of call callback if set
            if self._end_call_callback:
                await self._end_call_callback()
            
            # End the call - agent hangs up
            await self.push_frame(EndFrame())
            return
        
        # Wait for session to be ready (should already be ready)
        try:
            await asyncio.wait_for(self._session_ready.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for SDK session to connect")
            await self.push_frame(TextFrame(text="I'm still connecting. Please wait a moment."))
            await self.push_frame(LLMFullResponseEndFrame())
            return
        
        # Start long-operation filler task - only sends fillers after 10 seconds
        # This handles the case where SDK/tools take a long time
        self._long_op_task = asyncio.create_task(self._stream_long_operation_fillers())
        
        try:
            first_response = True
            response_chunks = []  # Collect for Zep persistence
            logger.info(f"Starting SDK query for: {text[:50]}...")
            async for response_text in self.session.query(text):
                if response_text:
                    response_chunks.append(response_text)
                    
                    # On first SDK response, cancel long-op fillers
                    if first_response:
                        self._cancel_long_op_filler()
                        first_response = False
                    
                    # Send TextFrame to TTS
                    await self.push_frame(TextFrame(text=response_text))
                    logger.debug(f"SDK response chunk: {response_text[:50]}...")
            
            # Signal end of response
            await self.push_frame(LLMFullResponseEndFrame())
            
            # Persist turn to Zep (async, doesn't block TTS)
            if self.zep_session and response_chunks:
                full_response = " ".join(response_chunks)
                asyncio.create_task(self._persist_to_zep(text, full_response))
        
        except asyncio.CancelledError:
            # User interrupted - this is expected
            self._cancel_long_op_filler()
            logger.info("SDK query cancelled due to user interruption")
            
        except Exception as e:
            self._cancel_long_op_filler()
            logger.error(f"SDK query error: {e}")
            await self.push_frame(TextFrame(text="I encountered an error. Please try again."))
            await self.push_frame(LLMFullResponseEndFrame())
    
    async def _persist_to_zep(self, user_message: str, assistant_message: str):
        """Persist conversation turn to Zep (background task)."""
        if not self.zep_session:
            return
        
        try:
            context = await self.zep_session.persist_turn(user_message, assistant_message)
            if context:
                # Update SDK session with new context for next turn
                self.session.update_zep_context(context)
                logger.debug(f"Zep context updated ({len(context)} chars)")
        except Exception as e:
            logger.warning(f"Failed to persist to Zep: {e}")


async def run_sdk_pipeline(
    websocket: WebSocket,
    stream_sid: str,
    call_sid: str,
    call_type: str = "inbound",
    callback_context: dict | None = None,
    user_context: dict | None = None,
    caller_phone: str | None = None,
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
        caller_phone: Phone number of the caller (from Twilio)
    """
    logger.info(f"Starting SDK pipeline: stream_sid={stream_sid}, call_type={call_type}, caller_phone={caller_phone}")
    
    # Determine working directory from user's repo
    cwd = None
    if user_context and user_context.get("repos"):
        repos = user_context["repos"]
        if repos and repos[0].get("local_path"):
            cwd = Path(repos[0]["local_path"])
    
    # Use caller_phone from argument (Twilio), fallback to user_context if available
    if not caller_phone and user_context and user_context.get("user"):
        caller_phone = user_context["user"].get("phone")
    
    # === Claude Agent SDK Session (persistent for entire call) ===
    session = VoiceAgentSession(
        user_context=user_context,
        cwd=cwd,
        caller_phone=caller_phone,
    )
    
    # === Zep Memory Session (real-time persistence) ===
    zep_session: ZepSession | None = None
    if settings.ZEP_API_KEY and caller_phone:
        # Use phone as user_id for Zep (consistent across calls)
        zep_user_id = f"phone:{caller_phone}"
        zep_session = ZepSession(
            user_id=zep_user_id,
            call_sid=call_sid,
            phone=caller_phone,
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
            eot_threshold=0.65,       # Balanced - responsive but not too jumpy
            eot_timeout_ms=3000,      # 3 sec max silence before forcing end-of-turn
            keyterm=["render", "deploy", "github", "commit", "push", "merge", "redis", "postgres"],
        ),
    )
    
    # === Text-to-Speech (Cartesia) ===
    tts = CartesiaTTSService(
        api_key=settings.CARTESIA_API_KEY,
        voice_id=settings.TTS_VOICE,
    )
    
    # === SDK Bridge (connects Pipecat to Claude Agent SDK) ===
    is_callback = call_type.startswith("outbound_")
    sdk_bridge = SDKBridgeProcessor(
        session=session,
        zep_session=zep_session,
        is_callback=is_callback,
    )
    
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
        logger.info("Client connected - connecting SDK session first...")
        
        # Start Zep session (warms cache, loads previous context) - run in parallel with SDK connect
        zep_context = None
        if zep_session:
            try:
                zep_context = await zep_session.start()
                if zep_context:
                    logger.info(f"Zep context loaded ({len(zep_context)} chars)")
                    session.set_initial_zep_context(zep_context)
            except Exception as e:
                logger.warning(f"Zep session start failed: {e}")
        
        # Connect SDK - this takes ~4 seconds but Twilio already said
        # "Connecting you to the Render infrastructure assistant" so user knows to wait
        await session.connect()
        logger.info("SDK session connected")
        
        # Mark session as ready so user input can be processed
        sdk_bridge.mark_session_ready()
        
        # NOW send greeting - pipeline is ready, greeting will be spoken
        if call_type.startswith("outbound_") and callback_context:
            # Callback - Twilio already greeted, just give the substance
            cb_task = callback_context.get('task_description') or callback_context.get('task_type') or 'your request'
            status = callback_context.get('status', 'completed')
            summary = callback_context.get('summary', '')
            
            if status == 'completed' and callback_context.get('success', True):
                if summary:
                    greeting = summary
                else:
                    greeting = f"I finished {cb_task}. Everything went smoothly."
            elif status == 'failed' or not callback_context.get('success', True):
                greeting = f"I ran into an issue with {cb_task}. {summary}" if summary else f"I ran into an issue with {cb_task}."
            elif call_type == "outbound_reminder":
                message = callback_context.get('reminder') or callback_context.get('message') or cb_task
                greeting = message
            else:
                greeting = f"{summary}" if summary else f"It's about {cb_task}."
        else:
            # Inbound call - full greeting
            greeting = "Hey, I'm your on-call engineer. What can I help you with?"
        
        logger.info(f"Sending greeting: {greeting}")
        await sdk_bridge.push_frame(TextFrame(text=greeting))
    
    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected - cleaning up session")
        
        # NOTE: Compression is now handled in _process_user_input when user says goodbye.
        # This handler runs in a different async context which causes "exit cancel scope" errors.
        # If user hangs up abruptly (no goodbye), we lose this session's memory.
        # Future improvement: Add Zep for real-time message persistence + ARQ for async compression.
        
        await session.disconnect()
        
        # Don't await task.cancel() - it causes async context issues
        # The pipeline will clean up naturally when the websocket closes
    
    # === Run ===
    runner = PipelineRunner(handle_sigint=False, force_gc=True)
    await runner.run(task)
