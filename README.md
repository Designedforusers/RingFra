# Render Voice Agent

Voice-controlled infrastructure management for Render. Call a phone number and manage your deployed applications using natural language.

## Features

- **Voice Commands**: Manage infrastructure by talking naturally
- **Render Integration**: List services, view logs, scale, deploy, rollback
- **Code Operations**: Analyze code, fix bugs, run tests, commit changes
- **Real-time Voice**: Powered by Deepgram STT, Claude LLM, and Cartesia TTS

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 20+ (for Claude Code CLI)
- API keys for: Twilio, Anthropic, Deepgram, Cartesia, Render, GitHub

### Setup

```bash
# Clone and setup
git clone <your-repo>
cd render-voice-agent

# Run setup script
./scripts/setup.sh

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Clone target repo (optional, for code operations)
./scripts/clone_target_repo.sh

# Run locally
python -m src.main
```

### Deploy to Render

1. Push to GitHub
2. Connect repo to Render
3. Configure environment variables
4. Deploy!

## Voice Commands

| Say This | Does This |
|----------|-----------|
| "What's running?" | Lists all services |
| "Show me the logs" | Gets recent logs |
| "What's wrong?" | Analyzes errors |
| "Fix the auth bug" | Finds and fixes the bug |
| "Scale up the API" | Increases instances |
| "Deploy the latest" | Triggers deployment |
| "Roll it back" | Rollbacks to previous |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Render Server                            │
│  ┌────────────┐   ┌────────────┐   ┌──────────────────────┐  │
│  │   Twilio   │──▶│  Pipecat   │──▶│  Claude + Tools      │  │
│  │  Webhook   │   │  Pipeline  │   │  - Render API        │  │
│  └────────────┘   │  - STT     │   │  - Claude Code CLI   │  │
│                   │  - LLM     │   └──────────────────────┘  │
│                   │  - TTS     │                              │
│                   └────────────┘                              │
└─────────────────────────────────────────────────────────────┘
```

## API Endpoints

- `GET /` - Service info
- `GET /health` - Health check
- `POST /twilio/incoming` - Twilio webhook for calls
- `WS /twilio/media-stream` - WebSocket for audio streaming

## Configuration

See `.env.example` for all configuration options.

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Format code
black src/ tests/
ruff check src/ tests/
```

## License

MIT
