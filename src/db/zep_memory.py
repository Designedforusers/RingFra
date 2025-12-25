"""
Zep memory integration for persistent conversation context.

Zep provides:
- Real-time message persistence (survives abrupt hangups)
- Knowledge graph with temporal facts
- P95 < 200ms context retrieval
- User-level memory across all conversations

Architecture:
    Each call creates a new thread under the user.
    Messages are persisted after each turn with return_context=True
    to get updated context in the same call (latency optimization).
"""

from typing import Any
from uuid import UUID

from loguru import logger

from src.config import settings

_zep_client = None


async def get_zep_client():
    """Get or create the Zep client singleton."""
    global _zep_client
    
    if _zep_client is not None:
        return _zep_client
    
    if not settings.ZEP_API_KEY:
        logger.warning("ZEP_API_KEY not set - Zep memory disabled")
        return None
    
    try:
        from zep_cloud import AsyncZep
        _zep_client = AsyncZep(api_key=settings.ZEP_API_KEY)
        logger.info("Zep client initialized")
        return _zep_client
    except ImportError:
        logger.error("zep-cloud package not installed")
        return None
    except Exception as e:
        logger.error(f"Failed to initialize Zep client: {e}")
        return None


async def ensure_zep_user(user_id: str, phone: str | None = None) -> bool:
    """
    Ensure a Zep user exists.
    
    Args:
        user_id: Unique user identifier (use phone or UUID)
        phone: Phone number for user metadata
        
    Returns:
        True if user exists or was created
    """
    client = await get_zep_client()
    if not client:
        return False
    
    try:
        # Try to get existing user
        try:
            await client.user.get(user_id)
            return True
        except Exception:
            pass  # User doesn't exist, create it
        
        # Create new user
        await client.user.add(
            user_id=user_id,
            metadata={"phone": phone} if phone else None,
        )
        logger.info(f"Created Zep user: {user_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to ensure Zep user {user_id}: {e}")
        return False


async def warm_user_cache(user_id: str) -> None:
    """
    Warm the user's cache for faster retrieval.
    
    Call this when user connects (call starts) to pre-load
    their knowledge graph into Zep's hot cache tier.
    """
    client = await get_zep_client()
    if not client:
        return
    
    try:
        await client.user.warm(user_id=user_id)
        logger.debug(f"Warmed Zep cache for user: {user_id}")
    except Exception as e:
        logger.warning(f"Failed to warm Zep cache for {user_id}: {e}")


async def create_thread(thread_id: str, user_id: str) -> bool:
    """
    Create a new conversation thread.
    
    Args:
        thread_id: Unique thread ID (use call_sid)
        user_id: User ID this thread belongs to
        
    Returns:
        True if thread was created
    """
    client = await get_zep_client()
    if not client:
        return False
    
    try:
        await client.thread.create(thread_id=thread_id, user_id=user_id)
        logger.info(f"Created Zep thread: {thread_id} for user: {user_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to create Zep thread {thread_id}: {e}")
        return False


async def add_messages_and_get_context(
    thread_id: str,
    user_message: str,
    assistant_message: str,
    user_name: str | None = None,
) -> str | None:
    """
    Add messages to thread and get updated context in one call.
    
    This is the key latency optimization for voice agents:
    - Persists messages immediately (survives hangup)
    - Returns context block in same call (no separate get_user_context)
    
    Args:
        thread_id: The thread ID (call_sid)
        user_message: What the user said
        assistant_message: What the agent responded
        user_name: Optional user name for graph attribution
        
    Returns:
        Context block string for next prompt, or None on error
    """
    client = await get_zep_client()
    if not client:
        return None
    
    try:
        from zep_cloud import Message
        
        messages = [
            Message(
                role="user",
                content=user_message,
                name=user_name or "User",
            ),
            Message(
                role="assistant",
                content=assistant_message,
                name="Assistant",
            ),
        ]
        
        # Add messages with return_context=True to get context in same call
        response = await client.thread.add_messages(
            thread_id=thread_id,
            messages=messages,
            return_context=True,
        )
        
        logger.debug(f"Added {len(messages)} messages to Zep thread {thread_id}")
        
        # Return context block for injection into next prompt
        return response.context if hasattr(response, 'context') else None
        
    except Exception as e:
        logger.error(f"Failed to add messages to Zep thread {thread_id}: {e}")
        return None


