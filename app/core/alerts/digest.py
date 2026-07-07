"""Daily digest batcher — collects, dedupes, and sends deferred alert events.

Sellers can set alert_preferences.frequency = "daily_digest" for an event
type. Previously the immediate engine (engine.py) correctly gated the
send in that case but had no batching counterpart, so daily_digest events
were silently dropped forever (logged as "alert.pending_digest" and never
recorded, never sent). This module is the missing batching counterpart.

Flow, per (org, event_type):
  1. Re-evaluate stored extractions / authenticity_audits since the last
     digest watermark (or org creation, if no digest has ever run) through
     the existing pure rules layer (rules.check_high_urgency /
     check_likely_fake) — this module does not duplicate rule logic.
  2. Exclude anything already present in alert_log (is_already_alerted_pg)
     — this is the no-drop / dedupe guarantee: a failed send never loses
     events because nothing is recorded until after a successful send, and
     a re-run after a failed send simply re-discovers the same events.
  3. Format ONE summary email per org (not one email per event) and send it
     via the org's configured Channel.
  4. Record each included event in alert_log only after a successful send.

Only AlertEventType.HIGH_URGENCY and AlertEventType.LIKELY_FAKE are
digestible here — fake_cluster and topic_spike require batch context
(a window of recent authenticity results / topic frequency stats) that has
no stored-data equivalent this module can reconstruct from a single row
scan, so they're out of scope for this task.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

import structlog

from app.core.alerts.channels.base import AlertMessage, Channel, ChannelError
from app.core.alerts.rules import AlertEvent, AlertEventType, check_high_urgency, check_likely_fake
from app.core.alerts.storage import (
    get_last_digest_watermark_pg,
    get_org_created_at_pg,
    get_org_notification_email_pg,
    get_preference_pg,
    is_already_alerted_pg,
    list_authenticity_audits_since_pg,
    list_extractions_since_pg,
    record_alert_sent_pg,
)
from app.core.alerts.unsubscribe import build_unsubscribe_url
from app.core.authenticity.schema import AuthenticityLabel, AuthenticityResult
from app.core.schemas import ReviewExtraction, Urgency

log = structlog.get_logger(__name__)

# Only these two event types have a stored-data equivalent this batcher can
# re-evaluate per-review; fake_cluster/topic_spike need batch context and
# are explicitly out of scope (see module docstring).
_DIGESTIBLE_EVENT_TYPES: tuple[AlertEventType, ...] = (
    AlertEventType.HIGH_URGENCY,
    AlertEventType.LIKELY_FAKE,
)


@dataclass(frozen=True)
class PendingDigestEvent:
    """One event awaiting inclusion in a daily digest email."""

    review_id: str
    event: AlertEvent


async def collect_pending_for_org(org_id: str) -> list[PendingDigestEvent]:
    """Collect all pending daily_digest events for one org, across both digestible types.

    Order: high_urgency events first, then likely_fake, each internally
    ordered by created_at (falls out of the underlying ORDER BY created_at
    queries — no extra sort needed here).
    """
    pending: list[PendingDigestEvent] = []

    for event_type in _DIGESTIBLE_EVENT_TYPES:
        pref = await asyncio.to_thread(get_preference_pg, org_id, event_type.value)
        enabled: bool = pref["enabled"] if pref is not None else True  # type: ignore[assignment]
        frequency: str = pref["frequency"] if pref is not None else "immediate"  # type: ignore[assignment]

        # Only digest events explicitly enabled AND configured daily_digest.
        # Disabled prefs and immediate-frequency prefs are the immediate
        # engine's territory (or simply off) — not this batcher's job.
        if not (enabled and frequency == "daily_digest"):
            continue

        since: datetime | None = await asyncio.to_thread(
            get_last_digest_watermark_pg, org_id, event_type.value
        )
        if since is None:
            since = await asyncio.to_thread(get_org_created_at_pg, org_id)

        if event_type == AlertEventType.HIGH_URGENCY:
            extraction_rows = await asyncio.to_thread(list_extractions_since_pg, org_id, since)
            for row in extraction_rows:
                extraction = ReviewExtraction(
                    product=row["product"] or "",  # type: ignore[arg-type]
                    urgency=Urgency(row["urgency"]) if row["urgency"] else Urgency.low,  # type: ignore[arg-type]
                    topics=row["topics"],  # type: ignore[arg-type]
                    cons=row["cons"],  # type: ignore[arg-type]
                )
                event = check_high_urgency(extraction)
                if event is None:
                    continue
                review_id = str(row["input_hash"])
                already = await asyncio.to_thread(
                    is_already_alerted_pg, org_id, review_id, event_type.value
                )
                if not already:
                    pending.append(PendingDigestEvent(review_id=review_id, event=event))

        elif event_type == AlertEventType.LIKELY_FAKE:
            audit_rows = await asyncio.to_thread(list_authenticity_audits_since_pg, org_id, since)
            for arow in audit_rows:
                auth = AuthenticityResult(
                    score=arow["score"],  # type: ignore[arg-type]
                    label=AuthenticityLabel(arow["label"]),  # type: ignore[arg-type]
                    # Flags aren't needed by check_likely_fake and stored flag
                    # strings aren't guaranteed to map to AuthenticityFlag
                    # members — pass an empty list rather than attempt an
                    # unsafe cast that could crash the sweep.
                    flags=[],
                    review_hash=arow["review_hash"],  # type: ignore[arg-type]
                    scored_at=arow["created_at"],  # type: ignore[arg-type]
                )
                event = check_likely_fake(auth)
                if event is None:
                    continue
                review_id = str(arow["review_hash"])
                already = await asyncio.to_thread(
                    is_already_alerted_pg, org_id, review_id, event_type.value
                )
                if not already:
                    pending.append(PendingDigestEvent(review_id=review_id, event=event))

    return pending


def build_digest_email(
    org_id: str,
    recipient_email: str,
    events: list[PendingDigestEvent],
) -> AlertMessage | None:
    """Format a single summary AlertMessage for all pending events, or None if empty.

    Pure formatting — no I/O. Caller must send nothing when this returns None.
    """
    if not events:
        return None

    count = len(events)
    subject = f"Review-IQ daily digest: {count} event(s) need attention"

    lines: list[str] = [
        f"Review-IQ daily digest — {count} event(s) since your last digest.",
        "",
    ]
    for pe in events:
        event = pe.event
        if event.event_type == AlertEventType.HIGH_URGENCY:
            topics = event.details.get("topics") or []
            cons = event.details.get("cons") or []
            detail = f"topics={topics}, cons={cons}"
        elif event.event_type == AlertEventType.LIKELY_FAKE:
            score = event.details.get("score")
            detail = f"score={score}"
        else:
            detail = str(dict(event.details))
        lines.append(f"- [{event.event_type}] review {pe.review_id}: {detail}")
    lines.append("")
    lines.append("Log in to Review-IQ to investigate and take action.")

    unsubscribe_url = build_unsubscribe_url(org_id)
    if unsubscribe_url:
        lines.append("")
        lines.append(f"Stop receiving these emails: {unsubscribe_url}")

    # AlertMessage.event is only used by ResendChannel for structured logging.
    # A multi-event digest doesn't map perfectly onto the single-event
    # AlertMessage shape; using the first event here is an acceptable
    # simplification — AlertMessage itself is shared with the immediate-path
    # engine and must not change shape for this task.
    return AlertMessage(
        org_id=org_id,
        event=events[0].event,
        subject=subject,
        body_text="\n".join(lines),
        recipient_email=recipient_email,
        unsubscribe_url=unsubscribe_url,
    )


async def run_digest_for_org(org_id: str, channel: Channel) -> list[PendingDigestEvent]:
    """Collect, send, and record one org's daily digest. Returns events actually sent.

    No-drop guarantee: alert_log is only written after a successful send, so
    a failed send (ChannelError) or a missing recipient email leaves every
    pending event un-recorded and therefore re-collectable on the next sweep.
    """
    events = await collect_pending_for_org(org_id)
    if not events:
        return []

    recipient_email = await asyncio.to_thread(get_org_notification_email_pg, org_id)
    if not recipient_email:
        log.info(
            "digest.no_recipient_configured",
            org_id=org_id,
            pending_count=len(events),
        )
        return []

    message = build_digest_email(org_id, recipient_email, events)
    assert message is not None  # events is non-empty, so build_digest_email can't return None

    try:
        await channel.send(message)
    except ChannelError:
        log.error(
            "digest.send_failed",
            org_id=org_id,
            pending_count=len(events),
            exc_info=True,
        )
        return []

    for pe in events:
        await asyncio.to_thread(
            record_alert_sent_pg,
            org_id,
            pe.review_id,
            pe.event.event_type,
            dict(pe.event.details),
        )

    log.info(
        "digest.sent",
        org_id=org_id,
        recipient=recipient_email,
        event_count=len(events),
    )
    return events
