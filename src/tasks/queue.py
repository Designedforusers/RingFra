"""
Task queue helpers for enqueuing background jobs.

Uses ARQ with Redis for reliable task execution.
"""

from typing import Any

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings
from loguru import logger

from src.config import settings

# Cached connection pool
_pool: ArqRedis | None = None


class RedisUnavailableError(Exception):
    """Raised when Redis is not configured or unavailable."""
    pass


async def get_redis_pool() -> ArqRedis:
    """Get or create the Redis connection pool."""
    global _pool
    if _pool is None:
        if not settings.REDIS_URL:
            raise RedisUnavailableError("REDIS_URL not configured - proactive features disabled")
        try:
            _pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
            logger.info("Redis pool created")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise RedisUnavailableError(f"Redis connection failed: {e}")
    return _pool


async def enqueue_task_with_callback(
    task_type: str,
    params: dict[str, Any],
    phone: str,
) -> str:
    """
    Queue a task that will call the user back when complete.

    Args:
        task_type: Type of task (e.g., "fix_bug", "deploy", "analyze")
        params: Task parameters
        phone: Phone number to call back

    Returns:
        Job ID
    """
    pool = await get_redis_pool()
    job = await pool.enqueue_job(
        "execute_task_and_callback",
        task_type,
        params,
        phone,
    )
    logger.info(f"Queued task {task_type} with callback to {phone}, job_id={job.job_id}")
    return job.job_id


async def enqueue_background_task(task_id: str) -> str:
    """
    Queue a background task for autonomous execution.

    The task details are stored in Postgres. This just enqueues the job
    to ARQ with the task_id reference.

    Args:
        task_id: UUID of the task in background_tasks table

    Returns:
        Job ID
    """
    pool = await get_redis_pool()
    job = await pool.enqueue_job(
        "execute_background_task",
        task_id,
    )

    # Update task with ARQ job ID
    from src.db.background_tasks import update_task_status
    await update_task_status(task_id, "pending", arq_job_id=job.job_id)

    logger.info(f"Queued background task {task_id}, arq_job_id={job.job_id}")
    return job.job_id


async def enqueue_reminder(
    phone: str,
    message: str,
    delay_seconds: int,
) -> str:
    """
    Schedule a reminder call for later.

    Args:
        phone: Phone number to call
        message: Reminder message
        delay_seconds: Seconds to wait before calling

    Returns:
        Job ID
    """
    pool = await get_redis_pool()
    job = await pool.enqueue_job(
        "reminder_callback",
        phone,
        message,
        _defer_by=delay_seconds,
    )
    logger.info(f"Scheduled reminder to {phone} in {delay_seconds}s, job_id={job.job_id}")
    return job.job_id


async def add_monitored_service(
    service_name: str,
    alert_threshold: str,
    phone: str,
) -> None:
    """
    Add a service to the monitoring list.

    Stores in Redis for the monitor worker to poll.

    Args:
        service_name: Name of the service to monitor
        alert_threshold: When to alert ("critical", "warning", "all")
        phone: Phone to call on alerts
    """
    pool = await get_redis_pool()
    key = f"monitor:{service_name}"
    await pool.set(
        key,
        f"{alert_threshold}:{phone}",
        ex=86400 * 7,  # Expire after 7 days
    )
    logger.info(f"Added {service_name} to monitoring, threshold={alert_threshold}")


async def remove_monitored_service(service_name: str) -> None:
    """Remove a service from monitoring."""
    pool = await get_redis_pool()
    key = f"monitor:{service_name}"
    await pool.delete(key)
    logger.info(f"Removed {service_name} from monitoring")


async def get_monitored_services() -> dict[str, tuple[str, str]]:
    """
    Get all monitored services.

    Returns:
        Dict of service_name -> (alert_threshold, phone)
    """
    pool = await get_redis_pool()
    keys = await pool.keys("monitor:*")
    result = {}
    for key in keys:
        service_name = key.decode().replace("monitor:", "")
        value = await pool.get(key)
        if value:
            threshold, phone = value.decode().split(":", 1)
            result[service_name] = (threshold, phone)
    return result
