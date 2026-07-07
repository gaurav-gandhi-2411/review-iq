from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.core.alerts.rules import AlertEvent


@dataclass(frozen=True)
class AlertMessage:
    """Prepared alert message ready for delivery via any Channel."""

    org_id: str
    event: AlertEvent
    subject: str
    body_text: str
    recipient_email: str
    # One-click unsubscribe link (see app/core/alerts/unsubscribe.py). None when
    # UNSUBSCRIBE_SIGNING_KEY / API_PUBLIC_BASE_URL aren't configured — channels
    # must omit the List-Unsubscribe header in that case, not send a broken link.
    unsubscribe_url: str | None = None


class ChannelError(Exception):
    """Raised when a Channel fails to deliver an alert."""


@runtime_checkable
class Channel(Protocol):
    """Pluggable delivery channel for alert messages.

    Concrete implementations: FakeChannel (tests), LogChannel (dev/fallback),
    ResendChannel (production — built in step 5 after provider confirmed).
    """

    async def send(self, message: AlertMessage) -> None:
        """Deliver the alert. Raises ChannelError on unrecoverable failure."""
        ...
