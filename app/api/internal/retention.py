"""POST /internal/retention/purge — token-protected trigger for the retention-window purge.

Designed to be called on a Cloud Scheduler cadence (daily is enough -- retention windows are
30/90 days, not hours), same shared-secret-header pattern as app/api/internal/digest.py:
X-Retention-Purge-Trigger-Token, never a query param.
"""

from __future__ import annotations

import hmac

import structlog
from fastapi import APIRouter, Header, HTTPException, status

from app.core.config import get_settings
from app.core.retention import purge_expired_extractions

router = APIRouter(prefix="/internal", tags=["internal"])
log = structlog.get_logger(__name__)


def _verify_trigger_token(provided: str | None) -> None:
    """Timing-safe check of the shared-secret trigger token.

    Same two-tier failure mode as app/api/internal/digest.py's _verify_trigger_token: 503
    when the server itself has no token configured (misconfiguration, not an attack), 401
    when a token was required but the provided one is missing or wrong.
    """
    settings = get_settings()
    if not settings.retention_purge_trigger_token:
        log.error("retention_trigger.not_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Retention purge trigger endpoint not configured on this server.",
        )
    if not provided or not hmac.compare_digest(provided, settings.retention_purge_trigger_token):
        log.warning("retention_trigger.token_rejected")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing trigger token.",
        )


@router.post("/retention/purge")
async def run_retention_purge(
    x_retention_purge_trigger_token: str | None = Header(default=None),
) -> dict[str, object]:
    """Purge every retained-mode org's extractions older than its own retention_days window."""
    _verify_trigger_token(x_retention_purge_trigger_token)
    return await purge_expired_extractions()
