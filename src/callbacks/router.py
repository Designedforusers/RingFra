"""
Notification channel router.

Decides whether to use voice call, SMS, or other channels
based on event severity and user preferences.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any

from loguru import logger

from src.callbacks.outbound import initiate_callback, send_sms


class Severity(str, Enum):
    """Event severity levels."""

    CRITICAL = "critical"  # Voice call immediately
    WARNING = "warning"  # SMS notification
    INFO = "info"  # Log only (no notification)


@dataclass
class Event:
    """Notification event."""

    severity: Severity
    event_type: str
    summary: str
    details: dict[str, Any] | None = None

    def to_context(self) -> dict[str, Any]:
        """Convert to callback context dict."""
        return {
            "severity": self.severity.value,
            "event_type": self.event_type,
            "summary": self.summary,
            "details": self.details or {},
        }


async def notify_user(phone: str, event: Event) -> str | None:
    """
    Route notification to appropriate channel based on severity.

    Args:
        phone: User phone number
        event: The event to notify about

    Returns:
        Message/Call SID if notification sent, None if info-only
    """
    logger.info(f"Routing notification: severity={event.severity}, type={event.event_type}")

    if event.severity == Severity.CRITICAL:
        # Voice call for critical issues
        logger.warning(f"CRITICAL: {event.summary}")
        return await initiate_callback(
            phone=phone,
            context=event.to_context(),
            callback_type="alert",
        )

    elif event.severity == Severity.WARNING:
        # SMS for warnings
        logger.warning(f"WARNING: {event.summary}")
        message = f"[Render Alert] {event.event_type}: {event.summary}"
        return await send_sms(phone, message)

    else:
        # Info level - just log, no notification
        logger.info(f"INFO: {event.summary}")
        return None


# Pre-built event constructors for common scenarios
def service_down_event(service_name: str, error: str) -> Event:
    """Create a service down event (critical)."""
    return Event(
        severity=Severity.CRITICAL,
        event_type="service_down",
        summary=f"{service_name} is down: {error}",
        details={"service": service_name, "error": error},
    )


def deploy_failed_event(service_name: str, error: str) -> Event:
    """Create a deploy failed event (critical)."""
    return Event(
        severity=Severity.CRITICAL,
        event_type="deploy_failed",
        summary=f"Deploy failed for {service_name}: {error}",
        details={"service": service_name, "error": error},
    )


def high_cpu_event(service_name: str, cpu_percent: float) -> Event:
    """Create a high CPU event (warning)."""
    return Event(
        severity=Severity.WARNING,
        event_type="high_cpu",
        summary=f"{service_name} CPU at {cpu_percent:.0f}%",
        details={"service": service_name, "cpu": cpu_percent},
    )


def high_memory_event(service_name: str, memory_percent: float) -> Event:
    """Create a high memory event (warning)."""
    return Event(
        severity=Severity.WARNING,
        event_type="high_memory",
        summary=f"{service_name} memory at {memory_percent:.0f}%",
        details={"service": service_name, "memory": memory_percent},
    )


def task_complete_event(task_type: str, result: str, success: bool = True) -> Event:
    """Create a task complete event (varies by success)."""
    severity = Severity.INFO if success else Severity.WARNING
    status = "completed" if success else "failed"
    return Event(
        severity=severity,
        event_type="task_complete",
        summary=f"{task_type} {status}: {result}",
        details={"task_type": task_type, "result": result, "success": success},
    )


def reminder_event(message: str) -> Event:
    """Create a reminder event (critical - needs voice call)."""
    return Event(
        severity=Severity.CRITICAL,
        event_type="reminder",
        summary=message,
        details={"reminder": message},
    )
