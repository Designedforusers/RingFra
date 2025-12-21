"""
Tool implementations for the voice agent.

Provides a unified interface for executing tools
from voice commands.
"""

from loguru import logger

from src.tools.code_tools import (
    analyze_code,
    commit_and_push,
    fix_bug,
    implement_feature,
    run_tests,
)
from src.tools.render_tools import (
    get_logs,
    get_metrics,
    list_services,
    rollback_deploy,
    scale_service,
    trigger_deploy,
    update_env_var,
)
from src.tools.utility_tools import get_status

# Tool registry
TOOLS = {
    # Infrastructure
    "list_services": list_services,
    "get_logs": get_logs,
    "get_metrics": get_metrics,
    "scale_service": scale_service,
    "trigger_deploy": trigger_deploy,
    "rollback_deploy": rollback_deploy,
    "update_env_var": update_env_var,
    # Code
    "analyze_code": analyze_code,
    "fix_bug": fix_bug,
    "implement_feature": implement_feature,
    "run_tests": run_tests,
    "commit_and_push": commit_and_push,
    # Utility
    "get_status": get_status,
}


async def execute_tool(name: str, arguments: dict) -> str:
    """
    Execute a tool by name with the given arguments.

    Args:
        name: Tool name
        arguments: Tool arguments

    Returns:
        str: Tool result (will be spoken to user)
    """
    if name not in TOOLS:
        logger.error(f"Unknown tool: {name}")
        return f"I don't recognize the tool '{name}'. Available tools are: {', '.join(TOOLS.keys())}"

    tool_fn = TOOLS[name]

    try:
        logger.info(f"Executing tool: {name}")
        result = await tool_fn(**arguments)
        logger.info(f"Tool {name} completed successfully")
        return result
    except Exception as e:
        logger.error(f"Tool {name} failed: {e}")
        return f"Sorry, there was an error executing {name}: {str(e)}"
