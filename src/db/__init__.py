"""
Database module for multi-tenant user management.

Provides:
- Connection pool management
- User lookup by phone
- Credential storage (encrypted)
- Session memory
- Task history
"""

from src.db.connection import get_pool, close_pool
from src.db.users import (
    get_user_by_phone,
    create_user,
    get_user_credentials,
    save_user_credentials,
)
from src.db.memory import (
    get_session_memory,
    update_session_memory,
)
from src.db.tasks import (
    create_task,
    update_task,
    get_user_tasks,
)

__all__ = [
    "get_pool",
    "close_pool",
    "get_user_by_phone",
    "create_user",
    "get_user_credentials",
    "save_user_credentials",
    "get_session_memory",
    "update_session_memory",
    "create_task",
    "update_task",
    "get_user_tasks",
]
