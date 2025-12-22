"""
Production-grade repository management using git worktrees.

Architecture:
  /app/user-repos/{user_id}/{repo}/
    .bare/                    # Bare clone (shared git objects)
    main/                     # Worktree: main branch (reference)
    work-{task_id}/           # Worktree: feature branch for task

Edge cases handled:
- Merge conflicts (rebase with conflict detection)
- Branch name collisions (retry with unique suffix)
- Token expiration (detect 401/403)
- Network failures (retry with backoff)
- Corrupted git state (detect and recover)
- Disk space (cleanup old worktrees)
- Concurrent tasks (isolated worktrees)
- Large repos (shallow clone option)

Best practices:
- Always fetch before branching
- Conventional commit messages
- Run tests before pushing
- Rebase onto latest main before PR
- Descriptive PR descriptions
- Auto-cleanup stale worktrees
"""

import asyncio
import shutil
import uuid
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import NamedTuple
from uuid import UUID

from loguru import logger

REPOS_BASE_DIR = Path("/app/user-repos")
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds
STALE_WORKTREE_DAYS = 7


class GitError(Exception):
    """Base exception for git operations."""
    pass


class AuthenticationError(GitError):
    """Token expired or invalid."""
    pass


class ConflictError(GitError):
    """Merge/rebase conflict detected."""
    pass


class NetworkError(GitError):
    """Network-related failure."""
    pass


class RepoNotFoundError(GitError):
    """Repository doesn't exist or was deleted."""
    pass


class TaskResult(NamedTuple):
    """Result of a task operation."""
    success: bool
    message: str
    data: dict | None = None


class CommitType(Enum):
    """Conventional commit types."""
    FIX = "fix"
    FEAT = "feat"
    DOCS = "docs"
    STYLE = "style"
    REFACTOR = "refactor"
    TEST = "test"
    CHORE = "chore"


def _extract_repo_name(repo_url: str) -> str:
    """Extract repo name from GitHub URL."""
    name = repo_url.rstrip("/").split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name


def _get_repo_base_path(user_id: UUID, repo_url: str) -> Path:
    """Get base path for a user's repo."""
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


def _sanitize_output(output: str, github_token: str | None) -> str:
    """Remove sensitive data from git output."""
    if github_token and github_token in output:
        output = output.replace(github_token, "***")
    return output


def _parse_git_error(output: str) -> GitError:
    """Parse git error output and return appropriate exception."""
    output_lower = output.lower()
    
    if "authentication" in output_lower or "401" in output or "403" in output:
        return AuthenticationError("GitHub token expired or invalid")
    
    if "could not resolve host" in output_lower or "network" in output_lower:
        return NetworkError("Network error - check connection")
    
    if "not found" in output_lower or "404" in output:
        return RepoNotFoundError("Repository not found - may have been deleted")
    
    if "conflict" in output_lower or "merge conflict" in output_lower:
        return ConflictError("Merge conflict detected")
    
    if "already exists" in output_lower:
        return GitError("Resource already exists")
    
    return GitError(output)


