"""
Claude Code Action workflow templates.

These can be added to user repos to enable:
1. @claude mentions in PRs/issues for interactive help
2. Automated PR review on open/sync
3. Manual workflow dispatch for on-demand reviews
"""

# =============================================================================
# Interactive Mode - Responds to @claude mentions
# =============================================================================
CLAUDE_INTERACTIVE_WORKFLOW = '''name: Claude Assistant

on:
  issue_comment:
    types: [created]
  pull_request_review_comment:
    types: [created]
  issues:
    types: [opened, assigned]

permissions:
  contents: write
  pull-requests: write
  issues: write
  id-token: write

jobs:
  claude:
    if: |
      (github.event_name == 'issue_comment' && contains(github.event.comment.body, '@claude')) ||
      (github.event_name == 'pull_request_review_comment' && contains(github.event.comment.body, '@claude')) ||
      (github.event_name == 'issues' && contains(github.event.issue.body, '@claude'))
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          fetch-depth: 20

      - name: Claude Code Action
        uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          # Responds to @claude mentions automatically
'''

# =============================================================================
# Auto Review Mode - Reviews every PR automatically
# =============================================================================
CLAUDE_AUTO_REVIEW_WORKFLOW = '''name: Claude PR Review

on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions:
  contents: read
  pull-requests: write
  issues: write
  id-token: write

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          fetch-depth: 20

      - name: Claude Code Action
        uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          prompt: |
            Review this PR for:
            1. Code quality and best practices
            2. Potential bugs or edge cases
            3. Security vulnerabilities
            4. Performance issues
            5. Test coverage gaps
            
            Be concise but thorough. Approve if changes look good,
            request changes if there are issues.
          claude_args: "--max-turns 5"
'''

# =============================================================================
# LLM-as-Judge Mode - Structured evaluation with scoring
# =============================================================================
CLAUDE_JUDGE_WORKFLOW = '''name: Claude PR Judge

on:
  pull_request:
    types: [opened, synchronize, reopened]
  workflow_dispatch:
    inputs:
      pr_number:
        description: 'PR number to evaluate'
        required: true
        type: string

permissions:
  contents: read
  pull-requests: write
  issues: write
  id-token: write

jobs:
  judge:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          fetch-depth: 20

      - name: Claude Code Action
        uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          prompt: |
            You are a code review judge. Evaluate this PR using the following rubric:
            
            ## Evaluation Criteria (score 1-5 for each)
            
            ### 1. Code Quality
            - Clean, readable code
            - Follows project conventions
            - No code smells
            
            ### 2. Correctness
            - Logic is sound
            - Edge cases handled
            - No obvious bugs
            
            ### 3. Security
            - No vulnerabilities introduced
            - Proper input validation
            - No secrets exposed
            
            ### 4. Performance
            - Efficient algorithms
            - No unnecessary operations
            - Proper resource management
            
            ### 5. Testing
            - Adequate test coverage
            - Tests are meaningful
            - Edge cases tested
            
            ## Output Format
            
            Provide your evaluation as:
            
            ```
            ## PR Evaluation
            
            | Criteria | Score | Notes |
            |----------|-------|-------|
            | Code Quality | X/5 | ... |
            | Correctness | X/5 | ... |
            | Security | X/5 | ... |
            | Performance | X/5 | ... |
            | Testing | X/5 | ... |
            
            **Overall Score: X/25**
            
            ### Verdict: [APPROVE / REQUEST_CHANGES / NEEDS_DISCUSSION]
            
            ### Summary
            [2-3 sentence summary]
            
            ### Required Changes (if any)
            - ...
            ```
          claude_args: "--max-turns 3 --model claude-sonnet-4-5-20250929"
'''

# =============================================================================
# Combined Workflow - All features in one
# =============================================================================
CLAUDE_FULL_WORKFLOW = '''name: Claude Code

on:
  issue_comment:
    types: [created]
  pull_request_review_comment:
    types: [created]
  pull_request:
    types: [opened, synchronize]
  issues:
    types: [opened, assigned]

permissions:
  contents: write
  pull-requests: write
  issues: write
  actions: read
  id-token: write

jobs:
  # Respond to @claude mentions
  interactive:
    if: |
      (github.event_name == 'issue_comment' && contains(github.event.comment.body, '@claude')) ||
      (github.event_name == 'pull_request_review_comment' && contains(github.event.comment.body, '@claude')) ||
      (github.event_name == 'issues' && contains(github.event.issue.body, '@claude'))
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          fetch-depth: 20

      - name: Claude Code Action
        uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          additional_permissions: |
            actions: read

  # Auto-review new PRs
  auto-review:
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          fetch-depth: 20

      - name: Claude Code Action
        uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          prompt: |
            Review this PR briefly. Focus on:
            - Potential bugs or issues
            - Security concerns
            - Code quality
            
            Keep feedback actionable and concise.
          claude_args: "--max-turns 3"
'''


