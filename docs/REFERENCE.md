# PhoneFix Technical Guide

> **Build an AI-powered on-call engineer you can call 24/7. Manage Render services, fix bugs, deploy code, and get callbacks when tasks complete—all through natural phone conversations.**

---

## Demo

[![PhoneFix Demo](https://img.youtube.com/vi/tUcLhMSpCJ0/maxresdefault.jpg)](https://youtu.be/tUcLhMSpCJ0)

**Try it yourself:** Deploy your own instance and call your Twilio number

Example prompts:
- "Check my Render services"
- "Are there any errors in the logs?"
- "Deploy to staging and call me back when it's done"
- "What's using the most CPU?"

---

## Quick Start

```bash
# 1. Clone and install
git clone https://github.com/Designedforusers/PhoneFix.git
cd PhoneFix
python -m venv venv && source venv/bin/activate
pip install -e .

# 2. Set required environment variables
export ANTHROPIC_API_KEY=sk-ant-...
export TWILIO_ACCOUNT_SID=your_sid
export TWILIO_AUTH_TOKEN=your_token
export TWILIO_PHONE_NUMBER=+1234567890
export DEEPGRAM_API_KEY=your_key
export CARTESIA_API_KEY=your_key
export RENDER_API_KEY=rnd_...
export GITHUB_TOKEN=ghp_...

# 3. Start the server
python -m uvicorn src.main:app --host 0.0.0.0 --port 8765

# 4. Expose locally with ngrok
ngrok http 8765

# 5. Configure Twilio webhook: https://YOUR_NGROK_URL/twilio/incoming
```

**Time estimate:** 30-60 minutes for full setup, ~5 minutes if you have all API keys.

**Cost estimate:**
| Service | Free Tier | Paid Usage |
|---------|-----------|------------|
| Twilio | $15 trial credit | ~$0.02/min for calls |
| Deepgram | Free tier available | ~$0.0043/min STT |
| Cartesia | 1000 chars free | ~$0.015/1K chars TTS |
| Anthropic | None | ~$0.01-0.05/call |
| Render | Free tier available | $7+/mo for production |

---

## Table of Contents

1. [How It Works](#how-it-works)
2. [Architecture Overview](#architecture-overview)
3. [Key Concepts](#key-concepts)
   - [What is MCP?](#what-is-mcp-model-context-protocol)
   - [What is Zep?](#what-is-zep)
   - [What is Pipecat?](#what-is-pipecat)
4. [Project Structure](#project-structure)
5. [Configuration](#configuration)
6. [The Voice Pipeline](#the-voice-pipeline)
7. [Claude Agent SDK Integration](#claude-agent-sdk-integration)
8. [Speech-to-Text with Deepgram Flux](#speech-to-text-with-deepgram-flux)
9. [Custom Tools](#custom-tools)
10. [MCP Server Configuration](#mcp-server-configuration)
11. [Background Tasks and Callbacks](#background-tasks-and-callbacks)
12. [Conversation Memory](#conversation-memory)
13. [Twilio Integration](#twilio-integration)
14. [Testing and Debugging](#testing-and-debugging)
15. [LLM Steering](#llm-steering)
16. [Deployment](#deployment)
17. [Common Patterns and Gotchas](#common-patterns-and-gotchas)

---

## How It Works

When someone calls the phone number:

1. **Twilio** receives the call and opens a WebSocket to your server
2. **Pipecat** manages the audio pipeline (input → processing → output)
3. **Deepgram Flux** converts speech to text with AI-powered turn detection
4. **Claude Agent SDK** processes the text with full tool access (same as Claude Code)
5. **Cartesia** converts Claude's response back to natural speech
6. **Twilio** plays the audio to the caller

For background tasks ("fix this and call me back"):

1. User's request is handed to an **ARQ worker** (Redis-backed task queue)
2. Worker spawns a **headless Claude session** with the same tools
3. Claude executes the task autonomously (no user interaction)
4. When complete, worker initiates an **outbound Twilio call** to the user
5. The callback delivers a summary of what was done

---

## Architecture Overview

### Inbound Call Flow

```mermaid
flowchart TB
    subgraph "1. Call Initiation"
        Phone["📱 Caller dials"] --> Twilio["Twilio receives call"]
        Twilio -->|"POST /twilio/incoming"| Handler["handle_incoming_call()"]
        Handler -->|"Returns TwiML"| TwiML["&lt;Say&gt; 'Connecting you to the Render infrastructure assistant.'<br/>&lt;Stream url='/twilio/media-stream'/&gt;"]
        TwiML --> Twilio
    end

    subgraph "2. WebSocket Connection"
        Twilio -->|"WebSocket upgrade"| WS["handle_media_stream()"]
        WS --> Parse["parse_telephony_websocket()<br/>extracts stream_sid, call_sid"]
        Parse --> LoadCtx["_load_user_context(caller_phone)"]
    end

    subgraph "3. Pipeline Initialization"
        LoadCtx --> RunPipeline["run_sdk_pipeline()"]
        RunPipeline --> Session["VoiceAgentSession()"]
        RunPipeline --> ZepSession["ZepSession()"]
        RunPipeline --> Transport["FastAPIWebsocketTransport"]
    end

    subgraph "4. Pipecat Pipeline"
        direction LR
        TransportIn["transport.input()"]
        STT["DeepgramFluxSTTService"]
        Bridge["SDKBridgeProcessor"]
        TTS["CartesiaTTSService"]
        TransportOut["transport.output()"]
        TransportIn --> STT --> Bridge --> TTS --> TransportOut
    end

    subgraph "5. on_client_connected Event"
        direction TB
        ZepStart["zep_session.start()<br/><i>warms cache, loads context</i>"]
        SDKConnect["session.connect()<br/><i>ClaudeSDKClient</i>"]
        MarkReady["sdk_bridge.mark_session_ready()"]
        Greeting["push_frame(TextFrame)<br/>'Hey, I'm your on-call engineer. What can I help you with?'"]
        ZepStart --> SDKConnect --> MarkReady --> Greeting
    end

    subgraph "6. Claude Agent SDK"
        SDK["ClaudeSDKClient"]
        SDK --> BuiltIn["Built-in Tools:<br/>Read, Write, Edit, Bash,<br/>Glob, Grep, NotebookEdit,<br/>Task, TodoWrite,<br/>WebFetch, WebSearch"]
        SDK --> RenderMCP["Render MCP<br/>20 tools"]
        SDK --> ExaMCP["Exa MCP<br/>web_search_exa,<br/>get_code_context_exa"]
        SDK --> ProactiveMCP["Proactive MCP"]
    end

    subgraph "7. Custom Tools (Proactive MCP)"
        handoff["handoff_task"]
        reminder["set_reminder"]
        sms["send_sms"]
        memory["update_user_memory"]
    end

    Transport -->|"on_client_connected"| ZepStart
    Bridge -->|"session.query(text)"| SDK
    ProactiveMCP --> handoff
```

### Background Task Flow

```mermaid
flowchart TB
    subgraph "1. Task Handoff (during call)"
        User["User: 'Deploy and call me back'"]
        Bridge["SDKBridgeProcessor"]
        SDK["Claude Agent SDK"]
        HandoffTool["mcp__proactive__handoff_task"]

        User --> Bridge --> SDK --> HandoffTool
    end

    subgraph "2. Task Persistence"
        CreateTask["create_background_task()"]
        Postgres[(Postgres<br/>background_tasks table)]
        Enqueue["enqueue_background_task()"]
        Redis[(Redis Queue)]

        HandoffTool --> CreateTask --> Postgres
        CreateTask --> Enqueue --> Redis
    end

    subgraph "3. Worker Execution"
        Worker["ARQ Worker"]
        GetTask["get_background_task(task_id)"]
        HeadlessSDK["Headless Claude Session<br/>query() function"]

        Redis --> Worker --> GetTask --> HeadlessSDK
    end

    subgraph "4. Worker MCP Servers"
        WorkerRender["Render MCP"]
        WorkerExa["Exa MCP"]
        WorkerNote["NOTE: No Proactive MCP<br/>worker can't schedule callbacks"]

        HeadlessSDK --> WorkerRender
        HeadlessSDK --> WorkerExa
    end

    subgraph "5. Task Completion"
        Structured["structured_output<br/>TASK_RESULT_SCHEMA"]
        UpdateStatus["update_task_status()"]
        Callback["initiate_callback()"]

        HeadlessSDK --> Structured --> UpdateStatus --> Callback
    end

    subgraph "6. Outbound Call"
        TwilioOut["Twilio outbound call"]
        WS2["handle_media_stream()"]
        Pipeline2["run_sdk_pipeline()<br/>call_type='outbound_task_complete'"]
        UserPhone["📱 User's Phone"]

        Callback --> TwilioOut --> WS2 --> Pipeline2 --> UserPhone
    end
```

### Key Components Reference

| Component | File | Purpose |
|-----------|------|---------|
| FastAPI app | `src/main.py` | Routes: `/twilio/incoming`, `/twilio/media-stream` |
| Incoming call handler | `src/voice/handlers.py` | `handle_incoming_call()` - returns TwiML |
| WebSocket handler | `src/voice/handlers.py` | `handle_media_stream()` - runs pipeline |
| Voice pipeline | `src/voice/sdk_pipeline.py` | `run_sdk_pipeline()` - Pipecat with STT/TTS |
| SDKBridgeProcessor | `src/voice/sdk_pipeline.py` | Bridges Pipecat frames ↔ Claude SDK |
| VoiceAgentSession | `src/agent/sdk_client.py` | Manages `ClaudeSDKClient` lifecycle |
| SDK options builder | `src/agent/sdk_client.py` | `get_sdk_options()` - MCP servers, tools |
| Custom tools | `src/agent/sdk_client.py` | `@tool` decorated functions |
| Background worker | `src/tasks/worker.py` | `WorkerSettings` class for ARQ |
| Task execution | `src/tasks/worker.py` | `execute_background_task()` - headless SDK |
| Outbound calls | `src/callbacks/outbound.py` | `initiate_callback()` - builds TwiML |
| Zep memory | `src/db/zep_memory.py` | `ZepSession` class |
| Task queue | `src/tasks/queue.py` | `enqueue_background_task()` |

### Verified Tool Lists

**Voice Pipeline (`get_sdk_options()` in `sdk_client.py`):**
```python
allowed_tools=[
    # File operations
    "Read", "Write", "Edit", "Glob", "Grep", "NotebookEdit",
    # Execution
    "Bash",
    # Task management
    "Task", "TodoWrite",
    # Web
    "WebFetch", "WebSearch",
    # Render MCP (20 tools)
    "mcp__render__list_services", "mcp__render__get_service",
    "mcp__render__list_deploys", "mcp__render__get_deploy",
    "mcp__render__list_logs", "mcp__render__list_log_label_values",
    "mcp__render__get_metrics", "mcp__render__list_workspaces",
    "mcp__render__select_workspace", "mcp__render__get_selected_workspace",
    "mcp__render__create_web_service", "mcp__render__create_static_site",
    "mcp__render__update_environment_variables",
    "mcp__render__list_postgres_instances", "mcp__render__get_postgres",
    "mcp__render__create_postgres", "mcp__render__query_render_postgres",
    "mcp__render__list_key_value", "mcp__render__get_key_value",
    "mcp__render__create_key_value",
    # Exa MCP
    "mcp__exa__web_search_exa", "mcp__exa__get_code_context_exa",
    # Proactive MCP (custom)
    "mcp__proactive__handoff_task", "mcp__proactive__send_sms",
    "mcp__proactive__set_reminder", "mcp__proactive__update_user_memory",
]
```

**Background Worker (`execute_background_task()` in `worker.py`):**
```python
# Same built-in + Render + Exa tools
# BUT: No Proactive MCP (worker can't schedule callbacks)
# PLUS: output_format with TASK_RESULT_SCHEMA for structured output
```

---

## Key Concepts

### What is MCP (Model Context Protocol)?

**MCP** is an open protocol that lets AI models connect to external tools and data sources. Think of it as a standardized way for Claude to talk to APIs.

```
┌─────────────────┐                      ┌─────────────────┐
│                 │   "list_services"    │                 │
│  Claude Agent   │ ──────────────────→  │   Render MCP    │
│      SDK        │                      │     Server      │
│                 │ ←──────────────────  │                 │
└─────────────────┘   [service data]     └─────────────────┘
                                                │
                                                ▼
                                         Render API
                                      (your services)
```

**Why MCP matters for this project:**
- **Render MCP** (`https://mcp.render.com/mcp`) - Manage infrastructure without writing API code
- **Exa MCP** - Web search for documentation and solutions
- **Custom MCP** - Your own tools (callbacks, reminders) packaged as an MCP server

**How tools are named:** MCP tools follow the pattern `mcp__{server}__{tool}`:
- `mcp__render__list_services` - List Render services
- `mcp__render__list_logs` - Get application logs
- `mcp__proactive__handoff_task` - Hand off work to background

### What is Zep?

**Zep** is a memory layer for AI applications. It stores conversation history and extracts facts about users.

```
Call 1 (Monday):
  User: "My main service is called api-prod"
  Claude: "Got it, I'll remember that"
                    │
                    ▼
            ┌───────────────┐
            │     Zep       │
            │  ───────────  │
            │  User Facts:  │
            │  • Main svc   │
            │    = api-prod │
            │  • Prefers    │
            │    SMS alerts │
            └───────────────┘
                    │
                    ▼
Call 2 (Thursday):
  User: "Check my logs"
  Claude: [Already knows to check api-prod]
```

**Why Zep matters:**
- **Cross-session memory** - Preferences persist across calls
- **Fast retrieval** - P95 < 200ms context loading
- **Abrupt hangup safe** - Messages persisted immediately per turn

**Integration points:**
- `ZepSession.start()` - Warms cache, loads previous context
- `ZepSession.persist_turn()` - Saves each conversation turn
- User ID format: `phone:+14155551234` (consistent across calls)

### What is Pipecat?

**Pipecat** is a framework for building real-time voice AI applications, built on Daily's real-time infrastructure. I chose Pipecat for its customization flexibility and active community.

It provides:
- **Transports** - WebSocket connections to Twilio
- **Services** - STT (Deepgram), TTS (Cartesia)
- **Processors** - Custom logic between input and output
- **Frames** - Data units that flow through the pipeline

```python
pipeline = Pipeline([
    transport.input(),    # Audio from Twilio
    stt,                  # Speech → Text
    sdk_bridge,           # Text → Claude → Text (your custom processor)
    tts,                  # Text → Speech
    transport.output(),   # Audio to Twilio
])
```

The `SDKBridgeProcessor` is where Claude integration happens—it receives transcribed text, sends it to Claude, and emits response text for TTS.

---

## Project Structure

```
src/
├── main.py                 # FastAPI app, routes, startup
├── config.py               # Pydantic settings (env vars)
├── notifications.py        # SMS notification helpers
├── agent/
│   └── sdk_client.py       # VoiceAgentSession, tools, SDK options
├── voice/
│   ├── sdk_pipeline.py     # Pipecat pipeline, SDKBridgeProcessor
│   ├── pipeline.py         # Fallback Pipecat pipeline (non-SDK)
│   ├── handlers.py         # Twilio webhooks, WebSocket handler
│   └── prompts.py          # System prompts, callback prompts
├── tasks/
│   ├── worker.py           # ARQ worker, execute_background_task
│   ├── queue.py            # Redis queue helpers
│   ├── monitors.py         # Service health monitoring
│   └── schemas.py          # TASK_RESULT_SCHEMA for structured output
├── callbacks/
│   ├── outbound.py         # initiate_callback, send_sms
│   └── router.py           # Event routing (critical→call, warning→SMS)
├── db/
│   ├── connection.py       # Postgres connection pool
│   ├── zep_memory.py       # ZepSession class
│   ├── memory.py           # Postgres fallback memory
│   ├── background_tasks.py # Task persistence
│   └── users.py            # Multi-tenant user management
├── repos/
│   └── manager.py          # Git worktree management (multi-tenant)
└── tools/
    ├── code_tools.py       # Code analysis tools
    └── render_tools.py     # Render API wrappers
```

---

## Configuration

All configuration uses Pydantic settings with environment variable loading:

```python
# src/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    # === Required ===
    TWILIO_ACCOUNT_SID: str
    TWILIO_AUTH_TOKEN: str
    TWILIO_PHONE_NUMBER: str
    ANTHROPIC_API_KEY: str
    DEEPGRAM_API_KEY: str
    CARTESIA_API_KEY: str
    RENDER_API_KEY: str
    GITHUB_TOKEN: str

    # === Voice Settings ===
    VOICE_MODEL: str = "claude-sonnet-4-5-20250929"
    TTS_VOICE: str = "228fca29-3a0a-435c-8728-5cb483251068"  # Cartesia voice ID
    USE_SDK_PIPELINE: bool = True  # Use Claude Agent SDK

    # === Optional Services ===
    REDIS_URL: str | None = None        # Required for background tasks
    ZEP_API_KEY: str | None = None      # Required for cross-session memory
    DATABASE_URL: str | None = None     # Required for multi-tenant mode
    EXA_API_KEY: str | None = None      # Web search

    # === Mode ===
    MULTI_TENANT: bool = False  # False = demo mode, True = production
```

**Environment file (.env):**

```bash
# Required
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+1XXXXXXXXXX  # Your Twilio number
ANTHROPIC_API_KEY=sk-ant-...
DEEPGRAM_API_KEY=...
CARTESIA_API_KEY=...
RENDER_API_KEY=rnd_...
GITHUB_TOKEN=ghp_...

# For background tasks (callbacks, reminders)
REDIS_URL=redis://localhost:6379

# For cross-session memory
ZEP_API_KEY=...

# For multi-tenant mode
DATABASE_URL=postgresql://user:pass@host:5432/db
```

---

## The Voice Pipeline

The voice pipeline (`src/voice/sdk_pipeline.py`) is the heart of the system. It connects Twilio's audio stream to Claude through a series of Pipecat processors.

### Pipeline Components

```python
async def run_sdk_pipeline(
    websocket: WebSocket,
    stream_sid: str,
    call_sid: str,
    call_type: str = "inbound",
    callback_context: dict | None = None,
    user_context: dict | None = None,
    caller_phone: str | None = None,
) -> None:
    """Run the voice pipeline with Claude Agent SDK as the brain."""

    # 1. Create Claude session (persists for entire call)
    session = VoiceAgentSession(
        user_context=user_context,
        cwd=cwd,
        caller_phone=caller_phone,
        callback_context=callback_context,
    )

    # 2. Create Zep session for memory
    zep_session = ZepSession(
        user_id=f"phone:{caller_phone}",
        call_sid=call_sid,
        phone=caller_phone,
    ) if settings.ZEP_API_KEY and caller_phone else None

    # 3. Configure Twilio transport
    serializer = TwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        account_sid=settings.TWILIO_ACCOUNT_SID,
        auth_token=settings.TWILIO_AUTH_TOKEN,
        params=TwilioFrameSerializer.InputParams(auto_hang_up=True),
    )

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=serializer,
        ),
    )

    # 4. Speech-to-Text (Deepgram Flux)
    stt = DeepgramFluxSTTService(
        api_key=settings.DEEPGRAM_API_KEY,
        model="flux-general-en",
        params=DeepgramFluxSTTService.InputParams(
            eot_threshold=0.65,      # End-of-turn sensitivity
            eot_timeout_ms=3000,     # Max silence before forcing turn end
            keyterm=["render", "deploy", "github", "redis", "postgres"],
        ),
    )

    # 5. Text-to-Speech (Cartesia)
    tts = CartesiaTTSService(
        api_key=settings.CARTESIA_API_KEY,
        voice_id=settings.TTS_VOICE,
    )

    # 6. SDK Bridge (the custom processor that connects to Claude)
    sdk_bridge = SDKBridgeProcessor(
        session=session,
        zep_session=zep_session,
        is_callback=call_type.startswith("outbound_"),
        caller_phone=caller_phone,
    )

    # 7. Build pipeline
    pipeline = Pipeline([
        transport.input(),   # Twilio audio in
        stt,                 # Speech → Text
        sdk_bridge,          # Text → Claude SDK → Text
        tts,                 # Text → Speech
        transport.output(),  # Audio out to Twilio
    ])

    # 8. Configure and run
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=8000,   # Twilio uses 8kHz
            audio_out_sample_rate=8000,
            allow_interruptions=True,     # User can interrupt Claude
            enable_metrics=True,
        ),
    )

    runner = PipelineRunner(handle_sigint=False, force_gc=True)
    await runner.run(task)
```

### The SDK Bridge Processor

This is the critical component that bridges Pipecat's frame-based architecture with Claude:

```python
class SDKBridgeProcessor(FrameProcessor):
    """Bridges Pipecat frames to Claude Agent SDK."""

    GOODBYE_PHRASES = [
        "bye", "goodbye", "hang up", "end the call",
        "thanks bye", "talk to you later",
    ]

    LONG_OPERATION_FILLERS = [
        "Still working on it...",
        "Almost there...",
        "Bear with me...",
    ]

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process incoming frames from the pipeline."""
        await super().process_frame(frame, direction)

        if isinstance(frame, StartInterruptionFrame):
            # User interrupted - stop Claude immediately
            await self._handle_interruption()
            await self.push_frame(frame, direction)

        elif isinstance(frame, TranscriptionFrame):
            # User finished speaking - send to Claude
            if frame.text and not self._processing:
                self._processing = True
                try:
                    await self._process_user_input(frame.text)
                finally:
                    self._processing = False
        else:
            await self.push_frame(frame, direction)
```

### Processing User Input

```python
async def _process_user_input(self, text: str):
    """Send user input to SDK and stream response to TTS."""
    logger.info(f"User said: {text}")

    # Check for goodbye - triggers memory compression
    if self._is_goodbye(text):
        await self._handle_goodbye()
        return

    # Start filler task for long operations (10+ seconds)
    self._long_op_task = asyncio.create_task(
        self._stream_long_operation_fillers()
    )

    try:
        first_response = True
        response_chunks = []

        # Query Claude and stream responses to TTS
        async for response_text in self.session.query(text):
            if response_text:
                response_chunks.append(response_text)

                if first_response:
                    # Cancel fillers on first response
                    self._cancel_long_op_filler()
                    first_response = False

                # Send text to TTS
                await self.push_frame(TextFrame(text=response_text))

        # Signal end of response
        await self.push_frame(LLMFullResponseEndFrame())

        # Persist turn to Zep (background, doesn't block)
        if self.zep_session and response_chunks:
            asyncio.create_task(
                self._persist_to_zep(text, " ".join(response_chunks))
            )

    except asyncio.CancelledError:
        # User interrupted
        self._cancel_long_op_filler()
        logger.info("Query cancelled due to interruption")
```

### Handling Goodbye

The goodbye sequence is critical for memory persistence:

```python
async def _handle_goodbye(self):
    """Handle goodbye - compress memory and end call."""
    logger.info("[GOODBYE] Starting goodbye sequence")

    # Start compression in background
    compress_task = asyncio.create_task(
        self.session.compress_and_save_memory()
    )

    # Keep audio flowing to prevent Twilio timeout
    await self.push_frame(TextFrame(text="Got it, just saving a note..."))
    await self.push_frame(LLMFullResponseEndFrame())

    # Keepalive while compressing
    while not compress_task.done():
        await asyncio.sleep(4)
        if not compress_task.done():
            await self.push_frame(TextFrame(text="Almost done..."))
            await self.push_frame(LLMFullResponseEndFrame())

    # Final goodbye
    await self.push_frame(TextFrame(text="Perfect, saved! Talk to you later."))
    await self.push_frame(LLMFullResponseEndFrame())
    await asyncio.sleep(1.5)  # Let TTS finish

    # End call
    await self.push_frame(EndFrame())
```

---

## Claude Agent SDK Integration

The `VoiceAgentSession` class (`src/agent/sdk_client.py`) manages the Claude session for each call.

### Known Limitation: No Token-Level Streaming

**Important:** The Claude Agent SDK currently does not support true token-by-token streaming. Text content in `TextBlock` is only available once the block is fully rendered, not as individual tokens are generated. This introduces latency compared to the raw Anthropic API's streaming capabilities.

**Impact on voice agents:**
- First response latency is typically 1-3 seconds instead of near-instant
- Users hear nothing while Claude "thinks" before the first word
- Long responses have a noticeable pause at the start

**Why we accept this tradeoff:**
- The SDK provides state-of-the-art agentic capabilities (tool calling, MCP, file operations)
- Managing tool loops, context, and permissions manually would be significantly more complex
- The `include_partial_messages=True` option provides *partial* streaming at the message level
- For voice agents, the thinking time often overlaps with natural conversation pauses

**GitHub Issue:** This limitation is tracked in [anthropics/claude-agent-sdk-python#164](https://github.com/anthropics/claude-agent-sdk-python/issues/164) - "Streaming support in TextBlock". The issue requests enabling incremental text rendering for `TextBlock` content to improve user experience.

**Our mitigation strategies:**
1. **Filler phrases** - Send "Let me check that..." while waiting for first response
2. **Latency tracking** - Log `[LATENCY] First text: Xms` to monitor performance
3. **Long operation fillers** - Stream "Still working on it..." every 8 seconds for slow tools

### Session Lifecycle

```python
class VoiceAgentSession:
    """Manages a Claude Agent SDK session for a phone call."""

    def __init__(
        self,
        user_context: dict | None = None,
        cwd: Path | None = None,
        caller_phone: str | None = None,
        callback_context: dict | None = None,
    ):
        self.user_context = user_context
        self.caller_phone = caller_phone
        self.callback_context = callback_context
        self.options = get_sdk_options(user_context, cwd, callback_context=callback_context)
        self.client: ClaudeSDKClient | None = None
        self._connected = False

    async def connect(self) -> None:
        """Connect to Claude Agent SDK."""
        if self._connected:
            return

        # Set session context for tools to access
        _set_session_context(self.user_context, self.caller_phone)

        self.client = ClaudeSDKClient(self.options)
        await self.client.connect()
        self._connected = True

    async def disconnect(self) -> None:
        """Disconnect from SDK."""
        if self.client and self._connected:
            await self.client.disconnect()
            self._connected = False
```

### Streaming Responses for TTS

The key to responsive voice is streaming partial responses:

```python
async def query(self, prompt: str, tool_callback=None) -> AsyncIterator[str]:
    """Send query and yield text chunks for TTS streaming."""
    if not self.client:
        raise RuntimeError("Session not connected")

    await self.client.query(prompt)

    # Track text already yielded (partial messages may overlap)
    yielded_text_length = 0

    async for message in self.client.receive_response():
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    text = block.text
                    # Only yield NEW text
                    if len(text) > yielded_text_length:
                        new_text = text[yielded_text_length:]
                        yielded_text_length = len(text)
                        yield new_text

                elif isinstance(block, ToolUseBlock):
                    logger.debug(f"Tool called: {block.name}")
                    if tool_callback:
                        tool_callback(block.name)

        elif isinstance(message, ResultMessage):
            if message.is_error:
                yield f"I encountered an error: {message.result}"
            logger.info(f"Query complete. Cost: ${message.total_cost_usd:.4f}")
```

### Building SDK Options

```python
def get_sdk_options(
    user_context: dict | None = None,
    cwd: Path | None = None,
    zep_context: str | None = None,
    callback_context: dict | None = None,
) -> ClaudeAgentOptions:
    """Build ClaudeAgentOptions for the voice agent."""

    system_prompt = _build_system_prompt(user_context, zep_context, callback_context)

    # MCP servers
    mcp_servers = {
        "render": {
            "type": "http",
            "url": "https://mcp.render.com/mcp",
            "headers": {"Authorization": f"Bearer {render_api_key}"},
        },
        "exa": {
            "type": "http",
            "url": f"https://mcp.exa.ai/mcp?exaApiKey={settings.EXA_API_KEY}",
        },
        "proactive": proactive_server,  # Custom tools
    }

    return ClaudeAgentOptions(
        cwd=working_dir,
        env={"GH_TOKEN": github_token},
        system_prompt=system_prompt,
        mcp_servers=mcp_servers,
        permission_mode="bypassPermissions",  # Full autonomy
        include_partial_messages=True,        # Stream for TTS
        setting_sources=["project"],          # Read CLAUDE.md
        allowed_tools=[
            # File operations
            "Read", "Write", "Edit", "Glob", "Grep", "Bash",
            # Render MCP
            "mcp__render__list_services",
            "mcp__render__list_logs",
            "mcp__render__get_metrics",
            # ... more tools
            # Custom tools
            "mcp__proactive__handoff_task",
            "mcp__proactive__set_reminder",
            "mcp__proactive__send_sms",
        ],
    )
```

---

## Speech-to-Text with Deepgram Flux

Deepgram Flux uses AI-powered turn detection instead of simple silence detection.

### Why Flux Matters

**Traditional VAD (Voice Activity Detection):**
- Listens for silence to determine when you're done
- Cuts off during natural pauses: "I want to... deploy the API"
- Waits too long when you're actually done

**Deepgram Flux:**
- Uses semantic understanding
- Knows when a thought is complete
- Handles pauses naturally

### Configuration

```python
stt = DeepgramFluxSTTService(
    api_key=settings.DEEPGRAM_API_KEY,
    model="flux-general-en",
    params=DeepgramFluxSTTService.InputParams(
        # End-of-turn threshold (0.0-1.0)
        # Lower = more responsive, may cut off mid-sentence
        # Higher = waits longer, feels sluggish
        eot_threshold=0.65,  # Balanced

        # Max silence before forcing end-of-turn
        eot_timeout_ms=3000,  # 3 seconds

        # Domain-specific keywords for better accuracy
        keyterm=["render", "deploy", "github", "redis", "postgres"],
    ),
)
```

---

## Custom Tools

Custom tools are defined using the `@tool` decorator and bundled into an MCP server.

### The handoff_task Tool

This is the most important custom tool—it enables "do X and call me back":

```python
from claude_agent_sdk import tool, create_sdk_mcp_server

@tool("handoff_task", """Hand off a task to run AFTER the call ends.
The background agent will execute autonomously with full tool access.

Use when user says things like:
- "Deploy to staging and call me back"
- "Fix the bug and let me know when it's done"
- "Run the tests and call me with the results"
""", {
    "task_type": str,  # "deploy", "fix_bug", "run_tests"
    "plan": dict,      # {"objective": str, "steps": list, "success_criteria": str}
    "notify_on": str,  # "success", "failure", "both"
})
async def handoff_task_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Hand off task for background execution with callback."""
    ctx = _get_session_context()
    phone = ctx.get("caller_phone")
    user_id = ctx.get("user_context", {}).get("user_id")

    # Save task to database
    task_id = await create_background_task(
        user_id=user_id,
        phone=phone,
        task_type=args.get("task_type", "task"),
        plan=args.get("plan", {}),
    )

    # Queue for background execution
    await enqueue_background_task(task_id)

    return {
        "content": [{
            "type": "text",
            "text": f"Task handed off. I'll call you back when done."
        }]
    }
```

### The set_reminder Tool

```python
@tool("set_reminder", """Call the user back after a delay.
Use for 'call me back in X minutes' or 'remind me in an hour'.""", {
    "message": str,
    "delay_minutes": int,
})
async def set_reminder_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Set a reminder that triggers a callback."""
    ctx = _get_session_context()
    phone = ctx.get("caller_phone")

    await enqueue_reminder(
        phone=phone,
        message=args["message"],
        delay_seconds=args["delay_minutes"] * 60,
    )

    return {
        "content": [{
            "type": "text",
            "text": f"Reminder set for {args['delay_minutes']} minutes."
        }]
    }
```

### Bundling Tools as MCP Server

```python
proactive_tools = [
    handoff_task_tool,
    send_sms_tool,
    set_reminder_tool,
    update_user_memory_tool,
]

proactive_server = create_sdk_mcp_server(
    name="proactive",
    version="1.0.0",
    tools=proactive_tools,
)
```

### Session Context for Tools

Tools need access to session data. Use `contextvars` for async-safe isolation:

```python
from contextvars import ContextVar

_session_context_var: ContextVar[dict] = ContextVar('session_context', default={})

def _set_session_context(user_context: dict | None, caller_phone: str | None):
    """Set session context for tools to access."""
    _session_context_var.set({
        "user_context": user_context or {},
        "caller_phone": caller_phone,
        "github_token": settings.GITHUB_TOKEN,
    })

def _get_session_context() -> dict:
    """Get current session context."""
    return _session_context_var.get()
```

---

## MCP Server Configuration

### Render MCP

The official Render MCP server provides infrastructure management:

```python
"render": {
    "type": "http",
    "url": "https://mcp.render.com/mcp",
    "headers": {"Authorization": f"Bearer {render_api_key}"},
}
```

**Available tools:**
- `mcp__render__list_services` - List all services
- `mcp__render__get_service` - Get service details
- `mcp__render__list_logs` - Fetch application logs
- `mcp__render__get_metrics` - CPU, memory, request metrics
- `mcp__render__list_deploys` - Deployment history
- `mcp__render__update_environment_variables` - Update env vars
- `mcp__render__create_web_service` - Create new services

### Exa MCP

Web search for documentation and solutions:

```python
"exa": {
    "type": "http",
    "url": f"https://mcp.exa.ai/mcp?exaApiKey={settings.EXA_API_KEY}",
}
```

**Available tools:**
- `mcp__exa__web_search_exa` - Search the web
- `mcp__exa__get_code_context_exa` - Find code examples

### Tool Allowlisting

You must explicitly allow which tools the agent can use:

```python
allowed_tools=[
    # Core file operations
    "Read", "Write", "Edit", "Glob", "Grep", "Bash",

    # Render MCP
    "mcp__render__list_services",
    "mcp__render__get_service",
    "mcp__render__list_logs",
    "mcp__render__get_metrics",
    "mcp__render__list_deploys",

    # Custom tools
    "mcp__proactive__handoff_task",
    "mcp__proactive__set_reminder",
    "mcp__proactive__send_sms",
]
```

---

## Background Tasks and Callbacks

### ARQ Worker Setup

The background worker (`src/tasks/worker.py`) executes tasks handed off during calls:

```python
from arq import cron
from arq.connections import RedisSettings

class WorkerSettings:
    """ARQ worker configuration."""

    functions = [
        execute_background_task,
        reminder_callback,
    ]

    cron_jobs = [
        cron(check_service_health, minute={0, 15, 30, 45}),
    ]

    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    max_jobs = 1       # One at a time (memory)
    job_timeout = 1800  # 30 minutes
```

### Headless Claude Execution

The worker spawns a headless Claude session with the same tools:

```python
async def execute_background_task(ctx: dict, task_id: str) -> dict:
    """Execute task with full Claude SDK capabilities."""

    task = await get_background_task(task_id)
    plan = task["plan"]

    # Build autonomous system prompt
    system_prompt = f"""You are executing a background task AUTONOMOUSLY.
The user is NOT on the call.

## CRITICAL RULES
- Do NOT ask questions or wait for input
- Make decisions and proceed
- If something fails, try to fix it yourself

## Your Task
**Objective**: {plan.get('objective')}
**Steps**: {plan.get('steps')}
**Success Criteria**: {plan.get('success_criteria')}
"""

    # Build headless query options
    query_options = ClaudeAgentOptions(
        cwd=user_repo_path,
        system_prompt=system_prompt,
        mcp_servers=mcp_servers,
        permission_mode="bypassPermissions",
        allowed_tools=[...],
        output_format={
            "type": "json_schema",
            "schema": TASK_RESULT_SCHEMA,
        },
    )

    # Execute
    async for msg in query(prompt=f"Execute: {plan['objective']}", options=query_options):
        if isinstance(msg, ResultMessage) and msg.structured_output:
            summary = msg.structured_output.get("summary")

    # Call user back
    await initiate_callback(
        phone=task["phone"],
        context={"summary": summary, "success": True},
        callback_type="task_complete",
    )
```

### Running the Worker

```bash
python -m arq src.tasks.worker.WorkerSettings
```

---

## Conversation Memory

### ZepSession Class

```python
# src/db/zep_memory.py

class ZepSession:
    """Manages Zep memory for a single voice call."""

    def __init__(self, user_id: str, call_sid: str, phone: str | None = None):
        self.user_id = user_id  # e.g., "phone:+14155551234"
        self.thread_id = f"call-{call_sid}"
        self.phone = phone

    async def start(self) -> str | None:
        """Initialize Zep session for this call."""
        client = await get_zep_client()

        # Warm cache for fast retrieval
        await client.user.warm(user_id=self.user_id)

        # Load previous context
        self._context = await get_user_context_by_user(self.user_id)

        # Create thread for this call
        await client.thread.create(
            thread_id=self.thread_id,
            user_id=self.user_id,
        )

        return self._context

    async def persist_turn(self, user_message: str, assistant_message: str) -> str | None:
        """Persist conversation turn and get updated context."""
        client = await get_zep_client()

        response = await client.thread.add_messages(
            thread_id=self.thread_id,
            messages=[
                Message(role="user", content=user_message),
                Message(role="assistant", content=assistant_message),
            ],
            return_context=True,  # Get context in same call
        )

        return response.context
```

---

## Twilio Integration

### Incoming Call Handler

```python
# src/voice/handlers.py

async def handle_incoming_call(request: Request) -> Response:
    """Handle Twilio incoming call webhook."""
    form_data = await request.form()
    caller_phone = form_data.get("From", "")
    call_sid = form_data.get("CallSid", "")

    host = request.headers.get("host")
    ws_url = f"wss://{host}/twilio/media-stream"

    response = VoiceResponse()

    # Initial greeting while WebSocket connects
    response.say("Connecting you to the infrastructure assistant.", voice="Polly.Matthew")

    # Start recording
    start = Start()
    start.recording(recording_channels="dual", track="both")
    response.append(start)

    # Connect to WebSocket
    connect = Connect()
    stream = Stream(url=ws_url)
    stream.parameter(name="callerPhone", value=caller_phone)
    stream.parameter(name="callSid", value=call_sid)
    connect.append(stream)
    response.append(connect)

    response.pause(length=3600)

    return Response(content=str(response), media_type="application/xml")
```

### Outbound Callbacks

```python
# src/callbacks/outbound.py

def _get_websocket_url() -> str:
    """Get the WebSocket URL for media streams."""
    if settings.APP_ENV == "production":
        return f"wss://{settings.APP_BASE_URL}/twilio/media-stream"
    return f"ws://{settings.HOST}:{settings.PORT}/twilio/media-stream"

async def initiate_callback(phone: str, context: dict, callback_type: str) -> str:
    """Start outbound call with context for the agent."""
    client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    ws_url = _get_websocket_url()

    # Greeting based on result
    if context.get("success", True):
        greeting = f"Hey, that {context.get('task_type')} finished successfully."
    else:
        greeting = f"Hey, that {context.get('task_type')} ran into an issue."

    context_json = html.escape(json.dumps(context))

    twiml = f"""
    <Response>
        <Say voice="Polly.Matthew">{greeting}</Say>
        <Start><Recording recordingChannels="dual" track="both" /></Start>
        <Connect>
            <Stream url="{ws_url}">
                <Parameter name="callbackContext" value="{context_json}" />
                <Parameter name="callType" value="outbound_{callback_type}" />
                <Parameter name="callerPhone" value="{phone}" />
            </Stream>
        </Connect>
        <Pause length="3600" />
    </Response>
    """

    call = client.calls.create(to=phone, from_=settings.TWILIO_PHONE_NUMBER, twiml=twiml)
    return call.sid
```

---

## Testing and Debugging

### Testing on the Phone

It's best to test on the phone. Voice agents have timing-sensitive behavior (interruptions, turn detection, TTS pacing) that's impossible to test without actual audio.

Test these scenarios:
| Prompt | Expected Behavior |
|--------|-------------------|
| "Check my services" | Lists services briefly |
| "Any errors in the logs?" | Checks logs, summarizes |
| "Deploy to staging" | Triggers deploy without asking |
| "Deploy and call me back" | Calls handoff_task, confirms callback |
| "Remind me in 5 minutes" | Calls set_reminder |
| "What's using the most CPU?" | Gets metrics, identifies top service |

### Logging and Tracing

Every call is tagged with `call_sid` for correlation. Key log patterns:

```bash
# Call lifecycle
MEDIA STREAM: stream_sid=MZ123, call_sid=CA456
VoiceAgentSession connected
Client connected - connecting SDK session first...

# Latency (aim for <2000ms E2E)
[LATENCY] End-to-end (user done -> first TTS): 1847ms
[LATENCY] First text: 1203ms
[LATENCY] Query complete: 3421ms total, 5 text chunks

# User interaction
User said: check my logs for errors
Starting SDK query for: check my logs for errors...
SDK response chunk: I found 3 errors in the last hour...

# Tool execution
Tool call: mcp__render__list_logs with args: {"service": "api"}
[CALLBACK_TOOL] mcp__proactive__handoff_task called

# Goodbye and cleanup
[GOODBYE] Starting goodbye sequence
[GOODBYE] Compression complete: 847 chars
[GOODBYE] Pushing EndFrame to terminate call
```

**Debugging slow calls:**
1. Check `[LATENCY] End-to-end` - if >3000ms, users will notice
2. Check `[LATENCY] First text` - SDK processing time
3. Look for tool calls that take too long

**Debugging failed calls:**
1. Search for `MEDIA STREAM ERROR` or `SDK query error`
2. Check worker logs: `ANTHROPIC_API_KEY: NOT SET`
3. Look for `Timeout waiting for SDK session to connect`

### Running Tests

```bash
# Run all tests
pytest tests/ --ignore=tests/test_signup.py

# Run specific test file
pytest tests/test_tools.py -v

# Run with coverage
pytest tests/ --cov=src --cov-report=html

# Type check
mypy src/

# Lint
ruff check src/
```

---

## LLM Steering

The key to a good voice agent is **steering** Claude through the system prompt.

### Response Length

Voice responses should be SHORT:

```python
<style>
- Keep responses under 2 sentences when possible
- Never list more than 3 items without asking first
- Say "I found 5 errors, want me to list them?" instead of listing all
</style>
```

### Autonomous Action

Claude tends to ask for confirmation. Override this for voice:

```python
<autonomy>
- Just do it. Don't ask "would you like me to..."
- If user says "deploy", deploy. Don't ask which service if there's only one.
- Make reasonable assumptions: "Deploying to staging since you didn't specify."
</autonomy>
```

### Callback Behavior

The trickiest part—Claude often says "I'll call you back" but forgets to call the tool:

```python
<callbacks>
CRITICAL: When user wants a callback:

1. Call handoff_task or set_reminder FIRST
2. THEN say "I'll call you back"

WRONG: "Sure, I'll call you back when it's done" (no tool called!)
RIGHT: [calls handoff_task] "Got it, I'll call you back when the deploy finishes"
</callbacks>
```

### Error Handling

Prevent verbose error dumps:

```python
<errors>
- Never read full stack traces aloud
- Summarize: "There's a null pointer error in the auth module"
- Offer to fix: "Want me to take a look at fixing it?"
</errors>
```

---

## Deployment

### render.yaml

```yaml
services:
  - type: web
    name: phonefix
    runtime: docker
    plan: standard
    healthCheckPath: /health
    envVars:
      - key: ANTHROPIC_API_KEY
        sync: false
      - key: TWILIO_ACCOUNT_SID
        sync: false
      # ... other env vars

  - type: worker
    name: phonefix-worker
    runtime: docker
    plan: standard
    dockerCommand: python -m arq src.tasks.worker.WorkerSettings

databases:
  - name: phonefix-db
    plan: starter
  - name: phonefix-redis
    type: redis
    plan: starter
```

### Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y git curl && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI
RUN curl -fsSL https://claude.ai/install.sh | sh

COPY pyproject.toml .
RUN pip install -e .

COPY . .

CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8765"]
```

### Twilio Configuration

1. Buy a phone number in Twilio console
2. Configure webhook:
   - Voice → A Call Comes In
   - URL: `https://your-app.onrender.com/twilio/incoming`
   - Method: POST

---

## Common Patterns and Gotchas

### Code Reference: Critical Implementation Details

These patterns are verified against the actual codebase.

### 1. Frame Timing with Pipecat

**Source:** `on_client_connected` handler in `src/voice/sdk_pipeline.py`

The pipeline must be ready before sending frames. Frames sent before `StartFrame` are dropped by Pipecat.

```python
# WRONG: Sending frames before pipeline is ready
await sdk_bridge.push_frame(TextFrame(text="Hello"))  # Dropped!

# RIGHT: Wait for connection event
@transport.event_handler("on_client_connected")
async def on_client_connected(transport, client):
    await session.connect()
    sdk_bridge.mark_session_ready()
    await sdk_bridge.push_frame(TextFrame(text="Hello"))  # Works!
```

### 2. Twilio Keepalive

**Source:** `_process_user_input()` goodbye handling in `src/voice/sdk_pipeline.py`

Twilio drops connections after ~30 seconds of silence. The goodbye sequence sends filler audio while compressing memory:

```python
# Send filler audio during long operations
while not compress_task.done():
    await asyncio.sleep(4)
    await self.push_frame(TextFrame(text="Almost done..."))
    await self.push_frame(LLMFullResponseEndFrame())  # Flush TTS
```

### 3. ClaudeAgentOptions vs Dict

**Source:** `execute_background_task()` in `src/tasks/worker.py`

The worker must use `ClaudeAgentOptions`, not a dict. This was a real bug that caused `'dict' object has no attribute 'can_use_tool'`:

```python
# WRONG - causes "'dict' object has no attribute 'can_use_tool'"
query_options = {"cwd": "/app", "system_prompt": "..."}

# RIGHT
query_options = ClaudeAgentOptions(cwd="/app", system_prompt="...")
```

### 4. Context Variables for Async Safety

**Source:** `_session_context_var` and helpers in `src/agent/sdk_client.py`

Each concurrent call needs isolated context. Without `contextvars`, tools would access the wrong caller's phone number:

```python
# Each concurrent call gets isolated context
_session_context_var: ContextVar[dict] = ContextVar('session_context')

# Set at call start
_session_context_var.set({"phone": caller_phone})

# Access from any tool (correct phone for THIS call)
ctx = _session_context_var.get()
```

### 5. Graceful Degradation

**Source:** `handoff_task_tool()` error handling in `src/agent/sdk_client.py`

When Redis is unavailable, fall back to SMS notification:

```python
try:
    await enqueue_background_task(task_id)
except RedisUnavailableError:
    # Fall back to SMS
    await send_sms(phone, "[PhoneFix] Sorry, I couldn't schedule your task...")
    return {"is_error": True}
```

### 6. Fallback Callback Safety Net

**Source:** `_schedule_fallback_callback()` in `src/voice/sdk_pipeline.py`

When user requests a callback but Claude forgets to call the tool, the goodbye handler schedules a fallback:

```python
# In _process_user_input goodbye handling
if self._callback_requested and not self._callback_scheduled:
    logger.warning("[GOODBYE] Callback requested but not scheduled - scheduling fallback")
    await self._schedule_fallback_callback(self._last_user_message)
```

The fallback is cancelled if `handoff_task` succeeds via `cancel_fallback_reminder()` in `src/tasks/queue.py`.

### 7. Worker Uses `query()` Not `ClaudeSDKClient`

**Source:** `execute_background_task()` in `src/tasks/worker.py`

The background worker uses the standalone `query()` function, not `ClaudeSDKClient`. This is intentional - the worker doesn't need session management:

```python
# Voice pipeline uses ClaudeSDKClient (persistent session)
self.client = ClaudeSDKClient(self.options)
await self.client.connect()

# Worker uses query() function (one-shot execution)
async for msg in query(prompt=prompt, options=query_options):
    # Process messages
```

### 8. MULTI_TENANT Mode Differences

**Source:** `get_sdk_options()` in `src/agent/sdk_client.py`

In `MULTI_TENANT=True` mode, additional repo management tools are available:

```python
# Only in MULTI_TENANT mode
if settings.MULTI_TENANT:
    proactive_tools.extend([
        setup_repo_for_task_tool,  # Clone repo, create worktree
        ship_changes_tool,          # Commit, push, create PR
        cleanup_task_tool,          # Remove worktree
    ])
```

### 9. Zep Persistence Happens Per-Turn

**Source:** `_persist_to_zep()` in `src/voice/sdk_pipeline.py`

Messages are persisted to Zep after each turn, not just on goodbye. This ensures abrupt hangups don't lose conversation:

```python
# In _process_user_input, after SDK responds
if self.zep_session and response_chunks:
    full_response = " ".join(response_chunks)
    # Background task - doesn't block TTS
    asyncio.create_task(self._persist_to_zep(text, full_response))
```

---

## Running the Project

```bash
# Start web server
python -m uvicorn src.main:app --host 0.0.0.0 --port 8765

# Start background worker (separate terminal)
python -m arq src.tasks.worker.WorkerSettings

# Run tests
pytest tests/ --ignore=tests/test_signup.py

# Type check
mypy src/

# Lint
ruff check src/
```

---

## Summary

This tutorial covered building a complete voice-controlled infrastructure management system:

1. **Pipecat** - Real-time audio streaming between Twilio and your app
2. **Deepgram Flux** - AI-powered turn detection for natural conversations
3. **Claude Agent SDK** - Full coding capabilities with tool access
4. **MCP servers** - Connect Claude to Render infrastructure and web search
5. **Custom tools** - Callbacks, reminders, SMS for proactive features
6. **ARQ workers** - Autonomous background task execution
7. **Zep** - Persistent conversation memory across calls

The key insight: **Claude doesn't just answer questions—it takes action.** Users call in, describe a problem, and Claude investigates logs, fixes code, deploys changes, and calls back with results.
