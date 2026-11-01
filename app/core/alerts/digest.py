"""Daily and weekly digest batcher — collects, dedupes, and sends deferred alert events.

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

Weekly cadence (S20 M3b): the same pipeline runs with cadence="weekly" for event types whose
preference is frequency="weekly_digest". Differences: (a) the watermark is read from alert_log
rows tagged details->>'cadence' = 'weekly', and weekly sends tag their rows that way (daily
rows stay untagged, so daily behaviour is unchanged); (b) the email adds week-over-week
counts and top complaint topics computed from stored extractions. Per-review dedupe
(is_already_alerted_pg) is cadence-agnostic and is what actually prevents a re-send.
"""

from __future__ import annotations

import asyncio
import html
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

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
    list_extraction_summaries_since_pg,
    list_extractions_since_pg,
    recipient_is_suppressed,
    record_alert_sent_pg,
)
from app.core.alerts.unsubscribe import build_unsubscribe_url
from app.core.authenticity.schema import AuthenticityLabel, AuthenticityResult
from app.core.config import get_settings
from app.core.schemas import ReviewExtraction, Urgency

log = structlog.get_logger(__name__)

Cadence = Literal["daily", "weekly"]

# cadence -> the alert_preferences.frequency value that routes an event type to that cadence.
_FREQUENCY_FOR_CADENCE: dict[str, str] = {"daily": "daily_digest", "weekly": "weekly_digest"}

# Weekly body caps: keep one email readable and bounded regardless of org volume.
_WEEKLY_MAX_LISTED_EVENTS = 10
_WEEKLY_TOP_TOPICS = 5

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


