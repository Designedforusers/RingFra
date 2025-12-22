"""
Repository management using git worktrees.

Architecture:
  /app/user-repos/{user_id}/{repo}/
    .bare/                    # Bare clone (shared git objects)
    main/                     # Worktree: main branch (reference)
    work-{task_id}/           # Worktree: feature branch for task

This approach:
- Shares git objects across worktrees (efficient)
- Keeps main branch protected
- Allows parallel tasks in separate worktrees
- Enables clean PR workflow
"""

import asyncio
import shutil
import uuid
from pathlib import Path
from uuid import UUID

from loguru import logger

REPOS_BASE_DIR = Path("/app/user-repos")


def _extract_repo_name(repo_url: str) -> str:
    """Extract repo name from GitHub URL."""
    name = repo_url.rstrip("/").split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name


def _get_repo_base_path(user_id: UUID, repo_url: str) -> Path:
    """Get base path for a user's repo (contains .bare and worktrees)."""
    repo_name = _extract_repo_name(repo_url)
    return REPOS_BASE_DIR / str(user_id) / repo_name


def _get_bare_path(user_id: UUID, repo_url: str) -> Path:
    """Get path to the bare clone."""
    return _get_repo_base_path(user_id, repo_url) / ".bare"


def _get_main_worktree_path(user_id: UUID, repo_url: str) -> Path:
    """Get path to the main branch worktree."""
    return _get_repo_base_path(user_id, repo_url) / "main"


def _build_auth_url(repo_url: str, github_token: str) -> str:
    """Build authenticated GitHub URL."""
    if "github.com" in repo_url:
        return repo_url.replace("https://", f"https://{github_token}@")
    return repo_url


