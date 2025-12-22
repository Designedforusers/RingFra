"""
Code operation tools using Claude Agent SDK.

These tools provide full Claude Code capabilities with native MCP support:
- Reading/understanding code
- Writing fixes and features
- Running tests
- Git operations
- Render infrastructure management via MCP
"""

import os

from claude_agent_sdk import query, ClaudeAgentOptions
from loguru import logger

from src.config import settings


# Thread-local storage for current user context
_current_user_context: dict | None = None


def set_current_user_context(context: dict | None) -> None:
    """Set the current user context for code tools."""
    global _current_user_context
    _current_user_context = context


def get_current_user_context() -> dict | None:
    """Get the current user context."""
    return _current_user_context


def get_agent_options(
    allowed_tools: list[str] | None = None,
    permission_mode: str = "default",
    include_render_mcp: bool = True,
    repo_path: str | None = None,
    render_api_key: str | None = None,
) -> ClaudeAgentOptions:
    """
    Build Claude Agent SDK options with MCP servers configured.

    Args:
        allowed_tools: List of allowed tools
        permission_mode: Permission mode (default, acceptEdits, bypassPermissions)
        include_render_mcp: Whether to include Render MCP server
        repo_path: Custom repo path (for multi-tenant)
        render_api_key: Custom Render API key (for multi-tenant)

    Returns:
        ClaudeAgentOptions: Configured options for the SDK
    """
    mcp_servers = {}

    # Use user's Render API key if available
    api_key = render_api_key or settings.RENDER_API_KEY

    if include_render_mcp:
        # Render MCP for infrastructure operations
        mcp_servers["render"] = {
            "type": "http",
            "url": "https://mcp.render.com/mcp",
            "headers": {
                "Authorization": f"Bearer {api_key}"
            }
        }

    # Use provided repo path, or user's repo, or default
    if repo_path:
        working_dir = repo_path
    elif os.path.exists(settings.TARGET_REPO_PATH):
        working_dir = settings.TARGET_REPO_PATH
    else:
        working_dir = os.getcwd()

    return ClaudeAgentOptions(
        cwd=working_dir,
        allowed_tools=allowed_tools,
        permission_mode=permission_mode,
        mcp_servers=mcp_servers if mcp_servers else None,
    )


async def run_agent(prompt: str, options: ClaudeAgentOptions) -> str:
    """
    Run Claude Agent SDK query and collect the final result.

    Args:
        prompt: The prompt to send
        options: ClaudeAgentOptions for the agent

    Returns:
        str: The final text result from the agent
    """
    result_text = ""

    try:
        async for message in query(prompt=prompt, options=options):
            # Handle dict-style messages
            if isinstance(message, dict):
                msg_type = message.get("type")
                msg_subtype = message.get("subtype")

                if msg_type == "assistant":
                    content = message.get("content")
                    if content and isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                result_text = block.get("text", "")

                elif msg_type == "result":
                    if msg_subtype == "success":
                        result = message.get("result")
                        if result:
                            result_text = str(result)
                    elif msg_subtype in ("error", "error_during_execution"):
                        error = message.get("error")
                        result_text = f"Error: {error}" if error else "An error occurred"

                elif msg_type == "system" and msg_subtype == "init":
                    mcp_servers = message.get("mcp_servers", [])
                    for server in mcp_servers:
                        if server.get("status") != "connected":
                            logger.warning(f"MCP server {server.get('name')} not connected: {server.get('status')}")

            # Handle object-style messages
            else:
                msg_type = getattr(message, "type", None)
                msg_subtype = getattr(message, "subtype", None)

                if msg_type == "assistant":
                    content = getattr(message, "content", None)
                    if content and isinstance(content, list):
                        for block in content:
                            if hasattr(block, "type") and block.type == "text":
                                result_text = getattr(block, "text", "")

                elif msg_type == "result":
                    if msg_subtype == "success":
                        result = getattr(message, "result", None)
                        if result:
                            result_text = str(result)
                    elif msg_subtype in ("error", "error_during_execution"):
                        error = getattr(message, "error", None)
                        result_text = f"Error: {error}" if error else "An error occurred"

                elif msg_type == "system" and msg_subtype == "init":
                    mcp_servers = getattr(message, "mcp_servers", [])
                    for server in mcp_servers:
                        status = server.get("status") if isinstance(server, dict) else getattr(server, "status", None)
                        name = server.get("name") if isinstance(server, dict) else getattr(server, "name", None)
                        if status != "connected":
                            logger.warning(f"MCP server {name} not connected: {status}")

    except Exception as e:
        logger.error(f"Agent execution error: {e}")
        result_text = f"Error during execution: {str(e)}"

    return result_text


