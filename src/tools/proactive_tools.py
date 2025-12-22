"""
Proactive agent tools.

Enables:
- Schedule background tasks with callbacks
- Set reminders
- Enable/disable service monitoring
"""

from typing import Any

from loguru import logger

from src.config import settings


async def schedule_callback(
    task_type: str,
    params: dict[str, Any] | None = None,
    caller_phone: str | None = None,
) -> str:
    """
    Schedule a task to run in the background and call back when complete.

    Args:
        task_type: Type of task (fix_bug, deploy, etc.)
        params: Task parameters
        caller_phone: Phone to call back (injected by pipeline)

    Returns:
        Confirmation message
    """
    if not settings.REDIS_URL:
        return "Background tasks are not available - Redis not configured."

    if not caller_phone:
        return "I need your phone number to call you back. Please provide it."

    params = params or {}

    try:
        from src.tasks.queue import enqueue_task_with_callback

        job_id = await enqueue_task_with_callback(
            task_type=task_type,
            params=params,
            phone=caller_phone,
        )

        task_desc = params.get("description", task_type)
        return f"Got it. I'll work on {task_desc} and call you back when it's done."

    except Exception as e:
        logger.error(f"Failed to schedule callback: {e}")
        return f"Sorry, I couldn't schedule that task: {str(e)}"


async def set_reminder(
    message: str,
    delay_minutes: int,
    caller_phone: str | None = None,
) -> str:
    """
    Set a reminder to call the user back later.

    Args:
        message: What to remind about
        delay_minutes: Minutes to wait
        caller_phone: Phone to call (injected by pipeline)

    Returns:
        Confirmation message
    """
    if not settings.REDIS_URL:
        return "Reminders are not available - Redis not configured."

    if not caller_phone:
        return "I need your phone number to call you back. Please provide it."

    if delay_minutes < 1:
        return "Please specify a delay of at least 1 minute."

    if delay_minutes > 10080:  # 7 days
        return "Reminders can only be set up to 7 days in advance."

    try:
        from src.tasks.queue import enqueue_reminder

        delay_seconds = delay_minutes * 60
        job_id = await enqueue_reminder(
            phone=caller_phone,
            message=message,
            delay_seconds=delay_seconds,
        )

        # Format the time nicely
        if delay_minutes >= 60:
            hours = delay_minutes // 60
            mins = delay_minutes % 60
            if mins > 0:
                time_str = f"{hours} hour{'s' if hours > 1 else ''} and {mins} minute{'s' if mins > 1 else ''}"
            else:
                time_str = f"{hours} hour{'s' if hours > 1 else ''}"
        else:
            time_str = f"{delay_minutes} minute{'s' if delay_minutes > 1 else ''}"

        return f"I'll call you back in {time_str} to remind you about: {message}"

    except Exception as e:
        logger.error(f"Failed to set reminder: {e}")
        return f"Sorry, I couldn't set that reminder: {str(e)}"


async def enable_monitoring(
    service_name: str,
    alert_threshold: str = "critical",
    caller_phone: str | None = None,
) -> str:
    """
    Enable proactive monitoring for a service.

    Args:
        service_name: Service to monitor
        alert_threshold: When to alert (critical, warning, all)
        caller_phone: Phone to call on alerts

    Returns:
        Confirmation message
    """
    if not settings.REDIS_URL:
        return "Monitoring is not available - Redis not configured."

    phone = caller_phone or settings.OWNER_PHONE
    if not phone:
        return "I need a phone number to send alerts to. Please provide one."

    if alert_threshold not in ["critical", "warning", "all"]:
        alert_threshold = "critical"

    try:
        from src.tasks.queue import add_monitored_service

        await add_monitored_service(
            service_name=service_name,
            alert_threshold=alert_threshold,
            phone=phone,
        )

        threshold_desc = {
            "critical": "critical issues only",
            "warning": "warnings and critical issues",
            "all": "any issues",
        }[alert_threshold]

        return f"I'll monitor {service_name} and call you for {threshold_desc}."

    except Exception as e:
        logger.error(f"Failed to enable monitoring: {e}")
        return f"Sorry, I couldn't enable monitoring: {str(e)}"


async def disable_monitoring(service_name: str) -> str:
    """
    Stop monitoring a service.

    Args:
        service_name: Service to stop monitoring

    Returns:
        Confirmation message
    """
    if not settings.REDIS_URL:
        return "Monitoring is not available - Redis not configured."

    try:
        from src.tasks.queue import remove_monitored_service

        await remove_monitored_service(service_name)
        return f"Stopped monitoring {service_name}."

    except Exception as e:
        logger.error(f"Failed to disable monitoring: {e}")
        return f"Sorry, I couldn't disable monitoring: {str(e)}"
