"""
User management database operations.
"""

from typing import Any
from uuid import UUID

from loguru import logger

from src.db.connection import get_pool
from src.db.encryption import encrypt, decrypt


async def get_user_by_phone(phone: str) -> dict[str, Any] | None:
    """
    Look up a user by phone number.
    
    Args:
        phone: Phone number in E.164 format (+1234567890)
        
    Returns:
        User dict or None if not found
    """
    pool = await get_pool()
    
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, phone, email, created_at FROM users WHERE phone = $1",
            phone
        )
        
        if row:
            return dict(row)
        return None


async def create_user(phone: str, email: str | None = None) -> dict[str, Any]:
    """
    Create a new user.
    
    Args:
        phone: Phone number in E.164 format
        email: Optional email address
        
    Returns:
        Created user dict
    """
    pool = await get_pool()
    
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO users (phone, email)
            VALUES ($1, $2)
            RETURNING id, phone, email, created_at
            """,
            phone, email
        )
        
        # Initialize empty session memory
        await conn.execute(
            "INSERT INTO session_memory (user_id) VALUES ($1) ON CONFLICT DO NOTHING",
            row['id']
        )
        
        logger.info(f"Created user {row['id']} for phone {phone}")
        return dict(row)


async def get_or_create_user(phone: str) -> dict[str, Any]:
    """Get existing user or create new one."""
    user = await get_user_by_phone(phone)
    if user:
        return user
    return await create_user(phone)


async def get_user_credentials(user_id: UUID, provider: str) -> dict[str, Any] | None:
    """
    Get decrypted credentials for a user and provider.
    
    Args:
        user_id: User UUID
        provider: Provider name ('render', 'github')
        
    Returns:
        Credentials dict with decrypted tokens, or None
    """
    pool = await get_pool()
    
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT access_token, refresh_token, expires_at
            FROM credentials
            WHERE user_id = $1 AND provider = $2
            """,
            user_id, provider
        )
        
        if row:
            return {
                "access_token": decrypt(row['access_token']),
                "refresh_token": decrypt(row['refresh_token']) if row['refresh_token'] else None,
                "expires_at": row['expires_at'],
            }
        return None


async def save_user_credentials(
    user_id: UUID,
    provider: str,
    access_token: str,
    refresh_token: str | None = None,
    expires_at: Any = None,
) -> None:
    """
    Save encrypted credentials for a user.
    
    Args:
        user_id: User UUID
        provider: Provider name ('render', 'github')
        access_token: OAuth access token
        refresh_token: Optional refresh token
        expires_at: Optional expiration timestamp
    """
    pool = await get_pool()
    
    encrypted_access = encrypt(access_token)
    encrypted_refresh = encrypt(refresh_token) if refresh_token else None
    
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO credentials (user_id, provider, access_token, refresh_token, expires_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (user_id, provider)
            DO UPDATE SET
                access_token = EXCLUDED.access_token,
                refresh_token = EXCLUDED.refresh_token,
                expires_at = EXCLUDED.expires_at,
                updated_at = NOW()
            """,
            user_id, provider, encrypted_access, encrypted_refresh, expires_at
        )
        
        logger.info(f"Saved {provider} credentials for user {user_id}")


async def get_user_repos(user_id: UUID) -> list[dict[str, Any]]:
    """Get all repos for a user."""
    pool = await get_pool()
    
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, github_url, local_path, default_branch, settings, last_synced
            FROM repos
            WHERE user_id = $1
            ORDER BY created_at
            """,
            user_id
        )
        
        return [dict(row) for row in rows]


async def add_user_repo(
    user_id: UUID,
    github_url: str,
    local_path: str | None = None,
    default_branch: str = "main",
    settings: dict | None = None,
) -> dict[str, Any]:
    """Add a repo for a user."""
    pool = await get_pool()
    
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO repos (user_id, github_url, local_path, default_branch, settings)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, github_url, local_path, default_branch, settings
            """,
            user_id, github_url, local_path, default_branch, settings or {}
        )
        
        logger.info(f"Added repo {github_url} for user {user_id}")
        return dict(row)