async def collect_pending_for_org(
    org_id: str, cadence: Cadence = "daily"
) -> list[PendingDigestEvent]:
    """Collect all pending digest events for one org and cadence, across both digestible types.

    cadence="daily" matches preferences set to daily_digest, "weekly" those set to
    weekly_digest; an event type configured for the other cadence (or immediate) is skipped.

    Order: high_urgency events first, then likely_fake, each internally
    ordered by created_at (falls out of the underlying ORDER BY created_at
    queries — no extra sort needed here).
    """
    pending: list[PendingDigestEvent] = []
    target_frequency = _FREQUENCY_FOR_CADENCE[cadence]

    for event_type in _DIGESTIBLE_EVENT_TYPES:
        pref = await asyncio.to_thread(get_preference_pg, org_id, event_type.value)
        enabled: bool = pref["enabled"] if pref is not None else True  # type: ignore[assignment]
        frequency: str = pref["frequency"] if pref is not None else "immediate"  # type: ignore[assignment]

        # Only digest events explicitly enabled AND configured for this cadence.
        # Disabled prefs and immediate-frequency prefs are the immediate
        # engine's territory (or simply off) — not this batcher's job.
        if not (enabled and frequency == target_frequency):
            continue

        if cadence == "daily":
            since: datetime | None = await asyncio.to_thread(
                get_last_digest_watermark_pg, org_id, event_type.value
            )
        else:
            since = await asyncio.to_thread(
                get_last_digest_watermark_pg, org_id, event_type.value, cadence
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
    subject = f"Samidha Reviews daily digest: {count} event(s) need attention"

    lines: list[str] = [
        f"Samidha Reviews daily digest — {count} event(s) since your last digest.",
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
    lines.append("Log in to Samidha Reviews to investigate and take action.")

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


@dataclass(frozen=True)
class WeeklyStats:
    """Week-over-week counts for the weekly digest body (this 7 days vs the 7 before)."""

    reviews_this: int
    reviews_prev: int
    urgent_this: int
    urgent_prev: int
    # (topic, count this week, count previous week), most-complained first.
    top_topics: tuple[tuple[str, int, int], ...]


def compute_weekly_stats(rows: list[dict[str, object]], now: datetime) -> WeeklyStats:
    """Aggregate extraction summaries into this-7-days vs previous-7-days counts. Pure.

    Window: (now-7d, now] is "this week", (now-14d, now-7d] is "previous". Rows outside both
    (or with a missing created_at) are ignored. A "complaint" row is one with at least one
    con or urgency == high; its topics feed the top-topics ranking. Topics are lowercased and
    stripped; non-string/empty topics are dropped, so malformed stored data cannot crash it.
    """
    week_start = now - timedelta(days=7)
    prev_start = now - timedelta(days=14)
    reviews_this = reviews_prev = urgent_this = urgent_prev = 0
    topics_this: Counter[str] = Counter()
    topics_prev: Counter[str] = Counter()

    for row in rows:
        created = row.get("created_at")
        if not isinstance(created, datetime):
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        if week_start < created <= now:
            is_this = True
        elif prev_start < created <= week_start:
            is_this = False
        else:
            continue
        is_urgent = row.get("urgency") == "high"
        cons = row.get("cons")
        is_complaint = is_urgent or (isinstance(cons, list) and len(cons) > 0)
        raw_topics = row.get("topics")
        topics = raw_topics if isinstance(raw_topics, list) else []
        if is_this:
            reviews_this += 1
            urgent_this += int(is_urgent)
        else:
            reviews_prev += 1
            urgent_prev += int(is_urgent)
        if is_complaint:
            for t in topics:
                if isinstance(t, str) and t.strip():
                    (topics_this if is_this else topics_prev)[t.strip().lower()] += 1

    ranked = sorted(topics_this.items(), key=lambda kv: (-kv[1], kv[0]))[:_WEEKLY_TOP_TOPICS]
    return WeeklyStats(
        reviews_this=reviews_this,
        reviews_prev=reviews_prev,
        urgent_this=urgent_this,
        urgent_prev=urgent_prev,
        top_topics=tuple((t, n, topics_prev.get(t, 0)) for t, n in ranked),
    )


def _delta(this: int, prev: int) -> str:
    """Render 'this (+d vs prev the week before)' with an explicit sign; no ratios (prev may be 0)."""
    return f"{this} ({this - prev:+d} vs {prev} the week before)"


def _event_line(pe: PendingDigestEvent) -> str:
    """One plain-text line for a pending event (same shape as the daily digest lines)."""
    event = pe.event
    if event.event_type == AlertEventType.HIGH_URGENCY:
        topics = event.details.get("topics") or []
        cons = event.details.get("cons") or []
        detail = f"topics={topics}, cons={cons}"
    elif event.event_type == AlertEventType.LIKELY_FAKE:
        detail = f"score={event.details.get('score')}"
    else:
        detail = str(dict(event.details))
    return f"[{event.event_type}] review {pe.review_id}: {detail}"


def build_weekly_digest_email(
    org_id: str,
    recipient_email: str,
    events: list[PendingDigestEvent],
    stats: WeeklyStats | None,
) -> AlertMessage | None:
    """Format the weekly digest (plain text + HTML), or None if there are no events.

    Pure formatting; reads only settings and unsubscribe config. `stats` may be None (the
    stats query failed): the week-over-week section is then omitted and the email still goes
    out. Every interpolated value is html-escaped in the HTML part. The dashboard link comes
    from DASHBOARD_URL and is omitted when unset.
    """
    if not events:
        return None

    count = len(events)
    subject = f"Samidha Reviews weekly digest: {count} event(s) need attention"
    base = get_settings().dashboard_url.strip().rstrip("/")
    dashboard_link = f"{base}/dashboard" if base else None
    unsubscribe_url = build_unsubscribe_url(org_id)

    listed = events[:_WEEKLY_MAX_LISTED_EVENTS]
    more = count - len(listed)

    text: list[str] = [
        f"Samidha Reviews weekly digest — {count} event(s) since your last digest.",
        "",
    ]
    htm: list[str] = [
        "<html><body>",
        f"<h2>Samidha Reviews weekly digest</h2><p>{count} event(s) since your last digest.</p>",
    ]
    if stats is not None:
        reviews_line = f"Reviews analysed: {_delta(stats.reviews_this, stats.reviews_prev)}"
        urgent_line = f"High-urgency reviews: {_delta(stats.urgent_this, stats.urgent_prev)}"
        text += ["This week vs the week before:", f"- {reviews_line}", f"- {urgent_line}", ""]
        htm += [
            "<h3>This week vs the week before</h3><ul>",
            f"<li>{html.escape(reviews_line)}</li><li>{html.escape(urgent_line)}</li></ul>",
        ]
        if stats.top_topics:
            text.append("Top complaint topics:")
            htm.append("<h3>Top complaint topics</h3><ol>")
            for topic, n_this, n_prev in stats.top_topics:
                line = f"{topic}: {_delta(n_this, n_prev)}"
                text.append(f"- {line}")
                htm.append(f"<li>{html.escape(line)}</li>")
            text.append("")
            htm.append("</ol>")
    text.append("Items needing attention:")
    htm.append("<h3>Items needing attention</h3><ul>")
    for pe in listed:
        line = _event_line(pe)
        text.append(f"- {line}")
        htm.append(f"<li>{html.escape(line)}</li>")
    if more > 0:
        text.append(f"- ...and {more} more")
        htm.append(f"<li>...and {more} more</li>")
    htm.append("</ul>")
    text.append("")
    if dashboard_link:
        text.append(f"Open your dashboard: {dashboard_link}")
        href = html.escape(dashboard_link, quote=True)
        htm.append(f'<p><a href="{href}">Open your dashboard</a></p>')
    else:
        text.append("Log in to Samidha Reviews to investigate and take action.")
        htm.append("<p>Log in to Samidha Reviews to investigate and take action.</p>")
    if unsubscribe_url:
        text += ["", f"Stop receiving these emails: {unsubscribe_url}"]
        unsub_href = html.escape(unsubscribe_url, quote=True)
        htm.append(f'<p><small><a href="{unsub_href}">Stop receiving these emails</a></small></p>')
    htm.append("</body></html>")

    return AlertMessage(
        org_id=org_id,
        event=events[0].event,
        subject=subject,
        body_text="\n".join(text),
        recipient_email=recipient_email,
        unsubscribe_url=unsubscribe_url,
        body_html="".join(htm),
    )


async def run_digest_for_org(
    org_id: str, channel: Channel, cadence: Cadence = "daily"
) -> list[PendingDigestEvent]:
    """Collect, send, and record one org's digest for `cadence`. Returns events actually sent.

    No-drop guarantee: alert_log is only written after a successful send, so
    a failed send (ChannelError) or a missing recipient email leaves every
    pending event un-recorded and therefore re-collectable on the next sweep.
    """
    events = await (
        collect_pending_for_org(org_id)
        if cadence == "daily"
        else collect_pending_for_org(org_id, cadence)
    )
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

    if await recipient_is_suppressed(recipient_email, org_id=org_id):
        log.info("digest.recipient_suppressed", org_id=org_id, pending_count=len(events))
        return []

    if cadence == "weekly":
        message = await _build_weekly_message(org_id, recipient_email, events)
    else:
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
        details = dict(pe.event.details)
        if cadence == "weekly":
            # The per-cadence watermark key (see get_last_digest_watermark_pg). Daily rows stay
            # untagged so daily behaviour is unchanged.
            details["cadence"] = cadence
        await asyncio.to_thread(
            record_alert_sent_pg,
            org_id,
            pe.review_id,
            pe.event.event_type,
            details,
        )

    log.info(
        "digest.sent",
        org_id=org_id,
        recipient=recipient_email,
        event_count=len(events),
        cadence=cadence,
    )
    return events


async def _build_weekly_message(
    org_id: str, recipient_email: str, events: list[PendingDigestEvent]
) -> AlertMessage | None:
    """Fetch week-over-week stats (best effort) and build the weekly email.

    A stats failure must not suppress an otherwise-valid digest: log and send without the
    comparison section.
    """
    now = datetime.now(UTC)
    stats: WeeklyStats | None
    try:
        rows = await asyncio.to_thread(
            list_extraction_summaries_since_pg, org_id, now - timedelta(days=14)
        )
        stats = compute_weekly_stats(rows, now)
    except Exception:
        log.error("digest.weekly_stats_failed", org_id=org_id, exc_info=True)
        stats = None
    return build_weekly_digest_email(org_id, recipient_email, events, stats)
