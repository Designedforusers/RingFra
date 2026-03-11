"""
Tests for handoff_task plan normalization and database insertion.

Tests the three input formats Claude sends:
1. Dict - proper structured plan
2. JSON string - serialized JSON
3. Plain text - unstructured task description
"""

import json
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from uuid import UUID


class TestPlanNormalization:
    """Test plan normalization logic in handoff_task_tool."""

    def _normalize_plan(self, plan_raw):
        """
        Extract the plan normalization logic for testing.
        This mirrors the logic in handoff_task_tool.
        """
        if isinstance(plan_raw, dict):
            plan = plan_raw
        elif isinstance(plan_raw, str):
            try:
                plan = json.loads(plan_raw)
                if not isinstance(plan, dict):
                    plan = {"objective": str(plan), "steps": ["Execute the plan"]}
            except json.JSONDecodeError:
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

    def test_dict_input(self):
        """Test proper dict input passes through correctly."""
        plan_raw = {
            "objective": "Deploy to staging",
            "steps": ["Build", "Test", "Deploy"],
            "success_criteria": "Deployment successful"
        }
        
        plan = self._normalize_plan(plan_raw)
        
        assert plan["objective"] == "Deploy to staging"
        assert plan["steps"] == ["Build", "Test", "Deploy"]
        assert plan["success_criteria"] == "Deployment successful"

    def test_json_string_input(self):
        """Test JSON string is parsed correctly (the 08:11:34 case)."""
        plan_raw = '{"objective": "Analyze logs", "steps": ["Step 1", "Step 2"]}'
        
        plan = self._normalize_plan(plan_raw)
        
        assert plan["objective"] == "Analyze logs"
        assert plan["steps"] == ["Step 1", "Step 2"]
        assert isinstance(plan, dict)

    def test_json_string_with_newlines(self):
        """Test JSON string with escaped newlines (real production case)."""
        plan_raw = '{\n  "objective": "Analyze render-voice-agent runtime logs",\n  "steps": [\n    "1. Review logs",\n    "2. Categorize entries"\n  ]\n}'
        
        plan = self._normalize_plan(plan_raw)
        
        assert plan["objective"] == "Analyze render-voice-agent runtime logs"
        assert len(plan["steps"]) == 2

    def test_plain_text_input(self):
        """Test plain text is wrapped correctly (the 08:42:02 case)."""
        plan_raw = """Task: Follow up on Render phone agent logs review

Objective: Call user back to confirm review of logs is complete

Steps:
1. No additional work needed - logs have been reviewed
2. Call user back with summary

Success Criteria:
- User is informed that log review is complete"""
        
        plan = self._normalize_plan(plan_raw)
        
        # First line becomes objective
        assert "Task: Follow up" in plan["objective"]
        # All non-empty lines become steps
        assert len(plan["steps"]) > 0
        # Raw plan is preserved
        assert "raw_plan" in plan

    def test_empty_objective_gets_default(self):
        """Test empty objective gets default value."""
        plan_raw = {"steps": ["Do something"]}
        
        plan = self._normalize_plan(plan_raw)
        
        assert plan["objective"] == "Complete the requested task"

    def test_empty_steps_gets_default(self):
        """Test empty steps gets default value."""
        plan_raw = {"objective": "Do something"}
        
        plan = self._normalize_plan(plan_raw)
        
        assert plan["steps"] == ["Execute the plan as described"]

    def test_non_dict_json_value(self):
        """Test JSON that parses to non-dict (e.g., a string or array)."""
        plan_raw = '"just a string"'
        
        plan = self._normalize_plan(plan_raw)
        
        assert plan["objective"] == "just a string"
        assert plan["steps"] == ["Execute the plan"]

    def test_invalid_json_fallback(self):
        """Test invalid JSON falls back to plain text handling."""
        plan_raw = "This is not valid JSON {but has braces}"
        
        plan = self._normalize_plan(plan_raw)
        
        assert plan["objective"] == "This is not valid JSON {but has braces}"


class TestDatabaseInsertion:
    """Test that plan is correctly serialized for JSONB insertion."""

    def test_json_dumps_for_jsonb(self):
        """Verify json.dumps produces valid JSONB-compatible string."""
        plan = {
            "objective": "Test objective",
            "steps": ["Step 1", "Step 2"],
            "context": {"key": "value"}
        }
        
        plan_json = json.dumps(plan)
        
        # Should be a string
        assert isinstance(plan_json, str)
        # Should be valid JSON
        parsed = json.loads(plan_json)
        assert parsed == plan

    def test_unicode_in_plan(self):
        """Test plan with unicode characters serializes correctly."""
        plan = {
            "objective": "Fix the bug 🐛",
            "steps": ["Step with émojis 🚀"]
        }
        
        plan_json = json.dumps(plan)
        parsed = json.loads(plan_json)
        
        assert "🐛" in parsed["objective"]
        assert "🚀" in parsed["steps"][0]

    def test_nested_structures(self):
        """Test deeply nested plan structures serialize correctly."""
        plan = {
            "objective": "Complex task",
            "steps": ["Step 1"],
            "context": {
                "nested": {
                    "deeply": {
                        "value": [1, 2, 3]
                    }
                }
            }
        }
        
        plan_json = json.dumps(plan)
        parsed = json.loads(plan_json)
        
        assert parsed["context"]["nested"]["deeply"]["value"] == [1, 2, 3]


