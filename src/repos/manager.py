"""
Repository cloning and management.
"""

import asyncio
import os
import shutil
from pathlib import Path
from uuid import UUID

from loguru import logger

from src.config import settings

# Base directory for user repos
REPOS_BASE_DIR = Path("/app/user-repos")


def get_repo_path(user_id: UUID, repo_url: str) -> Path:
    """
    Get the local path for a user's repo.
    
    Structure: /app/user-repos/{user_id}/{repo_name}
    """
    # Extract repo name from URL
    # https://github.com/user/repo → repo
    # https://github.com/user/repo.git → repo
    repo_name = repo_url.rstrip("/").split("/")[-1]
    if repo_name.endswith(".git"):
        repo_name = repo_name[:-4]
    
    return REPOS_BASE_DIR / str(user_id) / repo_name


async def clone_repo(
    user_id: UUID,
    repo_url: str,
    github_token: str,
    branch: str = "main",
) -> Path:
    """
    Clone a repository for a user.
    
    Args:
        user_id: User UUID
        repo_url: GitHub repo URL
        github_token: GitHub access token for auth
        branch: Branch to clone
        
    Returns:
        Path to cloned repo
    """
    repo_path = get_repo_path(user_id, repo_url)
    
    # Create parent directory
    repo_path.parent.mkdir(parents=True, exist_ok=True)
    
    # If already exists, sync instead
    if repo_path.exists():
        logger.info(f"Repo already exists at {repo_path}, syncing instead")
        return await sync_repo(repo_path, github_token)
    
    # Build authenticated URL
    # https://github.com/user/repo → https://{token}@github.com/user/repo
    if "github.com" in repo_url:
        auth_url = repo_url.replace("https://", f"https://{github_token}@")
    else:
        auth_url = repo_url
    
    logger.info(f"Cloning {repo_url} to {repo_path}")
    
    # Clone the repo
    cmd = f'git clone --depth 1 --branch {branch} "{auth_url}" "{repo_path}"'
    
    process = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    
    stdout, stderr = await process.communicate()
    
    if process.returncode != 0:
        error = stderr.decode().strip()
        # Don't log the token
        error = error.replace(github_token, "***")
        logger.error(f"Clone failed: {error}")
        raise RuntimeError(f"Failed to clone repository: {error}")
    
    logger.info(f"Successfully cloned {repo_url}")
    
    # Update local_path in database
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
                str(repo_path), user_id, repo_url
            )
    except Exception as e:
        logger.error(f"Failed to update repo path in DB: {e}")
    
    return repo_path


async def sync_repo(repo_path: Path, github_token: str | None = None) -> Path:
    """
    Sync (pull) an existing repo.
    
    Args:
        repo_path: Path to the repo
        github_token: Optional token to update credentials
        
    Returns:
        The repo path
    """
    if not repo_path.exists():
        raise RuntimeError(f"Repo not found at {repo_path}")
    
    logger.info(f"Syncing repo at {repo_path}")
    
    # Fetch and pull
    cmd = f'cd "{repo_path}" && git fetch origin && git pull origin'
    
    process = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    
    stdout, stderr = await process.communicate()
    
    if process.returncode != 0:
        error = stderr.decode().strip()
        if github_token:
            error = error.replace(github_token, "***")
        logger.warning(f"Sync warning: {error}")
        # Don't fail on sync errors, might just be up to date
    
    logger.info(f"Synced repo at {repo_path}")
    return repo_path


async def ensure_repo_available(
    user_id: UUID,
    repo_url: str,
    github_token: str,
) -> Path:
    """
    Ensure a repo is cloned and up to date.
    
    Clones if not present, syncs if already exists.
    
    Args:
        user_id: User UUID
        repo_url: GitHub repo URL
        github_token: GitHub access token
        
    Returns:
        Path to the repo
    """
    repo_path = get_repo_path(user_id, repo_url)
    
    if repo_path.exists():
        return await sync_repo(repo_path, github_token)
    else:
        return await clone_repo(user_id, repo_url, github_token)


async def delete_repo(user_id: UUID, repo_url: str) -> None:
    """Delete a cloned repo."""
    repo_path = get_repo_path(user_id, repo_url)
    
    if repo_path.exists():
        shutil.rmtree(repo_path)
        logger.info(f"Deleted repo at {repo_path}")


async def get_user_repo_for_task(user_context: dict, repo_hint: str | None = None) -> tuple[Path, str] | None:
    """
    Get the appropriate repo for a user's task.
    
    Args:
        user_context: User context from pipeline
        repo_hint: Optional hint from user (e.g., "my-api")
        
    Returns:
        Tuple of (repo_path, repo_url) or None if no repo available
    """
    repos = user_context.get("repos", [])
    credentials = user_context.get("credentials", {})
    user_id = user_context.get("user_id")
    
    if not repos:
        return None
    
    github_creds = credentials.get("github")
    if not github_creds:
        return None
    
    github_token = github_creds.get("access_token")
    if not github_token:
        return None
    
    # If hint provided, try to match
    if repo_hint:
        hint_lower = repo_hint.lower()
        for repo in repos:
            repo_url = repo.get("github_url", "")
            if hint_lower in repo_url.lower():
                path = await ensure_repo_available(user_id, repo_url, github_token)
                return (path, repo_url)
    
    # Default to first repo
    repo = repos[0]
    repo_url = repo.get("github_url", "")
    
    # Check if already cloned
    local_path = repo.get("local_path")
    if local_path and Path(local_path).exists():
        await sync_repo(Path(local_path), github_token)
        return (Path(local_path), repo_url)
    
    # Clone it
    path = await ensure_repo_available(user_id, repo_url, github_token)
    return (path, repo_url)
