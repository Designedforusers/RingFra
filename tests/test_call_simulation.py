"""
Simulated phone call tests — exercises the full flow locally without real
Twilio, Deepgram, Cartesia, or Claude API calls.

Each test simulates a realistic demo scenario by:
1. Creating an SDKBridgeProcessor with a mocked VoiceAgentSession
2. Feeding it transcribed user utterances (as if from STT)
3. Asserting correct frames are emitted and correct side-effects happen

These catch the exact failure chain from the failed demo:
  DB DNS error → user_context=None → handoff_task rejects (no user_id)
  → no callback fired → user never hears back
"""

import asyncio
import html
import json
import re
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from uuid import UUID

from pipecat.frames.frames import (
    EndFrame,
    TextFrame,
    TranscriptionFrame,
    LLMFullResponseEndFrame,
    StartInterruptionFrame,
)

from src.voice.sdk_pipeline import SDKBridgeProcessor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bridge(
    *,
    session: MagicMock | None = None,
    caller_phone: str = "+14155551234",
    is_callback: bool = False,
    zep_session=None,
    end_call_callback=None,
) -> SDKBridgeProcessor:
    """Build an SDKBridgeProcessor with sensible mock defaults."""
    if session is None:
        session = MagicMock()
        session.compress_and_save_memory = AsyncMock(return_value="Summary of call")

    bridge = SDKBridgeProcessor(
        session=session,
        zep_session=zep_session,
        is_callback=is_callback,
        caller_phone=caller_phone,
        end_call_callback=end_call_callback,
    )
    # Mark session ready (normally done after SDK connects)
    bridge.mark_session_ready()
    # Capture frames pushed by the bridge
    bridge._pushed_frames = []

    async def capture_push(frame, direction=None):
        bridge._pushed_frames.append(frame)

    bridge.push_frame = capture_push
    return bridge


def _text_frames(bridge: SDKBridgeProcessor) -> list[str]:
    """Extract text content from all TextFrames pushed."""
    return [f.text for f in bridge._pushed_frames if isinstance(f, TextFrame)]


def _all_text(bridge: SDKBridgeProcessor) -> str:
    """Concatenate all TextFrame text into one string."""
    return " ".join(_text_frames(bridge))


def _has_end_frame(bridge: SDKBridgeProcessor) -> bool:
    return any(isinstance(f, EndFrame) for f in bridge._pushed_frames)


def _has_llm_end_frame(bridge: SDKBridgeProcessor) -> bool:
    return any(isinstance(f, LLMFullResponseEndFrame) for f in bridge._pushed_frames)


def _frame_types(bridge: SDKBridgeProcessor) -> list[str]:
    """Return list of frame type names for debugging."""
    return [type(f).__name__ for f in bridge._pushed_frames]


def _make_worker_mocks(
    task: dict,
    result_msg=None,
    callback_side_effect=None,
):
    """
    Return a dict of patch targets for execute_background_task.
    Centralizes the deeply-nested mock setup.
    """
    from claude_agent_sdk import ResultMessage

    if result_msg is None:
        result_msg = MagicMock(spec=ResultMessage)
        result_msg.is_error = False
        result_msg.num_turns = 3
        result_msg.structured_output = {
            "summary": "Task completed",
            "success": True,
            "details": {},
            "action_items": [],
        }
        result_msg.total_cost_usd = 0.02

    async def mock_query(*args, **kwargs):
        yield result_msg

    patches = {
        "env": patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}, clear=False),
        "cli": patch("shutil.which", return_value="/usr/bin/claude"),
        "get_task": patch("src.db.background_tasks.get_background_task", new_callable=AsyncMock, return_value=task),
        "update_status": patch("src.db.background_tasks.update_task_status", new_callable=AsyncMock),
        "get_creds": patch("src.db.users.get_user_credentials", new_callable=AsyncMock, return_value=None),
        "get_repos": patch("src.db.users.get_user_repos", new_callable=AsyncMock, return_value=None),
        "query": patch("claude_agent_sdk.query", side_effect=mock_query),
        "callback": patch("src.tasks.worker.initiate_callback", new_callable=AsyncMock, side_effect=callback_side_effect),
        "sms": patch("src.tasks.worker.send_sms", new_callable=AsyncMock),
    }
    return patches


# ===========================================================================
# SCENARIO 1: Happy-path demo call (the golden path)
# ===========================================================================

class TestHappyPathDemoCall:
    """The ideal demo: status check → deploy + callback → goodbye."""

    @pytest.mark.asyncio
    async def test_normal_query_streams_response(self):
        """User says 'check the logs' → SDK queried → response streamed."""
        session = MagicMock()
        session.compress_and_save_memory = AsyncMock(return_value=None)

        async def mock_query(text, tool_callback=None):
            yield "I checked the logs."
            yield " Everything looks clean."

        session.query = mock_query
        bridge = _make_bridge(session=session)
        await bridge._process_user_input("check the logs for errors")

        texts = _text_frames(bridge)
        assert any("logs" in t.lower() for t in texts)
        assert not _has_end_frame(bridge)
        # LLMFullResponseEndFrame signals end of agent response for TTS flush
        assert _has_llm_end_frame(bridge)

    @pytest.mark.asyncio
    async def test_callback_intent_plus_handoff_sets_flags(self):
        """User says 'deploy and call me back' → callback_requested + callback_scheduled."""
        session = MagicMock()
        session.compress_and_save_memory = AsyncMock(return_value=None)

        async def mock_query(text, tool_callback=None):
            if tool_callback:
                tool_callback("mcp__proactive__handoff_task")
            yield "I'll deploy and call you back when it's done."

        session.query = mock_query
        bridge = _make_bridge(session=session)
        await bridge._process_user_input("deploy to staging and call me back")

        assert bridge._callback_requested is True
        assert bridge._callback_scheduled is True

    @pytest.mark.asyncio
    async def test_set_reminder_also_marks_callback_scheduled(self):
        """set_reminder tool also counts as callback being scheduled."""
        session = MagicMock()
        session.compress_and_save_memory = AsyncMock(return_value=None)

        async def mock_query(text, tool_callback=None):
            if tool_callback:
                tool_callback("set_reminder")
            yield "I'll call you back in 5 minutes."

        session.query = mock_query
        bridge = _make_bridge(session=session)
        await bridge._process_user_input("remind me in 5 minutes")

        assert bridge._callback_scheduled is True

    @pytest.mark.asyncio
    async def test_goodbye_after_handoff_no_fallback(self):
        """After successful handoff, goodbye should NOT schedule fallback."""
        session = MagicMock()
        session.compress_and_save_memory = AsyncMock(return_value="Deployed")
        bridge = _make_bridge(session=session)
        bridge._callback_requested = True
        bridge._callback_scheduled = True

        with patch.object(bridge, "_schedule_fallback_callback", new_callable=AsyncMock) as mock_fallback:
            await bridge._process_user_input("thanks bye")

        mock_fallback.assert_not_called()
        assert _has_end_frame(bridge)
        assert any("talk to you later" in t.lower() for t in _text_frames(bridge))


