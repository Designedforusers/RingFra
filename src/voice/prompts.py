"""
System prompts and tool configurations for the voice agent.
"""

import json
from typing import Any

SYSTEM_PROMPT = """You are an AI infrastructure assistant for applications deployed on Render. You're speaking with a developer who wants to manage their production systems via voice commands.

## Your Capabilities

You have powerful tools at your disposal:

### Infrastructure Tools (via Render API)
- **list_services**: See all deployed services, databases, and workers
- **get_logs**: Retrieve and analyze application logs
- **get_metrics**: Check CPU, memory, and request metrics
- **scale_service**: Scale services up or down
- **trigger_deploy**: Deploy the latest code
- **rollback_deploy**: Rollback to a previous deployment
- **update_env_var**: Update an environment variable

### Code Tools (via Claude Agent SDK)
- **analyze_code**: Read and understand the codebase
- **fix_bug**: Find and fix bugs, run tests, commit changes
- **implement_feature**: Add new features with tests
- **run_tests**: Execute the test suite
- **commit_and_push**: Commit and push changes

## Response Guidelines

1. **Be concise**: This is voice - keep responses under 2-3 sentences when possible
2. **Confirm actions**: Before making changes, briefly confirm what you're about to do
3. **Provide progress**: For longer operations, give brief status updates
4. **Handle errors gracefully**: If something fails, explain simply and suggest alternatives

## Conversation Style

- Speak naturally, as if you're a helpful colleague
- Use "I'll" and "Let me" rather than formal language
- Don't read out full code - summarize what you found/changed
- Ask clarifying questions if a request is ambiguous

## Safety

- Never expose full API keys or secrets
- Confirm before destructive operations (rollbacks, env var changes)
- If you're unsure about a request, ask for clarification

## Example Interactions

User: "What's running on production?"
You: "You have 3 services running: a FastAPI backend, a React frontend, and a PostgreSQL database. The backend had 2 errors in the last hour. Want me to look into those?"

User: "Fix the login bug"
You: "I'll look at the authentication code and find the issue. Give me a moment... Found it - there's a missing null check in the token validation. I'll fix it, run the tests, and commit. Should I deploy after?"

User: "Scale up the API"
You: "I'll scale the FastAPI backend from 1 to 2 instances. This will take about 30 seconds. Done - you now have 2 instances running."

### Proactive Tools
- **schedule_callback**: Run a task in the background and call back when done
- **set_reminder**: Set a reminder to call back later
- **enable_monitoring**: Watch a service and call if issues detected

## Proactive Patterns

When the user says things like:
- "Fix this and call me back" → Use schedule_callback with the task
- "Remind me in 2 hours to check the deploy" → Use set_reminder
- "Watch the API and call me if it goes down" → Use enable_monitoring
"""


# Callback-specific system prompt for outbound calls
CALLBACK_SYSTEM_PROMPT = """You are an AI infrastructure assistant calling the user back with an update.

## Context
{context}

## Your Task

1. **Start by delivering the update** - Tell them what happened
2. **Be ready for follow-up questions** - They may want more details or to take action
3. **You still have full access to tools** - Can check logs, scale, deploy, etc.

## Response Style

- Be conversational: "Hi, I'm calling back about..."
- Be concise: Get to the point quickly
- Be helpful: Offer next steps if appropriate

## Example Opening

For a completed task: "Hi, I'm calling back about the bug fix you asked me to work on. Good news - I found and fixed the issue. The tests are passing. Would you like me to deploy it?"

For an alert: "Hi, I detected an issue with your API service - it's showing high CPU usage at 92%. Would you like me to scale it up or investigate the logs?"

For a reminder: "Hi, you asked me to remind you to check on the deployment. It's been running for 2 hours now. Want me to pull up the logs?"
"""


def get_callback_prompt(context: dict[str, Any]) -> str:
    """
    Get the system prompt for a callback call.

    Args:
        context: The callback context (task result, alert, reminder, etc.)

    Returns:
        Formatted system prompt
    """
    context_str = json.dumps(context, indent=2)
    return CALLBACK_SYSTEM_PROMPT.format(context=context_str)