async def get_user_context(thread_id: str) -> str | None:
    """
    Get context block for a thread.
    
    Use this on call start to load previous conversation context.
    For mid-call updates, use add_messages_and_get_context instead.
    
    Args:
        thread_id: The thread ID
        
    Returns:
        Context block string, or None
    """
    client = await get_zep_client()
    if not client:
        return None
    
    try:
        response = await client.thread.get_user_context(thread_id=thread_id)
        return response.context if hasattr(response, 'context') else None
    except Exception as e:
        logger.warning(f"Failed to get Zep context for thread {thread_id}: {e}")
        return None


async def get_user_context_by_user(user_id: str) -> str | None:
    """
    Get context for a user from their most recent thread.
    
    Useful when starting a new call and we don't have a thread yet.
    Creates a temporary thread to retrieve context, then we can
    create the real thread for this call.
    
    Args:
        user_id: The user ID
        
    Returns:
        Context block string, or None
    """
    client = await get_zep_client()
    if not client:
        return None
    
    try:
        # List user's threads to find most recent (using user.get_threads, not thread.list_by_user)
        threads = await client.user.get_threads(user_id=user_id)
        if threads and len(threads) > 0:
            # Sort by created_at descending to get most recent
            sorted_threads = sorted(
                threads, 
                key=lambda t: t.created_at if hasattr(t, 'created_at') and t.created_at else "",
                reverse=True
            )
            thread_id = sorted_threads[0].thread_id
            return await get_user_context(thread_id)
        return None
    except Exception as e:
        logger.warning(f"Failed to get Zep context for user {user_id}: {e}")
        return None


class ZepSession:
    """
    Manages Zep memory for a single voice call.
    
    Usage:
        zep = ZepSession(user_id="phone:+1234567890", call_sid="CA123")
        await zep.start()  # Warms cache, creates thread
        
        # After each turn
        context = await zep.persist_turn(user_msg, assistant_msg)
        
        # Context is automatically updated - no cleanup needed
    """
    
    def __init__(self, user_id: str, call_sid: str, phone: str | None = None):
        self.user_id = user_id
        self.thread_id = f"call-{call_sid}"
        self.phone = phone
        self._started = False
        self._context: str | None = None
    
    async def start(self) -> str | None:
        """
        Initialize Zep session for this call.
        
        - Ensures user exists
        - Warms user cache
        - Creates thread for this call
        - Loads previous context
        
        Returns:
            Initial context block from previous conversations
        """
        if self._started:
            return self._context
        
        # Ensure user exists
        await ensure_zep_user(self.user_id, self.phone)
        
        # Warm cache for fast retrieval
        await warm_user_cache(self.user_id)
        
        # Get previous context before creating new thread
        self._context = await get_user_context_by_user(self.user_id)
        
        # Create thread for this call
        await create_thread(self.thread_id, self.user_id)
        
        self._started = True
        logger.info(f"ZepSession started: user={self.user_id}, thread={self.thread_id}")
        
        return self._context
    
    async def persist_turn(
        self,
        user_message: str,
        assistant_message: str,
    ) -> str | None:
        """
        Persist a conversation turn and get updated context.
        
        Call this after each user/assistant exchange.
        
        Args:
            user_message: What the user said
            assistant_message: What the agent responded
            
        Returns:
            Updated context block for next prompt
        """
        if not self._started:
            logger.warning("ZepSession.persist_turn called before start()")
            return None
        
        context = await add_messages_and_get_context(
            thread_id=self.thread_id,
            user_message=user_message,
            assistant_message=assistant_message,
        )
        
        if context:
            self._context = context
        
        return self._context
    
    @property
    def context(self) -> str | None:
        """Get the current context block."""
        return self._context