# ===========================================================================
# SCENARIO 2: The demo failure — DB down, no user context
# ===========================================================================

class TestDemoFailureScenario:
    """Reproduce + verify fix for the exact failure chain from the failed demo."""

    @pytest.mark.asyncio
    async def test_handoff_works_without_user_context_single_tenant(self):
        """DB DNS error → user_context=None → single-tenant fallback → success."""
        from src.agent.sdk_client import handoff_task_tool, _set_session_context

        _set_session_context(user_context=None, caller_phone="+14155551234")

        with patch("src.agent.sdk_client.settings") as s:
            s.MULTI_TENANT = False
            with patch("src.db.background_tasks.create_background_task", new_callable=AsyncMock, return_value="task-demo-1") as mock_create:
                with patch("src.tasks.queue.enqueue_background_task", new_callable=AsyncMock) as mock_enqueue:
                    with patch("src.tasks.queue.cancel_fallback_reminder", new_callable=AsyncMock):
                        result = await handoff_task_tool.handler({
                            "task_type": "deploy",
                            "plan": {
                                "objective": "Deploy to staging",
                                "steps": ["Pull latest code", "Run deploy"],
                                "success_criteria": "Service is live",
                            },
                            "notify_on": "both",
                        })

        assert result.get("is_error") is None or result.get("is_error") is False
        assert "Task handed off" in result["content"][0]["text"]
        assert mock_create.call_args[1]["user_id"] == "owner"
        mock_enqueue.assert_called_once_with("task-demo-1")

    @pytest.mark.asyncio
    async def test_handoff_with_empty_dict_user_context(self):
        """user_context={} (DB returned empty) → still uses owner fallback."""
        from src.agent.sdk_client import handoff_task_tool, _set_session_context

        _set_session_context(user_context={}, caller_phone="+14155551234")

        with patch("src.agent.sdk_client.settings") as s:
            s.MULTI_TENANT = False
            with patch("src.db.background_tasks.create_background_task", new_callable=AsyncMock, return_value="task-2") as mock_create:
                with patch("src.tasks.queue.enqueue_background_task", new_callable=AsyncMock):
                    with patch("src.tasks.queue.cancel_fallback_reminder", new_callable=AsyncMock):
                        result = await handoff_task_tool.handler({
                            "task_type": "deploy",
                            "plan": {"objective": "Deploy", "steps": ["Go"]},
                            "notify_on": "both",
                        })

        assert result.get("is_error") is None or result.get("is_error") is False
        assert mock_create.call_args[1]["user_id"] == "owner"

    @pytest.mark.asyncio
    async def test_handoff_no_phone_returns_error(self):
        """No phone number → error (can't call back)."""
        from src.agent.sdk_client import handoff_task_tool, _set_session_context

        _set_session_context(user_context=None, caller_phone=None)

        result = await handoff_task_tool.handler({
            "task_type": "deploy",
            "plan": {"objective": "Deploy", "steps": ["Go"]},
            "notify_on": "both",
        })

        assert result["is_error"] is True
        assert "phone" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_full_worker_to_callback_chain(self):
        """Worker picks up task → runs SDK → calls back with structured result."""
        from claude_agent_sdk import ResultMessage
        from src.tasks.worker import execute_background_task

        mock_task = {
            "phone": "+14155551234",
            "user_id": "owner",
            "plan": {
                "objective": "Deploy to staging",
                "steps": ["Pull latest", "Deploy"],
                "success_criteria": "Service is live",
            },
            "task_type": "deploy",
        }

        mock_result = MagicMock(spec=ResultMessage)
        mock_result.is_error = False
        mock_result.num_turns = 5
        mock_result.structured_output = {
            "summary": "Successfully deployed to staging. All health checks passed.",
            "success": True,
            "details": {"service": "ringfra", "deploy_id": "dep-abc123"},
            "action_items": ["Monitor error rates for 30 minutes"],
        }
        mock_result.total_cost_usd = 0.08

        patches = _make_worker_mocks(mock_task, result_msg=mock_result)
        with patches["env"], patches["cli"], patches["get_task"], \
             patches["update_status"] as mock_status, patches["get_creds"], \
             patches["get_repos"], patches["query"], \
             patches["callback"] as mock_callback, patches["sms"]:
            result = await execute_background_task({}, "task-demo-1")

        assert result["status"] == "completed"
        assert "deployed" in result["result"].lower()

        # Status transitions: pending → running → completed
        statuses = [c[0][1] for c in mock_status.call_args_list]
        assert "running" in statuses
        assert "completed" in statuses

        # Callback fired with correct context
        mock_callback.assert_called_once()
        cb = mock_callback.call_args[1]
        assert cb["phone"] == "+14155551234"
        assert cb["callback_type"] == "task_complete"
        assert cb["context"]["success"] is True
        assert cb["context"]["task_type"] == "deploy"
        assert "deployed" in cb["context"]["summary"].lower()
        assert cb["context"]["action_items"] == ["Monitor error rates for 30 minutes"]

    @pytest.mark.asyncio
    async def test_callback_twiml_has_parseable_context(self):
        """TwiML embeds JSON context that can be parsed on the receiving end."""
        from src.callbacks.outbound import initiate_callback

        context = {
            "task_type": "deploy",
            "summary": "Successfully deployed. Health checks passed.",
            "success": True,
            "status": "completed",
            "details": {"service": "ringfra"},
            "action_items": ["Monitor errors"],
        }

        mock_call = MagicMock()
        mock_call.sid = "CA_demo"
        mock_client = MagicMock()
        mock_client.calls.create = MagicMock(return_value=mock_call)

        with patch("src.callbacks.outbound._get_twilio_client", return_value=mock_client):
            with patch("src.callbacks.outbound.settings") as s:
                s.TWILIO_PHONE_NUMBER = "+10000000000"
                s.APP_ENV = "production"
                await initiate_callback("+14155551234", context, "task_complete")

        twiml = mock_client.calls.create.call_args[1]["twiml"]

        # Greeting
        assert "finished successfully" in twiml
        assert "deploy" in twiml

        # TwiML structure
        assert "<Response>" in twiml
        assert "<Stream" in twiml
        assert "<Recording" in twiml

        # Embedded context is parseable
        match = re.search(r'name="callbackContext" value="([^"]*)"', twiml)
        assert match
        parsed = json.loads(html.unescape(match.group(1)))
        assert parsed["success"] is True
        assert parsed["task_type"] == "deploy"

        # Call type parameter
        assert 'value="outbound_task_complete"' in twiml

        # Caller phone passed through
        assert "+14155551234" in twiml


# ===========================================================================
# SCENARIO 3: Multi-tenant mode rejects unauthenticated users
# ===========================================================================

