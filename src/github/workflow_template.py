"""
Claude Code Action workflow template.

This can be added to user repos to enable AI-powered PR reviews.
"""

CLAUDE_CODE_ACTION_WORKFLOW = '''name: Claude Code Action

on:
  workflow_dispatch:
    inputs:
      pr_number:
        description: 'PR number to review'
        required: true
        type: string
      model:
        description: 'Claude model to use'
        required: false
        default: 'claude-sonnet-4-20250514'
        type: choice
        options:
          - claude-sonnet-4-20250514
          - claude-opus-4-20250514
      effort:
        description: 'Review effort level'
        required: false
        default: 'medium'
        type: choice
        options:
          - low
          - medium
          - high

jobs:
  review:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    
    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          fetch-depth: 0
      
      - name: Run Claude Code Action
        uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          model: ${{ github.event.inputs.model }}
          pr_number: ${{ github.event.inputs.pr_number }}
          review_effort: ${{ github.event.inputs.effort }}
'''


def get_workflow_content() -> str:
    """Get the Claude Code Action workflow YAML content."""
    return CLAUDE_CODE_ACTION_WORKFLOW


async def add_workflow_to_repo(
    owner: str,
    repo: str,
    github_token: str,
) -> bool:
    """
    Add Claude Code Action workflow to a repository.
    
    Creates .github/workflows/claude-code-action.yml
    
    Args:
        owner: Repository owner
        repo: Repository name
        github_token: GitHub access token
        
    Returns:
        True if successful
    """
    import base64
    import httpx
    
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/.github/workflows/claude-code-action.yml"
    
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github.v3+json",
    }
    
    # Encode content
    content = base64.b64encode(CLAUDE_CODE_ACTION_WORKFLOW.encode()).decode()
    
    payload = {
        "message": "Add Claude Code Action workflow for AI-powered PR reviews",
        "content": content,
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.put(url, headers=headers, json=payload)
        
        if resp.status_code in (201, 200):
            return True
        else:
            # May already exist
            if resp.status_code == 422:
                return True
            return False
