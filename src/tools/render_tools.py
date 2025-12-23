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
    """Get Claude Agent SDK options configured for Render MCP.
    
    Config follows SDK docs exactly for HTTP MCP servers.
    """
    def stderr_callback(line: str) -> None:
        """Log stderr from Claude CLI subprocess."""
        logger.warning(f"Claude CLI stderr: {line}")
    
    # Log the MCP URL for debugging
    logger.info(f"Configuring Render MCP at: {settings.RENDER_MCP_URL}")
    logger.info(f"API key present: {bool(settings.RENDER_API_KEY)}, length: {len(settings.RENDER_API_KEY) if settings.RENDER_API_KEY else 0}")
    
    # Allow all Render MCP tools - format is mcp__<server>__<tool>
    render_tools = [
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
    ]
    
    return ClaudeAgentOptions(
        mcp_servers={
            "render": {
                "type": "http",
                "url": settings.RENDER_MCP_URL,
                "headers": {
                    "Authorization": f"Bearer {settings.RENDER_API_KEY}",
                },
            }
        },
        allowed_tools=render_tools,
        permission_mode="bypassPermissions",
        stderr=stderr_callback,
    )


async def run_render_agent(prompt: str, timeout: float = 30.0) -> str:
    """Run a query against Render MCP via Claude Agent SDK.
    
    Args:
        prompt: The prompt to send
        timeout: Maximum time to wait (seconds)
        
    Returns:
        Result text or empty string on failure
    """
    import asyncio
    
    result_text = ""
    mcp_connected = False
    options = get_render_agent_options()
    
    logger.info("Starting Claude Agent SDK query with Render MCP...")

    try:
        async def _run_query():
            nonlocal result_text, mcp_connected
            message_count = 0
            async for message in query(prompt=prompt, options=options):
                message_count += 1
                # Log ALL messages at INFO level for debugging MCP issues
                if isinstance(message, dict):
                    msg_type = message.get("type")
                    msg_subtype = message.get("subtype")
                    logger.info(f"SDK Message #{message_count}: type={msg_type}, subtype={msg_subtype}, keys={list(message.keys())}")
                else:
                    msg_type = getattr(message, "type", None)
                    msg_subtype = getattr(message, "subtype", None)
                    logger.info(f"SDK Message #{message_count}: type={msg_type}, subtype={msg_subtype}, class={type(message).__name__}")
                
                # Handle dict-style messages
                if isinstance(message, dict):
                    # Check MCP connection status from system.init
                    if msg_type == "system" and msg_subtype == "init":
                        logger.info(f"system.init received! Full message: {message}")
                        mcp_servers = message.get("mcp_servers", [])
                        logger.info(f"MCP servers in init: {mcp_servers}")
                        for server in mcp_servers:
                            name = server.get("name", "")
                            status = server.get("status", "")
                            logger.info(f"MCP server '{name}' status: {status}")
                            if name == "render":
                                if status == "connected":
                                    mcp_connected = True
                                else:
                                    logger.error(f"Render MCP failed to connect: {status}")

                    elif msg_type == "assistant":
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
                            logger.error(f"SDK error: {error}")
                            result_text = ""
                
                # Handle object-style messages
                else:
                    if msg_type == "system" and msg_subtype == "init":
                        logger.info(f"system.init received (object)! message={message}")
                        mcp_servers = getattr(message, "mcp_servers", [])
                        logger.info(f"MCP servers in init (object): {mcp_servers}")
                        for server in mcp_servers:
                            name = server.get("name") if isinstance(server, dict) else getattr(server, "name", "")
                            status = server.get("status") if isinstance(server, dict) else getattr(server, "status", "")
                            logger.info(f"MCP server '{name}' status: {status}")
                            if name == "render":
                                if status == "connected":
                                    mcp_connected = True
                                else:
                                    logger.error(f"Render MCP failed to connect: {status}")

                    elif msg_type == "assistant":
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
                            logger.error(f"SDK error: {error}")
                            result_text = ""

        # Run with timeout
        await asyncio.wait_for(_run_query(), timeout=timeout)

    except asyncio.TimeoutError:
        logger.error(f"Render agent query timed out after {timeout}s")
        result_text = ""
    except Exception as e:
        import traceback
        logger.error(f"Render agent error: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        result_text = ""

    if result_text:
        logger.info(f"Claude Agent SDK query completed, result length: {len(result_text)}, mcp_connected: {mcp_connected}")
    else:
        logger.warning(f"Claude Agent SDK query returned empty result, mcp_connected: {mcp_connected}")
    return result_text


# =============================================================================
# Tools using MCP (list, logs, metrics, env vars)
# =============================================================================

async def list_services() -> str:
    """List all services deployed on Render (MCP with API fallback)."""
    logger.info("Listing Render services")

    # Try MCP first
    try:
        prompt = """Use the Render MCP to list all services in the current workspace.

Respond concisely (this will be spoken aloud):
- How many services are there
- List them by name and type (web service, database, etc.)
- Note any that are suspended or have issues

Keep under 100 words."""

        result = await run_render_agent(prompt)
        if result and len(result) > 20 and "error" not in result.lower():
            return result
        logger.warning(f"MCP returned insufficient result: {result}")
    except Exception as e:
        logger.warning(f"MCP failed, falling back to direct API: {e}")

    # Fallback to direct API
    logger.info("Using direct Render API fallback for list_services")
    try:
        data = await render_api("GET", "/services")
        services = data if isinstance(data, list) else data.get("services", data.get("items", []))
        
        if not services:
            return "You don't have any services deployed on Render yet."
        
        # Format for voice
        web_services = []
        databases = []
        other = []
        
        for svc in services:
            svc_data = svc.get("service", svc)
            name = svc_data.get("name", "Unknown")
            svc_type = svc_data.get("type", "unknown")
            suspended = svc_data.get("suspended", "not_suspended")
            
            status = " (suspended)" if suspended != "not_suspended" else ""
            
            if svc_type == "web_service":
                web_services.append(f"{name}{status}")
            elif svc_type in ("postgres", "redis"):
                databases.append(f"{name}{status}")
            else:
                other.append(f"{name}{status}")
        
        parts = []
        parts.append(f"You have {len(services)} services on Render.")
        
        if web_services:
            parts.append(f"Web services: {', '.join(web_services)}.")
        if databases:
            parts.append(f"Databases: {', '.join(databases)}.")
        if other:
            parts.append(f"Other: {', '.join(other)}.")
        
        return " ".join(parts)
        
    except Exception as e:
        logger.error(f"Direct API also failed: {e}")
        return f"I couldn't retrieve your services. Error: {str(e)}"


async def get_logs(
    service_name: str, lines: int = 50, filter: str | None = None
) -> str:
    """Get logs for a service (MCP with API fallback)."""
    logger.info(f"Getting logs for {service_name}")

    # Try MCP first
    try:
        filter_instruction = f'Filter for logs containing "{filter}".' if filter else ""

        prompt = f"""Use the Render MCP to get the last {lines} logs for the service named "{service_name}".
{filter_instruction}

Analyze the logs and respond concisely (this will be spoken aloud):
- Any errors or warnings found
- Most recent error message if any
- Overall health assessment

Keep under 75 words."""

        result = await run_render_agent(prompt)
        if result and len(result) > 20 and "error" not in result.lower():
            return result
        logger.warning(f"MCP returned insufficient result for logs: {result}")
    except Exception as e:
        logger.warning(f"MCP failed for logs, falling back to API: {e}")

    # Fallback to direct API using Render's logs endpoint
    logger.info("Using direct Render API fallback for get_logs")
    try:
        service = await get_service_by_name(service_name)
        if not service:
            return f"I couldn't find a service named '{service_name}'."
        
        service_id = service.get("id")
        
        # Fetch logs via Render API (uses different endpoint structure)
        async with httpx.AsyncClient() as client:
            # Render logs API - GET /services/{serviceId}/logs
            # Note: This returns streaming logs, we need to use their query endpoint
            response = await client.get(
                f"https://api.render.com/v1/services/{service_id}/logs",
                headers={
                    "Authorization": f"Bearer {settings.RENDER_API_KEY}",
                    "Accept": "application/json",
                },
                params={"limit": min(lines, 100)},  # API max is usually 100
                timeout=30.0,
            )
            
            if response.status_code == 200:
                logs_data = response.json()
                # Parse and summarize logs
                log_entries = logs_data if isinstance(logs_data, list) else logs_data.get("logs", [])
                
                if not log_entries:
                    return f"{service_name} is running but has no recent logs."
                
                # Extract log messages and analyze
                messages = []
                errors = []
                warnings = []
                
                for entry in log_entries[:lines]:
                    msg = entry.get("message", "") if isinstance(entry, dict) else str(entry)
                    messages.append(msg)
                    if "error" in msg.lower():
                        errors.append(msg)
                    elif "warning" in msg.lower() or "warn" in msg.lower():
                        warnings.append(msg)
                
                # Build summary
                summary_parts = []
                if errors:
                    summary_parts.append(f"Found {len(errors)} errors. Latest: {errors[0][:100]}")
                if warnings:
                    summary_parts.append(f"{len(warnings)} warnings")
                if not errors and not warnings:
                    summary_parts.append(f"{service_name} looks healthy. No errors in recent logs.")
                
                return " ".join(summary_parts)
            else:
                logger.warning(f"Logs API returned {response.status_code}: {response.text}")
                status = "running" if service.get("suspended") == "not_suspended" else "suspended"
                return f"{service_name} is {status}. Couldn't fetch detailed logs via API."
                
    except Exception as e:
        logger.error(f"Direct API also failed for logs: {e}")
        return f"I couldn't retrieve logs for {service_name}."


async def get_metrics(service_name: str, period: str = "1h") -> str:
    """Get performance metrics for a service (MCP with API fallback)."""
    logger.info(f"Getting metrics for {service_name}")

    # Try MCP first
    try:
        prompt = f"""Use the Render MCP to get metrics for the service named "{service_name}" over the last {period}.

Report concisely (this will be spoken aloud):
- CPU usage
- Memory usage
- Request counts if available
- Any concerning trends

Keep under 50 words."""

        result = await run_render_agent(prompt)
        if result and len(result) > 20 and "error" not in result.lower():
            return result
        logger.warning(f"MCP returned insufficient result for metrics: {result}")
    except Exception as e:
        logger.warning(f"MCP failed for metrics, falling back to API: {e}")

    # Fallback - at least confirm service exists
    logger.info("Using direct Render API fallback for get_metrics")
    try:
        service = await get_service_by_name(service_name)
        if not service:
            return f"I couldn't find a service named '{service_name}'."
        
        status = "running" if service.get("suspended") == "not_suspended" else "suspended"
        svc_type = service.get("type", "service")
        return f"{service_name} is a {svc_type} and is currently {status}. Detailed metrics require the MCP connection."
    except Exception as e:
        logger.error(f"Direct API also failed for metrics: {e}")
        return f"I couldn't retrieve metrics for {service_name}."


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
