"""
Callback module for proactive outbound communications.

Handles:
- Outbound voice calls
- SMS notifications
- Channel routing based on severity
"""

from src.callbacks.outbound import initiate_callback, send_sms
from src.callbacks.router import notify_user

__all__ = ["initiate_callback", "send_sms", "notify_user"]
