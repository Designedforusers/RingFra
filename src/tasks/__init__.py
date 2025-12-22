"""
Background tasks module using ARQ.

Handles:
- Task execution with callbacks
- Service health monitoring
- Scheduled reminders
"""

from src.tasks.queue import (
    enqueue_reminder,
    enqueue_task_with_callback,
    get_redis_pool,
)

__all__ = [
    "enqueue_task_with_callback",
    "enqueue_reminder",
    "get_redis_pool",
]
