# RingFra: Step-by-Step Setup Guide

> Get from zero to your first phone call in ~30 minutes.

**Prerequisites:** Python 3.11+, Node.js 20+, ngrok installed

---

## Step 1: Get Your API Keys (15 min)

You'll need accounts with these services. All have free tiers.

| Service | What You Need | Get It Here |
|---------|---------------|-------------|
| **Anthropic** | API key (`sk-ant-...`) | [console.anthropic.com](https://console.anthropic.com) |
| **Twilio** | Account SID, Auth Token, Phone Number | [twilio.com/try-twilio](https://www.twilio.com/try-twilio) |
| **Deepgram** | API key | [console.deepgram.com](https://console.deepgram.com/signup) |
| **Cartesia** | API key | [cartesia.ai](https://cartesia.ai) |
| **Render** | API key (`rnd_...`) | [render.com](https://render.com) |
| **GitHub** | Personal access token | [github.com/settings/tokens](https://github.com/settings/tokens) |

**Twilio phone number:** Buy one in the Twilio console → Phone Numbers → Buy a Number (~$1.15/month)

---

## Step 2: Clone and Install (2 min)

```bash
git clone https://github.com/Designedforusers/RingFra.git
cd RingFra

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -e .
```

---

## Step 3: Configure Environment (3 min)

```bash
cp .env.example .env
```

Edit `.env` with your API keys:

```bash
# Required - Voice Pipeline
ANTHROPIC_API_KEY=sk-ant-...
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+1234567890  # Your Twilio number

# Required - STT/TTS
DEEPGRAM_API_KEY=...
CARTESIA_API_KEY=...

# Required - Infrastructure Access
RENDER_API_KEY=rnd_...
GITHUB_TOKEN=ghp_...
```

---

## Step 4: Start the Server (1 min)

```bash
python -m uvicorn src.main:app --host 0.0.0.0 --port 8765
```

You should see:
```
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8765
```

---

## Step 5: Expose with ngrok (1 min)

In a new terminal:

```bash
ngrok http 8765
```

Copy the forwarding `https://` URL that ngrok displays

---

## Step 6: Configure Twilio Webhook (2 min)

1. Go to [Twilio Console](https://console.twilio.com) → Phone Numbers → Manage → Active Numbers
2. Click your phone number
3. Scroll to **Voice Configuration**
4. Set "A Call Comes In" to:
   - **Webhook**: `https://YOUR_NGROK_URL/twilio/incoming`
   - **Method**: POST
5. Save

---

## Step 7: Make Your First Call (1 min)

Call your Twilio phone number.

You should hear: *"Connecting you to the Render infrastructure assistant."*

Then: *"Hey, I'm your on-call engineer. What can I help you with?"*

Try saying:
- "Check my Render services"
- "Are there any errors in the logs?"
- "What's using the most CPU?"

---

## Troubleshooting

### No audio / silent call

| Symptom | Fix |
|---------|-----|
| Hear "Connecting..." then silence | Check `CARTESIA_API_KEY` is valid |
| Call drops immediately | Check ngrok is running, Twilio webhook is correct |
| "I'm still connecting..." loops | Check `ANTHROPIC_API_KEY` is valid |

### Common errors

| Error | Fix |
|-------|-----|
| `ModuleNotFoundError` | Run `pip install -e .` again |
| `Connection refused` | Make sure server is running on port 8765 |
| Twilio 11200 error | ngrok URL changed - update Twilio webhook |

---

## Next Steps

### Enable Background Tasks (Callbacks)

To use "fix it and call me back" functionality, you need Redis:

```bash
# Install Redis locally
brew install redis  # macOS
# or
sudo apt install redis-server  # Ubuntu

# Start Redis
redis-server

# Add to .env
REDIS_URL=redis://localhost:6379
```

Then start the worker in a new terminal:
```bash
python -m arq src.tasks.worker.WorkerSettings
```

### Enable Conversation Memory

For cross-call memory (remembers your preferences):

1. Sign up at [getzep.com](https://www.getzep.com/)
2. Add to `.env`:
   ```
   ZEP_API_KEY=...
   ```

### Deploy to Render

See [Technical Guide → Deployment](REFERENCE.md#deployment) for full `render.yaml` configuration.

Quick version:
1. Push repo to GitHub
2. Connect repo to Render
3. Create Web Service, Worker, Redis, and Postgres from `render.yaml`
4. Set environment variables in Render dashboard
5. Update Twilio webhook to your Render URL

---

## Architecture Overview

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

For deep architecture details, see [REFERENCE.md](REFERENCE.md).

---

## Cost Estimate

| Service | Free Tier | What You'll Use |
|---------|-----------|-----------------|
| Twilio | $15 trial credit | ~$0.02/min calls |
| Deepgram | Free tier available | ~$0.0043/min STT |
| Cartesia | 1000 chars free | ~$0.015/1K chars TTS |
| Anthropic | None | ~$0.01-0.05/call |
| Render | Free tier available | $0 for testing |

**Total for testing:** Free with trial credits

**Production estimate:** ~$0.08-0.15/min of call time

*Pricing estimates based on actual usage logs from testing. Rates may vary—check each provider's current pricing.*

---

## Full Documentation

| Doc | What's In It |
|-----|--------------|
| [**Technical Guide**](REFERENCE.md) | Architecture deep-dive, code patterns, gotchas, production learnings |
| [**CLAUDE.md**](../CLAUDE.md) | Project context for Claude Code |

---

**Questions?** Open an issue on GitHub.
