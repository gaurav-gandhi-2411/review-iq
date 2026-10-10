"""POST /internal/judgeme/sync -- token-protected trigger for the Judge.me sweep.

Same shared-secret-header pattern as /internal/ingest/tick and /internal/retention/purge. The token
is checked BEFORE the feature flag so an unauthenticated caller learns nothing about the flag.
Flag off: returns {"enabled": false} and performs no I/O.
"""

from __future__ import annotations

import hmac

import structlog
from fastapi import APIRouter, Header, HTTPException, status

from app.core.config import get_settings
from app.core.ingestion.judgeme_job import run_due_installations

router = APIRouter(prefix="/internal", tags=["internal"])
log = structlog.get_logger(__name__)


def _verify_trigger_token(provided: str | None) -> None:
    configured = get_settings().judgeme_sync_trigger_token
    if not configured:
        log.error("judgeme_sync.not_configured")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Judge.me sync trigger not configured."
        )
    if not provided or not hmac.compare_digest(provided, configured):
        log.warning("judgeme_sync.token_rejected")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing trigger token.")


@router.post("/judgeme/sync")
async def run_judgeme_sync(
    x_judgeme_sync_token: str | None = Header(default=None),
) -> dict[str, object]:
    _verify_trigger_token(x_judgeme_sync_token)
    return await run_due_installations()