async def _run_git_command(
    cmd: str,
    cwd: Path | None = None,
    github_token: str | None = None,
    retries: int = MAX_RETRIES,
) -> tuple[bool, str]:
    """
    Run a git command with retry logic and error parsing.
    
    Returns:
        Tuple of (success, output)
    """
    full_cmd = f'cd "{cwd}" && {cmd}' if cwd else cmd
    
    for attempt in range(retries):
        process = await asyncio.create_subprocess_shell(
            full_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        
        stdout, stderr = await process.communicate()
        output = stdout.decode().strip() or stderr.decode().strip()
        output = _sanitize_output(output, github_token)
        
        if process.returncode == 0:
            return True, output
        
        # Check if error is retryable
        if "network" in output.lower() or "timeout" in output.lower():
            if attempt < retries - 1:
                logger.warning(f"Git command failed (attempt {attempt + 1}), retrying...")
                await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                continue
        
        # Non-retryable error
        break
    
    return False, output


async def _detect_default_branch(bare_path: Path, github_token: str) -> str:
    """Detect the default branch (main or master)."""
    success, output = await _run_git_command(
        "git symbolic-ref refs/remotes/origin/HEAD",
        cwd=bare_path,
        github_token=github_token,
        retries=1,
    )
    
    if success and output:
        # refs/remotes/origin/main -> main
        return output.split("/")[-1]
    
    # Fallback: check if main exists
    success, _ = await _run_git_command(
        "git rev-parse --verify origin/main",
        cwd=bare_path,
        retries=1,
    )
    
    return "main" if success else "master"


async def cleanup_stale_worktrees(user_id: UUID, repo_url: str) -> int:
    """
    Remove worktrees older than STALE_WORKTREE_DAYS.
    
    Returns:
        Number of worktrees cleaned up
    """
    base_path = _get_repo_base_path(user_id, repo_url)
    bare_path = _get_bare_path(user_id, repo_url)
    
    if not base_path.exists():
        return 0
    
    cleaned = 0
    cutoff = datetime.now() - timedelta(days=STALE_WORKTREE_DAYS)
    
    for item in base_path.iterdir():
        if item.name.startswith("work-") and item.is_dir():
            # Check modification time
            mtime = datetime.fromtimestamp(item.stat().st_mtime)
            if mtime < cutoff:
                logger.info(f"Cleaning up stale worktree: {item}")
                await _run_git_command(
                    f'git worktree remove "{item}" --force',
                    cwd=bare_path,
                )
                if item.exists():
                    shutil.rmtree(item, ignore_errors=True)
                cleaned += 1
    
    # Prune worktree references
    if bare_path.exists():
        await _run_git_command("git worktree prune", cwd=bare_path)
    
    return cleaned


async def setup_repo(
    user_id: UUID,
    repo_url: str,
    github_token: str,
    shallow: bool = True,
) -> Path:
    """
    Set up a repository with bare clone + main worktree.
    
    Args:
        user_id: User UUID
        repo_url: GitHub repo URL
        github_token: GitHub access token
        shallow: Use shallow clone for large repos
        
    Returns:
        Path to the main worktree
        
    Raises:
        AuthenticationError: Token invalid
        RepoNotFoundError: Repo doesn't exist
        GitError: Other git errors
    """
    bare_path = _get_bare_path(user_id, repo_url)
    main_path = _get_main_worktree_path(user_id, repo_url)
    
    # Already set up?
    if bare_path.exists() and main_path.exists():
        logger.info(f"Repo already set up at {main_path}")
        await sync_main(user_id, repo_url, github_token)
        return main_path
    
    # Cleanup any partial state
    if bare_path.exists() and not main_path.exists():
        logger.warning("Found bare repo without main worktree, cleaning up...")
        shutil.rmtree(bare_path.parent, ignore_errors=True)
    
    # Create directory structure
    bare_path.parent.mkdir(parents=True, exist_ok=True)
    
    auth_url = _build_auth_url(repo_url, github_token)
    
    # Clone as bare repo
    logger.info(f"Setting up repo {repo_url} for user {user_id}")
    
    depth_arg = "--depth 100" if shallow else ""
    success, output = await _run_git_command(
        f'git clone --bare {depth_arg} "{auth_url}" "{bare_path}"',
        github_token=github_token,
    )
    
    if not success:
        raise _parse_git_error(output)
    
    # Configure for proper fetching
    await _run_git_command(
        'git config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*"',
        cwd=bare_path,
    )
    
    # Fetch to populate remote refs
    await _run_git_command(
        f'git fetch "{auth_url}"',
        cwd=bare_path,
        github_token=github_token,
    )
    
    # Detect default branch
    default_branch = await _detect_default_branch(bare_path, github_token)
    
    # Create main worktree
    success, output = await _run_git_command(
        f'git worktree add "{main_path}" {default_branch}',
        cwd=bare_path,
        github_token=github_token,
    )
    
    if not success:
        # Try origin/branch
        success, output = await _run_git_command(
            f'git worktree add "{main_path}" origin/{default_branch}',
            cwd=bare_path,
            github_token=github_token,
        )
    
    if not success:
        raise GitError(f"Failed to create main worktree: {output}")
    
    logger.info(f"Repo set up successfully at {main_path}")
    
    # Update database
    await _update_repo_path_in_db(user_id, repo_url, str(main_path))
    
    # Cleanup old worktrees
    await cleanup_stale_worktrees(user_id, repo_url)
    
    return main_path


async def sync_main(
    user_id: UUID,
    repo_url: str,
    github_token: str,
) -> Path:
    """
    Sync the main worktree with remote.
    
    Returns:
        Path to the main worktree
    """
    bare_path = _get_bare_path(user_id, repo_url)
    main_path = _get_main_worktree_path(user_id, repo_url)
    
    if not bare_path.exists():
        return await setup_repo(user_id, repo_url, github_token)
    
    auth_url = _build_auth_url(repo_url, github_token)
    
    # Fetch in bare repo
    success, output = await _run_git_command(
        f'git fetch "{auth_url}" --prune',
        cwd=bare_path,
        github_token=github_token,
    )
    
    if not success:
        error = _parse_git_error(output)
        if isinstance(error, AuthenticationError):
            raise error
        logger.warning(f"Fetch warning: {output}")
    
    # Reset main worktree to match remote
    if main_path.exists():
        default_branch = await _detect_default_branch(bare_path, github_token)
        await _run_git_command(
            f"git reset --hard origin/{default_branch}",
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
    commit_type: CommitType = CommitType.FIX,
) -> tuple[Path, str]:
    """
    Create a new worktree for a task on a feature branch.
    
    Always fetches latest before creating to ensure fresh code.
    
    Args:
        user_id: User UUID
        repo_url: GitHub repo URL
        github_token: GitHub access token
        task_description: Description for branch name
        commit_type: Type of change (fix, feat, etc.)
        
    Returns:
        Tuple of (worktree_path, branch_name)
    """
    bare_path = _get_bare_path(user_id, repo_url)
    base_path = _get_repo_base_path(user_id, repo_url)
    
    # Ensure repo is set up
    if not bare_path.exists():
        await setup_repo(user_id, repo_url, github_token)
    
    # Always fetch latest before creating task branch
    auth_url = _build_auth_url(repo_url, github_token)
    success, output = await _run_git_command(
        f'git fetch "{auth_url}" --prune',
        cwd=bare_path,
        github_token=github_token,
    )
    
    if not success:
        error = _parse_git_error(output)
        if isinstance(error, (AuthenticationError, NetworkError)):
            raise error
    
    # Detect default branch
    default_branch = await _detect_default_branch(bare_path, github_token)
    
    # Generate branch name with retry for collisions
    for attempt in range(3):
        task_id = str(uuid.uuid4())[:8]
        
        if task_description:
            # Sanitize description for branch name
            safe_desc = "".join(
                c if c.isalnum() or c == "-" else "-" 
                for c in task_description.lower()
            )
            safe_desc = "-".join(filter(None, safe_desc.split("-")))[:30]
            branch_name = f"agent/{commit_type.value}/{safe_desc}-{task_id}"
        else:
            branch_name = f"agent/{commit_type.value}/task-{task_id}"
        
        worktree_path = base_path / f"work-{task_id}"
        
        # Create new branch from default branch
        success, output = await _run_git_command(
            f'git worktree add -b "{branch_name}" "{worktree_path}" origin/{default_branch}',
            cwd=bare_path,
            github_token=github_token,
        )
        
        if success:
            logger.info(f"Created task worktree at {worktree_path} on branch {branch_name}")
            return worktree_path, branch_name
        
        if "already exists" not in output.lower():
            raise GitError(f"Failed to create task worktree: {output}")
        
        # Branch exists, retry with new ID
        logger.debug(f"Branch {branch_name} exists, retrying...")
    
    raise GitError("Failed to create unique branch name after 3 attempts")


async def commit_changes(
    worktree_path: Path,
    message: str,
    commit_type: CommitType = CommitType.FIX,
    scope: str | None = None,
) -> TaskResult:
    """
    Commit changes with conventional commit message.
    
    Args:
        worktree_path: Path to the worktree
        message: Commit message (without type prefix)
        commit_type: Type of change
        scope: Optional scope (e.g., "auth", "api")
        
    Returns:
        TaskResult with success status
    """
    # Check if there are changes to commit
    success, output = await _run_git_command(
        "git status --porcelain",
        cwd=worktree_path,
    )
    
    if not output.strip():
        return TaskResult(False, "No changes to commit", None)
    
    # Stage all changes
    await _run_git_command("git add -A", cwd=worktree_path)
    
    # Format conventional commit message
    if scope:
        full_message = f"{commit_type.value}({scope}): {message}"
    else:
        full_message = f"{commit_type.value}: {message}"
    
    # Commit
    success, output = await _run_git_command(
        f'git commit -m "{full_message}"',
        cwd=worktree_path,
    )
    
    if success:
        return TaskResult(True, f"Committed: {full_message}", {"message": full_message})
    else:
        return TaskResult(False, f"Commit failed: {output}", None)


async def rebase_on_latest(
    worktree_path: Path,
    bare_path: Path,
    github_token: str,
    repo_url: str,
) -> TaskResult:
    """
    Rebase current branch on latest main.
    
    This ensures clean history and catches conflicts early.
    
    Returns:
        TaskResult indicating success or conflict
    """
    auth_url = _build_auth_url(repo_url, github_token)
    
    # Fetch latest
    await _run_git_command(
        f'git fetch "{auth_url}"',
        cwd=bare_path,
        github_token=github_token,
    )
    
    # Detect default branch
    default_branch = await _detect_default_branch(bare_path, github_token)
    
    # Attempt rebase
    success, output = await _run_git_command(
        f"git rebase origin/{default_branch}",
        cwd=worktree_path,
        github_token=github_token,
    )
    
    if success:
        return TaskResult(True, "Rebased successfully", None)
    
    if "conflict" in output.lower():
        # Abort the rebase
        await _run_git_command("git rebase --abort", cwd=worktree_path)
        return TaskResult(
            False, 
            "Rebase conflict - changes conflict with recent updates to main",
            {"conflict": True}
        )
    
    return TaskResult(False, f"Rebase failed: {output}", None)


async def push_branch(
    worktree_path: Path,
    github_token: str,
    repo_url: str,
    force: bool = False,
) -> TaskResult:
    """
    Push the current branch to remote.
    
    Args:
        worktree_path: Path to the worktree
        github_token: GitHub access token
        repo_url: GitHub repo URL
        force: Force push (after rebase)
        
    Returns:
        TaskResult with branch name
    """
    auth_url = _build_auth_url(repo_url, github_token)
    
    # Get current branch name
    success, branch = await _run_git_command(
        "git rev-parse --abbrev-ref HEAD",
        cwd=worktree_path,
    )
    
    if not success:
        return TaskResult(False, "Failed to get branch name", None)
    
    # Push to remote
    force_flag = "--force-with-lease" if force else ""
    success, output = await _run_git_command(
        f'git push {force_flag} -u "{auth_url}" {branch}',
        cwd=worktree_path,
        github_token=github_token,
    )
    
    if success:
        logger.info(f"Pushed branch {branch}")
        return TaskResult(True, f"Pushed branch {branch}", {"branch": branch})
    
    error = _parse_git_error(output)
    if isinstance(error, AuthenticationError):
        raise error
    
    return TaskResult(False, f"Push failed: {output}", None)


async def cleanup_worktree(
    worktree_path: Path,
    delete_remote_branch: bool = False,
    github_token: str | None = None,
    repo_url: str | None = None,
) -> None:
    """
    Remove a task worktree after completion.
    
    Args:
        worktree_path: Path to the worktree to remove
        delete_remote_branch: Also delete the remote branch
        github_token: Required if deleting remote branch
        repo_url: Required if deleting remote branch
    """
    if not worktree_path.exists():
        return
    
    # Get branch name before cleanup
    branch = None
    if delete_remote_branch:
        success, branch = await _run_git_command(
            "git rev-parse --abbrev-ref HEAD",
            cwd=worktree_path,
        )
    
    # Find the bare repo
    bare_path = worktree_path.parent / ".bare"
    
    if bare_path.exists():
        # Use git worktree remove
        await _run_git_command(
            f'git worktree remove "{worktree_path}" --force',
            cwd=bare_path,
        )
        
        # Delete remote branch if requested
        if delete_remote_branch and branch and github_token and repo_url:
            auth_url = _build_auth_url(repo_url, github_token)
            await _run_git_command(
                f'git push "{auth_url}" --delete {branch}',
                cwd=bare_path,
                github_token=github_token,
            )
    
    # Fallback cleanup
    if worktree_path.exists():
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
    commit_type: CommitType = CommitType.FIX,
) -> tuple[Path, str, str] | None:
    """
    Get a worktree ready for a task.
    
    This is the main entry point for the voice agent.
    Creates a new feature branch and worktree for the task.
    
    Args:
        user_context: User context from pipeline
        task_description: Description of what the agent will do
        repo_hint: Optional repo name hint (e.g., "my-api")
        commit_type: Type of change (fix, feat, etc.)
        
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
    
    try:
        # Create task worktree (always fetches latest)
        worktree_path, branch_name = await create_task_worktree(
            user_id=user_id,
            repo_url=repo_url,
            github_token=github_token,
            task_description=task_description,
            commit_type=commit_type,
        )
        
        return worktree_path, branch_name, repo_url
        
    except AuthenticationError:
        logger.error("GitHub token expired")
        return None
    except RepoNotFoundError:
        logger.error(f"Repo not found: {repo_url}")
        return None
    except GitError as e:
        logger.error(f"Git error: {e}")
        return None


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
    
    try:
        # Ensure setup and sync main
        main_path = await sync_main(user_id, repo_url, github_token)
        return main_path, repo_url
        
    except AuthenticationError:
        logger.error("GitHub token expired")
        return None
    except GitError as e:
        logger.error(f"Git error: {e}")
        return None


async def ship_changes(
    worktree_path: Path,
    repo_url: str,
    branch_name: str,
    github_token: str,
    title: str,
    description: str = "",
    run_tests_command: str | None = None,
    auto_merge: bool = False,
) -> TaskResult:
    """
    Complete workflow: commit, test, rebase, push, create PR.
    
    This is the "ship it" command for the voice agent.
    
    Args:
        worktree_path: Path to the worktree
        repo_url: GitHub repo URL
        branch_name: Branch name
        github_token: GitHub access token
        title: PR title
        description: PR description
        run_tests_command: Optional test command to run first
        auto_merge: Whether to auto-merge if tests pass
        
    Returns:
        TaskResult with PR URL or error
    """
    bare_path = worktree_path.parent / ".bare"
    
    # 1. Run tests if specified
    if run_tests_command:
        logger.info(f"Running tests: {run_tests_command}")
        success, output = await _run_git_command(
            run_tests_command,
            cwd=worktree_path,
        )
        if not success:
            return TaskResult(
                False,
                f"Tests failed - not shipping. Output: {output[:200]}",
                {"tests_failed": True}
            )
        logger.info("Tests passed")
    
    # 2. Rebase on latest main
    rebase_result = await rebase_on_latest(worktree_path, bare_path, github_token, repo_url)
    if not rebase_result.success:
        return rebase_result
    
    # 3. Push (force after rebase)
    push_result = await push_branch(worktree_path, github_token, repo_url, force=True)
    if not push_result.success:
        return push_result
    
    # 4. Create PR
    from src.github.actions import create_pull_request, parse_repo_url
    
    try:
        owner, repo = parse_repo_url(repo_url)
    except ValueError as e:
        return TaskResult(False, str(e), None)
    
    # Detect default branch for PR base
    default_branch = await _detect_default_branch(bare_path, github_token)
    
    pr = await create_pull_request(
        owner=owner,
        repo=repo,
        title=title,
        body=description,
        head=branch_name,
        base=default_branch,
        github_token=github_token,
    )
    
    if not pr:
        return TaskResult(False, "Failed to create PR", None)
    
    pr_url = pr.get("html_url", "")
    pr_number = pr.get("number")
    
    logger.info(f"Created PR #{pr_number}: {pr_url}")
    
    # 5. Auto-merge if requested and tests passed
    if auto_merge:
        # TODO: Enable auto-merge via GitHub API
        pass
    
    return TaskResult(
        True,
        f"Created PR #{pr_number}",
        {"pr_url": pr_url, "pr_number": pr_number, "branch": branch_name}
    )
