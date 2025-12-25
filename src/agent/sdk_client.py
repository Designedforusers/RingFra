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


# === Session Context (for tools to access) ===
# Uses contextvars for async-safe, per-task isolation.
# This allows multiple concurrent calls without context bleeding.
from contextvars import ContextVar

_session_context_var: ContextVar[dict] = ContextVar('session_context', default={})


def _set_session_context(user_context: dict | None, caller_phone: str | None) -> None:
    """Set the session context for tools to access (async-safe)."""
    ctx = {
        "user_context": user_context or {},
        "caller_phone": caller_phone,
        "github_token": settings.GITHUB_TOKEN,
        "task_context": {},  # Stores worktree info between tool calls
    }
    
    # Override with user-specific credentials if available
    if user_context:
        creds = user_context.get("credentials", {})
        github_creds = creds.get("github", {})
        if github_creds.get("access_token"):
            ctx["github_token"] = github_creds["access_token"]
    
    _session_context_var.set(ctx)


def _get_session_context() -> dict:
    """Get the current session context (async-safe)."""
    return _session_context_var.get()


def _update_task_context(task_ctx: dict) -> None:
    """Update the task context (worktree info, branch, etc.)."""
    ctx = _session_context_var.get()
    if ctx:
        ctx["task_context"] = task_ctx
        _session_context_var.set(ctx)


# Custom tools for proactive features
@tool("schedule_callback", "Execute a background task and call back when done. Use for 'fix X and call me back' or 'deploy and let me know'. NOT for simple timed callbacks - use set_reminder for those.", {
    "task_description": str,
    "notify_on": str,  # "success", "failure", "both"
})
async def schedule_callback_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Schedule a callback when async task completes."""
    from src.tasks.queue import enqueue_task_with_callback, RedisUnavailableError
    from src.callbacks.outbound import send_sms
    
    ctx = _get_session_context()
    phone = ctx.get("caller_phone")
    if not phone:
        return {
            "content": [{"type": "text", "text": "No phone number available for callback"}],
            "is_error": True,
        }
    
    try:
        task_id = await enqueue_task_with_callback(
            task_type=args.get("task_description", "task"),
            params={"notify_on": args.get("notify_on", "both")},
            phone=phone,
        )
        return {
            "content": [{
                "type": "text",
                "text": f"Callback scheduled. I'll call you back when the task is done. Task ID: {task_id}"
            }]
        }
    except RedisUnavailableError:
        # Graceful degradation: notify user via SMS
        await send_sms(phone, f"[PhoneFix] Sorry, I couldn't schedule your callback for '{args.get('task_description', 'task')}'. The background service is temporarily unavailable. Please try again later or call back.")
        return {
            "content": [{
                "type": "text",
                "text": "I couldn't schedule the callback right now - the background service is temporarily unavailable. I've sent you an SMS to let you know. Please try again in a few minutes."
            }],
            "is_error": True,
        }


@tool("send_sms", "Send an SMS notification to the user", {
    "message": str,
})
async def send_sms_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Send SMS to user."""
    from src.notifications import send_sms
    
    # Get phone from session context
    ctx = _get_session_context()
    phone = ctx.get("caller_phone")
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


