"""POST /internal/detectors/run — token-protected trigger for the Phase 2 detector sweep
(batch-defect + fake-campaign, app/core/alerts/detector_sweep.py).

Designed to be called on a schedule (e.g. every 6 hours) by a free scheduler (Cloud Scheduler
HTTP target) hitting this endpoint with the shared-secret token in a header — mirrors
app/api/internal/ingest_tick.py / app/api/internal/digest.py exactly, including the two-tier
503 (unconfigured) / 401 (bad token) failure mode and the header-not-query-param placement.
"""

from __future__ import annotations

import hmac

import structlog
from fastapi import APIRouter, Header, HTTPException, status

from app.core.alerts.detector_sweep import run_detector_sweep
from app.core.alerts.engine import _get_default_channel
from app.core.config import get_settings

router = APIRouter(prefix="/internal", tags=["internal"])
log = structlog.get_logger(__name__)


def _verify_trigger_token(provided: str | None) -> None:
    """Timing-safe check of the shared-secret trigger token — mirrors
    app/api/internal/digest.py's _verify_trigger_token exactly."""
    settings = get_settings()
    if not settings.detector_sweep_trigger_token:
        log.error("detector_sweep_trigger.not_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Detector sweep trigger endpoint not configured on this server.",
        )
    if not provided or not hmac.compare_digest(provided, settings.detector_sweep_trigger_token):
        log.warning("detector_sweep_trigger.token_rejected")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing trigger token.",
        )


@router.post("/detectors/run")
async def run_detectors(
    x_detector_sweep_token: str | None = Header(default=None),
) -> dict[str, object]:
    """Run one full Phase 2 detector sweep cycle (batch-defect + fake-campaign, whichever are
    globally enabled) across every org with dated extraction data.

    Per-org failure isolation lives inside run_detector_sweep itself (not this handler) — this
    endpoint always returns 200 with the full per-detector result breakdown, even if some orgs
    failed for one or both detectors.
    """
    _verify_trigger_token(x_detector_sweep_token)

    channel = _get_default_channel()
    result = await run_detector_sweep(channel)

    log.info("detector_sweep_trigger.completed", **result)
    return {"ok": True, **result}
