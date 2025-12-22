"""
Task history database operations.

Tracks all tasks performed by the agent.
"""

from typing import Any
from uuid import UUID

from loguru import logger

from src.db.connection import get_pool


async def create_task(
    user_id: UUID,
    task_type: str,
    input_data: dict | None = None,
    repo_id: UUID | None = None,
) -> UUID:
    """
    Create a new task record.
    
    Args:
        user_id: User UUID
        task_type: Type of task (fix_bug, deploy, etc.)
        input_data: Task input parameters
        repo_id: Optional associated repo
        
    Returns:
        Task UUID
    """
    pool = await get_pool()
    
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO tasks (user_id, repo_id, type, input, status)
            VALUES ($1, $2, $3, $4, 'running')
            RETURNING id
            """,
            user_id, repo_id, task_type, input_data or {}
        )
        
        task_id = row['id']
        logger.info(f"Created task {task_id} ({task_type}) for user {user_id}")
        return task_id


async def update_task(
    task_id: UUID,
    status: str,
    result: dict | None = None,
) -> None:
    """
    Update task status and result.
    
    Args:
        task_id: Task UUID
        status: New status ('success', 'failed', 'cancelled')
        result: Task result data
    """
    pool = await get_pool()
    
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE tasks
            SET status = $2,
                result = $3,
                completed_at = NOW()
            WHERE id = $1
            """,
            task_id, status, result or {}
        )
        
        logger.info(f"Updated task {task_id} to {status}")


async def get_user_tasks(
    user_id: UUID,
    limit: int = 10,
    task_type: str | None = None,
) -> list[dict[str, Any]]:
    """
    Get recent tasks for a user.
    
    Args:
        user_id: User UUID
        limit: Max tasks to return
        task_type: Optional filter by type
        
    Returns:
        List of task dicts
    """
    pool = await get_pool()
    
    async with pool.acquire() as conn:
        if task_type:
            rows = await conn.fetch(
                """
                SELECT id, type, status, input, result, created_at, completed_at
                FROM tasks
                WHERE user_id = $1 AND type = $2
                ORDER BY created_at DESC
                LIMIT $3
                """,
                user_id, task_type, limit
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, type, status, input, result, created_at, completed_at
                FROM tasks
                WHERE user_id = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                user_id, limit
            )
        
        return [dict(row) for row in rows]


async def get_task(task_id: UUID) -> dict[str, Any] | None:
    """Get a specific task by ID."""
    pool = await get_pool()
    
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, user_id, repo_id, type, status, input, result, created_at, completed_at
            FROM tasks
            WHERE id = $1
            """,
            task_id
        )
        
        if row:
            return dict(row)
        return None