@tool("set_reminder", "Call the user back after a delay. Use for 'call me back in X minutes' or 'remind me in an hour'. This is a TIMED callback - the call happens after the specified delay.", {
    "message": str,
    "delay_minutes": int,
})
async def set_reminder_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Set a reminder that triggers a callback."""
    from src.tasks.queue import enqueue_reminder, RedisUnavailableError
    from src.callbacks.outbound import send_sms
    
    ctx = _get_session_context()
    phone = ctx.get("caller_phone")
    if not phone:
        return {
            "content": [{"type": "text", "text": "No phone number available for reminder"}],
            "is_error": True,
        }
    
    delay_seconds = args["delay_minutes"] * 60
    
    try:
        reminder_id = await enqueue_reminder(
            phone=phone,
            message=args["message"],
            delay_seconds=delay_seconds,
        )
        return {
            "content": [{
                "type": "text",
                "text": f"Reminder set for {args['delay_minutes']} minutes from now."
            }]
        }
    except RedisUnavailableError:
        # Graceful degradation: notify user via SMS
        await send_sms(phone, f"[PhoneFix] Sorry, I couldn't set your reminder for {args['delay_minutes']} minutes. The background service is temporarily unavailable. Please set a manual reminder or call back later.")
        return {
            "content": [{
                "type": "text",
                "text": f"I couldn't set the reminder right now - the background service is temporarily unavailable. I've sent you an SMS about it. Please try again in a few minutes."
            }],
            "is_error": True,
        }


# === Repo Management Tools (Production mode only) ===

@tool("setup_repo_for_task", "Clone a repo and create an isolated worktree for a task. Use this before making code changes.", {
    "repo_url": str,  # GitHub repo URL (can get from Render service)
    "task_description": str,  # Brief description like "fix auth bug"
})
async def setup_repo_for_task_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Set up a repo with isolated worktree for a task."""
    from src.repos.manager import create_task_worktree, CommitType
    from uuid import uuid4
    
    repo_url = args["repo_url"]
    task_desc = args.get("task_description", "task")
    
    # Get context
    ctx = _get_session_context()
    github_token = ctx.get("github_token")
    if not github_token:
        return {
            "content": [{"type": "text", "text": "No GitHub token available"}],
            "is_error": True,
        }
    
    # Use user_id from context or generate one
    user_context = ctx.get("user_context", {})
    user_id = user_context.get("user_id") or uuid4()
    
    try:
        # Determine commit type from task description
        desc_lower = task_desc.lower()
        if "fix" in desc_lower or "bug" in desc_lower:
            commit_type = CommitType.FIX
        elif "feature" in desc_lower or "add" in desc_lower:
            commit_type = CommitType.FEAT
        elif "refactor" in desc_lower:
            commit_type = CommitType.REFACTOR
        else:
            commit_type = CommitType.FIX
        
        # Create worktree
        worktree_path, branch_name = await create_task_worktree(
            user_id=user_id,
            repo_url=repo_url,
            github_token=github_token,
            commit_type=commit_type,
        )
        
        # Store task context for later tools (ship_changes, cleanup)
        _update_task_context({
            "worktree_path": str(worktree_path),
            "branch_name": branch_name,
            "repo_url": repo_url,
            "user_id": str(user_id),
        })
        
        return {
            "content": [{
                "type": "text",
                "text": f"Repo ready at {worktree_path} on branch {branch_name}. You can now make changes."
            }],
        }
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Failed to set up repo: {e}"}],
            "is_error": True,
        }


@tool("ship_changes", "Push changes and create a PR. Use after making code changes.", {
    "title": str,  # PR title
    "description": str,  # PR description (optional)
    "run_tests": bool,  # Whether to run tests first (default: True)
})
async def ship_changes_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Ship changes: commit, push, create PR."""
    from src.repos.manager import ship_changes, TestStrategy
    from pathlib import Path
    
    # Get task context from session
    ctx = _get_session_context()
    task_ctx = ctx.get("task_context", {})
    worktree_path = task_ctx.get("worktree_path")
    branch_name = task_ctx.get("branch_name")
    repo_url = task_ctx.get("repo_url")
    
    if not all([worktree_path, branch_name, repo_url]):
        return {
            "content": [{"type": "text", "text": "No active task. Use setup_repo_for_task first."}],
            "is_error": True,
        }
    
    github_token = ctx.get("github_token")
    if not github_token:
        return {
            "content": [{"type": "text", "text": "No GitHub token available"}],
            "is_error": True,
        }
    
    test_strategy = TestStrategy.LOCAL if args.get("run_tests", True) else TestStrategy.NONE
    
    try:
        result = await ship_changes(
            worktree_path=Path(worktree_path),
            repo_url=repo_url,
            branch_name=branch_name,
            github_token=github_token,
            title=args["title"],
            description=args.get("description", ""),
            test_strategy=test_strategy,
        )
        
        if result.success:
            return {
                "content": [{"type": "text", "text": result.message}],
            }
        else:
            return {
                "content": [{"type": "text", "text": f"Ship failed: {result.message}"}],
                "is_error": True,
            }
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Failed to ship: {e}"}],
            "is_error": True,
        }


@tool("cleanup_task", "Clean up worktree after task is complete.", {})
async def cleanup_task_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Clean up worktree after task completion."""
    from src.repos.manager import cleanup_worktree
    from pathlib import Path
    
    ctx = _get_session_context()
    task_ctx = ctx.get("task_context", {})
    worktree_path = task_ctx.get("worktree_path")
    
    if not worktree_path:
        return {
            "content": [{"type": "text", "text": "No active task to clean up."}],
        }
    
    try:
        await cleanup_worktree(Path(worktree_path))
        # Clear task context
        _update_task_context({})
        return {
            "content": [{"type": "text", "text": "Task cleaned up successfully."}],
        }
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Cleanup warning: {e}"}],
        }


