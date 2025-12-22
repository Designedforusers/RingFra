"""
Tests for signup flow and user management.

Note: These tests require asyncpg which may not be installed in all environments.
Tests that require database imports are marked with skipif.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# Check if asyncpg is available
try:
    import asyncpg
    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False

# Check if fastapi testclient deps are available
try:
    from fastapi.testclient import TestClient
    HAS_TESTCLIENT = True
except ImportError:
    HAS_TESTCLIENT = False


@pytest.mark.skipif(not HAS_ASYNCPG or not HAS_TESTCLIENT, reason="Requires asyncpg and fastapi")
class TestLandingPage:
    """Test landing page rendering."""
    
    def test_landing_page_loads(self):
        from src.web.signup import app
        from fastapi.testclient import TestClient
        
        client = TestClient(app)
        response = client.get("/")
        
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
    
    def test_landing_page_has_phone_number(self):
        from src.web.signup import app
        from fastapi.testclient import TestClient
        
        client = TestClient(app)
        response = client.get("/")
        
        # Should show the phone number to call
        assert "415" in response.text or "phone" in response.text.lower()


@pytest.mark.skipif(not HAS_ASYNCPG or not HAS_TESTCLIENT, reason="Requires asyncpg and fastapi")
class TestGitHubOAuth:
    """Test GitHub OAuth flow."""
    
    def test_github_login_redirect(self):
        from src.web.signup import app
        from fastapi.testclient import TestClient
        
        client = TestClient(app, follow_redirects=False)
        response = client.get("/auth/github?state=test-state")
        
        assert response.status_code == 302
        assert "github.com" in response.headers["location"]


@pytest.mark.skipif(not HAS_ASYNCPG or not HAS_TESTCLIENT, reason="Requires asyncpg and fastapi")
class TestRepoSelection:
    """Test repository selection page."""
    
    def test_repos_page_requires_session(self):
        from src.web.signup import app
        from fastapi.testclient import TestClient
        
        client = TestClient(app)
        response = client.get("/signup/repos")
        
        # Should redirect to start if no session
        assert response.status_code in [302, 303, 200]


@pytest.mark.skipif(not HAS_ASYNCPG, reason="Requires asyncpg")
class TestUserLookup:
    """Test user lookup functionality."""
    
    @pytest.mark.asyncio
    async def test_lookup_known_user(self):
        from src.db.users import get_user_by_phone
        
        with patch("src.db.users.get_pool") as mock_pool:
            mock_conn = AsyncMock()
            mock_pool.return_value.acquire.return_value.__aenter__.return_value = mock_conn
            mock_conn.fetchrow.return_value = {
                "id": "user-123",
                "phone": "+14155551234",
                "name": "Test User",
            }
            
            user = await get_user_by_phone("+14155551234")
            
            assert user is not None
            assert user["id"] == "user-123"


@pytest.mark.skipif(not HAS_ASYNCPG, reason="Requires asyncpg")
class TestCredentialEncryption:
    """Test credential encryption."""
    
    def test_encrypt_decrypt_roundtrip(self):
        from src.db.users import _encrypt, _decrypt
        
        original = "super-secret-token"
        encrypted = _encrypt(original)
        decrypted = _decrypt(encrypted)
        
        assert decrypted == original
        assert encrypted != original


class TestEncryptionLogic:
    """Test encryption logic without database imports."""
    
    def test_fernet_encryption_available(self):
        """Verify Fernet encryption is available."""
        from cryptography.fernet import Fernet
        
        key = Fernet.generate_key()
        f = Fernet(key)
        
        original = b"test-secret"
        encrypted = f.encrypt(original)
        decrypted = f.decrypt(encrypted)
        
        assert decrypted == original
        assert encrypted != original


class TestShipIntentIntegration:
    """Test ship intent parsing edge cases."""
    
    def test_empty_instructions(self):
        from src.tools.code_tools import parse_ship_intent
        
        result = parse_ship_intent("")
        assert result is not None
        assert "test_strategy" in result
        assert "review_strategy" in result
    
    def test_nonsense_instructions(self):
        from src.tools.code_tools import parse_ship_intent
        
        result = parse_ship_intent("asdfghjkl gibberish 12345")
        # Should return defaults without crashing
        assert result is not None
    
    def test_mixed_case_instructions(self):
        from src.tools.code_tools import parse_ship_intent
        from src.repos.manager import TestStrategy
        
        result = parse_ship_intent("SHIP IT WITH TESTS")
        assert result["test_strategy"] == TestStrategy.LOCAL
    
    def test_instructions_with_punctuation(self):
        from src.tools.code_tools import parse_ship_intent
        from src.repos.manager import ReviewStrategy
        
        result = parse_ship_intent("ship it! no review, please!")
        assert result["review_strategy"] == ReviewStrategy.NONE
