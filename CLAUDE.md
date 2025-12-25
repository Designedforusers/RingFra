# PhoneFix - Voice Agent for Render Infrastructure

## Overview
Voice-controlled infrastructure management. Call the phone number, talk to Claude, manage your Render services.

## Architecture
```
Phone Call → Twilio → Pipecat (STT/TTS) → Claude Agent SDK
                                              ↓
                                         Tools:
                                         - Render MCP (logs, deploy, metrics)
                                         - Bash/gh CLI (git operations)
                                         - Proactive (SMS, callbacks, reminders)
```

## Key Files
- `src/voice/sdk_pipeline.py` - Pipecat pipeline, STT/TTS, greeting flow, Zep integration
- `src/voice/handlers.py` - Twilio webhooks, call routing
- `src/agent/sdk_client.py` - Claude Agent SDK session, tools, system prompt
- `src/db/zep_memory.py` - Zep Cloud integration for real-time memory
- `src/db/memory.py` - Postgres session memory (backup)
- `src/tasks/worker.py` - ARQ background worker

## Commands
```bash
# Run locally
python -m uvicorn src.main:app --host 0.0.0.0 --port 8765

# Tests (skip signup tests - module import issues)
pytest tests/ --ignore=tests/test_signup.py

# Type check
mypy src/

# Lint
ruff check src/
```

## Environment
- Python 3.11+ (Docker uses 3.11)
- Render for hosting (web service + Postgres + Redis)
- Twilio for phone/SMS
- Deepgram Flux for STT
- Cartesia for TTS

## Memory Architecture
| Layer | Storage | Purpose |
|-------|---------|---------|
| **Zep** | Zep Cloud | Real-time message persistence, knowledge graph, P95 < 200ms retrieval |
| Postgres summary | `session_memory.summary` | Backup rolling context (compression on goodbye) |
| CLAUDE.md | User's repo directory | Stable user preferences (SDK reads via `setting_sources=["project"]`) |
| update_user_memory tool | Writes to user's CLAUDE.md | Agent auto-updates when it learns preferences/patterns |

### Zep Integration (`src/db/zep_memory.py`)
- **User ID**: `phone:{caller_phone}` - consistent across all calls from same number
- **Thread ID**: `call-{call_sid}` - one thread per call
- **On call start**: `zep_session.start()` warms cache + loads previous context
- **After each turn**: `persist_turn()` with `return_context=True` for single-call persistence + context update
- **Abrupt hangup safe**: Messages persisted immediately, not just on goodbye

## Code Style
- Async everywhere (asyncio)
- Loguru for logging
- Type hints required
- Pydantic for config/validation

## Important Patterns
- **Frame timing**: SDK must connect BEFORE sending greeting (Pipecat drops frames before StartFrame)
- **Compression on goodbye**: `compress_and_save_memory()` saves summary to Postgres
- **Multi-tenant**: `MULTI_TENANT=true` enables git worktree isolation per user
- **Contextvars**: `_session_context_var` for async-safe per-call isolation

## Gotchas
- Pipecat drops frames sent before pipeline receives `StartFrame`
- Cartesia free tier: 2 concurrent TTS requests max
- STT settings: `eot_threshold=0.65`, `eot_timeout_ms=3000` (faster but may cut off)
- `on_client_disconnected` runs in different async context - don't do heavy work there

## Phone Number
+1 415 853 6485

## Render Services
- Web: `render-voice-agent` (srv-d53qa0re5dus73b3b40g)
- DB: `render-voice-agent-db`
- Redis: `render-voice-agent-redis`