async def _run_git_command(cmd: str, cwd: Path | None = None, github_token: str | None = None) -> tuple[bool, str]:
    """Run a git command and return (success, output)."""
    full_cmd = f'cd "{cwd}" && {cmd}' if cwd else cmd
    
    process = await asyncio.create_subprocess_shell(
        full_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    
    stdout, stderr = await process.communicate()
    output = stdout.decode().strip() or stderr.decode().strip()
    
    # Sanitize token from output
    if github_token and github_token in output:
        output = output.replace(github_token, "***")
    
    return process.returncode == 0, output


async def setup_repo(
    user_id: UUID,
    repo_url: str,
    github_token: str,
    default_branch: str = "main",
) -> Path:
    """
    Set up a repository with bare clone + main worktree.
    
    This is called once per repo, on first use.
    
    Args:
        user_id: User UUID
        repo_url: GitHub repo URL
        github_token: GitHub access token
        default_branch: Default branch name
        
    Returns:
        Path to the main worktree
    """
    bare_path = _get_bare_path(user_id, repo_url)
    main_path = _get_main_worktree_path(user_id, repo_url)
    
    # Already set up?
    if bare_path.exists() and main_path.exists():
        logger.info(f"Repo already set up at {main_path}")
        await sync_main(user_id, repo_url, github_token)
        return main_path
    
    # Create directory structure
    bare_path.parent.mkdir(parents=True, exist_ok=True)
    
    auth_url = _build_auth_url(repo_url, github_token)
    
    # Clone as bare repo
    logger.info(f"Setting up repo {repo_url} for user {user_id}")
    
    success, output = await _run_git_command(
        f'git clone --bare "{auth_url}" "{bare_path}"',
        github_token=github_token,
    )
    
    if not success:
        raise RuntimeError(f"Failed to clone repository: {output}")
    
    # Configure the bare repo to fetch all branches
    await _run_git_command(
        'git config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*"',
        cwd=bare_path,
    )
    
    # Create main worktree
    success, output = await _run_git_command(
        f'git worktree add "{main_path}" {default_branch}',
        cwd=bare_path,
        github_token=github_token,
    )
    
    if not success:
        # Try with 'master' if 'main' fails
        if default_branch == "main":
            success, output = await _run_git_command(
                f'git worktree add "{main_path}" master',
                cwd=bare_path,
                github_token=github_token,
            )
    
    if not success:
        raise RuntimeError(f"Failed to create main worktree: {output}")
    
    logger.info(f"Repo set up successfully at {main_path}")
    
    # Update database
    await _update_repo_path_in_db(user_id, repo_url, str(main_path))
    
    return main_path


async def sync_main(
    user_id: UUID,
    repo_url: str,
    github_token: str,
) -> Path:
    """
    Sync the main worktree with remote.
    
    Args:
        user_id: User UUID
        repo_url: GitHub repo URL
        github_token: GitHub access token
        
    Returns:
        Path to the main worktree
    """
    bare_path = _get_bare_path(user_id, repo_url)
    main_path = _get_main_worktree_path(user_id, repo_url)
    
    if not bare_path.exists():
        return await setup_repo(user_id, repo_url, github_token)
    
    auth_url = _build_auth_url(repo_url, github_token)
    
    # Fetch in bare repo
    await _run_git_command(
        f'git fetch "{auth_url}" --prune',
        cwd=bare_path,
        github_token=github_token,
    )
    
    # Pull in main worktree
    if main_path.exists():
        await _run_git_command(
            "git pull --ff-only",
            cwd=main_path,
            github_token=github_token,
        )
    
    logger.debug(f"Synced main worktree at {main_path}")
    return main_path


async def create_task_worktree(
    user_id: UUID,
    repo_url: str,
    github_token: str,
    task_description: str | None = None,
) -> tuple[Path, str]:
    """
    Create a new worktree for a task on a feature branch.
    
    Args:
        user_id: User UUID
        repo_url: GitHub repo URL
        github_token: GitHub access token
        task_description: Optional description for branch name
        
    Returns:
        Tuple of (worktree_path, branch_name)
    """
    bare_path = _get_bare_path(user_id, repo_url)
    base_path = _get_repo_base_path(user_id, repo_url)
    
    # Ensure repo is set up
    if not bare_path.exists():
        await setup_repo(user_id, repo_url, github_token)
    
    # Generate branch name
    task_id = str(uuid.uuid4())[:8]
    if task_description:
        # Sanitize description for branch name
        safe_desc = "".join(c if c.isalnum() or c == "-" else "-" for c in task_description.lower())
        safe_desc = safe_desc[:30].strip("-")
        branch_name = f"agent/{safe_desc}-{task_id}"
    else:
        branch_name = f"agent/task-{task_id}"
    
    worktree_path = base_path / f"work-{task_id}"
    
    # Create new branch from main and worktree
    success, output = await _run_git_command(
        f'git worktree add -b "{branch_name}" "{worktree_path}" origin/main',
        cwd=bare_path,
        github_token=github_token,
    )
    
    if not success:
        # Try with origin/master
        success, output = await _run_git_command(
            f'git worktree add -b "{branch_name}" "{worktree_path}" origin/master',
            cwd=bare_path,
            github_token=github_token,
        )
    
    if not success:
        raise RuntimeError(f"Failed to create task worktree: {output}")
    
    logger.info(f"Created task worktree at {worktree_path} on branch {branch_name}")
    return worktree_path, branch_name


async def push_branch(
    worktree_path: Path,
    github_token: str,
    repo_url: str,
) -> bool:
    """
    Push the current branch to remote.
    
    Args:
        worktree_path: Path to the worktree
        github_token: GitHub access token
        repo_url: GitHub repo URL for auth
        
    Returns:
        True if successful
    """
    auth_url = _build_auth_url(repo_url, github_token)
    
    # Get current branch name
    success, branch = await _run_git_command(
        "git rev-parse --abbrev-ref HEAD",
        cwd=worktree_path,
    )
    
    if not success:
        return False
    
    # Push to remote
    success, output = await _run_git_command(
        f'git push -u "{auth_url}" {branch}',
        cwd=worktree_path,
        github_token=github_token,
    )
    
    if success:
        logger.info(f"Pushed branch {branch}")
    else:
        logger.error(f"Failed to push: {output}")
    
    return success


async def cleanup_worktree(worktree_path: Path) -> None:
    """
    Remove a task worktree after completion.
    
    Args:
        worktree_path: Path to the worktree to remove
    """
    if not worktree_path.exists():
        return
    
    # Find the bare repo (parent's .bare)
    bare_path = worktree_path.parent / ".bare"
    
    if bare_path.exists():
        # Use git worktree remove
        await _run_git_command(
            f'git worktree remove "{worktree_path}" --force',
            cwd=bare_path,
        )
    else:
        # Fallback to manual removal
        shutil.rmtree(worktree_path, ignore_errors=True)
    
    logger.info(f"Cleaned up worktree at {worktree_path}")


async def _update_repo_path_in_db(user_id: UUID, repo_url: str, local_path: str) -> None:
    """Update the local_path in the repos table."""
    try:
        from src.db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE repos
                SET local_path = $1, last_synced = NOW()
                WHERE user_id = $2 AND github_url = $3
                """,
                local_path, user_id, repo_url
            )
    except Exception as e:
        logger.error(f"Failed to update repo path in DB: {e}")


# === High-level API for voice agent ===

async def get_repo_for_task(
    user_context: dict,
    task_description: str | None = None,
    repo_hint: str | None = None,
) -> tuple[Path, str, str] | None:
    """
    Get a worktree ready for a task.
    
    This is the main entry point for the voice agent.
    Creates a new feature branch and worktree for the task.
    
    Args:
        user_context: User context from pipeline
        task_description: Description of what the agent will do
        repo_hint: Optional repo name hint (e.g., "my-api")
        
    Returns:
        Tuple of (worktree_path, branch_name, repo_url) or None
    """
    repos = user_context.get("repos", [])
    credentials = user_context.get("credentials", {})
    user_id = user_context.get("user_id")
    
    if not repos or not user_id:
        return None
    
    github_creds = credentials.get("github")
    if not github_creds:
        return None
    
    github_token = github_creds.get("access_token")
    if not github_token:
        return None
    
    # Find the right repo
    repo_url = None
    if repo_hint:
        hint_lower = repo_hint.lower()
        for repo in repos:
            url = repo.get("github_url", "")
            if hint_lower in url.lower():
                repo_url = url
                break
    
    if not repo_url:
        repo_url = repos[0].get("github_url", "")
    
    if not repo_url:
        return None
    
    # Create task worktree
    worktree_path, branch_name = await create_task_worktree(
        user_id=user_id,
        repo_url=repo_url,
        github_token=github_token,
        task_description=task_description,
    )
    
    return worktree_path, branch_name, repo_url


async def get_main_for_reading(
    user_context: dict,
    repo_hint: str | None = None,
) -> tuple[Path, str] | None:
    """
    Get the main worktree for read-only operations.
    
    Use this for code analysis, exploration, etc.
    Does NOT create a feature branch.
    
    Args:
        user_context: User context from pipeline
        repo_hint: Optional repo name hint
        
    Returns:
        Tuple of (main_path, repo_url) or None
    """
    repos = user_context.get("repos", [])
    credentials = user_context.get("credentials", {})
    user_id = user_context.get("user_id")
    
    if not repos or not user_id:
        return None
    
    github_creds = credentials.get("github")
    if not github_creds:
        return None
    
    github_token = github_creds.get("access_token")
    if not github_token:
        return None
    
    # Find the right repo
    repo_url = None
    if repo_hint:
        hint_lower = repo_hint.lower()
        for repo in repos:
            url = repo.get("github_url", "")
            if hint_lower in url.lower():
                repo_url = url
                break
    
    if not repo_url:
        repo_url = repos[0].get("github_url", "")
    
    if not repo_url:
        return None
    
    # Ensure setup and sync main
    main_path = await sync_main(user_id, repo_url, github_token)
    
    return main_path, repo_url
