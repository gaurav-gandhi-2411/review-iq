"""POST /internal/ingest/tick — token-protected trigger for the durable bulk-ingest
drain worker (Option B of the 2026-07-07 CSV-throttling fix).

Intended to be called by a free scheduler (Cloud Scheduler HTTP target) on a
short cadence (e.g. every minute), draining a few pending batch_job_rows so
bulk ingestion survives a Cloud Run restart/scale-down between an upload and
its own BackgroundTask completing. See app/core/ingest_worker.py's module
docstring for the full design.

Auth mirrors app/api/internal/digest.py's _verify_trigger_token exactly — a
shared-secret header token (not a query param, to keep it out of access
logs/URLs) with a two-tier 503 (server misconfigured) / 401 (bad token)
failure mode.
"""

from __future__ import annotations

import hmac

import structlog
from fastapi import APIRouter, Header, HTTPException, status

from app.core.config import get_settings
from app.core.ingest_worker import drain_rows
from app.core.ratelimit import set_bulk_call_class

router = APIRouter(prefix="/internal", tags=["internal"])
log = structlog.get_logger(__name__)


def _verify_trigger_token(provided: str | None) -> None:
    """Timing-safe check of the shared-secret trigger token.

    Two-tier failure mode, mirroring app/api/internal/digest.py's
    _verify_trigger_token: 503 when the server itself has no token configured
    (misconfiguration, not an attack), 401 when a token was required but the
    provided one is missing or wrong.
    """
    settings = get_settings()
    if not settings.ingest_tick_token:
        log.error("ingest_tick.not_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ingest tick endpoint not configured on this server.",
        )
    if not provided or not hmac.compare_digest(provided, settings.ingest_tick_token):
        log.warning("ingest_tick.token_rejected")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing trigger token.",
        )


@router.post("/ingest/tick")
async def run_ingest_tick(
    x_ingest_tick_token: str | None = Header(default=None),
) -> dict[str, object]:
    """Drain up to INGEST_TICK_ROWS pending batch_job_rows, across all orgs.

    The Option A bulk limiter (app/core/ratelimit.py) stays active under this
    worker path — belt-and-braces: set here explicitly, and again inside
    drain_rows() itself so the guarantee holds regardless of caller.
    """
    _verify_trigger_token(x_ingest_tick_token)

    set_bulk_call_class()
    result = await drain_rows(get_settings().ingest_tick_rows)

    log.info("ingest_tick.completed", **result)
    return {"ok": True, **result}