@tool("update_user_memory", "Update the user's CLAUDE.md with important information to remember. Use this when you learn user preferences, project patterns, or important context that should persist. Be concise - add bullet points, not paragraphs.", {
    "section": str,  # Section header like "Preferences", "Projects", "Workflows"
    "content": str,  # Content to add (will be appended as bullet points)
})
async def update_user_memory_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Update user's CLAUDE.md file with new information."""
    from pathlib import Path
    import aiofiles
    
    ctx = _get_session_context()
    user_context = ctx.get("user_context", {})
    
    # Get working directory (user's repo)
    repos = user_context.get("repos", [])
    if not repos or not repos[0].get("local_path"):
        return {
            "content": [{"type": "text", "text": "No user repo configured - memory not saved to file."}],
        }
    
    repo_path = Path(repos[0]["local_path"])
    claude_md_path = repo_path / "CLAUDE.md"
    
    section = args["section"]
    new_content = args["content"]
    
    try:
        # Read existing content or start fresh
        if claude_md_path.exists():
            async with aiofiles.open(claude_md_path, "r") as f:
                existing = await f.read()
        else:
            existing = "# User Memory\n\nThis file is automatically updated by your voice agent.\n"
        
        # Check if section exists
        section_header = f"## {section}"
        if section_header in existing:
            # Append to existing section (before next ## or end of file)
            lines = existing.split("\n")
            new_lines = []
            in_section = False
            content_added = False
            
            for line in lines:
                if line.strip() == section_header:
                    in_section = True
                    new_lines.append(line)
                elif line.startswith("## ") and in_section:
                    # End of our section, add content before next section
                    if not content_added:
                        new_lines.append(f"- {new_content}")
                        content_added = True
                    in_section = False
                    new_lines.append(line)
                else:
                    new_lines.append(line)
            
            # If section was last, add at end
            if in_section and not content_added:
                new_lines.append(f"- {new_content}")
            
            updated = "\n".join(new_lines)
        else:
            # Add new section at end
            updated = existing.rstrip() + f"\n\n{section_header}\n- {new_content}\n"
        
        # Write back
        async with aiofiles.open(claude_md_path, "w") as f:
            await f.write(updated)
        
        return {
            "content": [{"type": "text", "text": f"Memory updated: added to {section}"}],
        }
    except Exception as e:
        logger.error(f"Failed to update CLAUDE.md: {e}")
        return {
            "content": [{"type": "text", "text": f"Couldn't save to memory file: {e}"}],
            "is_error": True,
        }


