"""Phase 2 detector sweep — batch-defect + fake-campaign, alerted through the EXISTING engine.

Per org (scoped to orgs with at least one dated extraction, via
list_orgs_with_dated_extractions_pg): run whichever detectors are globally enabled
(settings.enable_batch_defect_detector / enable_fake_campaign_detector), alert on
above-threshold flags via app.core.alerts.engine.evaluate_and_alert's `precomputed_events` path
-- reuses the identical dedupe/preference/frequency/send/record pipeline every other alert type
uses, not a parallel system.

One org's failure never aborts the sweep for other orgs (mirrors app/core/alerts/digest.py's
per-org isolation). Triggered by POST /internal/detectors/run (app/api/internal/detectors.py),
itself driven by a Cloud Scheduler job -- same shape as ingest-tick/digest-daily.

CONFIDENCE THRESHOLDS -- deliberately conservative (a false predictive alert burns seller trust
worst, per explicit product direction): only flags clearing these bars are alerted. Weaker flags
remain visible via the existing on-demand GET /insights/batch-defects-style reporting path (once
a campaign equivalent is wired) at each detector's own lower CONFIDENCE_REPORT_THRESHOLD, just
not auto-alerted to the seller.

DEDUPE -- both detector types pass a synthetic, stable cluster key as `review_id` to
evaluate_and_alert (never None), so the SAME underlying cluster is alerted at most once across
repeated sweeps, via the existing alert_log dedupe ledger (is_already_alerted_pg/
record_alert_sent_pg) -- no new dedupe mechanism. See _batch_defect_dedupe_key/
_fake_campaign_dedupe_key for the exact keying and why each is bucketed the way it is.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from app.core.alerts.channels.base import Channel
from app.core.alerts.engine import evaluate_and_alert
from app.core.alerts.rules import AlertEvent, AlertEventType
from app.core.config import get_settings
from app.core.detectors.batch_defect import annotated_reviews_from_rows, scan_batch_defects
from app.core.detectors.campaign import campaign_reviews_from_rows, scan_corpus
from app.core.storage_pg import list_dated_extractions_pg, list_orgs_with_dated_extractions_pg

log = structlog.get_logger(__name__)

# "HIGH tier" per explicit product direction: min(1, max(0, (ratio-3)/(10-3))) saturates at 1.0
# by ratio>=10x baseline; 0.7 corresponds to ~7.9x baseline -- comfortably above the base
# spike gate (ratio>=3x, count>=4) that merely makes a flag worth reporting at all.
BATCH_DEFECT_ALERT_THRESHOLD = 0.7

# The review-pattern detector (app/core/detectors/campaign.py) has no confidence score: it returns
# only alerts that already meet its pre-registered rule, so there is no second threshold here.
# Older alerts are not replayed when the detector is enabled or the sweep restarts.
FAKE_CAMPAIGN_RECENT_DAYS = 14


def _batch_defect_dedupe_key(product_id: str, topic: str, window_start_iso: str) -> str:
    """Month-bucketed: stable across sweeps for the SAME cluster (minor day-to-day window drift
    as new reviews arrive doesn't change the key), but still allows a genuinely NEW spike on the
    same product+topic in a different month to alert again."""
    month = window_start_iso[:7]  # "YYYY-MM" prefix of an ISO8601 timestamp
    return f"batch_defect:{product_id}:{topic}:{month}"


def _fake_campaign_dedupe_key(product_id: str, window_start_iso: str) -> str:
    """Day-bucketed: review patterns are short discrete events. The detector evaluates each
    arrival using only earlier reviews and applies a 7-day cooldown, so an alert's window start
    is identical on every re-scan and the key is stable across sweeps."""
    day = window_start_iso[:10]  # "YYYY-MM-DD" prefix of an ISO8601 timestamp
    return f"fake_campaign:{product_id}:{day}"


async def _sweep_batch_defect_for_org(
    org_id: str, rows: list[dict[str, Any]], channel: Channel
) -> int:
    """Run batch-defect for one org's already-fetched dated rows; alert on above-threshold
    flags. Returns the number of alerts actually sent (post-dedupe/pref, per evaluate_and_alert's
    own return contract)."""
    reviews = annotated_reviews_from_rows(rows)
    flags = scan_batch_defects(reviews)
    sent = 0
    for flag in flags:
        if flag.confidence < BATCH_DEFECT_ALERT_THRESHOLD:
            continue
        dedupe_key = _batch_defect_dedupe_key(
            flag.product_id, flag.topic, flag.evidence["window_start"]
        )
        result = await evaluate_and_alert(
            org_id=org_id,
            review_id=dedupe_key,
            precomputed_events=[
                AlertEvent(event_type=AlertEventType.BATCH_DEFECT, details=flag.to_dict())
            ],
            channel=channel,
        )
        sent += len(result)
    return sent


async def _sweep_fake_campaign_for_org(
    org_id: str, rows: list[dict[str, Any]], channel: Channel
) -> int:
    """Run fake-campaign for one org's already-fetched dated rows; alert on above-threshold
    flags. Returns the number of alerts actually sent."""
    reviews = campaign_reviews_from_rows(rows)
    since = datetime.now(UTC) - timedelta(days=FAKE_CAMPAIGN_RECENT_DAYS)
    flags = scan_corpus(reviews, since=since)
    sent = 0
    for flag in flags:
        dedupe_key = _fake_campaign_dedupe_key(flag.product_id, flag.evidence["window"]["start"])
        result = await evaluate_and_alert(
            org_id=org_id,
            review_id=dedupe_key,
            precomputed_events=[
                AlertEvent(event_type=AlertEventType.FAKE_CAMPAIGN, details=flag.to_dict())
            ],
            channel=channel,
        )
        sent += len(result)
    return sent


async def run_detector_sweep(channel: Channel) -> dict[str, Any]:
    """Sweep every org with dated extraction data, running whichever detectors are globally
    enabled. One org's exception never aborts the sweep for other orgs (mirrors
    app/core/alerts/digest.py's run_digest_sweep isolation).

    ENABLE_BATCH_DEFECT_DETECTOR / ENABLE_FAKE_CAMPAIGN_DETECTOR are GLOBAL flags (matching the
    existing batch-defect on-demand endpoint's design) -- they gate whether the sweep runs AT
    ALL for that detector, across every org, not a per-org toggle. A per-org toggle would need
    new alert_preferences-style plumbing not built here.

    Returns a summary dict: {"batch_defect": {"orgs": int, "sent": int, "failed_orgs": [...]},
    "fake_campaign": {...}}.
    """
    settings = get_settings()

    results: dict[str, Any] = {
        "batch_defect": {"orgs": 0, "sent": 0, "failed_orgs": []},
        "fake_campaign": {"orgs": 0, "sent": 0, "failed_orgs": []},
    }

    if not settings.enable_batch_defect_detector and not settings.enable_fake_campaign_detector:
        log.info("detector_sweep.skipped_both_disabled")
        return results

    org_ids = await asyncio.to_thread(list_orgs_with_dated_extractions_pg)

    for org_id in org_ids:
        rows = await asyncio.to_thread(list_dated_extractions_pg, org_id)

        if settings.enable_batch_defect_detector:
            try:
                results["batch_defect"]["sent"] += await _sweep_batch_defect_for_org(
                    org_id, rows, channel
                )
                results["batch_defect"]["orgs"] += 1
            except Exception:  # noqa: BLE001 — one org's failure must not kill the sweep
                log.error("detector_sweep.batch_defect_failed", org_id=org_id, exc_info=True)
                results["batch_defect"]["failed_orgs"].append(org_id)

        if settings.enable_fake_campaign_detector:
            try:
                results["fake_campaign"]["sent"] += await _sweep_fake_campaign_for_org(
                    org_id, rows, channel
                )
                results["fake_campaign"]["orgs"] += 1
            except Exception:  # noqa: BLE001 — one org's failure must not kill the sweep
                log.error("detector_sweep.fake_campaign_failed", org_id=org_id, exc_info=True)
                results["fake_campaign"]["failed_orgs"].append(org_id)

    log.info(
        "detector_sweep.completed",
        orgs_scanned=len(org_ids),
        batch_defect_orgs=results["batch_defect"]["orgs"],
        batch_defect_sent=results["batch_defect"]["sent"],
        fake_campaign_orgs=results["fake_campaign"]["orgs"],
        fake_campaign_sent=results["fake_campaign"]["sent"],
    )
    return results
