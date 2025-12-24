"""
Claude Code Action workflow template reference.

Users should set this up themselves via:
1. Install Claude GitHub App: https://github.com/apps/claude
2. Add ANTHROPIC_API_KEY secret to repo
3. Copy workflow to .github/workflows/claude.yml

Or run `/install-github-app` in Claude Code CLI.

See: https://docs.anthropic.com/en/docs/claude-code/github-actions
"""

# Basic workflow that responds to @claude mentions and auto-reviews PRs
CLAUDE_WORKFLOW_EXAMPLE = '''name: Claude Code

on:
  issue_comment:
    types: [created]
  pull_request_review_comment:
    types: [created]
  pull_request:
    types: [opened, synchronize]

permissions:
  contents: write
  pull-requests: write
  issues: write
  id-token: write

jobs:
  claude:
    if: |
      github.event_name == 'pull_request' ||
      contains(github.event.comment.body, '@claude')
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 20

      - uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
'''


def get_workflow_example() -> str:
    """Get example workflow YAML for reference."""
    return CLAUDE_WORKFLOW_EXAMPLE
