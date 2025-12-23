"""
Claude Agent SDK client for voice-controlled coding agent.

This is the brain of the system - a persistent ClaudeSDKClient session
that maintains conversation context throughout a phone call.

Architecture:
    Phone Call -> Pipecat (STT/TTS) -> ClaudeSDKClient (persistent) -> Tools
                                              |
                                              +-> Render MCP (infrastructure)
                                              +-> Code tools (Read, Write, Edit, Bash, Glob, Grep)
                                              +-> Custom tools (callbacks, reminders)

The SDK handles:
- Context compression automatically (around 50k tokens)
- Tool execution loop (Claude calls tool -> sees result -> decides next action)
- MCP server connections
- File checkpointing for rewinding changes
"""

import asyncio
from pathlib import Path
from typing import AsyncIterator, Callable, Any
from uuid import UUID

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    tool,
    create_sdk_mcp_server,
)
from loguru import logger

from src.config import settings


# Custom tools for proactive features
@tool("schedule_callback", "Schedule a callback to the user when a task completes", {
    "task_description": str,
    "notify_on": str,  # "success", "failure", "both"
})
async def schedule_callback_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Schedule a callback when async task completes."""
    from src.tasks.worker import schedule_callback
    
    task_id = await schedule_callback(
        task_description=args["task_description"],
        notify_on=args.get("notify_on", "both"),
    )
    
    return {
        "content": [{
            "type": "text",
            "text": f"Callback scheduled. I'll call you back when the task is done. Task ID: {task_id}"
        }]
    }


@tool("send_sms", "Send an SMS notification to the user", {
    "message": str,
})
async def send_sms_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Send SMS to user."""
    from src.notifications import send_sms
    
    # Phone is injected by the session manager
    phone = args.get("_caller_phone")
    if not phone:
        return {
            "content": [{"type": "text", "text": "No phone number available for SMS"}],
            "is_error": True,
        }
    
    success = await send_sms(phone, args["message"])
    
    return {
        "content": [{
            "type": "text",
            "text": "SMS sent successfully" if success else "Failed to send SMS"
        }]
    }


@tool("set_reminder", "Set a reminder to check on something later", {
    "message": str,
    "delay_minutes": int,
})
async def set_reminder_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Set a reminder that triggers a callback."""
    from src.tasks.worker import schedule_reminder
    
    reminder_id = await schedule_reminder(
        message=args["message"],
        delay_minutes=args["delay_minutes"],
    )
    
    return {
        "content": [{
            "type": "text",
            "text": f"Reminder set for {args['delay_minutes']} minutes from now."
        }]
    }


def get_sdk_options(
    user_context: dict | None = None,
    cwd: Path | None = None,
) -> ClaudeAgentOptions:
    """
    Build ClaudeAgentOptions for the voice agent session.
    
    Configured to match Claude Code as closely as possible:
    - All standard tools enabled
    - Render MCP for infrastructure
    - Full autonomy (bypassPermissions)
    - Custom tools for proactive features
    """
    
    # Determine working directory
    if cwd:
        working_dir = str(cwd)
    elif user_context and user_context.get("repos"):
        # Use first repo's local path if available
        repos = user_context.get("repos", [])
        if repos and repos[0].get("local_path"):
            working_dir = repos[0]["local_path"]
        else:
            working_dir = "/app"
    else:
        working_dir = "/app"
    
    # Get user's Render API key if available (multi-tenant)
    render_api_key = settings.RENDER_API_KEY
    if user_context:
        creds = user_context.get("credentials", {})
        render_creds = creds.get("render", {})
        if render_creds.get("api_key"):
            render_api_key = render_creds["api_key"]
    
    # Create custom MCP server for proactive tools
    proactive_server = create_sdk_mcp_server(
        name="proactive",
        version="1.0.0",
        tools=[schedule_callback_tool, send_sms_tool, set_reminder_tool],
    )
    
    # Build system prompt
    system_prompt = _build_system_prompt(user_context)
    
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
        # Custom proactive tools
        "proactive": proactive_server,
    }
    
    return ClaudeAgentOptions(
        # Working directory for file operations
        cwd=working_dir,
        
        # System prompt with user context
        system_prompt=system_prompt,
        
        # MCP servers
        mcp_servers=mcp_servers,
        
        # Full autonomy - accept all edits automatically
        permission_mode="bypassPermissions",
        
        # Allow all standard Claude Code tools + MCP tools
        allowed_tools=[
            # File operations
            "Read",
            "Write",
            "Edit",
            "Glob",
            "Grep",
            "NotebookEdit",
            
            # Execution
            "Bash",
            
            # Task management
            "Task",
            "TodoWrite",
            
            # Web
            "WebFetch",
            "WebSearch",
            
            # MCP - Render (all tools)
            "mcp__render__list_services",
            "mcp__render__get_service",
            "mcp__render__list_deploys",
            "mcp__render__get_deploy",
            "mcp__render__list_logs",
            "mcp__render__list_log_label_values",
            "mcp__render__get_metrics",
            "mcp__render__list_workspaces",
            "mcp__render__select_workspace",
            "mcp__render__get_selected_workspace",
            "mcp__render__create_web_service",
            "mcp__render__create_static_site",
            "mcp__render__update_environment_variables",
            "mcp__render__list_postgres_instances",
            "mcp__render__get_postgres",
            "mcp__render__create_postgres",
            "mcp__render__query_render_postgres",
            "mcp__render__list_key_value",
            "mcp__render__get_key_value",
            "mcp__render__create_key_value",
            
            # MCP - Proactive (custom tools)
            "mcp__proactive__schedule_callback",
            "mcp__proactive__send_sms",
            "mcp__proactive__set_reminder",
        ],
        
        # Enable partial message streaming for TTS
        include_partial_messages=True,
        
        # Enable file checkpointing so we can rewind changes
        # enable_file_checkpointing=True,  # Uncomment when needed
    )


def _build_system_prompt(user_context: dict | None) -> str:
    """Build the system prompt with user context."""
    
    base_prompt = """You are an expert on-call engineer accessible via phone. You help users manage their code and infrastructure through voice commands.

