"""
Error handling utilities for the voice agent.
"""

from typing import Any


class VoiceAgentError(Exception):
    """Base exception for voice agent errors."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_voice_response(self) -> str:
        """Convert error to a voice-friendly message."""
        return f"I encountered an issue: {self.message}"


class ToolExecutionError(VoiceAgentError):
    """Error during tool execution."""

    def __init__(self, tool_name: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message, details)
        self.tool_name = tool_name

    def to_voice_response(self) -> str:
        return f"I couldn't complete the {self.tool_name} operation: {self.message}"


class RenderAPIError(VoiceAgentError):
    """Error communicating with Render API."""

    def to_voice_response(self) -> str:
        return f"I had trouble connecting to Render: {self.message}"


class CodeOperationError(VoiceAgentError):
    """Error during code operations."""

    def to_voice_response(self) -> str:
        return f"I ran into a code issue: {self.message}"


def format_error_for_voice(error: Exception) -> str:
    """
    Format any exception for voice output.

    Args:
        error: The exception to format

    Returns:
        str: Voice-friendly error message
    """
    if isinstance(error, VoiceAgentError):
        return error.to_voice_response()

    # Generic error handling
    error_msg = str(error)
    if len(error_msg) > 100:
        error_msg = error_msg[:100] + "..."

    return f"Something went wrong: {error_msg}"