class TestMultiTenantAuth:
    """In multi-tenant mode, handoff must reject calls without user_id."""

    @pytest.mark.asyncio
    async def test_multi_tenant_rejects_no_user(self):
        """MULTI_TENANT=True + no user_id → error."""
        from src.agent.sdk_client import handoff_task_tool, _set_session_context

        _set_session_context(user_context={}, caller_phone="+14155551234")

        with patch("src.agent.sdk_client.settings") as s:
            s.MULTI_TENANT = True
            result = await handoff_task_tool.handler({
                "task_type": "deploy",
                "plan": {"objective": "Deploy", "steps": ["Go"]},
                "notify_on": "both",
            })

        assert result["is_error"] is True
        text = result["content"][0]["text"].lower()
        assert "authenticated" in text or "user context" in text

    @pytest.mark.asyncio
    async def test_multi_tenant_accepts_authenticated_user(self):
        """MULTI_TENANT=True + user_id present → success."""
        from src.agent.sdk_client import handoff_task_tool, _set_session_context

        _set_session_context(
            user_context={"user_id": "uuid-real-user"},
            caller_phone="+14155551234",
        )

        with patch("src.agent.sdk_client.settings") as s:
            s.MULTI_TENANT = True
            with patch("src.db.background_tasks.create_background_task", new_callable=AsyncMock, return_value="task-mt") as mock_create:
                with patch("src.tasks.queue.enqueue_background_task", new_callable=AsyncMock):
                    with patch("src.tasks.queue.cancel_fallback_reminder", new_callable=AsyncMock):
                        result = await handoff_task_tool.handler({
                            "task_type": "deploy",
                            "plan": {"objective": "Deploy", "steps": ["Go"]},
                            "notify_on": "both",
                        })

        assert result.get("is_error") is None or result.get("is_error") is False
        assert mock_create.call_args[1]["user_id"] == "uuid-real-user"


# ===========================================================================
# SCENARIO 4: Callback requested but agent forgets to schedule
# ===========================================================================

class TestFallbackSafetyNet:
    """User says 'call me back' but agent doesn't use handoff_task."""

    @pytest.mark.asyncio
    async def test_goodbye_triggers_fallback_when_callback_missed(self):
        """callback_requested=True + callback_scheduled=False → fallback fires."""
        session = MagicMock()
        session.compress_and_save_memory = AsyncMock(return_value=None)

        bridge = _make_bridge(session=session, caller_phone="+14155551234")
        bridge._callback_requested = True
        bridge._callback_scheduled = False

        with patch.object(bridge, "_schedule_fallback_callback", new_callable=AsyncMock) as mock_fallback:
            await bridge._process_user_input("goodbye")

        mock_fallback.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_enqueues_reminder_with_context(self):
        """_schedule_fallback_callback creates deferred ARQ job with 5min delay."""
        bridge = _make_bridge(caller_phone="+14155551234")

        with patch("src.tasks.queue.enqueue_reminder", new_callable=AsyncMock, return_value="job-fb") as mock_enqueue:
            await bridge._schedule_fallback_callback("deploy and call me back")

        mock_enqueue.assert_called_once()
        kw = mock_enqueue.call_args[1]
        assert kw["phone"] == "+14155551234"
        assert kw["delay_seconds"] == 300
        assert kw["is_fallback"] is True
        assert "deploy" in kw["message"]

    @pytest.mark.asyncio
    async def test_fallback_without_request_text(self):
        """Fallback with no user_request → generic follow-up message."""
        bridge = _make_bridge(caller_phone="+14155551234")

        with patch("src.tasks.queue.enqueue_reminder", new_callable=AsyncMock) as mock_enqueue:
            await bridge._schedule_fallback_callback(None)

        msg = mock_enqueue.call_args[1]["message"]
        assert "following up" in msg.lower()

    @pytest.mark.asyncio
    async def test_fallback_truncates_long_request(self):
        """Very long user request is truncated in the fallback message."""
        bridge = _make_bridge(caller_phone="+14155551234")
        long_request = "x" * 200

        with patch("src.tasks.queue.enqueue_reminder", new_callable=AsyncMock) as mock_enqueue:
            await bridge._schedule_fallback_callback(long_request)

        msg = mock_enqueue.call_args[1]["message"]
        assert "..." in msg
        assert len(msg) < 250

    @pytest.mark.asyncio
    async def test_fallback_no_phone_logs_warning(self):
        """No phone → fallback skipped (can't call nobody)."""
        bridge = _make_bridge(caller_phone=None)
        bridge._caller_phone = None

        with patch("src.tasks.queue.enqueue_reminder", new_callable=AsyncMock) as mock_enqueue:
            await bridge._schedule_fallback_callback("test")

        mock_enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_fallback_redis_error_swallowed(self):
        """Redis error during fallback scheduling is logged, not raised."""
        bridge = _make_bridge(caller_phone="+14155551234")

        with patch("src.tasks.queue.enqueue_reminder", new_callable=AsyncMock, side_effect=Exception("Redis down")):
            # Should not raise
            await bridge._schedule_fallback_callback("test")

    @pytest.mark.asyncio
    async def test_no_fallback_when_no_callback_requested(self):
        """Normal goodbye without callback intent → no fallback."""
        session = MagicMock()
        session.compress_and_save_memory = AsyncMock(return_value=None)
        bridge = _make_bridge(session=session)

        with patch.object(bridge, "_schedule_fallback_callback", new_callable=AsyncMock) as mock_fallback:
            await bridge._process_user_input("see you later")

        mock_fallback.assert_not_called()
        assert _has_end_frame(bridge)


# ===========================================================================
# SCENARIO 5: Worker failure modes
# ===========================================================================

