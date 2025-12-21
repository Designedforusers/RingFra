"""
Utility tools for general status and help.
"""

from loguru import logger

from src.tools.render_tools import run_render_agent


async def get_status() -> str:
    """
    Get a quick status overview of all services.

    Returns:
        str: Status summary
    """
    logger.info("Getting status overview")

    prompt = """Use the Render MCP to get an overview of all services in the workspace.

Provide a concise status report (this will be spoken aloud):
- Total number of services
- How many are running vs suspended vs failed
- Any services that need attention
- Recent deployment activity if notable

Keep under 75 words."""

    result = await run_render_agent(prompt)
    return result or "I couldn't retrieve the status overview."
