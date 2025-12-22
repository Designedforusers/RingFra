"""
Session memory database operations.

Stores conversation context across calls.
"""

from typing import Any
from uuid import UUID

from loguru import logger

from src.db.connection import get_pool


async def get_session_memory(user_id: UUID) -> dict[str, Any] | None:
    """
    Get session memory for a user.
    
    Args:
        user_id: User UUID
        
    Returns:
        Memory dict with summary and preferences, or None
    """
    pool = await get_pool()
    
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT summary, preferences, updated_at
            FROM session_memory
            WHERE user_id = $1
            """,
            user_id
        )
        
        if row:
            return {
                "summary": row['summary'],
                "preferences": row['preferences'] or {},
                "updated_at": row['updated_at'],
            }
        return None


async def update_session_memory(
    user_id: UUID,
    summary: str | None = None,
    preferences: dict | None = None,
) -> None:
    """
    Update session memory for a user.
    
    Args:
        user_id: User UUID
        summary: Conversation summary (optional, updates if provided)
        preferences: User preferences (optional, merges if provided)
    """
    pool = await get_pool()
    
    async with pool.acquire() as conn:
        if summary is not None and preferences is not None:
            # Update both
            await conn.execute(
                """
                INSERT INTO session_memory (user_id, summary, preferences, updated_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (user_id)
                DO UPDATE SET
                    summary = EXCLUDED.summary,
                    preferences = session_memory.preferences || EXCLUDED.preferences,
                    updated_at = NOW()
                """,
                user_id, summary, preferences
            )
        elif summary is not None:
            # Update just summary
            await conn.execute(
                """
                INSERT INTO session_memory (user_id, summary, updated_at)
                VALUES ($1, $2, NOW())
                ON CONFLICT (user_id)
                DO UPDATE SET
                    summary = EXCLUDED.summary,
                    updated_at = NOW()
                """,
                user_id, summary
            )
        elif preferences is not None:
            # Merge preferences
            await conn.execute(
                """
                INSERT INTO session_memory (user_id, preferences, updated_at)
                VALUES ($1, $2, NOW())
                ON CONFLICT (user_id)
                DO UPDATE SET
                    preferences = session_memory.preferences || EXCLUDED.preferences,
                    updated_at = NOW()
                """,
                user_id, preferences
            )
        
        logger.debug(f"Updated session memory for user {user_id}")
