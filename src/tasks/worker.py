"""
ARQ worker for background tasks.

Handles:
- Task execution with callbacks
- Periodic health monitoring
- Scheduled reminders
"""

from typing import Any

from arq import cron
from arq.connections import RedisSettings
from loguru import logger

from src.callbacks.outbound import initiate_callback, send_sms
from src.callbacks.router import (
    Event,
    Severity,
    deploy_failed_event,
    high_cpu_event,
    high_memory_event,
    notify_user,
)
from src.config import settings
from src.tasks.monitors import (
    HealthStatus,
    poll_render_services,
)
from src.tasks.queue import get_monitored_services

# =============================================================================
# Task Handlers
# =============================================================================

async def execute_task_and_callback(
    ctx: dict,
    task_type: str,
    params: dict[str, Any],
    phone: str,
) -> dict[str, Any]:
    """
    Execute a background task and call the user when complete.

    This is the main task handler for "do X and call me back" requests.
    """
    logger.info(f"Executing task: {task_type} with params: {params}")

    result = {
        "task_type": task_type,
        "status": "completed",
        "success": True,
        "summary": "",
    }

    try:
        # Import tools lazily to avoid circular imports
        from src.tools import execute_tool

        # Execute the requested task
        if task_type in ["fix_bug", "implement_feature", "run_tests", "analyze_code"]:
            # Code tools
            tool_result = await execute_tool(task_type, params)
            result["summary"] = tool_result
        elif task_type in ["trigger_deploy", "scale_service", "rollback_deploy"]:
            # Infrastructure tools
            tool_result = await execute_tool(task_type, params)
            result["summary"] = tool_result
        else:
            # Generic task - just log completion
            result["summary"] = f"Task '{task_type}' completed"

        logger.info(f"Task {task_type} completed successfully")

    except Exception as e:
        logger.error(f"Task {task_type} failed: {e}")
        result["status"] = "failed"
        result["success"] = False
        result["summary"] = f"Task failed: {str(e)}"

    # Call the user back with the result
    try:
        await initiate_callback(
            phone=phone,
            context=result,
            callback_type="task_complete",
        )
    except Exception as e:
        logger.error(f"Failed to initiate callback: {e}")
        # Fall back to SMS
        await send_sms(
            phone=phone,
            message=f"[Render Agent] {task_type} {result['status']}: {result['summary'][:100]}",
        )

    return result


