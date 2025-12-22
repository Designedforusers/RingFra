"""
Repository management module using git worktrees.

Handles:
- Bare clone setup (one-time)
- Task worktrees on feature branches
- Main worktree for read-only ops
- Push and cleanup
"""

from src.repos.manager import (
    setup_repo,
    sync_main,
    create_task_worktree,
    push_branch,
    cleanup_worktree,
    get_repo_for_task,
    get_main_for_reading,
)

__all__ = [
    "setup_repo",
    "sync_main",
    "create_task_worktree",
    "push_branch",
    "cleanup_worktree",
    "get_repo_for_task",
    "get_main_for_reading",
]