def get_sdk_options(
    user_context: dict | None = None,
    cwd: Path | None = None,
    zep_context: str | None = None,
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
    
    # Get user's credentials if available (multi-tenant)
    render_api_key = settings.RENDER_API_KEY
    github_token = settings.GITHUB_TOKEN or ""
    
    if user_context:
        creds = user_context.get("credentials", {})
        # Render API key
        render_creds = creds.get("render", {})
        if render_creds.get("api_key"):
            render_api_key = render_creds["api_key"]
        # GitHub token
        github_creds = creds.get("github", {})
        if github_creds.get("access_token"):
            github_token = github_creds["access_token"]
    
    # Create custom MCP server for proactive tools
    proactive_tools = [
        schedule_callback_tool,
        send_sms_tool,
        set_reminder_tool,
        update_user_memory_tool,  # Always available for memory persistence
    ]
    
    # Add repo management tools in multi-tenant (production) mode
    if settings.MULTI_TENANT:
        proactive_tools.extend([
            setup_repo_for_task_tool,
            ship_changes_tool,
            cleanup_task_tool,
        ])
    
    proactive_server = create_sdk_mcp_server(
        name="proactive",
        version="1.0.0",
        tools=proactive_tools,
    )
    
    # Build system prompt with Zep context
    system_prompt = _build_system_prompt(user_context, zep_context)
    
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
        # Exa MCP - web search and code context
        # Tools: web_search_exa, get_code_context_exa
        "exa": {
            "type": "http",
            "url": f"https://mcp.exa.ai/mcp?exaApiKey={settings.EXA_API_KEY}" if settings.EXA_API_KEY else "https://mcp.exa.ai/mcp",
        },
        # Custom proactive tools
        "proactive": proactive_server,
    }
    
    # Environment variables for tools (gh CLI, git, etc.)
    env_vars = {}
    if github_token:
        env_vars["GH_TOKEN"] = github_token
        env_vars["GITHUB_TOKEN"] = github_token  # Some tools use this
    
    return ClaudeAgentOptions(
        # Working directory for file operations
        cwd=working_dir,
        
        # Environment variables (GH_TOKEN for gh CLI)
        env=env_vars,
        
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
            
            # MCP - Exa (web search and code context)
            "mcp__exa__web_search_exa",
            "mcp__exa__get_code_context_exa",
            
            # MCP - Proactive (custom tools)
            "mcp__proactive__schedule_callback",
            "mcp__proactive__send_sms",
            "mcp__proactive__set_reminder",
            "mcp__proactive__update_user_memory",
        ] + (
            # Repo management tools (production mode only)
            [
                "mcp__proactive__setup_repo_for_task",
                "mcp__proactive__ship_changes",
                "mcp__proactive__cleanup_task",
            ] if settings.MULTI_TENANT else []
        ),
        
        # Enable partial message streaming for TTS
        include_partial_messages=True,
        
        # Enable Claude Code filesystem-based configuration
        # This allows SDK to read CLAUDE.md from user's working directory
        setting_sources=["project"],
        
        # Enable file checkpointing so we can rewind changes
        # enable_file_checkpointing=True,  # Uncomment when needed
    )