def get_workflow_content(workflow_type: str = "full") -> str:
    """
    Get the Claude Code Action workflow YAML content.
    
    Args:
        workflow_type: One of "interactive", "auto_review", "judge", or "full"
        
    Returns:
        Workflow YAML content
    """
    workflows = {
        "interactive": CLAUDE_INTERACTIVE_WORKFLOW,
        "auto_review": CLAUDE_AUTO_REVIEW_WORKFLOW,
        "judge": CLAUDE_JUDGE_WORKFLOW,
        "full": CLAUDE_FULL_WORKFLOW,
    }
    return workflows.get(workflow_type, CLAUDE_FULL_WORKFLOW)


async def add_workflow_to_repo(
    owner: str,
    repo: str,
    github_token: str,
    workflow_type: str = "full",
    workflow_name: str = "claude.yml",
) -> dict:
    """
    Add Claude Code Action workflow to a repository.
    
    Creates .github/workflows/{workflow_name}
    
    Args:
        owner: Repository owner
        repo: Repository name
        github_token: GitHub access token
        workflow_type: Type of workflow to add
        workflow_name: Name of the workflow file
        
    Returns:
        Result dict with success status and message
    """
    import base64
    import httpx
    
    workflow_content = get_workflow_content(workflow_type)
    path = f".github/workflows/{workflow_name}"
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    
    async with httpx.AsyncClient() as client:
        # Check if file already exists
        check_resp = await client.get(url, headers=headers)
        
        # Encode content
        content = base64.b64encode(workflow_content.encode()).decode()
        
        payload = {
            "message": f"Add Claude Code Action workflow ({workflow_type})",
            "content": content,
        }
        
        # If file exists, include its SHA for update
        if check_resp.status_code == 200:
            existing = check_resp.json()
            payload["sha"] = existing["sha"]
            payload["message"] = f"Update Claude Code Action workflow ({workflow_type})"
        
        resp = await client.put(url, headers=headers, json=payload)
        
        if resp.status_code in (200, 201):
            return {
                "success": True,
                "message": f"Workflow added: {path}",
                "url": f"https://github.com/{owner}/{repo}/blob/main/{path}",
            }
        else:
            return {
                "success": False,
                "message": f"Failed to add workflow: {resp.status_code} - {resp.text[:200]}",
            }


async def check_workflow_exists(
    owner: str,
    repo: str,
    github_token: str,
    workflow_name: str = "claude.yml",
) -> bool:
    """Check if Claude workflow already exists in repo."""
    import httpx
    
    path = f".github/workflows/{workflow_name}"
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github.v3+json",
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers)
        return resp.status_code == 200


async def setup_repo_for_claude(
    owner: str,
    repo: str,
    github_token: str,
    workflow_type: str = "full",
) -> dict:
    """
    Full setup for Claude Code Action on a repo.
    
    1. Adds the workflow file
    2. Checks for CLAUDE.md (optional)
    3. Returns setup status
    
    Note: User must manually add ANTHROPIC_API_KEY secret
    
    Args:
        owner: Repository owner
        repo: Repository name
        github_token: GitHub access token
        workflow_type: Type of workflow to add
        
    Returns:
        Setup result dict
    """
    import httpx
    
    result = {
        "workflow_added": False,
        "claude_md_exists": False,
        "needs_api_key": True,
        "messages": [],
    }
    
    # Add workflow
    workflow_result = await add_workflow_to_repo(
        owner, repo, github_token, workflow_type
    )
    result["workflow_added"] = workflow_result["success"]
    result["messages"].append(workflow_result["message"])
    
    # Check for CLAUDE.md
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github.v3+json",
    }
    
    async with httpx.AsyncClient() as client:
        claude_md_url = f"https://api.github.com/repos/{owner}/{repo}/contents/CLAUDE.md"
        resp = await client.get(claude_md_url, headers=headers)
        result["claude_md_exists"] = resp.status_code == 200
    
    if not result["claude_md_exists"]:
        result["messages"].append(
            "Tip: Add a CLAUDE.md file to define coding standards for Claude"
        )
    
    result["messages"].append(
        f"Action required: Add ANTHROPIC_API_KEY secret at "
        f"https://github.com/{owner}/{repo}/settings/secrets/actions"
    )
    
    return result
