# RingFra

**[ringfra.dev](https://ringfra.dev)**

> **An AI-powered on-call engineer you can call 24/7.** Manage Render services, fix bugs, deploy code, and get callbacks when tasks complete—all through natural phone conversations.

| Demo | Tutorial |
|------|----------|
| [![Demo Video](https://img.youtube.com/vi/tUcLhMSpCJ0/maxresdefault.jpg)](https://www.youtube.com/watch?v=tUcLhMSpCJ0) | [![Tutorial Video](https://img.youtube.com/vi/rUTlpQbNw3s/maxresdefault.jpg)](https://youtu.be/rUTlpQbNw3s) |

---

## What It Does

Call a phone number. Talk to Claude. Manage your infrastructure.

```
You: "Check my services for errors"
AI:  "I found 3 errors in the API logs from the last hour.
      Two are null pointer exceptions in the auth module,
      one is a timeout connecting to Redis. Want me to look into fixing them?"

You: "Fix the auth issue and call me back when it's done"
AI:  "Got it. I'll fix the auth bug and call you back."
      [Hangs up, works autonomously, calls you back 10 minutes later]
AI:  "Hey, I fixed the null pointer issue. It was a missing user check
      in the login handler. I've pushed the fix and deployed to staging."
```

---

## Production Stats

| Metric | Value |
|--------|-------|
| **P50 response latency** | 3.3s |
| **Cost per call** | ~$0.08-0.15/min |

---

## Key Features

- **Voice-first**: Natural phone conversations, not chat interfaces
- **Autonomous execution**: "Fix it and call me back" actually works
- **Full tool access**: Same capabilities as Claude Code (file ops, git, bash, web search)
- **Render-native**: Deep integration with Render MCP for infrastructure management
- **Persistent memory**: Remembers your preferences across calls (via Zep)
- **Callback system**: Background tasks with proactive phone callbacks when done

---

## Architecture

```
Phone ←→ Twilio ←→ Pipecat [Deepgram STT → SDK Bridge → Cartesia TTS]
                                              ↓
                                       Claude Agent SDK
                                              ↓
                                 ┌────────────┼────────────┐
                                 │            │            │
                            Render MCP    Bash/gh     Proactive
                            (deploy,      (git ops)   (callbacks,
                             logs,                     SMS)
                             metrics)
```

---

## Quick Start

```bash
# Clone and install
git clone https://github.com/Designedforusers/RingFra.git
cd RingFra
python -m venv venv && source venv/bin/activate
pip install -e .

# Set environment variables (see .env.example)
export ANTHROPIC_API_KEY=sk-ant-...
export TWILIO_ACCOUNT_SID=...
export TWILIO_AUTH_TOKEN=...
export TWILIO_PHONE_NUMBER=+1...
export DEEPGRAM_API_KEY=...
export CARTESIA_API_KEY=...
export RENDER_API_KEY=rnd_...
export GITHUB_TOKEN=ghp_...

# Run
python -m uvicorn src.main:app --host 0.0.0.0 --port 8765

# Expose with ngrok, configure Twilio webhook, call your number
```

**Full setup guide:** [docs/TUTORIAL.md](docs/TUTORIAL.md)

---

## Documentation

| Doc | Purpose |
|-----|---------|
| [**TUTORIAL.md**](docs/TUTORIAL.md) | Step-by-step setup guide (15 min read) |
| [**Technical Guide**](docs/REFERENCE.md) | Architecture deep-dive, code patterns, gotchas, production learnings |
| [**CLAUDE.md**](CLAUDE.md) | Project context for Claude Code |

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Voice Pipeline | [Pipecat](https://github.com/pipecat-ai/pipecat) |
| AI | [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) |
| STT | Deepgram Flux |
| TTS | Cartesia |
| Telephony | Twilio |
| Infrastructure | Render (MCP integration) |
| Memory | Zep Cloud |
| Task Queue | ARQ + Redis |

---

## Example Commands

| Say This | What Happens |
|----------|--------------|
| "Check my services" | Lists all Render services |
| "Any errors in the logs?" | Fetches and analyzes logs |
| "Deploy to staging" | Triggers deployment |
| "What's using the most CPU?" | Gets metrics, identifies issues |
| "Fix it and call me back" | Hands off to background worker, calls you when done |
| "Remind me in 30 minutes" | Schedules a callback |

---

## Deployment

Deploy to Render with one click using the included `render.yaml`:

- **Web service**: Handles incoming calls and voice pipeline
- **Worker**: Executes background tasks autonomously
- **Redis**: Task queue
- **Postgres**: User data and task history

See [Technical Guide](docs/REFERENCE.md#deployment) for full deployment guide.

---

## License

MIT