async def execute_background_task(ctx: dict, task_id: str) -> dict[str, Any]:
    """
    Execute a background task with full Claude SDK capabilities.
    
    Spawns a headless SDK session that executes the task plan autonomously,
    then calls the user back with the result.
    """
    from claude_agent_sdk import ClaudeAgentOptions, query

    from src.tasks.schemas import TASK_RESULT_SCHEMA

    from src.db.background_tasks import get_background_task, update_task_status
    from src.db.users import get_user_credentials, get_user_repos

    logger.info(f"Executing background task: {task_id}")

    # Load task from database
    task = await get_background_task(task_id)
    if not task:
        logger.error(f"Task {task_id} not found")
        return {"error": "Task not found"}

    phone = task["phone"]
    user_id = task["user_id"]
    plan = task["plan"]
    task_type = task["task_type"]

    # Update status to running
    await update_task_status(task_id, "running")

    # Load user context (credentials, repos)
    user_context = None
    cwd = "/app"
    try:
        # Get credentials
        github_creds = await get_user_credentials(user_id, "github")
        render_creds = await get_user_credentials(user_id, "render")
        repos = await get_user_repos(user_id)

        user_context = {
            "user_id": user_id,
            "credentials": {
                "github": github_creds or {},
                "render": render_creds or {},
            },
            "repos": repos or [],
        }

        # Set working directory to user's repo if available
        if repos and repos[0].get("local_path"):
            cwd = repos[0]["local_path"]
    except Exception as e:
        logger.warning(f"Failed to load full user context: {e}")

    # Build system prompt with the plan
    steps_formatted = "\n".join(f"  {i+1}. {step}" for i, step in enumerate(plan.get("steps", [])))
    system_prompt = f"""You are executing a background task AUTONOMOUSLY. The user is NOT on the call.

## CRITICAL RULES
- Do NOT ask questions or wait for user input
- Make decisions and proceed
- If something fails, try to fix it yourself
- If you truly cannot proceed, document why and stop

## Your Task
**Objective**: {plan.get('objective', 'Complete the requested task')}

**Steps**:
{steps_formatted}

**Success Criteria**: {plan.get('success_criteria', 'Task completes without errors')}

**Context**: {plan.get('context', 'No additional context')}

Execute each step. When done, provide a clear summary of what happened."""

    # Get API keys for MCP
    render_api_key = settings.RENDER_API_KEY
    github_token = settings.GITHUB_TOKEN or ""

    if user_context:
        creds = user_context.get("credentials", {})
        if creds.get("render", {}).get("access_token"):
            render_api_key = creds["render"]["access_token"]
        if creds.get("github", {}).get("access_token"):
            github_token = creds["github"]["access_token"]

    # Build MCP servers config
    mcp_servers = {
        "render": {
            "type": "http",
            "url": "https://mcp.render.com/mcp",
            "headers": {
                "Authorization": f"Bearer {render_api_key}",
            },
        },
    }

    # Environment for Bash tools
    env_vars = {}
    if github_token:
        env_vars["GH_TOKEN"] = github_token
        env_vars["GITHUB_TOKEN"] = github_token

    # Build query options with structured output for reliable result extraction
    query_options = ClaudeAgentOptions(
        cwd=cwd,
        env=env_vars,
        system_prompt=system_prompt,
        mcp_servers=mcp_servers,
        permission_mode="bypassPermissions",  # AUTONOMOUS - no prompts
        allowed_tools=[
            "Read", "Write", "Edit", "Glob", "Grep", "Bash",
            "mcp__render__list_services",
            "mcp__render__get_service",
            "mcp__render__list_deploys",
            "mcp__render__get_deploy",
            "mcp__render__list_logs",
            "mcp__render__get_metrics",
            "mcp__render__list_workspaces",
            "mcp__render__select_workspace",
        ],
        max_turns=30,
        output_format={
            "type": "json_schema",
            "schema": TASK_RESULT_SCHEMA
        },
    )

    structured_result = None
    total_cost = 0.0
    status = "completed"
    error_msg = None

    try:
        logger.info(f"Starting headless SDK session for task {task_id}")
        prompt = f"Execute this task: {plan.get('objective', task_type)}"

        async for msg in query(prompt=prompt, options=query_options):
            # Handle both dict and object style messages
            msg_type = msg.get("type") if isinstance(msg, dict) else getattr(msg, "type", None)
            msg_subtype = msg.get("subtype") if isinstance(msg, dict) else getattr(msg, "subtype", None)

            # Log tool usage for visibility
            if msg_type == "assistant":
                content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
                if content:
                    for block in content:
                        block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
                        if block_type == "tool_use":
                            tool_name = block.get("name") if isinstance(block, dict) else getattr(block, "name", None)
                            logger.info(f"[HEADLESS] Tool: {tool_name}")

            # Capture structured output from result message
            if msg_type == "result":
                struct_out = msg.get("structured_output") if isinstance(msg, dict) else getattr(msg, "structured_output", None)
                if struct_out:
                    structured_result = struct_out
                    logger.info(f"[HEADLESS] Structured result: {structured_result.get('summary', '')[:100]}")
                
                # Also try to get cost
                cost = msg.get("total_cost_usd") if isinstance(msg, dict) else getattr(msg, "total_cost_usd", None)
                if cost:
                    total_cost = cost

        # Extract summary from structured result (or fallback)
        if structured_result:
            summary = structured_result.get("summary", "Task completed")
            success = structured_result.get("success", True)
            details = structured_result.get("details", {})
            action_items = structured_result.get("action_items", [])
            logger.info(f"[HEADLESS] Using structured output: {summary[:100]}")
        else:
            logger.warning(f"Task {task_id} did not return structured output, using fallback")
            summary = "Task completed"
            success = True
            details = {}
            action_items = []

        logger.info(f"Task {task_id} completed. Cost: ${total_cost:.4f}")
        await update_task_status(
            task_id, 
            "completed", 
            result=summary,
            cost_usd=total_cost
        )

    except Exception as e:
        logger.error(f"Task {task_id} failed: {e}")
        status = "failed"
        error_msg = str(e)
        summary = f"Task failed: {e}"
        success = False
        details = {}
        action_items = []
        await update_task_status(task_id, "failed", error=error_msg)

    # Call the user back with the structured result
    try:
        await initiate_callback(
            phone=phone,
            context={
                "task_type": task_type,
                "objective": plan.get("objective", ""),
                "summary": summary,
                "status": status,
                "success": success,
                "details": details,
                "action_items": action_items,
            },
            callback_type="task_complete",
        )
    except Exception as e:
        logger.error(f"Failed to initiate callback for task {task_id}: {e}")
        # Fall back to SMS
        await send_sms(
            phone=phone,
            message=f"[PhoneFix] {task_type} {status}: {summary[:100]}",
        )

    return {"task_id": task_id, "status": status, "result": summary}