def _build_system_prompt(user_context: dict | None, zep_context: str | None = None) -> str:
    """Build the system prompt with user context and Zep memory."""
    
    base_prompt = """You are an expert on-call engineer accessible via phone. You help users manage their code and infrastructure through voice commands.

## Your Capabilities
- **Code**: Read, write, edit files. Run ANY bash command. Full shell access.
- **Git + GitHub CLI**: Full git and `gh` CLI access. Create branches, commits, PRs, merge, review - all via bash.
- **Infrastructure**: Manage Render services via MCP (deploy, logs, metrics, databases, env vars).
- **Web Search**: Use Exa MCP for real-time web search (web_search_exa) and code examples (get_code_context_exa).
- **Proactive**: Schedule callbacks ("fix this and call me back"), send SMS updates, set reminders.

## Voice Guidelines
- Keep responses CONCISE - they're spoken aloud
- You have FULL AUTONOMY - just do it, don't ask for confirmation
- Provide brief progress updates on long tasks

## Git/GitHub Workflow (use gh CLI)
```bash
# Create branch and switch
git checkout -b fix/issue-123

# Make changes, then commit
git add -A && git commit -m "fix: resolve issue with X"

# Push and create PR in one command
gh pr create --title "Fix: X" --body "Description" --fill

# Or push first, then create PR
git push -u origin fix/issue-123
gh pr create --fill

# Merge PR when ready
gh pr merge --squash --delete-branch
```

## Common Patterns
- "Check the logs" → Render MCP list_logs
- "Fix the bug" → git checkout -b fix/... → edit → test → gh pr create
- "Ship it" → git push → gh pr create --fill → gh pr merge
- "Deploy" → Render MCP trigger deploy
- "What's using memory?" → Render MCP get_metrics

## Important
- FULL AUTONOMY - run commands directly
- Run tests before pushing: pytest, npm test, go test
- Use conventional commits: fix:, feat:, refactor:
- The `gh` CLI is authenticated and ready to use

## Memory
Update the user's CLAUDE.md (via update_user_memory tool) when you learn:
- User preferences ("prefers concise responses", "always runs lint before commit")
- Project patterns ("uses pytest for tests", "main branch is production")
- Important context ("primary repo is PhoneFix", "uses Render for hosting")
Do this automatically without asking - be concise, use bullet points.
"""
    
    # Add Zep memory context if available (from previous conversations)
    if zep_context:
        base_prompt += f"""
## Memory from Previous Conversations
The following context is from your previous conversations with this user. Use it to provide personalized, context-aware responses:

{zep_context}
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
    - Zep memory context updates
    
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
        self._zep_context: str | None = None  # Zep context block for system prompt
        self.options = get_sdk_options(user_context, cwd)
        self.client: ClaudeSDKClient | None = None
        self._connected = False
    
    def set_initial_zep_context(self, context: str) -> None:
        """Set initial Zep context before connecting."""
        self._zep_context = context
        # Rebuild options with Zep context included
        self.options = get_sdk_options(self.user_context, self.cwd, self._zep_context)
    
    def update_zep_context(self, context: str) -> None:
        """Update Zep context after a turn (for next query)."""
        self._zep_context = context
        # Note: SDK maintains conversation history internally, so we don't need
        # to update options mid-session. The context is used for reference.
    
    async def connect(self) -> None:
        """Connect to Claude Agent SDK."""
        if self._connected:
            return
        
        # Set session context for tools to access
        _set_session_context(self.user_context, self.caller_phone)
        
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
    
    async def compress_and_save_memory(self) -> str | None:
        """
        Compress the conversation and save to database (like /compact).
        
        Call this before disconnecting to persist memory across sessions.
        Uses the same Claude session to generate the summary, ensuring
        full context awareness.
        
        Returns:
            The compressed summary, or None if compression failed
        """
        if not self.client or not self._connected:
            logger.warning("Cannot compress - session not connected")
            return None
        
        # Get user_id from context
        user_id = None
        if self.user_context:
            user_id = self.user_context.get("user_id")
        
        if not user_id:
            logger.debug("No user_id - skipping memory save (anonymous session)")
            return None
        
        logger.info("Compressing conversation for memory persistence...")
        
        # Ask Claude to summarize (uses full conversation context)
        compress_prompt = """Summarize our conversation concisely for future reference. Include:
1. What tasks were completed and their outcomes
2. What's still pending or needs follow-up
3. Key technical decisions made
4. Any user preferences or patterns you noticed (e.g., coding style, preferred tools)

Keep it under 200 words. Be specific about file names, PR numbers, and error messages."""
        
        try:
            await self.client.query(compress_prompt)
            
            summary = ""
            async for message in self.client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            summary = block.text.strip()
            
            if summary:
                # Save to database
                from src.db.memory import update_session_memory
                await update_session_memory(user_id, summary=summary)
                logger.info(f"Session memory saved ({len(summary)} chars)")
                return summary
            else:
                logger.warning("Compression produced empty summary")
                return None
                
        except Exception as e:
            logger.error(f"Failed to compress session: {e}")
            return None


# Convenience function for simple one-off queries (testing)
async def run_agent_query(prompt: str, user_context: dict | None = None) -> str:
    """Run a single query and return the full response."""
    async with VoiceAgentSession(user_context) as session:
        responses = []
        async for text in session.query(prompt):
            responses.append(text)
        return " ".join(responses)
