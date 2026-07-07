"""One-click unsubscribe: HMAC-signed org tokens, no login required.

Unsubscribing clears organizations.notification_email — the single choke point
both engine.py and digest.py already check before sending — so one action
stops every alert email for that org, immediate and digest alike. Re-enabling
notifications is a normal (authenticated) PUT /bff/alerts/notification-email.
"""

from __future__ import annotations

import hashlib
import hmac

from app.core.config import get_settings


def generate_unsubscribe_token(org_id: str) -> str:
    """Deterministic HMAC-SHA256 token for org_id, keyed by UNSUBSCRIBE_SIGNING_KEY."""
    settings = get_settings()
    return hmac.new(
        settings.unsubscribe_signing_key.encode(), org_id.encode(), hashlib.sha256
    ).hexdigest()


def verify_unsubscribe_token(org_id: str, token: str) -> bool:
    """Timing-safe check that `token` is the correct unsubscribe token for `org_id`."""
    settings = get_settings()
    if not settings.unsubscribe_signing_key:
        return False
    return hmac.compare_digest(generate_unsubscribe_token(org_id), token)


def build_unsubscribe_url(org_id: str) -> str | None:
    """Absolute unsubscribe link for this org, or None if not configured.

    Requires both UNSUBSCRIBE_SIGNING_KEY and API_PUBLIC_BASE_URL — without a
    public base URL there's nowhere to point the link, and without a signing
    key the token can't be verified. Callers must treat None as "omit the
    unsubscribe link/header for this send" rather than an error.
    """
    settings = get_settings()
    if not settings.unsubscribe_signing_key or not settings.api_public_base_url:
        return None
    token = generate_unsubscribe_token(org_id)
    base = settings.api_public_base_url.rstrip("/")
    return f"{base}/unsubscribe?org={org_id}&token={token}"
