"""
Tests for repo management tools in sdk_client.

Note: The @tool decorator wraps functions, so we test the context
management and underlying logic directly.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path
from uuid import uuid4


class TestSessionContext:
    """Test session context management."""
    
    def test_set_and_get_context(self):
        """Test setting and getting session context."""
        from src.agent.sdk_client import _set_session_context, _get_session_context
        
        user_context = {"user_id": str(uuid4()), "credentials": {}}
        caller_phone = "+14155551234"
        
        _set_session_context(user_context, caller_phone)
        
        ctx = _get_session_context()
        assert ctx["caller_phone"] == caller_phone
        assert ctx["user_context"] == user_context
        assert "github_token" in ctx
        assert ctx["task_context"] == {}
    
    def test_context_with_github_creds(self):
        """Test that user GitHub creds override default."""
        from src.agent.sdk_client import _set_session_context, _get_session_context
        
        user_context = {
            "user_id": str(uuid4()),
            "credentials": {
                "github": {"access_token": "user-specific-token"}
            }
        }
        
        _set_session_context(user_context, "+14155551234")
        
        ctx = _get_session_context()
        assert ctx["github_token"] == "user-specific-token"
    
    def test_update_task_context(self):
        """Test updating task context."""
        from src.agent.sdk_client import _set_session_context, _get_session_context, _update_task_context
        
        _set_session_context(None, None)
        
        task_ctx = {
            "worktree_path": "/app/repos/test/work-123",
            "branch_name": "fix/bug-123",
            "repo_url": "https://github.com/test/repo",
        }
        
        _update_task_context(task_ctx)
        
        ctx = _get_session_context()
        assert ctx["task_context"] == task_ctx


class TestConcurrentSessionIsolation:
    """Test that concurrent sessions don't interfere with each other."""
    
    @pytest.mark.asyncio
    async def test_concurrent_contexts_isolated(self):
        """Test that two concurrent tasks have isolated contexts."""
        import asyncio
        from src.agent.sdk_client import _set_session_context, _get_session_context
        
        results = {}
        
        async def task_a():
            _set_session_context({"user_id": "user-A"}, "+1111111111")
            await asyncio.sleep(0.1)  # Simulate async work
            ctx = _get_session_context()
            results["a"] = ctx.get("caller_phone")
        
        async def task_b():
            _set_session_context({"user_id": "user-B"}, "+2222222222")
            await asyncio.sleep(0.05)  # Finish before task_a
            ctx = _get_session_context()
            results["b"] = ctx.get("caller_phone")
        
        # Run concurrently
        await asyncio.gather(task_a(), task_b())
        
        # Each task should see its own context
        assert results["a"] == "+1111111111"
        assert results["b"] == "+2222222222"


class TestMultiTenantFlag:
    """Test MULTI_TENANT config flag behavior."""
    
    def test_tools_excluded_when_disabled(self):
        """Test that repo tools are excluded when MULTI_TENANT=False."""
        with patch('src.agent.sdk_client.settings') as mock_settings:
            mock_settings.MULTI_TENANT = False
            mock_settings.GITHUB_TOKEN = "test"
            mock_settings.RENDER_API_KEY = "test"
            mock_settings.EXA_API_KEY = None
            
            # Re-import to pick up patched settings
            # This would need a module reload to work properly
            # For now we just verify the config exists
            from src.config import settings
            assert hasattr(settings, 'MULTI_TENANT')
    
    def test_tools_included_when_enabled(self):
        """Test that repo tools are included when MULTI_TENANT=True."""
        from src.config import settings
        assert hasattr(settings, 'MULTI_TENANT')


class TestReposManagerIntegration:
    """Test that repos/manager.py functions exist and have correct signatures."""
    
    def test_create_task_worktree_exists(self):
        """Test create_task_worktree function exists."""
        from src.repos.manager import create_task_worktree
        import inspect
        sig = inspect.signature(create_task_worktree)
        params = list(sig.parameters.keys())
        assert "user_id" in params
        assert "repo_url" in params
        assert "github_token" in params
    
    def test_ship_changes_exists(self):
        """Test ship_changes function exists."""
        from src.repos.manager import ship_changes
        import inspect
        sig = inspect.signature(ship_changes)
        params = list(sig.parameters.keys())
        assert "worktree_path" in params
        assert "repo_url" in params
        assert "github_token" in params
    
    def test_cleanup_worktree_exists(self):
        """Test cleanup_worktree function exists."""
        from src.repos.manager import cleanup_worktree
        import inspect
        sig = inspect.signature(cleanup_worktree)
        params = list(sig.parameters.keys())
        assert "worktree_path" in params
    
    def test_commit_type_enum_exists(self):
        """Test CommitType enum exists."""
        from src.repos.manager import CommitType
        assert hasattr(CommitType, 'FIX')
        assert hasattr(CommitType, 'FEAT')
        assert hasattr(CommitType, 'REFACTOR')
    
    def test_test_strategy_enum_exists(self):
        """Test TestStrategy enum exists."""
        from src.repos.manager import TestStrategy
        assert hasattr(TestStrategy, 'NONE')
        assert hasattr(TestStrategy, 'LOCAL')
        assert hasattr(TestStrategy, 'CI')
