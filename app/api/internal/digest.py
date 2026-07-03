"""POST /internal/digest/run — token-protected trigger for the daily digest sweep.

Designed to be called once/day by a free scheduler (Cloud Scheduler HTTP target) hitting
this endpoint with the shared-secret token in a header — NOT a query param (avoid the
token leaking into access logs/URLs, unlike the existing Pub/Sub pattern which uses a
query param because Pub/Sub push subscriptions require that shape; this endpoint has no
such constraint, so use a header instead: X-Digest-Trigger-Token).
"""

from __future__ import annotations

import asyncio
import hmac

import structlog
from fastapi import APIRouter, Header, HTTPException, status

from app.core.alerts.digest import run_digest_for_org
from app.core.alerts.engine import _get_default_channel
from app.core.alerts.storage import list_orgs_with_daily_digest_pg
from app.core.config import get_settings

router = APIRouter(prefix="/internal", tags=["internal"])
log = structlog.get_logger(__name__)


def _verify_trigger_token(provided: str | None) -> None:
    """Timing-safe check of the shared-secret trigger token.

    Two-tier failure mode, mirroring app/api/webhooks/google.py's push-token check:
    503 when the server itself has no token configured (misconfiguration, not an
    attack), 401 when a token was required but the provided one is missing or wrong.
    """
    settings = get_settings()
    if not settings.digest_trigger_token:
        log.error("digest_trigger.not_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Digest trigger endpoint not configured on this server.",
        )
    if not provided or not hmac.compare_digest(provided, settings.digest_trigger_token):
        log.warning("digest_trigger.token_rejected")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing trigger token.",
        )


@router.post("/digest/run")
async def run_digest_sweep(
    x_digest_trigger_token: str | None = Header(default=None),
) -> dict[str, object]:
    """Iterate every org with an enabled daily_digest preference and run one digest cycle each.

    A single org's failure (DB error, Resend error not already swallowed inside
    run_digest_for_org) is caught and logged here so it can never abort the sweep for the
    remaining orgs — this endpoint always returns 200 with a per-org result breakdown, even
    if some orgs failed.
    """
    _verify_trigger_token(x_digest_trigger_token)

    org_ids = await asyncio.to_thread(list_orgs_with_daily_digest_pg)
    channel = _get_default_channel()

    sent_counts: dict[str, int] = {}
    failed_orgs: list[str] = []
    for org_id in org_ids:
        try:
            events = await run_digest_for_org(org_id, channel)
            sent_counts[org_id] = len(events)
        except Exception:
            log.error("digest_trigger.org_failed", org_id=org_id, exc_info=True)
            failed_orgs.append(org_id)

    total_events_sent = sum(sent_counts.values())
    log.info(
        "digest_trigger.sweep_complete",
        org_count=len(org_ids),
        total_events_sent=total_events_sent,
        failed_org_count=len(failed_orgs),
    )
    return {
        "ok": True,
        "orgs_processed": len(org_ids),
        "total_events_sent": total_events_sent,
        "sent_per_org": sent_counts,
        "failed_orgs": failed_orgs,
    }