async def analyze_code(query_text: str) -> str:
    """
    Analyze the codebase to understand or find specific patterns.

    Args:
        query_text: What to analyze (e.g., "authentication flow")

    Returns:
        str: Analysis summary
    """
    logger.info(f"Analyzing code: {query_text}")

    prompt = f"""Analyze this codebase and answer: {query_text}

Be concise - this will be spoken aloud. Focus on:
1. The key files/functions involved
2. How they work together
3. Any issues or concerns you notice

Keep your response under 100 words."""

    options = get_agent_options(
        allowed_tools=["Read", "Glob", "Grep"],
        permission_mode="bypassPermissions",
        include_render_mcp=False,
    )

    result = await run_agent(prompt, options)

    # Truncate for voice
    if len(result) > 300:
        result = result[:297] + "..."

    return result or "I couldn't find relevant information for that query."


async def fix_bug(
    description: str, auto_commit: bool = False, run_tests: bool = True
) -> str:
    """
    Find and fix a bug in the codebase.

    Args:
        description: Description of the bug
        auto_commit: Whether to commit the fix automatically
        run_tests: Whether to run tests after fixing

    Returns:
        str: Summary of what was fixed
    """
    logger.info(f"Fixing bug: {description}")

    test_instruction = "4. Run the test suite to verify the fix" if run_tests else ""
    commit_instruction = (
        f'5. Commit with message "fix: {description[:50]}"' if auto_commit else ""
    )

    prompt = f"""Fix this bug: {description}

Steps:
1. Search the codebase to find the relevant code
2. Understand the bug and its cause
3. Write a fix
{test_instruction}
{commit_instruction}

Be concise in your final response - it will be spoken aloud.
Summarize: what was the bug, what did you change, did tests pass?
Keep under 75 words."""

    tools = ["Read", "Edit", "Write", "Glob", "Grep"]
    if run_tests:
        tools.append("Bash")

    options = get_agent_options(
        allowed_tools=tools,
        permission_mode="acceptEdits",
        include_render_mcp=False,
    )

    result = await run_agent(prompt, options)

    # Truncate for voice
    if len(result) > 250:
        result = result[:247] + "..."

    return result or "I encountered an issue while trying to fix the bug."


async def implement_feature(description: str, auto_commit: bool = False) -> str:
    """
    Implement a new feature in the codebase.

    Args:
        description: Description of the feature
        auto_commit: Whether to commit automatically

    Returns:
        str: Summary of what was implemented
    """
    logger.info(f"Implementing feature: {description}")

    prompt = f"""Implement this feature: {description}

Steps:
1. Understand the existing code structure
2. Plan the implementation
3. Write the code
4. Add appropriate tests
5. Run tests to verify
{"6. Commit the changes" if auto_commit else ""}

Be concise in your final response - it will be spoken aloud.
Summarize: what files did you create/modify, what does the feature do?
Keep under 75 words."""

    options = get_agent_options(
        allowed_tools=["Read", "Edit", "Write", "Glob", "Grep", "Bash"],
        permission_mode="acceptEdits",
        include_render_mcp=False,
    )

    result = await run_agent(prompt, options)

    # Truncate for voice
    if len(result) > 250:
        result = result[:247] + "..."

    return result or "I encountered an issue while implementing the feature."


async def run_tests(test_path: str | None = None) -> str:
    """
    Run the test suite.

    Args:
        test_path: Optional specific test path

    Returns:
        str: Test results summary
    """
    logger.info(f"Running tests: {test_path or 'all'}")

    test_cmd = f"pytest {test_path}" if test_path else "pytest"

    prompt = f"""Run the tests with: {test_cmd}

Report concisely:
- How many tests passed/failed
- If any failed, which ones and why (briefly)

Keep under 50 words."""

    options = get_agent_options(
        allowed_tools=["Bash", "Read"],
        permission_mode="bypassPermissions",
        include_render_mcp=False,
    )

    result = await run_agent(prompt, options)

    # Truncate for voice
    if len(result) > 150:
        result = result[:147] + "..."

    return result or "Tests completed."


