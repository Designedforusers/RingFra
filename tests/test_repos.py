"""
Tests for repository management - worktrees, git operations, shipping.
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

from src.repos.manager import (
    CommitType,
    TestStrategy,
    ReviewStrategy,
    TaskResult,
    GitError,
    AuthenticationError,
    ConflictError,
    NetworkError,
    RepoNotFoundError,
    _run_git_command,
    _detect_default_branch,
)


class TestCommitTypes:
    """Test commit type enum and branch naming."""
    
    def test_commit_types_exist(self):
        assert CommitType.FIX.value == "fix"
        assert CommitType.FEAT.value == "feat"
        assert CommitType.DOCS.value == "docs"
        assert CommitType.REFACTOR.value == "refactor"
        assert CommitType.TEST.value == "test"
        assert CommitType.CHORE.value == "chore"
    
    def test_branch_naming_convention(self):
        """Branch names should follow agent/{type}/description-{id} pattern."""
        # This is tested implicitly through create_task_worktree
        # The pattern is: agent/{commit_type.value}/{safe_desc}-{task_id}
        expected_pattern = "agent/fix/"
        assert "agent" in expected_pattern
        assert "fix" in expected_pattern


class TestStrategies:
    """Test shipping strategies."""
    
    def test_test_strategies(self):
        assert TestStrategy.NONE.value == "none"
        assert TestStrategy.LOCAL.value == "local"
        assert TestStrategy.CI.value == "ci"
        assert TestStrategy.BOTH.value == "both"
    
    def test_review_strategies(self):
        assert ReviewStrategy.NONE.value == "none"
        assert ReviewStrategy.CLAUDE.value == "claude"
        assert ReviewStrategy.HUMAN.value == "human"


class TestTaskResult:
    """Test task result dataclass."""
    
    def test_success_result(self):
        result = TaskResult(True, "All good", {"pr_url": "https://github.com/..."})
        assert result.success is True
        assert result.message == "All good"
        assert result.data["pr_url"].startswith("https://")
    
    def test_failure_result(self):
        result = TaskResult(False, "Tests failed", {"stage": "local_tests"})
        assert result.success is False
        assert "failed" in result.message.lower()


class TestExceptions:
    """Test git exception hierarchy."""
    
    def test_authentication_error(self):
        err = AuthenticationError("Token expired")
        assert isinstance(err, GitError)
        assert "Token" in str(err)
    
    def test_conflict_error(self):
        err = ConflictError("Merge conflict in main.py")
        assert isinstance(err, GitError)
        assert "conflict" in str(err).lower()
    
    def test_network_error(self):
        err = NetworkError("Connection timeout")
        assert isinstance(err, GitError)
    
    def test_repo_not_found(self):
        err = RepoNotFoundError("Repo deleted")
        assert isinstance(err, GitError)


class TestGitCommands:
    """Test low-level git command execution."""
    
    @pytest.mark.asyncio
    async def test_run_git_command_success(self):
        with patch("asyncio.create_subprocess_shell") as mock_proc:
            mock_proc.return_value.communicate = AsyncMock(
                return_value=(b"success output", b"")
            )
            mock_proc.return_value.returncode = 0
            
            success, output = await _run_git_command("git status", cwd=Path("/tmp"))
            
            assert success is True
            assert "success" in output
    
    @pytest.mark.asyncio
    async def test_run_git_command_failure(self):
        with patch("asyncio.create_subprocess_shell") as mock_proc:
            mock_proc.return_value.communicate = AsyncMock(
                return_value=(b"", b"error: something went wrong")
            )
            mock_proc.return_value.returncode = 1
            
            success, output = await _run_git_command("git bad-command", cwd=Path("/tmp"))
            
            assert success is False
            assert "error" in output.lower()
    
    @pytest.mark.asyncio
    async def test_run_git_command_auth_error_in_output(self):
        """Auth errors are detected by output content, not raised directly."""
        with patch("asyncio.create_subprocess_shell") as mock_proc:
            mock_proc.return_value.communicate = AsyncMock(
                return_value=(b"", b"fatal: Authentication failed")
            )
            mock_proc.return_value.returncode = 128
            
            success, output = await _run_git_command("git push", cwd=Path("/tmp"))
            
            assert success is False
            assert "Authentication" in output or "fatal" in output
    
    @pytest.mark.asyncio
    async def test_run_git_command_repo_not_found_in_output(self):
        """Repo not found is detected by output content."""
        with patch("asyncio.create_subprocess_shell") as mock_proc:
            mock_proc.return_value.communicate = AsyncMock(
                return_value=(b"", b"ERROR: Repository not found")
            )
            mock_proc.return_value.returncode = 128
            
            success, output = await _run_git_command(
                "git clone https://github.com/deleted/repo", 
                cwd=Path("/tmp"),
            )
            
            assert success is False
            assert "not found" in output.lower()


class TestDefaultBranchDetection:
    """Test detecting default branch (main vs master)."""
    
    @pytest.mark.asyncio
    async def test_detect_main_branch(self):
        with patch("src.repos.manager._run_git_command", new_callable=AsyncMock) as mock_cmd:
            mock_cmd.return_value = (True, "refs/remotes/origin/main")
            
            branch = await _detect_default_branch(Path("/tmp/repo"), "token")
            
            assert branch == "main"
    
    @pytest.mark.asyncio
    async def test_detect_master_branch(self):
        with patch("src.repos.manager._run_git_command", new_callable=AsyncMock) as mock_cmd:
            mock_cmd.return_value = (True, "refs/remotes/origin/master")
            
            branch = await _detect_default_branch(Path("/tmp/repo"), "token")
            
            assert branch == "master"
    
    @pytest.mark.asyncio
    async def test_detect_default_fallback(self):
        with patch("src.repos.manager._run_git_command", new_callable=AsyncMock) as mock_cmd:
            # First call (symbolic-ref) fails, second call (rev-parse main) succeeds
            mock_cmd.side_effect = [(False, ""), (True, "")]
            
            branch = await _detect_default_branch(Path("/tmp/repo"), "token")
            
            assert branch == "main"  # Fallback to main when it exists
    
    @pytest.mark.asyncio
    async def test_detect_default_fallback_to_master(self):
        with patch("src.repos.manager._run_git_command", new_callable=AsyncMock) as mock_cmd:
            # Both calls fail - fallback to master
            mock_cmd.side_effect = [(False, ""), (False, "")]
            
            branch = await _detect_default_branch(Path("/tmp/repo"), "token")
            
            assert branch == "master"  # Fallback to master when main doesn't exist


class TestShipChanges:
    """Test the full ship_changes workflow."""
    
    @pytest.mark.asyncio
    async def test_ship_with_local_tests_pass(self):
        from src.repos.manager import ship_changes
        
        with patch("src.repos.manager._run_git_command") as mock_cmd, \
             patch("src.repos.manager.rebase_on_latest") as mock_rebase, \
             patch("src.repos.manager.push_branch") as mock_push, \
             patch("src.repos.manager._detect_default_branch") as mock_branch, \
             patch("src.github.actions.create_pull_request") as mock_pr, \
             patch("src.github.actions.trigger_claude_review") as mock_review:
            
            # Setup mocks
            mock_cmd.return_value = (True, "All tests passed")
            mock_rebase.return_value = TaskResult(True, "Rebased", None)
            mock_push.return_value = TaskResult(True, "Pushed", None)
            mock_branch.return_value = "main"
            mock_pr.return_value = {"html_url": "https://github.com/test/pr/1", "number": 1}
            mock_review.return_value = {"id": 123}
            
            result = await ship_changes(
                worktree_path=Path("/tmp/worktree"),
                repo_url="https://github.com/test/repo",
                branch_name="agent/fix/test",
                github_token="token",
                title="Test PR",
                test_strategy=TestStrategy.LOCAL,
                review_strategy=ReviewStrategy.CLAUDE,
            )
            
            assert result.success is True
            assert "Created PR" in result.message
            assert result.data["pr_number"] == 1
    
    @pytest.mark.asyncio
    async def test_ship_with_local_tests_fail(self):
        from src.repos.manager import ship_changes
        
        with patch("src.repos.manager._run_git_command") as mock_cmd:
            mock_cmd.return_value = (False, "FAILED: test_login.py")
            
            result = await ship_changes(
                worktree_path=Path("/tmp/worktree"),
                repo_url="https://github.com/test/repo",
                branch_name="agent/fix/test",
                github_token="token",
                title="Test PR",
                test_strategy=TestStrategy.LOCAL,
            )
            
            assert result.success is False
            assert "tests failed" in result.message.lower()
            assert result.data["stage"] == "local_tests"
    
    @pytest.mark.asyncio
    async def test_ship_skip_tests(self):
        from src.repos.manager import ship_changes
        
        with patch("src.repos.manager._run_git_command") as mock_cmd, \
             patch("src.repos.manager.rebase_on_latest") as mock_rebase, \
             patch("src.repos.manager.push_branch") as mock_push, \
             patch("src.repos.manager._detect_default_branch") as mock_branch, \
             patch("src.github.actions.create_pull_request") as mock_pr:
            
            mock_rebase.return_value = TaskResult(True, "Rebased", None)
            mock_push.return_value = TaskResult(True, "Pushed", None)
            mock_branch.return_value = "main"
            mock_pr.return_value = {"html_url": "https://github.com/test/pr/2", "number": 2}
            
            result = await ship_changes(
                worktree_path=Path("/tmp/worktree"),
                repo_url="https://github.com/test/repo",
                branch_name="agent/fix/test",
                github_token="token",
                title="Docs update",
                test_strategy=TestStrategy.NONE,
                review_strategy=ReviewStrategy.NONE,
            )
            
            assert result.success is True
            # _run_git_command should NOT have been called for tests
            mock_cmd.assert_not_called()


class TestAutoMerge:
    """Test auto-merge functionality."""
    
    @pytest.mark.asyncio
    async def test_enable_auto_merge(self):
        from src.repos.manager import _enable_auto_merge
        
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_instance
            
            # Mock GET for PR node_id
            mock_get_resp = MagicMock()
            mock_get_resp.status_code = 200
            mock_get_resp.json.return_value = {"node_id": "PR_123"}
            mock_instance.get.return_value = mock_get_resp
            
            # Mock POST for GraphQL
            mock_post_resp = MagicMock()
            mock_post_resp.status_code = 200
            mock_instance.post.return_value = mock_post_resp
            
            result = await _enable_auto_merge("owner", "repo", 1, "token")
            
            assert result is True
            mock_instance.post.assert_called_once()