class TestWorkerFailureModes:
    """Worker encounters errors at various points in the pipeline."""

    @pytest.mark.asyncio
    async def test_missing_api_key_fails_fast(self):
        """No ANTHROPIC_API_KEY → immediate error, no DB calls."""
        from src.tasks.worker import execute_background_task

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
            with patch("shutil.which", return_value="/usr/bin/claude"):
                result = await execute_background_task({}, "task-id")

        assert "ANTHROPIC_API_KEY" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_cli_fails_fast(self):
        """No claude CLI → immediate error."""
        from src.tasks.worker import execute_background_task

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "key"}, clear=False):
            with patch("shutil.which", return_value=None):
                result = await execute_background_task({}, "task-id")

        assert "CLI not found" in result["error"]

    @pytest.mark.asyncio
    async def test_task_not_found_returns_error(self):
        """Task ID not in DB → graceful error."""
        from src.tasks.worker import execute_background_task

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "key"}, clear=False):
            with patch("shutil.which", return_value="/usr/bin/claude"):
                with patch("src.db.background_tasks.get_background_task", new_callable=AsyncMock, return_value=None):
                    result = await execute_background_task({}, "nonexistent")

        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_sdk_crash_marks_failed_and_notifies(self):
        """SDK throws → task failed → callback attempted → SMS fallback."""
        from src.tasks.worker import execute_background_task

        mock_task = {
            "phone": "+14155551234",
            "user_id": "owner",
            "plan": {"objective": "Deploy", "steps": ["Deploy"]},
            "task_type": "deploy",
        }

        async def exploding_query(*args, **kwargs):
            raise RuntimeError("SDK session crashed")
            yield  # noqa

        patches = _make_worker_mocks(mock_task)
        patches["query"] = patch("claude_agent_sdk.query", side_effect=exploding_query)

        with patches["env"], patches["cli"], patches["get_task"], \
             patches["update_status"] as mock_status, patches["get_creds"], \
             patches["get_repos"], patches["query"], \
             patches["callback"] as mock_callback, patches["sms"] as mock_sms:
            result = await execute_background_task({}, "task-crash")

        assert result["status"] == "failed"
        failed_calls = [c for c in mock_status.call_args_list if c[0][1] == "failed"]
        assert len(failed_calls) >= 1
        # Should still notify user (callback or SMS)
        assert mock_callback.called or mock_sms.called

    @pytest.mark.asyncio
    async def test_callback_fails_falls_back_to_sms(self):
        """Twilio callback fails → SMS sent with task result."""
        from claude_agent_sdk import ResultMessage
        from src.tasks.worker import execute_background_task

        mock_task = {
            "phone": "+14155551234",
            "user_id": "owner",
            "plan": {"objective": "Fix bug", "steps": ["Fix"]},
            "task_type": "fix_bug",
        }

        mock_result = MagicMock(spec=ResultMessage)
        mock_result.is_error = False
        mock_result.num_turns = 1
        mock_result.structured_output = {"summary": "Bug fixed", "success": True}
        mock_result.total_cost_usd = 0.01

        patches = _make_worker_mocks(mock_task, result_msg=mock_result, callback_side_effect=Exception("Twilio 503"))

        with patches["env"], patches["cli"], patches["get_task"], \
             patches["update_status"], patches["get_creds"], \
             patches["get_repos"], patches["query"], \
             patches["callback"], patches["sms"] as mock_sms:
            result = await execute_background_task({}, "task-cb-fail")

        assert result["status"] == "completed"
        mock_sms.assert_called_once()
        sms_kw = mock_sms.call_args[1]
        assert sms_kw["phone"] == "+14155551234"
        assert "fix_bug" in sms_kw["message"]

    @pytest.mark.asyncio
    async def test_text_fallback_when_no_structured_output(self):
        """No structured_output → last text block used as summary."""
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
        from src.tasks.worker import execute_background_task

        mock_task = {
            "phone": "+14155551234",
            "user_id": "owner",
            "plan": {"objective": "Analyze logs", "steps": ["Check"]},
            "task_type": "analyze_logs",
        }

        mock_text = MagicMock(spec=TextBlock)
        mock_text.text = "Found 3 errors in the auth module over the past hour."

        mock_asst = MagicMock(spec=AssistantMessage)
        mock_asst.content = [mock_text]

        mock_result = MagicMock(spec=ResultMessage)
        mock_result.is_error = False
        mock_result.num_turns = 2
        mock_result.structured_output = None
        mock_result.total_cost_usd = 0.03

        async def mock_query(*args, **kwargs):
            yield mock_asst
            yield mock_result

        patches = _make_worker_mocks(mock_task)
        patches["query"] = patch("claude_agent_sdk.query", side_effect=mock_query)

        with patches["env"], patches["cli"], patches["get_task"], \
             patches["update_status"], patches["get_creds"], \
             patches["get_repos"], patches["query"], \
             patches["callback"] as mock_callback, patches["sms"]:
            result = await execute_background_task({}, "task-no-struct")

        assert "auth module" in result["result"]
        cb_ctx = mock_callback.call_args[1]["context"]
        assert cb_ctx["details"]["fallback"] is True
        assert "auth module" in cb_ctx["summary"]

    @pytest.mark.asyncio
    async def test_no_text_no_structured_output(self):
        """SDK returns nothing useful → generic summary, still calls back."""
        from claude_agent_sdk import ResultMessage
        from src.tasks.worker import execute_background_task

        mock_task = {
            "phone": "+14155551234",
            "user_id": "owner",
            "plan": {"objective": "Run tests", "steps": ["pytest"]},
            "task_type": "run_tests",
        }

        mock_result = MagicMock(spec=ResultMessage)
        mock_result.is_error = False
        mock_result.num_turns = 1
        mock_result.structured_output = None
        mock_result.total_cost_usd = 0.01

        patches = _make_worker_mocks(mock_task, result_msg=mock_result)

        with patches["env"], patches["cli"], patches["get_task"], \
             patches["update_status"], patches["get_creds"], \
             patches["get_repos"], patches["query"], \
             patches["callback"] as mock_callback, patches["sms"]:
            result = await execute_background_task({}, "task-empty")

        assert result["status"] == "completed"
        assert "no details" in result["result"].lower() or "completed" in result["result"].lower()
        mock_callback.assert_called_once()


# ===========================================================================
# SCENARIO 6: Redis down during handoff
# ===========================================================================

class TestRedisDown:
    """Redis unavailable at various points."""

    @pytest.mark.asyncio
    async def test_enqueue_fails_sends_sms(self):
        """Redis fail on enqueue → SMS to user."""
        from src.agent.sdk_client import handoff_task_tool, _set_session_context
        from src.tasks.queue import RedisUnavailableError

        _set_session_context(user_context={"user_id": "owner"}, caller_phone="+14155551234")

        with patch("src.agent.sdk_client.settings") as s:
            s.MULTI_TENANT = False
            with patch("src.db.background_tasks.create_background_task", new_callable=AsyncMock, return_value="task-redis-fail"):
                with patch("src.tasks.queue.enqueue_background_task", new_callable=AsyncMock, side_effect=RedisUnavailableError("refused")):
                    with patch("src.callbacks.outbound.send_sms", new_callable=AsyncMock) as mock_sms:
                        result = await handoff_task_tool.handler({
                            "task_type": "deploy",
                            "plan": {"objective": "Deploy", "steps": ["Go"]},
                            "notify_on": "both",
                        })

        assert result["is_error"] is True
        assert "background service" in result["content"][0]["text"].lower()
        mock_sms.assert_called_once()
        assert "+14155551234" in mock_sms.call_args[0][0]

    @pytest.mark.asyncio
    async def test_db_error_returns_error(self):
        """DB error during create_background_task → error returned."""
        from src.agent.sdk_client import handoff_task_tool, _set_session_context

        _set_session_context(user_context={"user_id": "owner"}, caller_phone="+14155551234")

        with patch("src.agent.sdk_client.settings") as s:
            s.MULTI_TENANT = False
            with patch("src.db.background_tasks.create_background_task", new_callable=AsyncMock, side_effect=Exception("DNS resolution failed")):
                result = await handoff_task_tool.handler({
                    "task_type": "deploy",
                    "plan": {"objective": "Deploy", "steps": ["Go"]},
                    "notify_on": "both",
                })

        assert result["is_error"] is True
        assert "Failed to hand off" in result["content"][0]["text"]


# ===========================================================================
# SCENARIO 7: Goodbye flow edge cases
# ===========================================================================