async def commit_and_push(message: str) -> str:
    """
    Commit changes and push to remote.

    Args:
        message: Commit message

    Returns:
        str: Result of git operations
    """
    logger.info(f"Committing: {message}")

    prompt = f"""Commit and push the current changes:

1. Stage all changes: git add -A
2. Commit with message: "{message}"
3. Push to origin

Report briefly: committed X files, pushed to origin.
Keep under 25 words."""

    options = get_agent_options(
        allowed_tools=["Bash"],
        permission_mode="bypassPermissions",
        include_render_mcp=False,
    )

    result = await run_agent(prompt, options)

    return result or "Changes committed and pushed."


async def trigger_pr_review(
    pr_number: int,
    model: str = "claude-sonnet-4-20250514",
    effort: str = "medium",
) -> str:
    """
    Trigger Claude Code Action to review a PR.
    
    Uses the current user context for credentials.
    
    Args:
        pr_number: PR number to review
        model: Claude model (sonnet or opus)
        effort: Review effort (low/medium/high)
        
    Returns:
        Status message
    """
    from src.github.actions import trigger_claude_review, parse_repo_url
    
    user_ctx = get_current_user_context()
    if not user_ctx:
        return "No user context - can't trigger review"
    
    repos = user_ctx.get("repos", [])
    if not repos:
        return "No repos configured"
    
    creds = user_ctx.get("credentials", {}).get("github", {})
    token = creds.get("access_token")
    if not token:
        return "No GitHub token"
    
    # Use first repo
    repo_url = repos[0].get("github_url", "")
    try:
        owner, repo = parse_repo_url(repo_url)
    except ValueError:
        return f"Invalid repo URL: {repo_url}"
    
    result = await trigger_claude_review(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        github_token=token,
        model=model,
        effort=effort,
    )
    
    if result:
        run_id = result.get("id")
        return f"Started Claude review for PR #{pr_number}. Workflow run: {run_id}"
    else:
        return "Failed to trigger review. Make sure claude-code-action.yml workflow exists."


async def trigger_test_run(branch: str = "main") -> str:
    """
    Trigger test workflow on GitHub Actions.
    
    Args:
        branch: Branch to test
        
    Returns:
        Status message
    """
    from src.github.actions import trigger_tests, parse_repo_url
    
    user_ctx = get_current_user_context()
    if not user_ctx:
        return "No user context - can't trigger tests"
    
    repos = user_ctx.get("repos", [])
    if not repos:
        return "No repos configured"
    
    creds = user_ctx.get("credentials", {}).get("github", {})
    token = creds.get("access_token")
    if not token:
        return "No GitHub token"
    
    repo_url = repos[0].get("github_url", "")
    try:
        owner, repo = parse_repo_url(repo_url)
    except ValueError:
        return f"Invalid repo URL: {repo_url}"
    
    result = await trigger_tests(
        owner=owner,
        repo=repo,
        branch=branch,
        github_token=token,
    )
    
    if result:
        run_id = result.get("id")
        return f"Started tests on branch {branch}. Workflow run: {run_id}"
    else:
        return "Failed to trigger tests. Make sure test.yml workflow exists."


async def check_workflow_status(run_id: int) -> str:
    """
    Check the status of a GitHub Actions workflow run.
    
    Args:
        run_id: Workflow run ID
        
    Returns:
        Status message
    """
    from src.github.actions import get_workflow_status, parse_repo_url
    
    user_ctx = get_current_user_context()
    if not user_ctx:
        return "No user context"
    
    repos = user_ctx.get("repos", [])
    if not repos:
        return "No repos configured"
    
    creds = user_ctx.get("credentials", {}).get("github", {})
    token = creds.get("access_token")
    if not token:
        return "No GitHub token"
    
    repo_url = repos[0].get("github_url", "")
    try:
        owner, repo = parse_repo_url(repo_url)
    except ValueError:
        return f"Invalid repo URL: {repo_url}"
    
    status = await get_workflow_status(owner, repo, run_id, token)
    
    if status:
        state = status.get("status", "unknown")
        conclusion = status.get("conclusion")
        name = status.get("name", "Workflow")
        
        if conclusion:
            return f"{name}: {conclusion}"
        else:
            return f"{name}: {state} (in progress)"
    else:
        return f"Couldn't get status for run {run_id}"
