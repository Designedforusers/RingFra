"""
GitHub Actions integration for triggering Claude Code Action and tests.
"""

import asyncio
from typing import Literal

import httpx
from loguru import logger

GITHUB_API = "https://api.github.com"


async def trigger_workflow(
    owner: str,
    repo: str,
    workflow_id: str,
    ref: str,
    github_token: str,
    inputs: dict | None = None,
) -> dict | None:
    """
    Trigger a GitHub Actions workflow.
    
    Args:
        owner: Repository owner
        repo: Repository name
        workflow_id: Workflow file name or ID
        ref: Branch/tag to run on
        github_token: GitHub access token
        inputs: Optional workflow inputs
        
    Returns:
        Workflow run info if successful
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches"
    
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    
    payload = {"ref": ref}
    if inputs:
        payload["inputs"] = inputs
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, json=payload)
        
        if resp.status_code == 204:
            logger.info(f"Triggered workflow {workflow_id} on {owner}/{repo}")
            # Get the latest run
            await asyncio.sleep(2)  # Wait for run to be created
            return await get_latest_workflow_run(owner, repo, workflow_id, github_token)
        else:
            logger.error(f"Failed to trigger workflow: {resp.status_code} - {resp.text}")
            return None


async def get_latest_workflow_run(
    owner: str,
    repo: str,
    workflow_id: str,
    github_token: str,
) -> dict | None:
    """Get the latest run of a workflow."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs"
    
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github.v3+json",
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers, params={"per_page": 1})
        
        if resp.status_code == 200:
            runs = resp.json().get("workflow_runs", [])
            return runs[0] if runs else None
        return None


async def get_workflow_status(
    owner: str,
    repo: str,
    run_id: int,
    github_token: str,
) -> dict | None:
    """Get the status of a workflow run."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/actions/runs/{run_id}"
    
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github.v3+json",
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers)
        
        if resp.status_code == 200:
            return resp.json()
        return None


async def wait_for_workflow(
    owner: str,
    repo: str,
    run_id: int,
    github_token: str,
    timeout: int = 600,
    poll_interval: int = 10,
) -> dict:
    """
    Wait for a workflow run to complete.
    
    Args:
        owner: Repository owner
        repo: Repository name
        run_id: Workflow run ID
        github_token: GitHub access token
        timeout: Maximum wait time in seconds
        poll_interval: Time between status checks
        
    Returns:
        Final workflow status
    """
    elapsed = 0
    
    while elapsed < timeout:
        status = await get_workflow_status(owner, repo, run_id, github_token)
        
        if status:
            conclusion = status.get("conclusion")
            if conclusion:  # None means still running
                logger.info(f"Workflow {run_id} completed with: {conclusion}")
                return status
        
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    
    return {"status": "timeout", "conclusion": "timeout"}


async def trigger_claude_review(
    owner: str,
    repo: str,
    pr_number: int,
    github_token: str,
    model: str = "claude-sonnet-4-6",
    effort: Literal["low", "medium", "high"] = "medium",
) -> dict | None:
    """
    Trigger Claude Code Action to review a PR.
    
    Args:
        owner: Repository owner
        repo: Repository name
        pr_number: Pull request number
        github_token: GitHub access token
        model: Claude model to use
        effort: Review effort level
        
    Returns:
        Workflow run info
    """
    logger.info(f"Triggering Claude review for PR #{pr_number} on {owner}/{repo}")
    
    return await trigger_workflow(
        owner=owner,
        repo=repo,
        workflow_id="claude-code-action.yml",
        ref="main",
        github_token=github_token,
        inputs={
            "pr_number": str(pr_number),
            "model": model,
            "effort": effort,
        },
    )


async def trigger_tests(
    owner: str,
    repo: str,
    branch: str,
    github_token: str,
    workflow_id: str = "test.yml",
) -> dict | None:
    """
    Trigger test workflow on a branch.
    
    Args:
        owner: Repository owner
        repo: Repository name
        branch: Branch to test
        github_token: GitHub access token
        workflow_id: Test workflow file name
        
    Returns:
        Workflow run info
    """
    logger.info(f"Triggering tests on {owner}/{repo} branch {branch}")
    
    return await trigger_workflow(
        owner=owner,
        repo=repo,
        workflow_id=workflow_id,
        ref=branch,
        github_token=github_token,
    )


async def create_pull_request(
    owner: str,
    repo: str,
    title: str,
    body: str,
    head: str,
    base: str,
    github_token: str,
) -> dict | None:
    """Create a pull request."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls"
    
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github.v3+json",
    }
    
    payload = {
        "title": title,
        "body": body,
        "head": head,
        "base": base,
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, json=payload)
        
        if resp.status_code == 201:
            pr = resp.json()
            logger.info(f"Created PR #{pr['number']}: {title}")
            return pr
        else:
            logger.error(f"Failed to create PR: {resp.status_code} - {resp.text}")
            return None


def parse_repo_url(repo_url: str) -> tuple[str, str]:
    """
    Parse owner and repo from a GitHub URL.
    
    Args:
        repo_url: URL like https://github.com/owner/repo
        
    Returns:
        Tuple of (owner, repo)
    """
    # Remove .git suffix if present
    url = repo_url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    
    # Extract owner/repo
    parts = url.split("/")
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    
    raise ValueError(f"Invalid repo URL: {repo_url}")


async def push_and_create_pr(
    worktree_path,
    repo_url: str,
    branch_name: str,
    github_token: str,
    title: str,
    body: str = "",
    base: str = "main",
) -> dict | None:
    """
    Push branch and create a pull request.
    
    High-level helper that combines push + PR creation.
    
    Args:
        worktree_path: Path to the worktree (for pushing)
        repo_url: GitHub repo URL
        branch_name: Branch to push and PR from
        github_token: GitHub access token
        title: PR title
        body: PR body/description
        base: Base branch (default: main)
        
    Returns:
        PR info dict or None if failed
    """
    from src.repos.manager import push_branch
    
    # Push the branch
    success = await push_branch(worktree_path, github_token, repo_url)
    if not success:
        logger.error("Failed to push branch")
        return None
    
    # Parse owner/repo
    try:
        owner, repo = parse_repo_url(repo_url)
    except ValueError as e:
        logger.error(str(e))
        return None
    
    # Create the PR
    pr = await create_pull_request(
        owner=owner,
        repo=repo,
        title=title,
        body=body,
        head=branch_name,
        base=base,
        github_token=github_token,
    )
    
    return pr