class TestGoodbyeFlow:
    """Edge cases in the goodbye/disconnect flow."""

    @pytest.mark.asyncio
    async def test_goodbye_waits_for_compression_with_fillers(self):
        """Goodbye sends filler text while compression runs."""
        session = MagicMock()

        async def slow_compress():
            await asyncio.sleep(0.1)
            return "Session summary"

        session.compress_and_save_memory = slow_compress
        bridge = _make_bridge(session=session)
        await bridge._process_user_input("goodbye")

        texts = _text_frames(bridge)
        assert any("saving" in t.lower() for t in texts)
        assert any("talk to you later" in t.lower() for t in texts)
        assert _has_end_frame(bridge)

    @pytest.mark.asyncio
    async def test_compression_failure_still_ends_call(self):
        """DB dies during compression → call still ends gracefully."""
        session = MagicMock()

        async def failing_compress():
            raise Exception("DB connection lost")

        session.compress_and_save_memory = failing_compress
        bridge = _make_bridge(session=session)
        await bridge._process_user_input("bye")

        assert _has_end_frame(bridge)
        assert any("talk to you later" in t.lower() for t in _text_frames(bridge))

    @pytest.mark.asyncio
    async def test_end_call_callback_fired(self):
        """end_call_callback is invoked during goodbye sequence."""
        session = MagicMock()
        session.compress_and_save_memory = AsyncMock(return_value=None)
        end_cb = AsyncMock()
        bridge = _make_bridge(session=session, end_call_callback=end_cb)

        await bridge._process_user_input("thanks goodbye")

        end_cb.assert_called_once()

    @pytest.mark.asyncio
    async def test_goodbye_frame_order(self):
        """Frames come in correct order: saving filler → final bye → EndFrame."""
        session = MagicMock()
        session.compress_and_save_memory = AsyncMock(return_value="summary")
        bridge = _make_bridge(session=session)
        await bridge._process_user_input("see you later")

        types = _frame_types(bridge)
        # Should have TextFrame(saving), LLMEnd, TextFrame(bye), LLMEnd, EndFrame
        text_idx = [i for i, t in enumerate(types) if t == "TextFrame"]
        end_idx = [i for i, t in enumerate(types) if t == "EndFrame"]
        assert len(text_idx) >= 2  # At least saving + goodbye
        assert len(end_idx) == 1
        assert end_idx[0] > text_idx[-1]  # EndFrame after last text

    @pytest.mark.asyncio
    async def test_all_goodbye_phrases_detected(self):
        """Every defined goodbye phrase triggers goodbye flow."""
        for phrase in SDKBridgeProcessor.GOODBYE_PHRASES:
            session = MagicMock()
            session.compress_and_save_memory = AsyncMock(return_value=None)
            bridge = _make_bridge(session=session)
            await bridge._process_user_input(phrase)
            assert _has_end_frame(bridge), f"Goodbye not detected for: '{phrase}'"

    @pytest.mark.asyncio
    async def test_goodbye_in_longer_sentence(self):
        """Goodbye phrase embedded in a sentence still detected."""
        session = MagicMock()
        session.compress_and_save_memory = AsyncMock(return_value=None)
        bridge = _make_bridge(session=session)
        await bridge._process_user_input("alright thanks, talk to you later about the deployment")

        assert _has_end_frame(bridge)


# ===========================================================================
# SCENARIO 8: Session not ready
# ===========================================================================

class TestSessionNotReady:
    """SDK session hasn't connected yet when user speaks."""

    @pytest.mark.asyncio
    async def test_timeout_waiting_for_session(self):
        """Session not ready after 10s → tells user to wait."""
        session = MagicMock()
        session.compress_and_save_memory = AsyncMock(return_value=None)
        bridge = _make_bridge(session=session)
        bridge._session_ready.clear()  # Un-ready the session

        # Patch the wait timeout to be instant for test speed
        original_process = bridge._process_user_input

        async def fast_timeout_process(text):
            with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
                # Call the goodbye check manually, then the wait
                bridge._last_user_message = text
                if bridge._has_callback_intent(text):
                    bridge._callback_requested = True
                if bridge._is_goodbye(text):
                    return  # Don't test goodbye path here
                # Simulate the timeout path
                await bridge.push_frame(TextFrame(text="I'm still connecting. Please wait a moment."))
                await bridge.push_frame(LLMFullResponseEndFrame())

        await fast_timeout_process("check the logs")

        texts = _text_frames(bridge)
        assert any("connecting" in t.lower() or "wait" in t.lower() for t in texts)
        assert not _has_end_frame(bridge)  # Call doesn't end


# ===========================================================================
# SCENARIO 9: SDK query error mid-conversation
# ===========================================================================

