"""Retention-window purge job (Session 12 P2c).

Deletes retained-mode orgs' extraction rows older than their own chosen retention_days
window. Triggered by POST /internal/retention/purge on a Cloud Scheduler cadence (same
token-protected pattern as app/api/internal/digest.py) -- see that module for the trigger
mechanism this one reuses.

Stateless-mode orgs are never touched here: they have nothing to purge by construction
(save_extraction_pg is never called for them, see app/api/v2/extract.py).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog

from app.core.storage_pg import list_orgs_with_retained_mode_pg, purge_org_extractions_pg

log = structlog.get_logger(__name__)


async def purge_expired_extractions() -> dict[str, object]:
    """Purge every retained-mode org's extractions older than its own retention_days.

    Runs synchronously per-org (no cross-org lock needed -- each org's DELETE is
    independent and org-scoped via _set_tenant()). Returns a summary dict for the
    triggering endpoint's response / log line.
    """
    import asyncio

    orgs = await asyncio.to_thread(list_orgs_with_retained_mode_pg)
    total_deleted = 0
    orgs_purged = 0
    errors: list[str] = []

    for org_id, retention_days in orgs:
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        try:
            deleted = await asyncio.to_thread(purge_org_extractions_pg, org_id, cutoff)
        except Exception as exc:  # noqa: BLE001 — one org's failure must not stop the sweep
            log.error("retention.purge_org_failed", org_id=org_id, error=str(exc))
            errors.append(org_id)
            continue
        if deleted:
            orgs_purged += 1
            total_deleted += deleted
            log.info(
                "retention.purged",
                org_id=org_id,
                retention_days=retention_days,
                rows_deleted=deleted,
                cutoff=cutoff.isoformat(),
            )

    summary = {
        "orgs_checked": len(orgs),
        "orgs_purged": orgs_purged,
        "rows_deleted": total_deleted,
        "errors": errors,
    }
    log.info("retention.purge_run_completed", **summary)
    return summary
