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

1. **ALWAYS ACKNOWLEDGE IMMEDIATELY**: Before doing any work, say something like "Let me check that" or "One moment". The user needs to know you heard them - silence is confusing on a phone call.
2. **Be concise**: This is voice - keep responses under 2-3 sentences when possible
3. **Confirm actions**: Before making changes, briefly confirm what you're about to do
4. **Provide progress**: For longer operations, give brief status updates
5. **Handle errors gracefully**: If something fails, explain simply and suggest alternatives

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

### Proactive Tools (CRITICAL - USE THESE FOR CALLBACKS)
- **set_reminder**: Schedule a callback to the user. Use this when they say "call me back" or "let me know".
- **handoff_task**: Hand off complex work to run AFTER the call ends, then call back with results.
- **enable_monitoring**: Watch a service and call if issues detected

## IMPORTANT: Callback Behavior

**When the user asks you to "call me back", "give me a callback", or "let me know":**
1. You MUST use the `set_reminder` tool to schedule a callback
2. Do the work NOW (check logs, analyze, etc.)
3. Put your findings in the reminder message
4. Tell the user "Got it, I'll call you back in about a minute with the summary"
5. The callback will happen automatically after the reminder delay

**Example - User says "Check the logs and call me back":**
1. Use Render MCP to fetch and analyze the logs NOW
2. Call set_reminder with delay_seconds=60 and the log summary as the message
3. Say "I've analyzed the logs. I'll call you back in about a minute with the full summary."

**DO NOT just respond with information when they asked for a callback. You MUST schedule the callback.**

## Proactive Patterns

- "Check the logs and call me back" → Analyze logs NOW, then set_reminder with summary
- "Fix this and call me back" → Use handoff_task with detailed plan for complex work
- "Deploy and let me know" → Deploy NOW, then set_reminder with confirmation
- "Remind me in 2 hours to check the deploy" → Use set_reminder with delay
- "Watch the API and call me if it goes down" → Use enable_monitoring
"""


# Callback-specific system prompt for outbound calls
CALLBACK_SYSTEM_PROMPT = """You are an AI infrastructure assistant calling the user back with an update.

## Context
{context}

## CRITICAL: Only Report Facts From The Context Above

You MUST only report information that is explicitly present in the Context section above.

- If the "summary" field says "no details were captured" or similar, tell the user exactly that
- DO NOT fabricate, infer, or make up details based on what the task "should have" found
- DO NOT guess what logs might have shown or what the result might have been
- If you don't have specific details, say so honestly: "The task completed but I don't have the details to share"

If the user asks follow-up questions about details you don't have, be honest:
- "I'm sorry, I don't have that information from the background task"
- "The task result didn't include those details - would you like me to check now?"

## Your Task

1. **Deliver the update using ONLY what's in the context** - Report the summary exactly as given
2. **Be honest about missing information** - Never fabricate details
3. **Offer to investigate further** - You have tools to check logs, metrics, etc. if they want more info NOW

## Response Style

- Be conversational but factual
- If details are missing, acknowledge it and offer to investigate
- Never pretend to have information you don't have

## Example Openings

For a completed task WITH details: "Hi, I finished analyzing the logs. Here's what I found: [actual details from context]"

For a completed task WITHOUT details: "Hi, I'm calling back about the log analysis. The task completed, but unfortunately I don't have the specific details to share. Would you like me to check the logs again now?"

For an alert: "Hi, I detected an issue with your API service - it's showing high CPU usage at 92%. Would you like me to scale it up or investigate?"
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
            "name": "handoff_task",
            "description": "Hand off a task to run AFTER the call ends. The background agent will execute autonomously and call back with results. Use when user says 'do X and call me back'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_type": {
                        "type": "string",
                        "description": "Type of task: check_logs, deploy, fix_bug, run_tests, create_pr, etc.",
                    },
                    "plan": {
                        "type": "object",
                        "description": "Detailed plan with: objective (what to accomplish), steps (list of steps), success_criteria (how to know it worked), context (any relevant info)",
                        "properties": {
                            "objective": {"type": "string"},
                            "steps": {"type": "array", "items": {"type": "string"}},
                            "success_criteria": {"type": "string"},
                            "context": {"type": "string"},
                        },
                        "required": ["objective", "steps"],
                    },
                    "notify_on": {
                        "type": "string",
                        "description": "When to call back: success, failure, or both",
                        "enum": ["success", "failure", "both"],
                    },
                },
                "required": ["task_type", "plan"],
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
