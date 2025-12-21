"""
Main FastAPI application entry point.

Handles:
- Twilio webhook for incoming calls
- WebSocket upgrade for media streams
- Health check endpoint
- Graceful shutdown
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import Response
from loguru import logger

from src.config import settings
from src.notifications import handle_render_webhook
from src.utils.logging import setup_logging
from src.voice.handlers import handle_incoming_call, handle_media_stream


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    setup_logging(settings.LOG_LEVEL)
    logger.info("Starting Render Voice Agent")
    logger.info(f"Environment: {settings.APP_ENV}")

    # Verify target repo exists
    if not os.path.exists(settings.TARGET_REPO_PATH):
        logger.warning(f"Target repo not found at {settings.TARGET_REPO_PATH}")
        logger.warning("Code operations will be limited")
    else:
        logger.info(f"Target repo found at {settings.TARGET_REPO_PATH}")

    yield

    logger.info("Shutting down Render Voice Agent")


app = FastAPI(
    title="Render Voice Agent",
    description="Voice-controlled infrastructure management for Render",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check():
    """Health check endpoint for Render."""
    return {
        "status": "healthy",
        "service": "render-voice-agent",
        "repo_available": os.path.exists(settings.TARGET_REPO_PATH),
    }


@app.post("/twilio/incoming")
async def twilio_incoming(request: Request):
    """
    Twilio webhook for incoming calls.

    Returns TwiML to:
    1. Play a brief connection message
    2. Connect to WebSocket for media streaming
    """
    return await handle_incoming_call(request)


@app.websocket("/twilio/media-stream")
async def twilio_media_stream(websocket: WebSocket):
    """
    WebSocket endpoint for Twilio media streams.

    Handles bidirectional audio streaming between
    Twilio and the Pipecat voice pipeline.
    """
    await handle_media_stream(websocket)


@app.post("/webhooks/render/deploy")
async def render_deploy_webhook(request: Request):
    """
    Render deploy webhook endpoint.

    Receives webhook notifications from Render when deploys complete
    and sends SMS to the caller who triggered the deploy.

    Configure at: https://dashboard.render.com -> Settings -> Webhooks
    Set URL to: https://your-service.onrender.com/webhooks/render/deploy
    """
    payload = await request.json()
    logger.info(f"Received Render webhook: {payload.get('type')}")
    return await handle_render_webhook(payload)


@app.get("/")
async def root():
    """Root endpoint with service info."""
    return {
        "service": "Render Voice Agent",
        "version": "1.0.0",
        "description": "Call the phone number to manage your Render infrastructure with voice commands",
        "endpoints": {
            "health": "/health",
            "twilio_webhook": "/twilio/incoming",
            "media_stream": "/twilio/media-stream (WebSocket)",
            "render_webhook": "/webhooks/render/deploy",
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.APP_ENV == "development",
    )
