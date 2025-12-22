"""
Encryption utilities for storing sensitive credentials.
"""

import base64
import os

from cryptography.fernet import Fernet
from loguru import logger

from src.config import settings


def _get_fernet() -> Fernet:
    """Get Fernet instance for encryption/decryption."""
    key = settings.ENCRYPTION_KEY
    if not key:
        # Generate a key if not provided (for development)
        # In production, ENCRYPTION_KEY should be set
        logger.warning("ENCRYPTION_KEY not set - using generated key (not persistent!)")
        key = Fernet.generate_key().decode()
    
    # Ensure key is properly formatted
    if len(key) == 32:
        # Raw 32-byte key - encode to base64
        key = base64.urlsafe_b64encode(key.encode()).decode()
    
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext: str) -> str:
    """Encrypt a string."""
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a string."""
    f = _get_fernet()
    return f.decrypt(ciphertext.encode()).decode()
