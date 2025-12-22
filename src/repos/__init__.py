"""
Production-grade repository management using git worktrees.

Handles:
- Bare clone setup (one-time, with shallow option)
- Task worktrees on feature branches (always fetch latest first)
- Main worktree for read-only ops
- Conventional commits
- Rebase before PR
- Auto-cleanup stale worktrees
- Proper error handling (auth, network, conflicts)
"""

from src.repos.manager import (
    # Core operations
    setup_repo,
    sync_main,
    create_task_worktree,
    commit_changes,
    rebase_on_latest,
    push_branch,
    cleanup_worktree,
    cleanup_stale_worktrees,
    # High-level API
    get_repo_for_task,
    get_main_for_reading,
    ship_changes,
    # Types
    CommitType,
    TaskResult,
    GitError,
    AuthenticationError,
    ConflictError,
    NetworkError,
    RepoNotFoundError,
)

__all__ = [
    # Core operations
    "setup_repo",
    "sync_main",
    "create_task_worktree",
    "commit_changes",
    "rebase_on_latest",
    "push_branch",
    "cleanup_worktree",
    "cleanup_stale_worktrees",
    # High-level API
    "get_repo_for_task",
    "get_main_for_reading",
    "ship_changes",
    # Types
    "CommitType",
    "TaskResult",
    "GitError",
    "AuthenticationError",
    "ConflictError",
    "NetworkError",
    "RepoNotFoundError",
]
