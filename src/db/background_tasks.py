"""
Background task management for async execution with callbacks.

Stores task plans in Postgres for durable execution by background workers.
"""

from typing import Any
from uuid import UUID

from loguru import logger

from src.db.connection import get_pool


async def create_background_task(
    user_id: UUID,
    phone: str,
    task_type: str,
    plan: dict[str, Any],
) -> str:
    """
    Create a new background task.
    
    Args:
        user_id: User who requested the task
        phone: Phone number to call back
        task_type: Type of task (deploy, fix_bug, run_tests, etc.)
        plan: Structured plan with objective, steps, success_criteria
        
    Returns:
        Task ID (UUID as string)
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO background_tasks (user_id, phone, task_type, plan)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            user_id, phone, task_type, plan
        )

        task_id = str(row['id'])
        logger.info(f"Created background task {task_id}: {task_type}")
        return task_id


async def get_background_task(task_id: str) -> dict[str, Any] | None:
    """
    Get a background task by ID with full details.
    
    Args:
        task_id: Task UUID
        
    Returns:
        Task dict with all fields, or None if not found
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT 
                id, user_id, phone, task_type, plan,
                status, started_at, completed_at,
                result_summary, error, cost_usd,
                created_at, arq_job_id
            FROM background_tasks
            WHERE id = $1
            """,
            UUID(task_id)
        )

        if row:
            return dict(row)
        return None


async def update_task_status(
    task_id: str,
    status: str,
    result: str | None = None,
    error: str | None = None,
    cost_usd: float | None = None,
    arq_job_id: str | None = None,
) -> None:
    """
    Update task status and optionally result/error.
    
    Args:
        task_id: Task UUID
        status: New status (pending, running, completed, failed)
        result: Result summary (for completed tasks)
        error: Error message (for failed tasks)
        cost_usd: Cost of execution
        arq_job_id: ARQ job ID for tracking
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        if status == "running":
            await conn.execute(
                """
                UPDATE background_tasks
                SET status = $2, started_at = NOW(), arq_job_id = $3
                WHERE id = $1
                """,
                UUID(task_id), status, arq_job_id
            )
        elif status == "completed":
            await conn.execute(
                """
                UPDATE background_tasks
                SET status = $2, completed_at = NOW(), 
                    result_summary = $3, cost_usd = $4
                WHERE id = $1
                """,
                UUID(task_id), status, result, cost_usd
            )
        elif status == "failed":
            await conn.execute(
                """
                UPDATE background_tasks
                SET status = $2, completed_at = NOW(), error = $3
                WHERE id = $1
                """,
                UUID(task_id), status, error
            )
        else:
            await conn.execute(
                """
                UPDATE background_tasks
                SET status = $2
                WHERE id = $1
                """,
                UUID(task_id), status
            )

        logger.info(f"Updated task {task_id} status to {status}")


async def get_user_pending_tasks(user_id: UUID) -> list[dict[str, Any]]:
    """Get all pending/running tasks for a user."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, task_type, plan, status, created_at
            FROM background_tasks
            WHERE user_id = $1 AND status IN ('pending', 'running')
            ORDER BY created_at DESC
            """,
            user_id
        )

        return [dict(row) for row in rows]
