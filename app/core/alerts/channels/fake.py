from __future__ import annotations

import structlog

from app.core.alerts.channels.base import AlertMessage

log = structlog.get_logger(__name__)


class FakeChannel:
    """In-memory channel that captures sent messages — zero external deps, for tests."""

    def __init__(self) -> None:
        self.sent: list[AlertMessage] = []

    async def send(self, message: AlertMessage) -> None:
        self.sent.append(message)


class LogChannel:
    """Structured-log channel — writes to structlog, no real email, $0.

    Useful as a safe dev-mode fallback when no real provider is configured.
    """

    async def send(self, message: AlertMessage) -> None:
        log.info(
            "alert.sent",
            org_id=message.org_id,
            event_type=message.event.event_type,
            recipient=message.recipient_email,
            subject=message.subject,
        )
