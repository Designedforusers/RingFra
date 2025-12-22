"""
ARQ worker for background tasks.

Handles:
- Task execution with callbacks
- Periodic health monitoring
- Scheduled reminders
"""

import asyncio
from typing import Any

from arq import cron
from arq.connections import RedisSettings
from loguru import logger

from src.config import settings
from src.callbacks.outbound import initiate_callback, send_sms
from src.callbacks.router import (
    Event,
    Severity,
    notify_user,
    service_down_event,
    deploy_failed_event,
    high_cpu_event,
    high_memory_event,
)
from src.tasks.monitors import (
    HealthStatus,
    poll_render_services,
    check_specific_service,
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
        reminder_callback,
    ]

    # Cron jobs for periodic tasks
    cron_jobs = [
        # Health check every 15 minutes
        cron(check_service_health, minute={0, 15, 30, 45}),
    ]

    # Redis connection
    @staticmethod
    def redis_settings() -> RedisSettings:
        if not settings.REDIS_URL:
            raise ValueError("REDIS_URL not configured")
        return RedisSettings.from_dsn(settings.REDIS_URL)

    # Worker settings
    max_jobs = 10
    job_timeout = 300  # 5 minutes
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
