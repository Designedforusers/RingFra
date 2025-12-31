# Building an Automated On-Call Agent with Claude Agent SDK

> **A complete guide to building a voice-controlled infrastructure management system that lets you manage your Render services, fix bugs, and deploy code—all through a phone call.**

This tutorial walks you through building PhoneFix, an AI-powered on-call engineer you can call 24/7. It uses the **Claude Agent SDK** for intelligent task execution, **Pipecat** for real-time voice processing, and integrates with **Render's MCP server** for infrastructure management.

---

## Demo

<!-- Replace with your actual video embed -->
[![PhoneFix Demo](https://img.youtube.com/vi/YOUR_VIDEO_ID/maxresdefault.jpg)](https://www.youtube.com/watch?v=YOUR_VIDEO_ID)

**Try it yourself:** Call **+1 415 853 6485** and ask it to check your Render services.

---

## Quick Start

Get up and running in under 5 minutes:

```bash
# 1. Clone and install
git clone https://github.com/your-org/phonefix.git
cd phonefix
python -m venv venv && source venv/bin/activate
pip install -e .

# 2. Set minimum required env vars
export ANTHROPIC_API_KEY=sk-ant-...
export TWILIO_ACCOUNT_SID=your_sid
export TWILIO_AUTH_TOKEN=your_token
export TWILIO_PHONE_NUMBER=+1234567890
export DEEPGRAM_API_KEY=your_key
export CARTESIA_API_KEY=your_key
export RENDER_API_KEY=rnd_...

# 3. Start the server
python -m uvicorn src.main:app --host 0.0.0.0 --port 8765

# 4. Expose locally with ngrok (for Twilio webhooks)
ngrok http 8765

# 5. Configure Twilio webhook to: https://YOUR_NGROK_URL/twilio/incoming
```

**Estimated time:** 30-60 minutes for full setup, ~5 minutes if you already have all API keys.

**Estimated costs:**
| Service | Free Tier | Paid Usage |
|---------|-----------|------------|
| Twilio | $15 credit | ~$0.02/min calls |
| Deepgram | $200 credit | ~$0.0043/min STT |
| Cartesia | 1000 chars free | ~$0.015/1K chars TTS |
| Anthropic | None | ~$0.01-0.05/call |
| Render | Free tier available | $7+/mo for production |

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Key Concepts: MCP and Zep](#key-concepts-mcp-and-zep)
3. [Project Setup](#project-setup)
4. [Voice Pipeline with Pipecat](#voice-pipeline-with-pipecat)
5. [Claude Agent SDK Integration](#claude-agent-sdk-integration)
6. [Deepgram STT Configuration](#deepgram-stt-configuration)
7. [Custom Tools with the @tool Decorator](#custom-tools-with-the-tool-decorator)
8. [MCP Server Configuration](#mcp-server-configuration)
9. [Background Worker for Autonomous Tasks](#background-worker-for-autonomous-tasks)
10. [Conversation Memory with Zep](#conversation-memory-with-zep)
11. [Twilio Webhooks and Callbacks](#twilio-webhooks-and-callbacks)
12. [Manual Testing and LLM Steering](#manual-testing-and-llm-steering)
13. [Testing](#testing)
14. [Deploying to Render](#deploying-to-render)

---

## Architecture Overview

```
Phone Call → Twilio WebSocket → Pipecat Pipeline → Claude Agent SDK
                                      │                    │
                                      │                    ├─→ File Tools (Read, Write, Edit, Bash)
                                      │                    ├─→ Render MCP (logs, deploy, metrics)
                                      │                    ├─→ Exa MCP (web search)
                                      │                    └─→ Custom Tools (callbacks, reminders)
                                      │
                               ┌──────┴──────┐
                               │             │
                          Deepgram STT   Cartesia TTS
                          (Speech→Text)  (Text→Speech)
```

**Key Components:**

| Component | Technology | Purpose |
|-----------|------------|---------|
| Voice Transport | Twilio + Pipecat | Bidirectional audio streaming |
| Speech-to-Text | Deepgram Flux | AI-powered turn detection |
| Text-to-Speech | Cartesia | Natural voice synthesis |
| AI Brain | Claude Agent SDK | Tool execution, reasoning |
| Infrastructure | Render MCP | Service management, logs, deploys |
| Background Tasks | ARQ + Redis | Async task execution with callbacks |
| Memory | Zep Cloud + Postgres | Conversation persistence |

---

## Key Concepts: MCP and Zep

Before diving in, let's understand two key technologies that make this system powerful:

### What is MCP (Model Context Protocol)?

**MCP** is an open protocol that lets AI models connect to external tools and data sources. Think of it as a universal adapter between Claude and the services it needs to control.

```
┌─────────────┐     MCP Protocol     ┌─────────────────┐
│   Claude    │ ←─────────────────── │   Render MCP    │
│   Agent     │                      │   Server        │
│   SDK       │ ────────────────────→│                 │
└─────────────┘   Tool calls/results └─────────────────┘
                                            │
                                            ▼
                                     ┌─────────────────┐
                                     │  Render API     │
                                     │  (your services)│
                                     └─────────────────┘
```

**Why MCP matters:**
- **Standardized** - One protocol to connect any tool
- **Secure** - Claude can only use tools you explicitly allow
- **Hosted** - Render provides `https://mcp.render.com/mcp` so you don't run your own server

**In this project**, we use three MCP servers:
1. **Render MCP** - Infrastructure management (logs, deploys, metrics)
2. **Exa MCP** - Web search for documentation and solutions
3. **Proactive MCP** - Our custom tools (callbacks, reminders, SMS)

### What is Zep?

**Zep** is a memory layer for AI applications. It stores conversation history and builds a knowledge graph about each user, enabling persistent context across sessions.

```
Call 1 (Monday):    "My API service is called 'main-api'"
                              │
                              ▼
                    ┌─────────────────┐
                    │      Zep        │
                    │  Memory Layer   │
                    │                 │
                    │ User: phone:+1. │
                    │ Facts:          │
                    │ - API = main-api│
                    │ - Prefers SMS   │
                    └─────────────────┘
                              │
                              ▼
Call 2 (Thursday):  "Check my API logs" → Zep provides context
                    Claude knows to check 'main-api' without asking
```

**Why Zep matters:**
- **Cross-session memory** - User preferences persist across calls
- **Fast retrieval** - P95 < 200ms for context loading
- **Knowledge graph** - Extracts entities and relationships automatically
- **Abrupt hangup safe** - Messages persisted immediately, not just on goodbye

---

## Project Setup

### Prerequisites

- Python 3.11+
- Redis (for background tasks)
- PostgreSQL (for multi-tenant mode)
- Accounts: Twilio, Anthropic, Deepgram, Cartesia, Render

### Installation

```bash
# Clone and setup
git clone https://github.com/your-org/phonefix.git
cd phonefix

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -e .
```

### Environment Variables

Create a `.env` file with your credentials:

```bash
# Twilio (Voice)
TWILIO_ACCOUNT_SID=your_account_sid
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_PHONE_NUMBER=+14158536485

# AI Services
ANTHROPIC_API_KEY=sk-ant-...
DEEPGRAM_API_KEY=your_deepgram_key
CARTESIA_API_KEY=your_cartesia_key

# Render Infrastructure
RENDER_API_KEY=rnd_...

# GitHub (for code operations)
GITHUB_TOKEN=ghp_...
GITHUB_REPO_URL=https://github.com/your-org/your-repo

# Optional: Background Tasks
REDIS_URL=redis://localhost:6379

# Optional: Conversation Memory
ZEP_API_KEY=your_zep_key

# Optional: Database (multi-tenant)
DATABASE_URL=postgresql://user:pass@host:5432/db
```

### Configuration Module

The configuration is managed with Pydantic for validation:

```python
# src/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    # Required
    TWILIO_ACCOUNT_SID: str
    TWILIO_AUTH_TOKEN: str
    TWILIO_PHONE_NUMBER: str
    ANTHROPIC_API_KEY: str
    DEEPGRAM_API_KEY: str
    CARTESIA_API_KEY: str
    RENDER_API_KEY: str

    # Voice Pipeline Settings
    USE_SDK_PIPELINE: bool = True  # Use Claude Agent SDK
    TTS_VOICE: str = "228fca29-3a0a-435c-8728-5cb483251068"  # Cartesia voice ID

    # Optional
    REDIS_URL: str | None = None
    ZEP_API_KEY: str | None = None
    DATABASE_URL: str | None = None

settings = Settings()
```

---

## Voice Pipeline with Pipecat

The voice pipeline is the heart of the system. It connects Twilio's audio stream to our AI agent through a series of processors.

### Pipeline Architecture

```python
# src/voice/sdk_pipeline.py

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.services.cartesia.tts import CartesiaTTSService
```

### The Core Pipeline Function

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

    # === 1. Create Claude Agent SDK Session ===
    session = VoiceAgentSession(
        user_context=user_context,
        cwd=cwd,
        caller_phone=caller_phone,
        callback_context=callback_context,
    )

    # === 2. Configure Twilio Transport ===
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

    # === 3. Speech-to-Text (Deepgram Flux) ===
    stt = DeepgramFluxSTTService(
        api_key=settings.DEEPGRAM_API_KEY,
        model="flux-general-en",
        params=DeepgramFluxSTTService.InputParams(
            eot_threshold=0.65,      # End-of-turn sensitivity
            eot_timeout_ms=3000,     # Max silence before forcing turn end
            keyterm=["render", "deploy", "github", "redis", "postgres"],
        ),
    )

    # === 4. Text-to-Speech (Cartesia) ===
    tts = CartesiaTTSService(
        api_key=settings.CARTESIA_API_KEY,
        voice_id=settings.TTS_VOICE,
    )

    # === 5. SDK Bridge (connects STT → Claude → TTS) ===
    sdk_bridge = SDKBridgeProcessor(
        session=session,
        zep_session=zep_session,
        is_callback=call_type.startswith("outbound_"),
        caller_phone=caller_phone,
    )

    # === 6. Build Pipeline ===
    pipeline = Pipeline([
        transport.input(),   # Twilio audio in
        stt,                 # Speech → Text
        sdk_bridge,          # Text → Claude SDK → Text
        tts,                 # Text → Speech
        transport.output(),  # Audio out to Twilio
    ])

    # === 7. Configure Task ===
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=8000,   # Twilio uses 8kHz
            audio_out_sample_rate=8000,
            allow_interruptions=True,     # User can interrupt
            enable_metrics=True,
        ),
        idle_timeout_secs=None,  # SDK manages lifecycle
    )

    # === 8. Run Pipeline ===
    runner = PipelineRunner(handle_sigint=False, force_gc=True)
    await runner.run(task)
```

### The SDK Bridge Processor

This is the **critical component** that bridges Pipecat's frame-based architecture with the Claude Agent SDK:

```python
class SDKBridgeProcessor(FrameProcessor):
    """Bridges Pipecat frames to Claude Agent SDK."""

    # Goodbye detection phrases
    GOODBYE_PHRASES = [
        "bye", "goodbye", "hang up", "end the call",
        "thanks bye", "talk to you later",
    ]

    # Filler phrases for long operations
    LONG_OPERATION_FILLERS = [
        "Still working on it...",
        "Almost there...",
        "Bear with me...",
    ]

    def __init__(self, session: VoiceAgentSession, ...):
        super().__init__()
        self.session = session
        self._processing = False
        self._session_ready = asyncio.Event()

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

    # Check for goodbye
    if self._is_goodbye(text):
        # Compress conversation for memory
        await self.session.compress_and_save_memory()

        # Send goodbye and end call
        await self.push_frame(TextFrame(text="Talk to you later."))
        await self.push_frame(LLMFullResponseEndFrame())
        await asyncio.sleep(1.5)  # Let TTS finish
        await self.push_frame(EndFrame())  # Hang up
        return

    # Start filler task for long operations (10+ seconds)
    self._long_op_task = asyncio.create_task(
        self._stream_long_operation_fillers()
    )

    try:
        # Query Claude SDK and stream responses
        async for response_text in self.session.query(text):
            if response_text:
                # Cancel fillers on first response
                self._cancel_long_op_filler()
                # Send text to TTS
                await self.push_frame(TextFrame(text=response_text))

        # Signal end of response
        await self.push_frame(LLMFullResponseEndFrame())

    except asyncio.CancelledError:
        logger.info("Query cancelled due to interruption")
```

### Handling Interruptions

```python
async def _handle_interruption(self):
    """Handle user interruption - stop SDK execution."""
    logger.info("User interrupted - stopping SDK")

    # Cancel filler task
    self._cancel_long_op_filler()

    # Interrupt the SDK session
    if self._session_ready.is_set():
        await self.session.interrupt()

    self._processing = False
```

### Long Operation Fillers

Keep the user engaged during long-running operations:

```python
async def _stream_long_operation_fillers(self):
    """Send periodic fillers for operations taking 10+ seconds."""
    try:
        await asyncio.sleep(10)  # Wait 10s before first filler
        fillers = self.LONG_OPERATION_FILLERS.copy()
        random.shuffle(fillers)

        for i, filler in enumerate(cycle(fillers)):
            await self.push_frame(TextFrame(text=filler))
            await asyncio.sleep(8)  # Every 8 seconds after
    except asyncio.CancelledError:
        pass  # Cancelled when SDK responds
```

---

## Claude Agent SDK Integration

The Claude Agent SDK provides a persistent session with full tool access—the same capabilities as Claude Code.

### VoiceAgentSession Class

```python
# src/agent/sdk_client.py

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

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

        # Build SDK options
        self.options = get_sdk_options(
            user_context, cwd, callback_context=callback_context
        )
        self.client: ClaudeSDKClient | None = None
        self._connected = False

    async def connect(self) -> None:
        """Connect to Claude Agent SDK."""
        if self._connected:
            return

        # Set session context for tools
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

The key to a responsive voice agent is **streaming partial responses** as they're generated:

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

    # Build system prompt
    system_prompt = _build_system_prompt(user_context, zep_context, callback_context)

    # MCP servers configuration
    mcp_servers = {
        # Render MCP - official hosted server
        "render": {
            "type": "http",
            "url": "https://mcp.render.com/mcp",
            "headers": {
                "Authorization": f"Bearer {render_api_key}",
            },
        },
        # Exa MCP - web search
        "exa": {
            "type": "http",
            "url": f"https://mcp.exa.ai/mcp?exaApiKey={settings.EXA_API_KEY}",
        },
        # Custom proactive tools
        "proactive": proactive_server,
    }

    return ClaudeAgentOptions(
        cwd=working_dir,
        env={"GH_TOKEN": github_token},  # For gh CLI
        system_prompt=system_prompt,
        mcp_servers=mcp_servers,

        # Full autonomy - no permission prompts
        permission_mode="bypassPermissions",

        # Enable streaming for real-time TTS
        include_partial_messages=True,

        # Read CLAUDE.md from working directory
        setting_sources=["project"],

        # Allowed tools
        allowed_tools=[
            # File operations
            "Read", "Write", "Edit", "Glob", "Grep", "Bash",

            # Render MCP tools
            "mcp__render__list_services",
            "mcp__render__get_service",
            "mcp__render__list_logs",
            "mcp__render__get_metrics",
            "mcp__render__list_deploys",
            # ... more Render tools

            # Custom tools
            "mcp__proactive__handoff_task",
            "mcp__proactive__set_reminder",
            "mcp__proactive__send_sms",
        ],
    )
```

### System Prompt for Voice

```python
def _build_system_prompt(user_context, zep_context, callback_context):
    """Build voice-optimized system prompt."""

    base_prompt = """<role>
You are an on-call engineer available via phone. Help users manage
code and infrastructure through voice commands.
</role>

<callbacks>
When user wants a callback ("call me back", "let me know when done"):

1. Background work (deploy, fix bug, run tests):
   → Call handoff_task FIRST with a detailed plan, then confirm

2. Quick work you can do now + callback:
   → Do the work, then call set_reminder with delay_minutes=2

3. Timed reminder ("remind me in X minutes"):
   → Call set_reminder with the delay

Always call the tool BEFORE saying "I'll call you back."
</callbacks>

<style>
- Concise: responses are spoken aloud
- Autonomous: just do it, don't ask for permission
- Progress updates: "checking logs now..." for long operations
</style>

<git>
Branch: git checkout -b fix/description
Commit: git add -A && git commit -m "fix: description"
PR: gh pr create --title "Fix: X" --body "..." --fill
</git>
"""

    # Add Zep memory context
    if zep_context:
        base_prompt += f"""
<MEMORY>
Context from previous conversations:
{zep_context}
</MEMORY>
"""

    return base_prompt
```

---

## Deepgram STT Configuration

Deepgram Flux is critical for natural voice interactions. It uses AI-powered turn detection instead of simple silence detection.

### Key Settings

```python
stt = DeepgramFluxSTTService(
    api_key=settings.DEEPGRAM_API_KEY,
    model="flux-general-en",  # AI-powered model
    params=DeepgramFluxSTTService.InputParams(
        # End-of-turn threshold (0.0-1.0)
        # Lower = more responsive, may cut off mid-sentence
        # Higher = waits longer, feels sluggish
        eot_threshold=0.65,  # Balanced setting

        # Max silence before forcing end-of-turn
        eot_timeout_ms=3000,  # 3 seconds

        # Domain-specific keywords for better accuracy
        keyterm=[
            "render", "deploy", "github", "commit",
            "push", "merge", "redis", "postgres"
        ],
    ),
)
```

### Why Flux Matters

Traditional STT uses **Voice Activity Detection (VAD)** - it listens for silence to determine when you're done speaking. This causes problems:

- Cuts off during natural pauses ("I want to... deploy the API")
- Waits too long when you're actually done

**Deepgram Flux** uses semantic understanding:
- Understands sentence structure
- Knows when a thought is complete
- Handles pauses naturally

---

## Custom Tools with the @tool Decorator

The Claude Agent SDK lets you define custom tools that integrate seamlessly with the agent.

### Creating Custom Tools

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
    from src.db.background_tasks import create_background_task
    from src.tasks.queue import enqueue_background_task

    ctx = _get_session_context()
    phone = ctx.get("caller_phone")
    user_id = ctx.get("user_context", {}).get("user_id")

    if not phone:
        return {
            "content": [{"type": "text", "text": "No phone for callback"}],
            "is_error": True,
        }

    # Parse and validate the plan
    plan = args.get("plan", {})
    if isinstance(plan, str):
        plan = {"objective": plan, "steps": ["Execute the plan"]}

    # Save task to database
    task_id = await create_background_task(
        user_id=user_id,
        phone=phone,
        task_type=args.get("task_type", "task"),
        plan=plan,
    )

    # Queue for background execution
    await enqueue_background_task(task_id)

    return {
        "content": [{
            "type": "text",
            "text": f"Task handed off. I'll call you back when done. Task ID: {task_id}"
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
    from src.tasks.queue import enqueue_reminder

    ctx = _get_session_context()
    phone = ctx.get("caller_phone")

    if not phone:
        return {"content": [{"type": "text", "text": "No phone"}], "is_error": True}

    delay_seconds = args["delay_minutes"] * 60

    await enqueue_reminder(
        phone=phone,
        message=args["message"],
        delay_seconds=delay_seconds,
    )

    return {
        "content": [{
            "type": "text",
            "text": f"Reminder set for {args['delay_minutes']} minutes."
        }]
    }
```

### Creating the MCP Server from Tools

```python
# Bundle custom tools into an MCP server
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

Tools need access to session data (phone number, credentials). Use `contextvars` for async-safe isolation:

```python
from contextvars import ContextVar

_session_context_var: ContextVar[dict] = ContextVar('session_context', default={})

def _set_session_context(user_context: dict | None, caller_phone: str | None):
    """Set session context for tools to access."""
    ctx = {
        "user_context": user_context or {},
        "caller_phone": caller_phone,
        "github_token": settings.GITHUB_TOKEN,
    }
    _session_context_var.set(ctx)

def _get_session_context() -> dict:
    """Get current session context."""
    return _session_context_var.get()
```

---

## MCP Server Configuration

MCP (Model Context Protocol) servers provide tools to Claude. We use three:

### 1. Render MCP (Infrastructure)

The official Render MCP server provides infrastructure management:

```python
mcp_servers = {
    "render": {
        "type": "http",
        "url": "https://mcp.render.com/mcp",
        "headers": {
            "Authorization": f"Bearer {render_api_key}",
        },
    },
}
```

**Available Tools:**
- `mcp__render__list_services` - List all services
- `mcp__render__get_service` - Get service details
- `mcp__render__list_logs` - Fetch application logs
- `mcp__render__get_metrics` - CPU, memory, request metrics
- `mcp__render__list_deploys` - Deployment history
- `mcp__render__update_environment_variables` - Update env vars
- `mcp__render__create_web_service` - Create new services
- And more...

### 2. Exa MCP (Web Search)

For researching documentation, APIs, and solutions:

```python
"exa": {
    "type": "http",
    "url": f"https://mcp.exa.ai/mcp?exaApiKey={settings.EXA_API_KEY}",
},
```

**Available Tools:**
- `mcp__exa__web_search_exa` - Search the web
- `mcp__exa__get_code_context_exa` - Find code examples

### 3. Proactive MCP (Custom Tools)

Our custom tools bundled as an MCP server:

```python
proactive_server = create_sdk_mcp_server(
    name="proactive",
    version="1.0.0",
    tools=[
        handoff_task_tool,
        send_sms_tool,
        set_reminder_tool,
        update_user_memory_tool,
    ],
)

mcp_servers["proactive"] = proactive_server
```

### Allowing Tools

You must explicitly allow which tools the agent can use:

```python
allowed_tools=[
    # Core file operations
    "Read", "Write", "Edit", "Glob", "Grep", "Bash",

    # Render MCP - infrastructure
    "mcp__render__list_services",
    "mcp__render__get_service",
    "mcp__render__list_logs",
    "mcp__render__get_metrics",
    "mcp__render__list_deploys",
    "mcp__render__get_deploy",
    "mcp__render__update_environment_variables",

    # Exa MCP - search
    "mcp__exa__web_search_exa",
    "mcp__exa__get_code_context_exa",

    # Proactive - custom
    "mcp__proactive__handoff_task",
    "mcp__proactive__set_reminder",
    "mcp__proactive__send_sms",
    "mcp__proactive__update_user_memory",
]
```

---

## Background Worker for Autonomous Tasks

When users say "fix this and call me back", the task runs in a background worker after the call ends.

### ARQ Worker Setup

```python
# src/tasks/worker.py

from arq import cron
from arq.connections import RedisSettings
from claude_agent_sdk import ClaudeAgentOptions, query, AssistantMessage, ResultMessage

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

    max_jobs = 10
    job_timeout = 1800  # 30 minutes
```

### Headless SDK Execution

The background worker spawns a **headless Claude session** with full tool access:

```python
async def execute_background_task(ctx: dict, task_id: str) -> dict:
    """Execute task with full Claude SDK capabilities."""

    # Load task from database
    task = await get_background_task(task_id)
    phone = task["phone"]
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

**Steps**:
{format_steps(plan.get('steps', []))}

**Success Criteria**: {plan.get('success_criteria')}

Execute each step. When done, provide a clear summary.
"""

    # Build headless query options
    query_options = ClaudeAgentOptions(
        cwd=user_repo_path,
        system_prompt=system_prompt,
        mcp_servers={
            "render": {...},
            "exa": {...},
        },
        permission_mode="bypassPermissions",  # Full autonomy
        allowed_tools=[
            "Read", "Write", "Edit", "Bash", "Glob", "Grep",
            "mcp__render__list_services",
            "mcp__render__list_logs",
            # ... all tools
        ],
        # Structured output for reliable result extraction
        output_format={
            "type": "json_schema",
            "schema": TASK_RESULT_SCHEMA,
        },
    )

    # Execute the task
    summary = ""
    async for msg in query(prompt=f"Execute: {plan['objective']}", options=query_options):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    summary = block.text
        elif isinstance(msg, ResultMessage):
            if msg.structured_output:
                summary = msg.structured_output.get("summary", summary)

    # Call the user back with results
    await initiate_callback(
        phone=phone,
        context={
            "task_type": task["task_type"],
            "summary": summary,
            "status": "completed",
            "success": True,
        },
        callback_type="task_complete",
    )

    return {"task_id": task_id, "status": "completed"}
```

### Task Queue Functions

```python
# src/tasks/queue.py

import arq

async def get_redis_pool():
    """Get ARQ Redis connection pool."""
    return await arq.create_pool(RedisSettings.from_dsn(settings.REDIS_URL))

async def enqueue_background_task(task_id: str):
    """Queue a task for background execution."""
    pool = await get_redis_pool()
    await pool.enqueue_job("execute_background_task", task_id)

async def enqueue_reminder(phone: str, message: str, delay_seconds: int):
    """Schedule a reminder callback."""
    pool = await get_redis_pool()
    await pool.enqueue_job(
        "reminder_callback",
        phone,
        message,
        _defer_by=timedelta(seconds=delay_seconds),
    )
```

### Running the Worker

```bash
# Start the ARQ worker
python -m arq src.tasks.worker.WorkerSettings
```

---

## Conversation Memory with Zep

Zep provides persistent conversation memory across calls with sub-200ms retrieval.

### ZepSession Class

```python
# src/db/zep_memory.py

from zep_cloud import AsyncZep, Message

class ZepSession:
    """Manages Zep memory for a single voice call."""

    def __init__(self, user_id: str, call_sid: str, phone: str | None = None):
        # Use phone-based user_id for consistency
        self.user_id = user_id  # e.g., "phone:+14155551234"
        self.thread_id = f"call-{call_sid}"
        self.phone = phone
        self._context: str | None = None

    async def start(self) -> str | None:
        """Initialize Zep session for this call."""
        client = await get_zep_client()

        # Ensure user exists
        await ensure_zep_user(self.user_id, self.phone)

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

    async def persist_turn(
        self,
        user_message: str,
        assistant_message: str,
    ) -> str | None:
        """Persist conversation turn and get updated context."""
        client = await get_zep_client()

        messages = [
            Message(role="user", content=user_message),
            Message(role="assistant", content=assistant_message),
        ]

        # Add messages with return_context=True for single-call optimization
        response = await client.thread.add_messages(
            thread_id=self.thread_id,
            messages=messages,
            return_context=True,  # Get context in same call
        )

        self._context = response.context
        return self._context
```

### Integrating with the Pipeline

```python
# In run_sdk_pipeline()

# Create Zep session
zep_session = None
if settings.ZEP_API_KEY and caller_phone:
    zep_session = ZepSession(
        user_id=f"phone:{caller_phone}",
        call_sid=call_sid,
        phone=caller_phone,
    )

# Start Zep and load previous context
@transport.event_handler("on_client_connected")
async def on_client_connected(transport, client):
    # Load Zep context before greeting
    if zep_session:
        zep_context = await zep_session.start()
        if zep_context:
            session.set_initial_zep_context(zep_context)

    # Connect SDK
    await session.connect()

    # Send greeting
    await sdk_bridge.push_frame(TextFrame(text="Hey, what can I help with?"))
```

### Persisting Each Turn

```python
# In SDKBridgeProcessor._process_user_input()

# After getting response from SDK
if self.zep_session and response_chunks:
    full_response = " ".join(response_chunks)
    # Run in background - don't block TTS
    asyncio.create_task(
        self._persist_to_zep(text, full_response)
    )

async def _persist_to_zep(self, user_message: str, assistant_message: str):
    """Persist turn in background."""
    if not self.zep_session:
        return

    context = await self.zep_session.persist_turn(
        user_message, assistant_message
    )

    if context:
        # Update SDK with new context for next turn
        self.session.update_zep_context(context)
```

---

## Twilio Webhooks and Callbacks

### Incoming Call Handler

```python
# src/voice/handlers.py

from twilio.twiml.voice_response import Connect, Start, Stream, VoiceResponse

async def handle_incoming_call(request: Request) -> Response:
    """Handle Twilio incoming call webhook."""
    form_data = await request.form()
    caller_phone = form_data.get("From", "")
    call_sid = form_data.get("CallSid", "")

    # Build WebSocket URL
    host = request.headers.get("host")
    ws_url = f"wss://{host}/twilio/media-stream"

    response = VoiceResponse()

    # Initial greeting (plays immediately while WebSocket connects)
    response.say(
        "Connecting you to the Render infrastructure assistant.",
        voice="Polly.Matthew",
    )

    # Start recording
    start = Start()
    start.recording(recording_channels="dual", track="both")
    response.append(start)

    # Connect to WebSocket for bidirectional streaming
    connect = Connect()
    stream = Stream(url=ws_url)
    stream.parameter(name="callerPhone", value=caller_phone)
    stream.parameter(name="callSid", value=call_sid)
    connect.append(stream)
    response.append(connect)

    # Keep connection alive
    response.pause(length=3600)

    return Response(content=str(response), media_type="application/xml")
```

### WebSocket Media Stream Handler

```python
async def handle_media_stream(websocket: WebSocket):
    """Handle Twilio WebSocket for audio streaming."""
    await websocket.accept()

    # Parse Twilio connection data
    _, call_data = await parse_telephony_websocket(websocket)

    stream_sid = call_data.get("stream_id")
    call_sid = call_data.get("call_id")
    custom_params = call_data.get("body", {})

    caller_phone = custom_params.get("callerPhone")
    call_type = custom_params.get("callType", "inbound")

    # Load user context
    user_context = await _load_user_context(caller_phone)

    # Parse callback context for outbound calls
    callback_context = None
    if custom_params.get("callbackContext"):
        callback_context = json.loads(custom_params["callbackContext"])

    # Run the voice pipeline
    await run_sdk_pipeline(
        websocket,
        stream_sid,
        call_sid,
        call_type=call_type,
        callback_context=callback_context,
        user_context=user_context,
        caller_phone=caller_phone,
    )
```

### Outbound Callbacks

```python
# src/callbacks/outbound.py

async def initiate_callback(
    phone: str,
    context: dict,
    callback_type: str = "task_complete",
) -> str:
    """Start outbound call with context for the agent."""
    client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    ws_url = "wss://your-app.onrender.com/twilio/media-stream"

    # Immediate greeting while WebSocket connects
    task_type = context.get("task_type", "task")
    success = context.get("success", True)

    if success:
        greeting = f"Hey, that {task_type} you asked me to run finished."
    else:
        greeting = f"Hey, that {task_type} ran into an issue."

    # Build TwiML with context
    context_json = html.escape(json.dumps(context))

    twiml = f"""
    <Response>
        <Say voice="Polly.Matthew">{greeting}</Say>
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

    call = client.calls.create(
        to=phone,
        from_=settings.TWILIO_PHONE_NUMBER,
        twiml=twiml.strip(),
    )

    return call.sid
```

---

## Manual Testing and LLM Steering

### Testing Without Making Phone Calls

You can test the pipeline locally without Twilio using the WebSocket test script:

```python
# scripts/test_call.py
#!/usr/bin/env python3
"""Simulate a test call to the voice agent."""

import asyncio
import json
import websockets

async def test_call(host: str = "localhost", port: int = 8765):
    """Simulate a test call to the voice agent."""
    uri = f"ws://{host}:{port}/twilio/media-stream"

    async with websockets.connect(uri) as ws:
        # Send Twilio-style connection events
        await ws.send(json.dumps({
            "event": "connected",
            "protocol": "Call",
            "version": "1.0.0",
        }))

        await ws.send(json.dumps({
            "event": "start",
            "sequenceNumber": "1",
            "start": {
                "streamSid": "test-stream-123",
                "accountSid": "test-account",
                "callSid": "test-call",
                "tracks": ["inbound", "outbound"],
                "customParameters": {
                    "callerPhone": "+14155551234",
                },
            },
            "streamSid": "test-stream-123",
        }))

        # Listen for responses
        while True:
            response = await asyncio.wait_for(ws.recv(), timeout=5.0)
            data = json.loads(response)
            if data.get("event") == "media":
                print(f"Audio received: {len(data['media']['payload'])} bytes")
            else:
                print(f"Event: {data.get('event')}")

if __name__ == "__main__":
    asyncio.run(test_call())
```

Run it:
```bash
# Terminal 1: Start the server
python -m uvicorn src.main:app --host 0.0.0.0 --port 8765

# Terminal 2: Run the test
python scripts/test_call.py
```

### LLM Steering: Shaping Claude's Behavior

The key to a good voice agent is **steering** Claude's responses through the system prompt. Here's how to tune behavior:

#### 1. Response Length

Voice responses should be SHORT. Add explicit guidance:

```python
system_prompt = """
<style>
- Keep responses under 2 sentences when possible
- Never list more than 3 items without asking first
- Say "I found 5 errors, want me to list them?" instead of listing all
</style>
"""
```

#### 2. Autonomous Action

Claude tends to ask for confirmation. Override this for voice:

```python
system_prompt = """
<autonomy>
- Just do it. Don't ask "would you like me to..."
- If user says "deploy", deploy. Don't ask which service if there's only one.
- Make reasonable assumptions and state them: "Deploying to staging since you didn't specify."
</autonomy>
"""
```

#### 3. Tool Usage Hints

Guide Claude on WHEN to use specific tools:

```python
system_prompt = """
<tool_hints>
- "check my services" → mcp__render__list_services
- "what's wrong" / "any errors" → mcp__render__list_logs with level filter
- "deploy" / "ship it" → mcp__render__trigger_deploy
- "call me back when done" → mcp__proactive__handoff_task FIRST, then confirm
</tool_hints>
"""
```

#### 4. Callback Behavior

The trickiest part is callbacks. Claude often says "I'll call you back" but forgets to call the tool:

```python
system_prompt = """
<callbacks>
CRITICAL: When user wants a callback:

1. Call handoff_task or set_reminder FIRST
2. THEN say "I'll call you back"

WRONG: "Sure, I'll call you back when it's done" (no tool called!)
RIGHT: [calls handoff_task] "Got it, I'll call you back when the deploy finishes"
</callbacks>
"""
```

#### 5. Error Handling

Prevent verbose error dumps:

```python
system_prompt = """
<errors>
- Never read full stack traces aloud
- Summarize: "There's a null pointer error in the auth module"
- Offer to fix: "Want me to take a look at fixing it?"
</errors>
"""
```

### Testing Specific Scenarios

Test these phrases to verify behavior:

| Test Phrase | Expected Behavior |
|-------------|-------------------|
| "Check my services" | Lists services briefly |
| "Any errors in the logs?" | Checks logs, summarizes issues |
| "Deploy to staging" | Triggers deploy without asking |
| "Deploy and call me back" | Calls handoff_task, confirms callback |
| "Remind me in 5 minutes to check the deploy" | Calls set_reminder |
| "What's using the most CPU?" | Gets metrics, identifies top service |

---

## Testing

### Test Structure

The project has comprehensive tests in the `tests/` directory:

```
tests/
├── conftest.py           # Shared fixtures
├── test_pipeline.py      # Voice pipeline and prompt tests
├── test_tools.py         # Tool execution tests
├── test_sdk_integration.py  # Claude Agent SDK integration
├── test_handoff_task.py  # Background task handling
└── ...
```

### Running Tests

```bash
# Run all tests (skip signup tests - module import issues)
pytest tests/ --ignore=tests/test_signup.py

# Run specific test file
pytest tests/test_tools.py -v

# Run single test
pytest tests/test_pipeline.py::TestHandlers::test_incoming_call_returns_twiml -v

# Run with coverage
pytest tests/ --cov=src --cov-report=html
```

### Key Test Examples

#### Testing Tool Execution

```python
# tests/test_tools.py

class TestCodeTools:
    """Tests for code operation tools using Claude Agent SDK."""

    @pytest.mark.asyncio
    async def test_analyze_code(self):
        """Test code analysis with mocked agent."""
        mock_text_block = MagicMock(spec=TextBlock)
        mock_text_block.text = "Found auth module in src/auth.py"

        mock_assistant_message = MagicMock(spec=AssistantMessage)
        mock_assistant_message.content = [mock_text_block]

        mock_result_message = MagicMock(spec=ResultMessage)
        mock_result_message.is_error = False
        mock_result_message.result = "Found auth module in src/auth.py"

        async def mock_query(*args, **kwargs):
            for msg in [mock_assistant_message, mock_result_message]:
                yield msg

        with patch("src.tools.code_tools.query", mock_query):
            from src.tools.code_tools import analyze_code
            result = await analyze_code("authentication flow")
            assert "auth" in result.lower()
```

#### Testing SDK Options

```python
# tests/test_sdk_integration.py

class TestClaudeAgentOptionsUsage:
    """Verify ClaudeAgentOptions is used instead of dict."""

    def test_query_options_is_claude_agent_options(self):
        """Confirm we're using ClaudeAgentOptions object, not dict."""
        from claude_agent_sdk import ClaudeAgentOptions

        query_options = ClaudeAgentOptions(
            cwd="/app",
            system_prompt="Test prompt",
            permission_mode="bypassPermissions",
            allowed_tools=["Read", "Write"],
        )

        # CRITICAL: Must be ClaudeAgentOptions, NOT dict
        assert isinstance(query_options, ClaudeAgentOptions)
        assert not isinstance(query_options, dict)
```

#### Testing Plan Normalization

```python
# tests/test_handoff_task.py

class TestPlanNormalization:
    """Test plan normalization logic in handoff_task_tool."""

    def test_json_string_input(self):
        """Test JSON string is parsed correctly."""
        plan_raw = '{"objective": "Analyze logs", "steps": ["Step 1"]}'
        plan = self._normalize_plan(plan_raw)
        
        assert plan["objective"] == "Analyze logs"
        assert isinstance(plan, dict)

    def test_plain_text_input(self):
        """Test plain text is wrapped correctly."""
        plan_raw = "Deploy to staging and verify health checks"
        plan = self._normalize_plan(plan_raw)
        
        assert "Deploy" in plan["objective"]
        assert len(plan["steps"]) > 0
```

### Prompts for Claude to Generate Tests

When you need new tests, give Claude these prompts:

#### For Tool Tests
```
Create pytest tests for the {tool_name} tool in src/tools/{file}.py.

Requirements:
- Mock the Claude Agent SDK query function
- Use MagicMock with proper spec (AssistantMessage, ResultMessage, TextBlock)
- Test success case, error case, and edge cases
- Follow the patterns in tests/test_tools.py
```

#### For Handler Tests
```
Write tests for the Twilio webhook handler in src/voice/handlers.py.

Requirements:
- Mock the FastAPI Request object with form data
- Verify TwiML response structure
- Test that callerPhone and callSid are passed through
- Check response content type is application/xml
```

#### For SDK Integration Tests
```
Add integration tests for {feature} in tests/test_sdk_integration.py.

Requirements:
- Use ClaudeAgentOptions (not dict!) - this is critical
- Mock the query generator function
- Test structured_output parsing when present
- Test text fallback when structured_output is None
- Verify fail-fast checks (API key, CLI presence)
```

#### For Background Worker Tests
```
Create tests for the background worker task execution.

Requirements:
- Mock get_background_task to return test task data
- Mock initiate_callback to verify it's called with correct context
- Test success path with structured output
- Test fallback path when structured_output is missing
- Use patch.dict for environment variables
```

---

## Deploying to Render

### render.yaml Configuration

```yaml
services:
  # Web Service (handles calls)
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
      - key: TWILIO_AUTH_TOKEN
        sync: false
      - key: TWILIO_PHONE_NUMBER
        sync: false
      - key: DEEPGRAM_API_KEY
        sync: false
      - key: CARTESIA_API_KEY
        sync: false
      - key: RENDER_API_KEY
        sync: false
      - key: GITHUB_TOKEN
        sync: false
      - key: REDIS_URL
        fromService:
          name: phonefix-redis
          type: redis
          property: connectionString
      - key: DATABASE_URL
        fromDatabase:
          name: phonefix-db
          property: connectionString

  # Background Worker
  - type: worker
    name: phonefix-worker
    runtime: docker
    plan: standard
    dockerCommand: python -m arq src.tasks.worker.WorkerSettings
    envVars:
      # Same env vars as web service
      - key: ANTHROPIC_API_KEY
        sync: false
      # ... etc

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

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI
RUN curl -fsSL https://claude.ai/install.sh | sh

# Install Python dependencies
COPY pyproject.toml .
RUN pip install -e .

# Copy application
COPY . .

# Run web server
CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8765"]
```

### Twilio Configuration

1. **Buy a phone number** in the Twilio console
2. **Configure the webhook**:
   - Voice Configuration → A Call Comes In
   - Webhook URL: `https://your-app.onrender.com/twilio/incoming`
   - HTTP Method: POST

---

## Key Patterns and Gotchas

### 1. Frame Timing with Pipecat

```python
# WRONG: Sending frames before pipeline is ready
await sdk_bridge.push_frame(TextFrame(text="Hello"))  # Dropped!

# RIGHT: Wait for connection, then send
@transport.event_handler("on_client_connected")
async def on_client_connected(transport, client):
    await session.connect()
    sdk_bridge.mark_session_ready()
    # NOW frames will be processed
    await sdk_bridge.push_frame(TextFrame(text="Hello"))
```

### 2. Keepalive During Long Operations

Twilio drops connections after ~30 seconds of silence:

```python
# Send filler audio while compressing memory
compress_task = asyncio.create_task(session.compress_and_save_memory())

await self.push_frame(TextFrame(text="Saving notes..."))
await self.push_frame(LLMFullResponseEndFrame())

while not compress_task.done():
    await asyncio.sleep(4)
    if not compress_task.done():
        await self.push_frame(TextFrame(text="Almost done..."))
        await self.push_frame(LLMFullResponseEndFrame())
```

### 3. Context Variables for Async Safety

```python
# Each concurrent call gets isolated context
from contextvars import ContextVar

_session_context_var: ContextVar[dict] = ContextVar('session_context')

# Set at call start
_session_context_var.set({"phone": caller_phone, ...})

# Access from any tool
ctx = _session_context_var.get()
phone = ctx["phone"]  # Correct phone for THIS call
```

### 4. Graceful Degradation

```python
try:
    await enqueue_background_task(task_id)
except RedisUnavailableError:
    # Fall back to SMS
    await send_sms(phone, "Background service unavailable. Please try again.")
    return {"is_error": True, ...}
```

### 5. E2E Latency Tracking

```python
# Track user-done → first-TTS latency
user_done_time = time.monotonic()

async for response in self.session.query(text):
    if first_response:
        latency = (time.monotonic() - user_done_time) * 1000
        logger.info(f"[LATENCY] E2E: {latency:.0f}ms")
        first_response = False

    await self.push_frame(TextFrame(text=response))
```

---

## Running the Project

```bash
# Start the web server
python -m uvicorn src.main:app --host 0.0.0.0 --port 8765

# Start the background worker (separate terminal)
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

You've now seen how to build a complete voice-controlled infrastructure management system:

1. **Pipecat** handles real-time audio streaming between Twilio and your app
2. **Deepgram Flux** provides AI-powered turn detection for natural conversations
3. **Claude Agent SDK** gives your agent full coding capabilities with tool access
4. **MCP servers** connect Claude to Render infrastructure and web search
5. **Custom tools** enable proactive features like callbacks and reminders
6. **ARQ workers** handle long-running tasks autonomously
7. **Zep** provides persistent conversation memory across calls

The key insight is that **Claude doesn't just answer questions—it takes action**. Users can call in, describe a problem, and Claude will investigate logs, fix code, deploy changes, and call back with results.

Happy building!
