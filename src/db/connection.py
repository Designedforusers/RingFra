"""
Database connection pool management.
"""

import asyncpg
from loguru import logger

from src.config import settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Get or create the database connection pool."""
    global _pool
    if _pool is None:
        if not settings.DATABASE_URL:
            raise ValueError("DATABASE_URL not configured")

        _pool = await asyncpg.create_pool(
            settings.DATABASE_URL,
            min_size=2,
            max_size=10,
        )
        logger.info("Database connection pool created")
    return _pool


async def close_pool() -> None:
    """Close the database connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("Database connection pool closed")


async def init_schema() -> None:
    """Initialize database schema if not exists."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Enable UUID extension
        await conn.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

        # Users table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                phone TEXT UNIQUE NOT NULL,
                email TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        ''')

        # Credentials table (encrypted tokens)
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS credentials (
                user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                provider TEXT NOT NULL,
                access_token TEXT NOT NULL,
                refresh_token TEXT,
                expires_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (user_id, provider)
            )
        ''')

        # Connected repos
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS repos (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                github_url TEXT NOT NULL,
                local_path TEXT,
                default_branch TEXT DEFAULT 'main',
                settings JSONB DEFAULT '{}',
                last_synced TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW()
            )
        ''')

        # Task history
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                repo_id UUID REFERENCES repos(id) ON DELETE SET NULL,
                type TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                input JSONB,
                result JSONB,
                created_at TIMESTAMP DEFAULT NOW(),
                completed_at TIMESTAMP
            )
        ''')

        # Session memory
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS session_memory (
                user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                summary TEXT,
                preferences JSONB DEFAULT '{}',
                updated_at TIMESTAMP DEFAULT NOW()
            )
        ''')

        # Background tasks (for async execution with callbacks)
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS background_tasks (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                phone VARCHAR(20) NOT NULL,
                task_type VARCHAR(50) NOT NULL,
                plan JSONB NOT NULL,
                status VARCHAR(20) DEFAULT 'pending',
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                result_summary TEXT,
                error TEXT,
                cost_usd DECIMAL(10,4),
                created_at TIMESTAMP DEFAULT NOW(),
                arq_job_id VARCHAR(100)
            )
        ''')

        # Create indexes
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone)')
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id)')
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_repos_user_id ON repos(user_id)')
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_background_tasks_user_id ON background_tasks(user_id)')
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_background_tasks_status ON background_tasks(status)')

        logger.info("Database schema initialized")
