"""
Schemas for background task structured outputs.

These schemas are used with the Claude Agent SDK's output_format
to ensure headless tasks return properly structured results
suitable for voice delivery via callback.
"""

# Universal schema for all background task types
# Works for: deploy, fix_bug, run_tests, create_pr, analyze_logs, etc.
TASK_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "1-2 sentence summary suitable for voice delivery to the user"
        },
        "success": {
            "type": "boolean",
            "description": "Whether the task completed successfully"
        },
        "details": {
            "type": "object",
            "description": "Task-specific details (services checked, files modified, test results, etc.)",
            "additionalProperties": True
        },
        "action_items": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Follow-up actions the user should consider, if any"
        }
    },
    "required": ["summary", "success"],
    "additionalProperties": False
}