def get_tools_config() -> list:
    """
    Get the tool configurations for the LLM.

    Returns:
        list: Tool definitions in Anthropic function calling format
    """
    return [
        # === Infrastructure Tools ===
        {
            "name": "list_services",
            "description": "List all services, databases, and workers deployed on Render",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
        {
            "name": "get_logs",
            "description": "Get recent logs for a service. Use to diagnose errors or check activity.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "Name of the service to get logs for",
                    },
                    "lines": {
                        "type": "integer",
                        "description": "Number of log lines to retrieve (default 50)",
                        "default": 50,
                    },
                    "filter": {
                        "type": "string",
                        "description": "Optional filter string (e.g., 'error', 'warning')",
                    },
                },
                "required": ["service_name"],
            },
        },
        {
            "name": "get_metrics",
            "description": "Get performance metrics (CPU, memory, requests) for a service",
            "input_schema": {
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "Name of the service",
                    },
                    "period": {
                        "type": "string",
                        "description": "Time period: '1h', '6h', '24h', '7d'",
                        "default": "1h",
                    },
                },
                "required": ["service_name"],
            },
        },
        {
            "name": "scale_service",
            "description": "Scale a service up or down (change number of instances)",
            "input_schema": {
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "Name of the service to scale",
                    },
                    "instances": {
                        "type": "integer",
                        "description": "Target number of instances",
                    },
                },
                "required": ["service_name", "instances"],
            },
        },
        {
            "name": "trigger_deploy",
            "description": "Trigger a new deployment of the latest code",
            "input_schema": {
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "Name of the service to deploy",
                    },
                },
                "required": ["service_name"],
            },
        },
        {
            "name": "rollback_deploy",
            "description": "Rollback to a previous deployment",
            "input_schema": {
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "Name of the service to rollback",
                    },
                    "deploy_id": {
                        "type": "string",
                        "description": "Optional specific deploy ID to rollback to (defaults to previous)",
                    },
                },
                "required": ["service_name"],
            },
        },
        {
            "name": "update_env_var",
            "description": "Update an environment variable for a service (will trigger redeploy)",
            "input_schema": {
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "Name of the service",
                    },
                    "key": {
                        "type": "string",
                        "description": "Environment variable name",
                    },
                    "value": {
                        "type": "string",
                        "description": "New value",
                    },
                },
                "required": ["service_name", "key", "value"],
            },
        },
        # === Code Tools ===
        {
            "name": "analyze_code",
            "description": "Analyze the codebase to understand architecture, find issues, or explain functionality",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to analyze or look for (e.g., 'authentication flow', 'database queries')",
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "fix_bug",
            "description": "Find and fix a bug in the codebase. Will read code, write fix, run tests, and optionally commit.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "Description of the bug to fix",
                    },
                    "auto_commit": {
                        "type": "boolean",
                        "description": "Whether to automatically commit the fix (default: false)",
                        "default": False,
                    },
                    "run_tests": {
                        "type": "boolean",
                        "description": "Whether to run tests after fixing (default: true)",
                        "default": True,
                    },
                },
                "required": ["description"],
            },
        },
        {
            "name": "implement_feature",
            "description": "Implement a new feature in the codebase with tests",
            "input_schema": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "Description of the feature to implement",
                    },
                    "auto_commit": {
                        "type": "boolean",
                        "description": "Whether to automatically commit (default: false)",
                        "default": False,
                    },
                },
                "required": ["description"],
            },
        },
        {
            "name": "run_tests",
            "description": "Run the test suite and report results",
            "input_schema": {
                "type": "object",
                "properties": {
                    "test_path": {
                        "type": "string",
                        "description": "Optional specific test file or directory",
                    },
                },
                "required": [],
            },
        },
        {
            "name": "commit_and_push",
            "description": "Commit current changes and push to remote (will trigger Render deploy)",
            "input_schema": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Commit message",
                    },
                },
                "required": ["message"],
            },
        },
        # === Utility Tools ===
        {
            "name": "get_status",
            "description": "Get a quick status overview of all services and recent activity",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
        # === Proactive Tools ===
        {
            "name": "schedule_callback",
            "description": "Schedule a background task and call the user back when complete. Use when user says 'do X and call me back' or 'work on this and let me know when done'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_type": {
                        "type": "string",
                        "description": "Type of task: fix_bug, implement_feature, run_tests, analyze_code, trigger_deploy, scale_service",
                    },
                    "params": {
                        "type": "object",
                        "description": "Parameters for the task (e.g., {description: 'fix login bug'} for fix_bug)",
                    },
                },
                "required": ["task_type"],
            },
        },
        {
            "name": "set_reminder",
            "description": "Set a reminder to call the user back later. Use when user says 'remind me in X hours/minutes'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "What to remind the user about",
                    },
                    "delay_minutes": {
                        "type": "integer",
                        "description": "Minutes to wait before calling back",
                    },
                },
                "required": ["message", "delay_minutes"],
            },
        },
        {
            "name": "enable_monitoring",
            "description": "Enable proactive monitoring for a service. Will call the user if issues are detected. Use when user says 'watch X and alert me'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "Name of the service to monitor",
                    },
                    "alert_threshold": {
                        "type": "string",
                        "description": "When to alert: 'critical' (only critical issues), 'warning' (warnings and critical), 'all' (any issue)",
                        "default": "critical",
                    },
                },
                "required": ["service_name"],
            },
        },
        {
            "name": "disable_monitoring",
            "description": "Stop monitoring a service.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "Name of the service to stop monitoring",
                    },
                },
                "required": ["service_name"],
            },
        },
    ]