async def reminder_callback(
    ctx: dict,
    phone: str,
    message: str,
) -> None:
    """
    Call the user with a reminder.

    Triggered by deferred ARQ job.
    """
    logger.info(f"Reminder triggered for {phone}: {message}")

    try:
        await initiate_callback(
            phone=phone,
            context={
                "reminder": message,
                "event_type": "reminder",
            },
            callback_type="reminder",
        )
    except Exception as e:
        logger.error(f"Failed to call for reminder: {e}")
        # Fall back to SMS
        await send_sms(phone=phone, message=f"[Reminder] {message}")


# =============================================================================
# Periodic Health Monitoring
# =============================================================================

async def check_service_health(ctx: dict) -> None:
    """
    Periodic health check of all monitored services.

    Runs every 15 minutes by default.
    Calls the owner if critical issues found, SMS for warnings.
    """
    logger.info("Running periodic health check")

    # Get monitored services
    monitored = await get_monitored_services()

    if not monitored and not settings.OWNER_PHONE:
        logger.debug("No monitored services and no owner phone - skipping health check")
        return

    # Poll all services
    report = await poll_render_services()

    # Check for issues
    for service in report.issues:
        # Check if this service is specifically monitored
        monitor_config = monitored.get(service.name)

        if monitor_config:
            threshold, phone = monitor_config
        elif settings.OWNER_PHONE:
            # Default to owner phone for unmonitored services
            threshold = "critical"
            phone = settings.OWNER_PHONE
        else:
            continue

        # Determine if we should alert based on threshold
        should_alert = False
        if threshold == "all":
            should_alert = True
        elif threshold == "warning" and service.status in [HealthStatus.WARNING, HealthStatus.CRITICAL]:
            should_alert = True
        elif threshold == "critical" and service.status == HealthStatus.CRITICAL:
            should_alert = True

        if not should_alert:
            continue

        # Create and send the appropriate event
        if "CPU" in service.message:
            event = high_cpu_event(service.name, service.cpu_percent or 0)
        elif "memory" in service.message.lower():
            event = high_memory_event(service.name, service.memory_percent or 0)
        elif "failed" in service.message.lower():
            event = deploy_failed_event(service.name, service.message)
        elif "suspended" in service.message.lower():
            event = Event(
                severity=Severity.WARNING,
                event_type="service_suspended",
                summary=f"{service.name} is suspended",
            )
        else:
            event = Event(
                severity=Severity.CRITICAL if service.status == HealthStatus.CRITICAL else Severity.WARNING,
                event_type="service_issue",
                summary=f"{service.name}: {service.message}",
            )

        await notify_user(phone, event)

    logger.info(f"Health check complete: {report.summary}")


# =============================================================================
# Worker Configuration
# =============================================================================

class WorkerSettings:
    """ARQ worker settings."""

    # Task functions that can be called
    functions = [
        execute_task_and_callback,
        execute_background_task,  # Autonomous SDK execution
        reminder_callback,
    ]

    # Cron jobs for periodic tasks
    cron_jobs = [
        # Health check every 15 minutes
        cron(check_service_health, minute={0, 15, 30, 45}),
    ]

    # Redis connection - must be a class attribute, not a method
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL) if settings.REDIS_URL else None

    # Worker settings
    max_jobs = 10
    job_timeout = 1800  # 30 minutes for background tasks
    keep_result = 3600  # 1 hour
    keep_result_forever = False

    # Logging
    @staticmethod
    async def on_startup(ctx: dict) -> None:
        logger.info("ARQ worker starting up")

    @staticmethod
    async def on_shutdown(ctx: dict) -> None:
        logger.info("ARQ worker shutting down")

    @staticmethod
    async def on_job_start(ctx: dict) -> None:
        logger.info(f"Job starting: {ctx.get('job_id')}")

    @staticmethod
    async def on_job_end(ctx: dict) -> None:
        logger.info(f"Job completed: {ctx.get('job_id')}")
