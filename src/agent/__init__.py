"""
Claude Agent SDK integration for voice-controlled coding agent.

This module provides:
- VoiceAgentSession: Persistent SDK session for phone calls
- SDK options configuration with Render MCP
- Custom tools for proactive features
"""

from src.agent.sdk_client import (
    VoiceAgentSession,
    get_sdk_options,
    run_agent_query,
)

__all__ = [
    "VoiceAgentSession",
    "get_sdk_options",
    "run_agent_query",
]