## Your Capabilities
- **Code**: Read, write, edit files. Run bash commands. Full access to the user's repositories.
- **Git**: Commit, push, create branches, create PRs.
- **Infrastructure**: Manage Render services via MCP (deploy, logs, metrics, databases).
- **Proactive**: Schedule callbacks ("fix this and call me back"), send SMS updates, set reminders.

## Voice Interaction Guidelines
- Keep responses concise - they will be spoken aloud
- Confirm actions before executing destructive operations
- Provide progress updates during long operations
- When multiple options exist, ask the user to choose
- If workspace selection is needed, ask which workspace to use

## Workflow Patterns
- "Check the logs" → Use Render MCP to fetch logs, summarize errors
- "Fix the bug" → Analyze code, make edits, run tests, commit
- "Deploy it" → Push changes, trigger deploy via Render MCP
- "Ship it" → Full workflow: test, push, PR, deploy
- "Call me back when done" → Schedule callback, continue working

## Important
- You have full autonomy to edit files and run commands
- Always run tests before pushing if a test command exists
- Use conventional commits (fix:, feat:, etc.)
- Create PRs for significant changes
"""
    
    if not user_context:
        return base_prompt
    
    # Add user-specific context
    context_parts = [base_prompt, "\n## User Context\n"]
    
    # User info
    user = user_context.get("user", {})
    if user.get("phone"):
        context_parts.append(f"**Caller**: {user.get('phone')}\n")
    
    # Repositories
    repos = user_context.get("repos", [])
    if repos:
        context_parts.append("\n**Connected Repositories**:\n")
        for repo in repos:
            url = repo.get("github_url", "")
            branch = repo.get("default_branch", "main")
            path = repo.get("local_path", "")
            context_parts.append(f"- {url} (branch: {branch})")
            if path:
                context_parts.append(f" at {path}")
            context_parts.append("\n")
    
    # Previous conversation summary
    memory = user_context.get("memory", {})
    if memory.get("summary"):
        context_parts.append(f"\n**Previous Conversation**:\n{memory.get('summary')}\n")
    
    return "".join(context_parts)


class VoiceAgentSession:
    """
    Manages a Claude Agent SDK session for a phone call.
    
    This wraps ClaudeSDKClient and handles:
    - Session lifecycle (connect/disconnect)
    - Message streaming for TTS
    - Interrupt handling
    - User context injection
    
    Usage:
        async with VoiceAgentSession(user_context) as session:
            async for text in session.query("check the logs"):
                # Stream text to TTS
                await tts.speak(text)
    """
    
    def __init__(
        self,
        user_context: dict | None = None,
        cwd: Path | None = None,
        caller_phone: str | None = None,
    ):
        self.user_context = user_context
        self.cwd = cwd
        self.caller_phone = caller_phone
        self.options = get_sdk_options(user_context, cwd)
        self.client: ClaudeSDKClient | None = None
        self._connected = False
    
    async def connect(self) -> None:
        """Connect to Claude Agent SDK."""
        if self._connected:
            return
        
        self.client = ClaudeSDKClient(self.options)
        await self.client.connect()
        self._connected = True
        logger.info("VoiceAgentSession connected")
    
    async def disconnect(self) -> None:
        """Disconnect from Claude Agent SDK."""
        if self.client and self._connected:
            await self.client.disconnect()
            self._connected = False
            logger.info("VoiceAgentSession disconnected")
    
    async def __aenter__(self) -> "VoiceAgentSession":
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()
    
    async def query(self, prompt: str) -> AsyncIterator[str]:
        """
        Send a query and yield text responses for TTS.
        
        This handles the full agent loop - Claude may call tools,
        get results, and continue. We yield text as it arrives
        for streaming to TTS.
        
        Args:
            prompt: User's voice input (transcribed)
            
        Yields:
            Text chunks to be spoken back to user
        """
        if not self.client or not self._connected:
            raise RuntimeError("Session not connected")
        
        logger.info(f"VoiceAgentSession query: {prompt[:100]}...")
        
        await self.client.query(prompt)
        
        async for message in self.client.receive_response():
            # Extract text from AssistantMessage
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text = block.text.strip()
                        if text:
                            yield text
                    elif isinstance(block, ToolUseBlock):
                        # Optionally announce tool usage
                        tool_name = block.name
                        if tool_name.startswith("mcp__render__"):
                            # Don't announce every MCP call, too noisy
                            pass
                        else:
                            logger.debug(f"Tool called: {tool_name}")
            
            # ResultMessage indicates completion
            elif isinstance(message, ResultMessage):
                if message.is_error:
                    yield f"I encountered an error: {message.result or 'Unknown error'}"
                logger.info(f"Query completed. Turns: {message.num_turns}, Cost: ${message.total_cost_usd or 0:.4f}")
    
    async def interrupt(self) -> None:
        """Interrupt the current operation."""
        if self.client and self._connected:
            await self.client.interrupt()
            logger.info("VoiceAgentSession interrupted")


# Convenience function for simple one-off queries (testing)
async def run_agent_query(prompt: str, user_context: dict | None = None) -> str:
    """Run a single query and return the full response."""
    async with VoiceAgentSession(user_context) as session:
        responses = []
        async for text in session.query(prompt):
            responses.append(text)
        return " ".join(responses)