class TestCreateBackgroundTask:
    """Test create_background_task database function."""

    @pytest.mark.asyncio
    async def test_create_background_task_passes_dict_directly(self):
        """Test that create_background_task passes dict directly (codec handles serialization)."""
        from src.db.background_tasks import create_background_task

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"id": UUID("12345678-1234-5678-1234-567812345678")})

        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock()))

        with patch("src.db.background_tasks.get_pool", return_value=mock_pool):
            plan = {"objective": "Test", "steps": ["Step 1"]}

            task_id = await create_background_task(
                user_id=UUID("12345678-1234-5678-1234-567812345678"),
                phone="+1234567890",
                task_type="test",
                plan=plan
            )

            # Verify fetchrow was called
            mock_conn.fetchrow.assert_called_once()

            # Get the actual call arguments
            call_args = mock_conn.fetchrow.call_args

            # The 4th positional arg should be the dict directly (codec handles JSONB)
            plan_arg = call_args[0][4]  # SQL is [0], then user_id, phone, task_type, plan

            # Should be a dict (codec set in connection.py handles serialization)
            assert isinstance(plan_arg, dict)
            assert plan_arg == plan


class TestSingleTenantFallback:
    """Test the single-tenant user_id fallback that prevents the demo failure."""

    @pytest.mark.asyncio
    async def test_handoff_single_tenant_uses_owner_id(self):
        """No user_id + MULTI_TENANT=False → uses 'owner' and succeeds."""
        from src.agent.sdk_client import handoff_task_tool, _set_session_context

        _set_session_context(user_context={}, caller_phone="+1234567890")

        mock_task_id = "task-123"
        with patch("src.agent.sdk_client.settings") as mock_settings:
            mock_settings.MULTI_TENANT = False
            with patch("src.db.background_tasks.create_background_task", new_callable=AsyncMock, return_value=mock_task_id) as mock_create:
                with patch("src.tasks.queue.enqueue_background_task", new_callable=AsyncMock):
                    with patch("src.tasks.queue.cancel_fallback_reminder", new_callable=AsyncMock):
                        result = await handoff_task_tool.handler({
                            "task_type": "deploy",
                            "plan": {"objective": "Deploy to staging", "steps": ["Deploy"]},
                            "notify_on": "both",
                        })

        # Should succeed, not error
        assert result.get("is_error") is None or result.get("is_error") is False
        assert "Task handed off" in result["content"][0]["text"]

        # Should have passed "owner" as user_id
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args
        assert call_kwargs[1]["user_id"] == "owner" or call_kwargs[0][0] == "owner"

    @pytest.mark.asyncio
    async def test_handoff_multi_tenant_rejects_no_user(self):
        """No user_id + MULTI_TENANT=True → returns error."""
        from src.agent.sdk_client import handoff_task_tool, _set_session_context

        _set_session_context(user_context={}, caller_phone="+1234567890")

        with patch("src.agent.sdk_client.settings") as mock_settings:
            mock_settings.MULTI_TENANT = True
            result = await handoff_task_tool.handler({
                "task_type": "deploy",
                "plan": {"objective": "Deploy", "steps": ["Deploy"]},
                "notify_on": "both",
            })

        assert result["is_error"] is True
        assert "authenticated user" in result["content"][0]["text"].lower() or "user context" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_handoff_db_error_returns_error(self):
        """create_background_task throws → error message returned."""
        from src.agent.sdk_client import handoff_task_tool, _set_session_context

        _set_session_context(
            user_context={"user_id": "user-123"},
            caller_phone="+1234567890",
        )

        with patch("src.agent.sdk_client.settings") as mock_settings:
            mock_settings.MULTI_TENANT = False
            with patch(
                "src.db.background_tasks.create_background_task",
                new_callable=AsyncMock,
                side_effect=Exception("DNS resolution failed for db host"),
            ):
                result = await handoff_task_tool.handler({
                    "task_type": "deploy",
                    "plan": {"objective": "Deploy", "steps": ["Deploy"]},
                    "notify_on": "both",
                })

        assert result["is_error"] is True
        assert "Failed to hand off task" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_handoff_redis_unavailable_sends_sms(self):
        """RedisUnavailableError from enqueue → SMS fallback sent."""
        from src.agent.sdk_client import handoff_task_tool, _set_session_context
        from src.tasks.queue import RedisUnavailableError

        _set_session_context(
            user_context={"user_id": "user-123"},
            caller_phone="+1234567890",
        )

        with patch("src.agent.sdk_client.settings") as mock_settings:
            mock_settings.MULTI_TENANT = False
            with patch("src.db.background_tasks.create_background_task", new_callable=AsyncMock, return_value="task-123"):
                with patch(
                    "src.tasks.queue.enqueue_background_task",
                    new_callable=AsyncMock,
                    side_effect=RedisUnavailableError("Redis not configured"),
                ):
                    with patch("src.callbacks.outbound.send_sms", new_callable=AsyncMock) as mock_sms:
                        result = await handoff_task_tool.handler({
                            "task_type": "deploy",
                            "plan": {"objective": "Deploy", "steps": ["Deploy"]},
                            "notify_on": "both",
                        })

        assert result["is_error"] is True
        assert "background service" in result["content"][0]["text"].lower()
        mock_sms.assert_called_once()
        assert "+1234567890" in mock_sms.call_args[0]
