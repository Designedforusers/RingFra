"""
Configuration management using Pydantic settings.

Loads from environment variables with validation.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings."""

    # Twilio
    TWILIO_ACCOUNT_SID: str
    TWILIO_AUTH_TOKEN: str
    TWILIO_PHONE_NUMBER: str

    # AI Services
    ANTHROPIC_API_KEY: str
    DEEPGRAM_API_KEY: str
    CARTESIA_API_KEY: str

    # Render
    RENDER_API_KEY: str
    RENDER_MCP_URL: str = "https://mcp.render.com/mcp"

    # GitHub
    GITHUB_TOKEN: str
    GITHUB_REPO_URL: str

    # Application
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    TARGET_REPO_PATH: str = "/app/target-repo"

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8765

    # Voice Pipeline
    VOICE_MODEL: str = "claude-sonnet-4-5-20250929"
    STT_MODEL: str = "nova-3"
    TTS_VOICE: str = "228fca29-3a0a-435c-8728-5cb483251068"  # Cartesia "Kiefer" - stable male voice for agents

    # Observability
    SENTRY_DSN: str | None = None  # Optional: Sentry error tracking
    LANGFUSE_PUBLIC_KEY: str | None = None  # Optional: LLM observability
    LANGFUSE_SECRET_KEY: str | None = None
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
