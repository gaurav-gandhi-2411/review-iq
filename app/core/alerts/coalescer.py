"""Urgent-alert coalescing -- cap immediate high_urgency emails per org, roll up the rest.

Problem: engine.evaluate_and_alert sends one email per high_urgency event, so a batch-ingested
CSV can send dozens of emails to one org and burn Resend's 100/day cap (shared with leads and
digests). Policy: at most URGENT_ALERT_MAX_PER_WINDOW (default 1) immediate urgent emails per
org per URGENT_ALERT_WINDOW_MINUTES (default 15). An event past the cap is NOT dropped: it is
recorded (alert_log, event_type 'urgent_deferred') and delivered in ONE roll-up email.

When a roll-up leaves (no new infrastructure -- the repo has no per-org timer):
  1. Piggyback: the next urgent event that wins a fresh window carries every pending deferred
     event as a section of its own email.
  2. Sweep: the scheduled digest sweep (POST /internal/digest/run) calls
     flush_deferred_urgent_for_org for every org with a pending deferred event. Today that runs
     daily; pointing a 15-minute Cloud Scheduler job at the same endpoint would tighten it with
     no code change (documented, deliberately NOT created here).

State lives in the database (alert_log marker rows, see storage.py), never in process memory, and
the claim is serialised per org with a transaction-scoped advisory lock, so concurrent ingest
workers cannot both win the same slot.

Never lose an alert: a failed send releases its slot and leaves the event(s) deferred (so the
next window or sweep delivers them); a coalescer DB error fails OPEN (send immediately -- a
possible extra email beats a lost urgent alert).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from app.core.alerts.channels.base import AlertMessage, Channel, ChannelError
from app.core.alerts.rules import AlertEvent, AlertEventType
from app.core.alerts.storage import (
    claim_urgent_window_pg,
    get_org_notification_email_pg,
    get_preference_pg,
    list_pending_urgent_deferred_pg,
    record_alert_sent_pg,
    record_urgent_deferred_pg,
    release_urgent_window_pg,
)
from app.core.alerts.unsubscribe import build_unsubscribe_url
from app.core.config import get_settings

log = structlog.get_logger(__name__)

# How many deferred reviews the roll-up lists by name; the rest are counted only. Five keeps
# the email scannable while still showing the worst offenders.
ROLLUP_TOP_N = 5


async def acquire_urgent_slot(org_id: str) -> tuple[bool, str | None]:
    """Try to claim the org's immediate-send slot. Returns (send_now, claim_token).

    (True, token)  slot won; caller sends, then must release_urgent_slot on failure.
    (False, None)  cap reached; caller must defer the event.
    (True, None)   coalescer DB error -> fail open; nothing to release.
    """
    settings = get_settings()
    token = uuid.uuid4().hex
    try:
        won = await asyncio.to_thread(
            claim_urgent_window_pg,
            org_id,
            token,
            settings.urgent_alert_window_minutes,
            settings.urgent_alert_max_per_window,
        )
    except Exception:
        log.error("alert.coalescer_claim_failed_open", org_id=org_id, exc_info=True)
        return True, None
    return (True, token) if won else (False, None)


async def release_urgent_slot(org_id: str, token: str | None) -> None:
    """Return a claimed slot after a failed send. Best-effort: never raises."""
    if token is None:
        return
    try:
        await asyncio.to_thread(release_urgent_window_pg, org_id, token)
    except Exception:
        # Worst case the window stays consumed until it expires; the events stay deferred.
        log.error("alert.coalescer_release_failed", org_id=org_id, exc_info=True)


async def defer_urgent_event(org_id: str, review_id: str, event: AlertEvent) -> None:
    """Record an event held back by the cap so a roll-up (or the digest sweep) delivers it."""
    await asyncio.to_thread(record_urgent_deferred_pg, org_id, review_id, dict(event.details))
    log.info("alert.urgent_deferred", org_id=org_id, review_id=review_id)


async def pending_deferred(
    org_id: str, exclude_review_id: str | None = None
) -> list[dict[str, Any]]:
    """Undelivered deferred urgent events for the org. Best-effort: [] on a DB error."""
    try:
        rows = await asyncio.to_thread(list_pending_urgent_deferred_pg, org_id)
    except Exception:
        log.error("alert.coalescer_pending_failed", org_id=org_id, exc_info=True)
        return []
    return [r for r in rows if r["review_id"] != exclude_review_id]


def _issue_count(item: dict[str, Any]) -> int:
    details = item.get("details") or {}
    return len(details.get("cons") or []) + len(details.get("topics") or [])


def rollup_lines(deferred: list[dict[str, Any]], org_id: str) -> list[str]:
    """Body lines for the roll-up section: count, top few by reported issues, dashboard link.

    Every deferred event is high-urgency by construction, so "by urgency" ranking falls back
    to the number of issues + topics the review reported (most first, oldest first on ties).
    """
    n = len(deferred)
    ranked = sorted(
        deferred,
        key=lambda d: (-_issue_count(d), str(d.get("deferred_at") or ""), d["review_id"]),
    )
    lines = [
        f"{n} more urgent review{'s' if n != 1 else ''} held back to avoid flooding your inbox:"
    ]
    for item in ranked[:ROLLUP_TOP_N]:
        details = item.get("details") or {}
        cons = ", ".join(str(c) for c in (details.get("cons") or [])[:3]) or "no issues listed"
        topics = ", ".join(str(t) for t in (details.get("topics") or [])[:3])
        suffix = f" [{topics}]" if topics else ""
        lines.append(f"- review {item['review_id']}: {cons}{suffix}")
    if n > ROLLUP_TOP_N:
        lines.append(f"- ...and {n - ROLLUP_TOP_N} more")
    base = get_settings().web_app_base_url.rstrip("/")
    lines.append(f"\nSee all of them in your dashboard: {base}")
    return lines


def rollup_subject(deferred: list[dict[str, Any]]) -> str:
    """Subject for a standalone roll-up email."""
    n = len(deferred)
    minutes = get_settings().urgent_alert_window_minutes
    word = "review" if n == 1 else "reviews"
    oldest = min((d["deferred_at"] for d in deferred if d.get("deferred_at")), default=None)
    if oldest is not None and (datetime.now(UTC) - oldest).total_seconds() > minutes * 60:
        return f"{n} more urgent {word} since your last urgent alert"
    return f"{n} more urgent {word} in the last {minutes} minutes"


def build_urgent_rollup_email(
    org_id: str, recipient_email: str, deferred: list[dict[str, Any]]
) -> AlertMessage:
    """Standalone roll-up AlertMessage (pure formatting, no I/O). `deferred` must be non-empty."""
    unsubscribe_url = build_unsubscribe_url(org_id)
    lines = rollup_lines(deferred, org_id)
    if unsubscribe_url:
        lines.append(f"\nStop receiving these emails: {unsubscribe_url}")
    return AlertMessage(
        org_id=org_id,
        event=AlertEvent(
            event_type=AlertEventType.HIGH_URGENCY,
            details={"rollup": True, "count": len(deferred)},
        ),
        subject=rollup_subject(deferred),
        body_text="\n".join(lines),
        recipient_email=recipient_email,
        unsubscribe_url=unsubscribe_url,
    )


async def mark_delivered(org_id: str, deferred: list[dict[str, Any]]) -> None:
    """Write the 'high_urgency' alert_log row that retires each deferred event (and dedupes)."""
    for item in deferred:
        await asyncio.to_thread(
            record_alert_sent_pg,
            org_id,
            item["review_id"],
            AlertEventType.HIGH_URGENCY.value,
            dict(item.get("details") or {}),
        )


async def flush_deferred_urgent_for_org(org_id: str, channel: Channel) -> list[str]:
    """Send ONE roll-up email for the org's pending deferred events. Returns review_ids sent.

    Respects the same gates as the immediate path: no notification email (unsubscribed) or a
    disabled high_urgency preference -> nothing is sent and the events stay deferred. Also
    respects the cap: if the window is busy the flush is a no-op and a later run delivers.
    A failed send releases the slot and leaves every event deferred.
    """
    pending = await pending_deferred(org_id)
    if not pending:
        return []

    recipient = await asyncio.to_thread(get_org_notification_email_pg, org_id)
    if not recipient:
        log.info("alert.rollup_no_recipient", org_id=org_id, pending_count=len(pending))
        return []

    pref = await asyncio.to_thread(get_preference_pg, org_id, AlertEventType.HIGH_URGENCY.value)
    if pref is not None and not pref["enabled"]:
        log.info("alert.rollup_suppressed_by_pref", org_id=org_id, pending_count=len(pending))
        return []

    send_now, token = await acquire_urgent_slot(org_id)
    if not send_now:
        log.info("alert.rollup_window_busy", org_id=org_id, pending_count=len(pending))
        return []

    # Re-read after winning the slot: another worker may have delivered some in between.
    pending = await pending_deferred(org_id)
    if not pending:
        await release_urgent_slot(org_id, token)
        return []

    message = build_urgent_rollup_email(org_id, recipient, pending)
    try:
        await channel.send(message)
    except ChannelError:
        log.error(
            "alert.rollup_send_failed", org_id=org_id, pending_count=len(pending), exc_info=True
        )
        await release_urgent_slot(org_id, token)
        return []

    await mark_delivered(org_id, pending)
    log.info("alert.rollup_sent", org_id=org_id, event_count=len(pending), recipient=recipient)
    return [str(p["review_id"]) for p in pending]