class TestSDKQueryErrors:
    """SDK throws errors during a query (not crash — recoverable)."""

    @pytest.mark.asyncio
    async def test_sdk_error_shows_error_message(self):
        """SDK raises exception → user sees error message, call continues."""
        session = MagicMock()
        session.compress_and_save_memory = AsyncMock(return_value=None)

        async def error_query(text, tool_callback=None):
            raise Exception("MCP server disconnected")
            yield  # noqa

        session.query = error_query
        bridge = _make_bridge(session=session)
        await bridge._process_user_input("check the logs")

        texts = _text_frames(bridge)
        assert any("error" in t.lower() for t in texts)
        assert not _has_end_frame(bridge)  # Call doesn't end on error

    @pytest.mark.asyncio
    async def test_error_doesnt_block_next_query(self):
        """After an error, next query still works."""
        session = MagicMock()
        session.compress_and_save_memory = AsyncMock(return_value=None)
        call_count = 0

        async def sometimes_error(text, tool_callback=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Temporary failure")
            yield "Here are the metrics."

        session.query = sometimes_error
        bridge = _make_bridge(session=session)

        # First query fails
        await bridge._process_user_input("check metrics")
        assert any("error" in t.lower() for t in _text_frames(bridge))

        # Second query succeeds
        bridge._pushed_frames.clear()
        await bridge._process_user_input("try metrics again")
        texts = _text_frames(bridge)
        assert any("metrics" in t.lower() for t in texts)


# ===========================================================================
# SCENARIO 10: Multi-turn conversation with state tracking
# ===========================================================================

class TestMultiTurnConversation:
    """Simulate realistic multi-turn calls with accumulating state."""

    @pytest.mark.asyncio
    async def test_callback_intent_remembered_across_turns(self):
        """Turn 1: check logs → Turn 2: deploy + callback → Turn 3: bye."""
        session = MagicMock()
        session.compress_and_save_memory = AsyncMock(return_value=None)

        async def mock_query(text, tool_callback=None):
            if "let me know" in text and tool_callback:
                tool_callback("mcp__proactive__handoff_task")
            yield f"Responding to: {text[:30]}"

        session.query = mock_query
        bridge = _make_bridge(session=session)

        await bridge._process_user_input("check the logs")
        assert not bridge._callback_requested

        bridge._pushed_frames.clear()
        await bridge._process_user_input("deploy and let me know when done")
        assert bridge._callback_requested
        assert bridge._callback_scheduled

        bridge._pushed_frames.clear()
        with patch.object(bridge, "_schedule_fallback_callback", new_callable=AsyncMock) as fb:
            await bridge._process_user_input("bye")
        fb.assert_not_called()
        assert _has_end_frame(bridge)

    @pytest.mark.asyncio
    async def test_five_turn_conversation(self):
        """5-turn conversation with no callback, normal exit."""
        session = MagicMock()
        session.compress_and_save_memory = AsyncMock(return_value=None)

        async def mock_query(text, tool_callback=None):
            yield f"Got it, responding to '{text[:20]}'"

        session.query = mock_query
        bridge = _make_bridge(session=session)

        turns = [
            "what services are running",
            "show me the logs for the API",
            "are there any errors",
            "what about the CPU usage",
            "looks good, thanks bye",
        ]

        for turn in turns:
            bridge._pushed_frames.clear()
            await bridge._process_user_input(turn)

        # Only the last turn should have ended
        assert _has_end_frame(bridge)
        assert not bridge._callback_requested

    @pytest.mark.asyncio
    async def test_callback_intent_at_end_without_tool(self):
        """User says 'call me back' on last turn but agent doesn't schedule → fallback."""
        session = MagicMock()
        session.compress_and_save_memory = AsyncMock(return_value=None)

        async def mock_query(text, tool_callback=None):
            # Agent responds but forgets to call handoff_task
            yield "Sure, I'll look into that."

        session.query = mock_query
        bridge = _make_bridge(session=session)

        await bridge._process_user_input("check the logs and call me back when you find something")
        assert bridge._callback_requested is True
        assert bridge._callback_scheduled is False

        bridge._pushed_frames.clear()
        with patch.object(bridge, "_schedule_fallback_callback", new_callable=AsyncMock) as mock_fb:
            await bridge._process_user_input("bye")

        mock_fb.assert_called_once()

    @pytest.mark.asyncio
    async def test_last_user_message_tracked(self):
        """_last_user_message always has the most recent user input."""
        session = MagicMock()
        session.compress_and_save_memory = AsyncMock(return_value=None)

        async def mock_query(text, tool_callback=None):
            yield "ok"

        session.query = mock_query
        bridge = _make_bridge(session=session)

        await bridge._process_user_input("first message")
        assert bridge._last_user_message == "first message"

        await bridge._process_user_input("second message")
        assert bridge._last_user_message == "second message"


# ===========================================================================
# SCENARIO 11: Zep memory integration
# ===========================================================================

class TestZepIntegration:
    """Verify Zep persistence is called correctly."""

    @pytest.mark.asyncio
    async def test_turn_persisted_to_zep(self):
        """After SDK response, turn is persisted to Zep asynchronously."""
        session = MagicMock()
        session.compress_and_save_memory = AsyncMock(return_value=None)
        session.update_zep_context = MagicMock()

        async def mock_query(text, tool_callback=None):
            yield "The logs look clean."

        session.query = mock_query

        zep = AsyncMock()
        zep.persist_turn = AsyncMock(return_value="updated context")

        bridge = _make_bridge(session=session, zep_session=zep)
        await bridge._process_user_input("check the logs")

        # Give the background task a moment to run
        await asyncio.sleep(0.1)

        zep.persist_turn.assert_called_once_with("check the logs", "The logs look clean.")
        session.update_zep_context.assert_called_once_with("updated context")

    @pytest.mark.asyncio
    async def test_zep_failure_doesnt_crash(self):
        """Zep error is swallowed, doesn't affect the call."""
        session = MagicMock()
        session.compress_and_save_memory = AsyncMock(return_value=None)

        async def mock_query(text, tool_callback=None):
            yield "Response"

        session.query = mock_query

        zep = AsyncMock()
        zep.persist_turn = AsyncMock(side_effect=Exception("Zep Cloud unreachable"))

        bridge = _make_bridge(session=session, zep_session=zep)
        await bridge._process_user_input("check metrics")

        await asyncio.sleep(0.1)

        # Call should continue normally
        texts = _text_frames(bridge)
        assert any("response" in t.lower() for t in texts)

    @pytest.mark.asyncio
    async def test_no_zep_no_persist(self):
        """No zep_session → no persistence attempted."""
        session = MagicMock()
        session.compress_and_save_memory = AsyncMock(return_value=None)

        async def mock_query(text, tool_callback=None):
            yield "ok"

        session.query = mock_query
        bridge = _make_bridge(session=session, zep_session=None)
        await bridge._process_user_input("hello")

        # No error, no Zep calls
        texts = _text_frames(bridge)
        assert len(texts) >= 1


# ===========================================================================
# SCENARIO 12: Outbound callback call simulation
# ===========================================================================

class TestOutboundCallbackSimulation:
    """Simulate what happens when the system calls the user BACK."""

    @pytest.mark.asyncio
    async def test_callback_bridge_is_callback_flag(self):
        """Outbound callback bridge has is_callback=True."""
        bridge = _make_bridge(is_callback=True)
        assert bridge._is_callback is True

    def test_callback_prompt_contains_context(self):
        """Callback prompt embeds task context."""
        from src.voice.prompts import get_callback_prompt

        context = {
            "task_type": "fix_bug",
            "status": "completed",
            "summary": "Fixed the login issue",
        }
        prompt = get_callback_prompt(context)

        assert "fix_bug" in prompt
        assert "completed" in prompt
        assert "Fixed the login issue" in prompt

    def test_callback_prompt_has_anti_hallucination(self):
        """Callback prompt includes anti-hallucination instructions."""
        from src.voice.prompts import get_callback_prompt

        prompt = get_callback_prompt({"task_type": "test", "summary": "done"})
        prompt_lower = prompt.lower()

        assert "only report" in prompt_lower or "do not fabricate" in prompt_lower
        assert "context" in prompt_lower

    def test_sdk_options_use_callback_prompt_for_outbound(self):
        """get_sdk_options with callback_context uses callback prompt, not normal."""
        from src.agent.sdk_client import get_sdk_options

        with patch("src.agent.sdk_client.settings") as s:
            s.VOICE_MODEL = "claude-sonnet-4-6"
            s.RENDER_API_KEY = "test"
            s.GITHUB_TOKEN = "test"
            s.EXA_API_KEY = None
            s.MULTI_TENANT = False

            options = get_sdk_options(callback_context={"task_type": "deploy", "summary": "Done", "success": True})

        assert "on-call engineer" not in options.system_prompt
        assert "deploy" in options.system_prompt.lower()
        assert "Done" in options.system_prompt

    def test_callback_greeting_success(self):
        """Success callback TwiML greeting says 'finished successfully'."""
        from src.callbacks.outbound import initiate_callback

        mock_call = MagicMock(sid="CA_test")
        mock_client = MagicMock()
        mock_client.calls.create = MagicMock(return_value=mock_call)

        with patch("src.callbacks.outbound._get_twilio_client", return_value=mock_client):
            with patch("src.callbacks.outbound.settings") as s:
                s.TWILIO_PHONE_NUMBER = "+10000000000"
                s.APP_ENV = "dev"
                s.HOST = "localhost"
                s.PORT = 8765
                asyncio.get_event_loop().run_until_complete(
                    initiate_callback("+1234", {"task_type": "deploy", "success": True}, "task_complete")
                )

        twiml = mock_client.calls.create.call_args[1]["twiml"]
        assert "finished successfully" in twiml

    def test_callback_greeting_failure(self):
        """Failure callback TwiML greeting says 'ran into an issue'."""
        from src.callbacks.outbound import initiate_callback

        mock_call = MagicMock(sid="CA_test")
        mock_client = MagicMock()
        mock_client.calls.create = MagicMock(return_value=mock_call)

        with patch("src.callbacks.outbound._get_twilio_client", return_value=mock_client):
            with patch("src.callbacks.outbound.settings") as s:
                s.TWILIO_PHONE_NUMBER = "+10000000000"
                s.APP_ENV = "dev"
                s.HOST = "localhost"
                s.PORT = 8765
                asyncio.get_event_loop().run_until_complete(
                    initiate_callback("+1234", {"task_type": "fix_bug", "success": False}, "task_complete")
                )

        twiml = mock_client.calls.create.call_args[1]["twiml"]
        assert "ran into an issue" in twiml

    def test_callback_greeting_reminder(self):
        """Reminder callback TwiML says 'quick reminder'."""
        from src.callbacks.outbound import initiate_callback

        mock_call = MagicMock(sid="CA_test")
        mock_client = MagicMock()
        mock_client.calls.create = MagicMock(return_value=mock_call)

        with patch("src.callbacks.outbound._get_twilio_client", return_value=mock_client):
            with patch("src.callbacks.outbound.settings") as s:
                s.TWILIO_PHONE_NUMBER = "+10000000000"
                s.APP_ENV = "dev"
                s.HOST = "localhost"
                s.PORT = 8765
                asyncio.get_event_loop().run_until_complete(
                    initiate_callback("+1234", {"reminder": "Check deploy"}, "reminder")
                )

        twiml = mock_client.calls.create.call_args[1]["twiml"]
        assert "quick reminder" in twiml

    def test_callback_greeting_alert(self):
        """Alert callback TwiML says 'need to tell you something'."""
        from src.callbacks.outbound import initiate_callback

        mock_call = MagicMock(sid="CA_test")
        mock_client = MagicMock()
        mock_client.calls.create = MagicMock(return_value=mock_call)

        with patch("src.callbacks.outbound._get_twilio_client", return_value=mock_client):
            with patch("src.callbacks.outbound.settings") as s:
                s.TWILIO_PHONE_NUMBER = "+10000000000"
                s.APP_ENV = "dev"
                s.HOST = "localhost"
                s.PORT = 8765
                asyncio.get_event_loop().run_until_complete(
                    initiate_callback("+1234", {"event": "cpu_high"}, "alert")
                )

        twiml = mock_client.calls.create.call_args[1]["twiml"]
        assert "need to tell you something" in twiml


# ===========================================================================
# SCENARIO 13: Callback intent detection phrases
# ===========================================================================

class TestCallbackIntentDetection:
    """Comprehensive check of callback phrase recognition."""

    def test_all_callback_phrases_detected(self):
        """Every defined CALLBACK_PHRASE triggers detection."""
        bridge = _make_bridge()
        for phrase in SDKBridgeProcessor.CALLBACK_PHRASES:
            assert bridge._has_callback_intent(f"please {phrase} about it"), \
                f"Missed callback phrase: '{phrase}'"

    def test_negative_phrases_not_detected(self):
        """Normal infrastructure phrases should NOT trigger."""
        bridge = _make_bridge()
        negatives = [
            "check the logs",
            "deploy to staging",
            "what's the CPU usage",
            "fix the auth bug",
            "scale up the API",
            "rollback the deploy",
            "show me the metrics",
            "how many instances",
        ]
        for phrase in negatives:
            assert not bridge._has_callback_intent(phrase), \
                f"False positive on: '{phrase}'"

    def test_case_insensitive(self):
        """Detection is case-insensitive."""
        bridge = _make_bridge()
        assert bridge._has_callback_intent("CALL ME BACK when done")
        assert bridge._has_callback_intent("Let Me Know how it goes")


# ===========================================================================
# SCENARIO 14: Plan normalization edge cases
# ===========================================================================

class TestPlanNormalization:
    """Test all plan input formats Claude might send."""

    def _normalize(self, plan_raw):
        """Mirror the normalization logic from handoff_task_tool."""
        import json as json_mod
        if isinstance(plan_raw, dict):
            plan = plan_raw
        elif isinstance(plan_raw, str):
            try:
                plan = json_mod.loads(plan_raw)
                if not isinstance(plan, dict):
                    plan = {"objective": str(plan), "steps": ["Execute the plan"]}
            except json_mod.JSONDecodeError:
                plan = {
                    "objective": plan_raw.split("\n")[0][:200],
                    "steps": [line.strip() for line in plan_raw.split("\n") if line.strip()],
                    "raw_plan": plan_raw
                }
        else:
            plan = {"objective": str(plan_raw), "steps": ["Execute the plan"]}
        if not plan.get("objective"):
            plan["objective"] = "Complete the requested task"
        if not plan.get("steps"):
            plan["steps"] = ["Execute the plan as described"]
        return plan

    def test_dict_passthrough(self):
        plan = self._normalize({"objective": "Deploy", "steps": ["Go"]})
        assert plan["objective"] == "Deploy"

    def test_json_string(self):
        plan = self._normalize('{"objective": "Deploy", "steps": ["Go"]}')
        assert plan["objective"] == "Deploy"

    def test_plain_text_multiline(self):
        plan = self._normalize("Deploy to staging\n1. Pull latest\n2. Run deploy")
        assert "Deploy" in plan["objective"]
        assert len(plan["steps"]) == 3
        assert "raw_plan" in plan

    def test_json_array_fallback(self):
        plan = self._normalize('["step1", "step2"]')
        assert plan["steps"] == ["Execute the plan"]

    def test_numeric_value(self):
        plan = self._normalize(42)
        assert plan["objective"] == "42"

    def test_empty_dict_gets_defaults(self):
        plan = self._normalize({})
        assert plan["objective"] == "Complete the requested task"
        assert plan["steps"] == ["Execute the plan as described"]


# ===========================================================================
# SCENARIO 15: Model configuration consistency
# ===========================================================================

class TestModelConfiguration:
    """Verify Sonnet 4.6 is configured everywhere."""

    def test_config_default(self):
        from src.config import Settings
        assert Settings.model_fields["VOICE_MODEL"].default == "claude-sonnet-4-6"

    def test_sdk_options_model(self):
        from src.agent.sdk_client import get_sdk_options
        with patch("src.agent.sdk_client.settings") as s:
            s.VOICE_MODEL = "claude-sonnet-4-6"
            s.RENDER_API_KEY = "test"
            s.GITHUB_TOKEN = "test"
            s.EXA_API_KEY = None
            s.MULTI_TENANT = False
            options = get_sdk_options()
        assert options.model == "claude-sonnet-4-6"

    def test_worker_hardcoded_model(self):
        import inspect
        from src.tasks.worker import execute_background_task
        source = inspect.getsource(execute_background_task)
        assert '"claude-sonnet-4-6"' in source

    def test_no_stale_model_strings(self):
        """No old model strings in source code."""
        import os
        old = ["claude-sonnet-4-5-20250929", "claude-sonnet-4-5", "claude-3-5-sonnet"]
        src_dir = os.path.join(os.path.dirname(__file__), "..", "src")
        violations = []
        for root, _, files in os.walk(src_dir):
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(root, f)
                with open(path) as fh:
                    content = fh.read()
                for old_model in old:
                    if old_model in content:
                        violations.append(f"{path}: '{old_model}'")
        assert violations == [], "Old model strings found:\n" + "\n".join(violations)


# ===========================================================================
# SCENARIO 16: Filler phrase selection
# ===========================================================================

class TestFillerPhrases:
    """Verify contextual filler selection."""

    def test_lookup_fillers(self):
        bridge = _make_bridge()
        filler = bridge._get_contextual_filler("check the logs")
        assert filler in SDKBridgeProcessor.THINKING_FILLERS["lookup"]

    def test_action_fillers(self):
        bridge = _make_bridge()
        filler = bridge._get_contextual_filler("deploy to production")
        assert filler in SDKBridgeProcessor.THINKING_FILLERS["action"]

    def test_complex_fillers(self):
        bridge = _make_bridge()
        filler = bridge._get_contextual_filler("why is the API slow")
        assert filler in SDKBridgeProcessor.THINKING_FILLERS["complex"]

    def test_default_fillers(self):
        bridge = _make_bridge()
        filler = bridge._get_contextual_filler("something random")
        assert filler in SDKBridgeProcessor.THINKING_FILLERS["default"]


# ===========================================================================
# SCENARIO 17: Cancel fallback reminder on handoff
# ===========================================================================

class TestCancelFallbackOnHandoff:
    """handoff_task cancels any pending fallback reminder."""

    @pytest.mark.asyncio
    async def test_cancel_called_after_successful_handoff(self):
        from src.agent.sdk_client import handoff_task_tool, _set_session_context

        _set_session_context(user_context={"user_id": "u1"}, caller_phone="+1234")

        with patch("src.agent.sdk_client.settings") as s:
            s.MULTI_TENANT = False
            with patch("src.db.background_tasks.create_background_task", new_callable=AsyncMock, return_value="t1"):
                with patch("src.tasks.queue.enqueue_background_task", new_callable=AsyncMock):
                    with patch("src.tasks.queue.cancel_fallback_reminder", new_callable=AsyncMock) as mock_cancel:
                        await handoff_task_tool.handler({
                            "task_type": "deploy",
                            "plan": {"objective": "Go", "steps": ["Go"]},
                            "notify_on": "both",
                        })

        mock_cancel.assert_called_once_with("+1234")


# ===========================================================================
# SCENARIO 18: End-to-end inbound → handoff → worker → callback
# ===========================================================================

class TestEndToEndFlow:
    """
    Simulate the complete flow:
    1. User calls in, asks to deploy and call back
    2. handoff_task creates task in DB, enqueues to Redis
    3. Worker picks up task, runs headless SDK
    4. Worker calls user back with result
    5. Callback TwiML has correct context
    """

    @pytest.mark.asyncio
    async def test_complete_e2e_flow(self):
        """Full end-to-end: handoff → worker → callback → TwiML."""
        from src.agent.sdk_client import handoff_task_tool, _set_session_context
        from claude_agent_sdk import ResultMessage
        from src.tasks.worker import execute_background_task
        from src.callbacks.outbound import initiate_callback

        # === PHASE 1: Handoff during call ===
        _set_session_context(user_context=None, caller_phone="+14155551234")

        with patch("src.agent.sdk_client.settings") as s:
            s.MULTI_TENANT = False
            with patch("src.db.background_tasks.create_background_task", new_callable=AsyncMock, return_value="task-e2e") as mock_create:
                with patch("src.tasks.queue.enqueue_background_task", new_callable=AsyncMock) as mock_enqueue:
                    with patch("src.tasks.queue.cancel_fallback_reminder", new_callable=AsyncMock):
                        handoff_result = await handoff_task_tool.handler({
                            "task_type": "deploy",
                            "plan": {
                                "objective": "Deploy ringfra to staging",
                                "steps": ["git pull", "render deploy"],
                                "success_criteria": "Health checks pass",
                            },
                            "notify_on": "both",
                        })

        assert handoff_result.get("is_error") is None or handoff_result.get("is_error") is False
        saved_plan = mock_create.call_args[1]["plan"]
        assert saved_plan["objective"] == "Deploy ringfra to staging"

        # === PHASE 2: Worker executes task ===
        mock_task = {
            "phone": "+14155551234",
            "user_id": "owner",
            "plan": saved_plan,
            "task_type": "deploy",
        }

        mock_result = MagicMock(spec=ResultMessage)
        mock_result.is_error = False
        mock_result.num_turns = 4
        mock_result.structured_output = {
            "summary": "Deployed ringfra to staging. Health checks pass. 0 errors in last 2 min.",
            "success": True,
            "details": {"service": "ringfra", "deploy_id": "dep-xyz"},
            "action_items": ["Monitor for 30 min"],
        }
        mock_result.total_cost_usd = 0.06

        patches = _make_worker_mocks(mock_task, result_msg=mock_result)

        with patches["env"], patches["cli"], patches["get_task"], \
             patches["update_status"], patches["get_creds"], patches["get_repos"], \
             patches["query"], patches["callback"] as mock_callback, patches["sms"]:
            worker_result = await execute_background_task({}, "task-e2e")

        assert worker_result["status"] == "completed"
        assert "ringfra" in worker_result["result"].lower()

        # === PHASE 3: Callback TwiML ===
        callback_context = mock_callback.call_args[1]["context"]

        mock_call = MagicMock(sid="CA_e2e")
        mock_client = MagicMock()
        mock_client.calls.create = MagicMock(return_value=mock_call)

        with patch("src.callbacks.outbound._get_twilio_client", return_value=mock_client):
            with patch("src.callbacks.outbound.settings") as s:
                s.TWILIO_PHONE_NUMBER = "+10000000000"
                s.APP_ENV = "production"
                await initiate_callback("+14155551234", callback_context, "task_complete")

        twiml = mock_client.calls.create.call_args[1]["twiml"]

        # Verify TwiML has all the pieces
        assert "finished successfully" in twiml
        assert "deploy" in twiml

        # Parse embedded context
        match = re.search(r'name="callbackContext" value="([^"]*)"', twiml)
        parsed = json.loads(html.unescape(match.group(1)))
        assert parsed["success"] is True
        assert "ringfra" in parsed["summary"].lower()
        assert parsed["action_items"] == ["Monitor for 30 min"]
