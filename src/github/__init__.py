"""
GitHub integration module.

Handles:
- GitHub Actions triggering
- Claude Code Action workflow management
- Webhook handling for action completion
"""

from src.github.actions import (
    trigger_claude_review,
    trigger_tests,
    get_workflow_status,
    wait_for_workflow,
)

__all__ = [
    "trigger_claude_review",
    "trigger_tests",
    "get_workflow_status",
    "wait_for_workflow",
]
