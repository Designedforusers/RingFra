"""
Render infrastructure tools - Hybrid MCP + API approach.

Uses:
- Render MCP (via Claude Agent SDK) for: list, logs, metrics, env vars
- Direct Render API for: scale, deploy, rollback (not supported by MCP)
"""

import httpx
from claude_agent_sdk import query, ClaudeAgentOptions
from loguru import logger

from src.config import settings
from src.notifications import track_deploy


# =============================================================================
# Direct Render API Client (for operations MCP doesn't support)
# =============================================================================

async def render_api(method: str, endpoint: str, data: dict | None = None) -> dict:
    """
    Make a request to the Render API directly.

    Used for operations not supported by Render MCP:
    - Scaling services
    - Triggering deploys
    - Rolling back deploys
    """
    async with httpx.AsyncClient() as client:
        response = await client.request(
            method=method,
            url=f"https://api.render.com/v1{endpoint}",
            headers={
                "Authorization": f"Bearer {settings.RENDER_API_KEY}",
                "Content-Type": "application/json",
            },
            json=data,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json() if response.text else {}


async def get_service_by_name(service_name: str) -> dict | None:
    """Find a service by name using the Render API."""
    try:
        data = await render_api("GET", "/services")
        services = data if isinstance(data, list) else data.get("services", data.get("items", []))

        for svc in services:
            svc_data = svc.get("service", svc)
            if svc_data.get("name", "").lower() == service_name.lower():
                return svc_data
        return None
    except Exception as e:
        logger.error(f"Error finding service {service_name}: {e}")
        return None


# =============================================================================
# Claude Agent SDK + Render MCP (for supported operations)
# =============================================================================

def get_render_agent_options() -> ClaudeAgentOptions:
    """Get Claude Agent SDK options configured for Render MCP."""
    
    def stderr_callback(line: str) -> None:
        """Log stderr from Claude CLI subprocess."""
        logger.warning(f"Claude CLI stderr: {line}")
    
    return ClaudeAgentOptions(
        mcp_servers={
            "render": {
                "type": "http",
                "url": "https://mcp.render.com/mcp",
                "headers": {
                    "Authorization": f"Bearer {settings.RENDER_API_KEY}"
                }
            }
        },
        permission_mode="bypassPermissions",
        stderr=stderr_callback,
    )


async def run_render_agent(prompt: str) -> str:
    """Run a query against Render MCP via Claude Agent SDK."""
    result_text = ""
    options = get_render_agent_options()
    
    logger.info(f"Starting Claude Agent SDK query...")

    try:
        async for message in query(prompt=prompt, options=options):
            logger.debug(f"Received message type: {type(message)}")
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

    except Exception as e:
        import traceback
        logger.error(f"Render agent error: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        result_text = f"Error: {str(e)}"

    logger.info(f"Claude Agent SDK query completed, result length: {len(result_text)}")
    return result_text


# =============================================================================
# Tools using MCP (list, logs, metrics, env vars)
# =============================================================================

async def list_services() -> str:
    """List all services deployed on Render (via MCP)."""
    logger.info("Listing Render services")

    prompt = """Use the Render MCP to list all services in the current workspace.

Respond concisely (this will be spoken aloud):
- How many services are there
- List them by name and type (web service, database, etc.)
- Note any that are suspended or have issues

Keep under 100 words."""

    result = await run_render_agent(prompt)
    return result or "I couldn't retrieve your services."


async def get_logs(
    service_name: str, lines: int = 50, filter: str | None = None
) -> str:
    """Get logs for a service (via MCP)."""
    logger.info(f"Getting logs for {service_name}")

    filter_instruction = f'Filter for logs containing "{filter}".' if filter else ""

    prompt = f"""Use the Render MCP to get the last {lines} logs for the service named "{service_name}".
{filter_instruction}

Analyze the logs and respond concisely (this will be spoken aloud):
- Any errors or warnings found
- Most recent error message if any
- Overall health assessment

Keep under 75 words."""

    result = await run_render_agent(prompt)
    return result or f"I couldn't retrieve logs for {service_name}."


async def get_metrics(service_name: str, period: str = "1h") -> str:
    """Get performance metrics for a service (via MCP)."""
    logger.info(f"Getting metrics for {service_name}")

    prompt = f"""Use the Render MCP to get metrics for the service named "{service_name}" over the last {period}.

Report concisely (this will be spoken aloud):
- CPU usage
- Memory usage
- Request counts if available
- Any concerning trends

Keep under 50 words."""

    result = await run_render_agent(prompt)
    return result or f"I couldn't retrieve metrics for {service_name}."


async def update_env_var(service_name: str, key: str, value: str) -> str:
    """Update an environment variable (via MCP)."""
    logger.info(f"Updating env var {key} for {service_name}")

    display_value = value[:3] + "***" if len(value) > 6 else "***"

    prompt = f"""Use the Render MCP to update the environment variable "{key}" for service "{service_name}".
Set the value to: {value}

Confirm the update was successful. Keep response under 30 words.
Note: This will trigger a redeploy."""

    result = await run_render_agent(prompt)

    if value in result:
        result = result.replace(value, display_value)

    return result or f"I couldn't update {key} for {service_name}."


# =============================================================================
# Tools using Direct API (scale, deploy, rollback - not supported by MCP)
# =============================================================================

async def scale_service(service_name: str, instances: int) -> str:
    """
    Scale a service to a specific number of instances.

    Uses direct Render API (not supported by MCP).
    """
    logger.info(f"Scaling {service_name} to {instances} instances")

    try:
        service = await get_service_by_name(service_name)

        if not service:
            return f"I couldn't find a service named '{service_name}'."

        service_id = service.get("id")
        current = service.get("numInstances", 1)

        # Scale the service via API
        await render_api("PATCH", f"/services/{service_id}", {"numInstances": instances})

        direction = "up" if instances > current else "down"
        return f"Scaled {service_name} {direction} from {current} to {instances} instances. It should be ready in about 30 seconds."

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error scaling service: {e}")
        return f"I couldn't scale {service_name}: HTTP error {e.response.status_code}"
    except Exception as e:
        logger.error(f"Error scaling service: {e}")
        return f"I couldn't scale the service: {str(e)}"


async def trigger_deploy(service_name: str, caller_phone: str | None = None) -> str:
    """
    Trigger a new deployment.

    Uses direct Render API (not supported by MCP).
    If caller_phone is provided, will send SMS notification when deploy completes.
    """
    logger.info(f"Triggering deploy for {service_name}")

    try:
        service = await get_service_by_name(service_name)

        if not service:
            return f"I couldn't find a service named '{service_name}'."

        service_id = service.get("id")

        # Trigger deploy via API
        deploy_response = await render_api("POST", f"/services/{service_id}/deploys")

        # Extract deploy ID for tracking
        deploy_id = deploy_response.get("id") or deploy_response.get("deploy", {}).get("id")

        # Track for SMS notification if we have caller phone
        if caller_phone and deploy_id:
            track_deploy(deploy_id, service_name, caller_phone)
            return f"Deployment triggered for {service_name}. I'll text you when it's live."
        elif caller_phone:
            return f"Deployment triggered for {service_name}. It should be live in a minute or two. I wasn't able to set up a notification though."
        else:
            return f"Deployment triggered for {service_name}. It should be live in a minute or two."

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error triggering deploy: {e}")
        return f"I couldn't trigger deployment: HTTP error {e.response.status_code}"
    except Exception as e:
        logger.error(f"Error triggering deploy: {e}")
        return f"I couldn't trigger the deployment: {str(e)}"


async def rollback_deploy(service_name: str, deploy_id: str | None = None) -> str:
    """
    Rollback to a previous deployment.

    Uses direct Render API (not supported by MCP).
    """
    logger.info(f"Rolling back {service_name}")

    try:
        service = await get_service_by_name(service_name)

        if not service:
            return f"I couldn't find a service named '{service_name}'."

        service_id = service.get("id")

        # Get recent deploys
        deploys_data = await render_api("GET", f"/services/{service_id}/deploys")
        deploys = deploys_data if isinstance(deploys_data, list) else deploys_data.get("deploys", deploys_data.get("items", []))

        if len(deploys) < 2:
            return f"There's no previous deployment to rollback to for {service_name}."

        # Find the target deploy (previous successful one)
        if deploy_id:
            target_deploy_id = deploy_id
        else:
            # Find the last successful deploy that isn't the current one
            target_deploy_id = None
            for deploy in deploys[1:]:  # Skip current
                deploy_data = deploy.get("deploy", deploy)
                if deploy_data.get("status") == "live":
                    target_deploy_id = deploy_data.get("id")
                    break

            if not target_deploy_id:
                # Just use the previous deploy
                target_deploy_id = deploys[1].get("deploy", deploys[1]).get("id")

        # Trigger rollback - this creates a new deploy from the old commit
        previous_deploy = deploys[1].get("deploy", deploys[1])
        commit = previous_deploy.get("commit", {}).get("id", "previous")

        # Render API rollback is done by redeploying a specific commit
        await render_api("POST", f"/services/{service_id}/deploys", {
            "commitId": commit
        })

        return f"Rolling back {service_name} to the previous deployment. Should be live in a minute or two."

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error rolling back: {e}")
        if e.response.status_code == 400:
            return f"I couldn't rollback {service_name}. The previous deployment may not be available."
        return f"I couldn't rollback: HTTP error {e.response.status_code}"
    except Exception as e:
        logger.error(f"Error rolling back: {e}")
        return f"I couldn't rollback: {str(e)}"
