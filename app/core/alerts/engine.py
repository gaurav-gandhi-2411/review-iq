"""Alert engine — rules → dedupe → preferences → channel → record.

evaluate_and_alert() is the single entry point. It handles per-review events
(high_urgency + likely_fake). Cluster and spike events require the caller to
compute them from batch context (check_fake_cluster / check_topic_spike in
rules.py) and call evaluate_and_alert with the augmented event list.
"""

from __future__ import annotations

import asyncio

import structlog

from app.core.alerts.channels.base import AlertMessage, Channel, ChannelError
from app.core.alerts.channels.fake import LogChannel
from app.core.alerts.channels.resend_channel import ResendChannel
from app.core.alerts.rules import (
    DEFAULT_THRESHOLDS,
    AlertEvent,
    AlertEventType,
    AlertThresholds,
    check_high_urgency,
    check_likely_fake,
)
from app.core.alerts.storage import (
    get_org_notification_email_pg,
    get_preference_pg,
    is_already_alerted_pg,
    record_alert_sent_pg,
)
from app.core.authenticity.schema import AuthenticityResult
from app.core.schemas import ReviewExtraction

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

_SUBJECT_TEMPLATES: dict[AlertEventType, str] = {
    AlertEventType.HIGH_URGENCY: "⚠️ Urgent customer review needs attention",
    AlertEventType.LIKELY_FAKE: "🚨 Suspicious review detected",
    AlertEventType.FAKE_CLUSTER: "🚨 {count} suspicious reviews in {window_hours}h — possible fake cluster",
    AlertEventType.TOPIC_SPIKE: "📈 Complaint spike: '{topic}' ({recent_count}x in recent window)",
}


def _format_subject(event: AlertEvent) -> str:
    template = _SUBJECT_TEMPLATES.get(event.event_type, "Review-IQ alert: {event_type}")
    try:
        return template.format(**event.details, event_type=event.event_type)
    except (KeyError, ValueError):
        return f"Review-IQ alert: {event.event_type}"


def _format_body(
    org_id: str,
    review_id: str | None,
    extraction: ReviewExtraction | None,
    event: AlertEvent,
) -> str:
    lines: list[str] = ["Review-IQ detected an event requiring your attention.", ""]

    if event.event_type == AlertEventType.HIGH_URGENCY:
        lines.append("A customer review has been flagged as HIGH URGENCY.")
        # extraction is guaranteed non-None whenever a HIGH_URGENCY event exists (see
        # evaluate_and_alert step 2), but mypy strict needs the explicit narrowing here.
        if extraction is not None:
            if extraction.cons:
                lines.append(f"Issues mentioned: {', '.join(extraction.cons[:3])}")
            if extraction.topics:
                lines.append(f"Topics: {', '.join(extraction.topics[:3])}")

    elif event.event_type == AlertEventType.LIKELY_FAKE:
        lines.append("A review was flagged as likely inauthentic.")
        score = event.details.get("score")
        if score is not None:
            lines.append(f"Authenticity score: {float(score):.2f} (lower = more suspicious)")
        reasons = event.details.get("reasons")
        if reasons:
            lines.append(f"Reason: {reasons}")

    elif event.event_type == AlertEventType.FAKE_CLUSTER:
        count = event.details.get("count", "?")
        hours = event.details.get("window_hours", "?")
        lines.append(
            f"{count} suspicious reviews appeared within {hours} hours. "
            "This may indicate a coordinated fake-review campaign."
        )

    elif event.event_type == AlertEventType.TOPIC_SPIKE:
        topic = event.details.get("topic", "?")
        recent = event.details.get("recent_count", "?")
        baseline = event.details.get("baseline", 0.0)
        lines.append(
            f"The complaint topic '{topic}' has spiked: "
            f"{recent} recent mentions vs a baseline of {float(baseline):.1f}."
        )

    if review_id:
        lines.append(f"\nReview reference: {review_id}")
    lines.append("\nLog in to Review-IQ to investigate and take action.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


async def evaluate_and_alert(
    *,
    org_id: str,
    review_id: str | None,
    extraction: ReviewExtraction | None = None,
    auth: AuthenticityResult | None = None,
    channel: Channel,
    recipient_email: str | None = None,
    thresholds: AlertThresholds = DEFAULT_THRESHOLDS,
) -> list[AlertEvent]:
    """Core alert loop: rules → dedupe → prefs → send → record.

    Extraction and authenticity scoring happen in decoupled pipelines in this
    codebase (no single funnel produces both at once) — pass whichever piece is
    available at the call site and leave the other as None. The corresponding
    rule check (check_high_urgency for extraction, check_likely_fake for auth)
    is simply skipped when its input is None.

    Args:
        org_id: Tenant identifier.
        review_id: Stable review identifier for dedupe (sha256 hex or connector ID).
                   Pass None for synthetic events (cluster/spike) that don't map to a
                   single review — these skip the per-review dedupe check.
        extraction: Extraction output for the review, if available at this call site.
        auth: Authenticity scoring output for the review, if available at this call site.
        channel: Delivery channel (FakeChannel for tests, real channel in production).
        recipient_email: Override the notification email. If None, looks up from
                         organizations.notification_email. If still None, skips
                         delivery but returns which events would have fired.
        thresholds: Alert rule thresholds (conservative defaults).

    Returns:
        List of AlertEvents for which an alert was successfully sent (deduped + enabled).
        Events suppressed by dedupe, disabled prefs, daily_digest, or missing email
        are excluded from the return value.
    """
    events: list[AlertEvent] = []
    if extraction is not None and (event := check_high_urgency(extraction, thresholds)):
        events.append(event)
    if auth is not None and (event := check_likely_fake(auth, thresholds)):
        events.append(event)
    if not events:
        return []

    # Resolve recipient email once for all events in this call.
    if recipient_email is None:
        recipient_email = await asyncio.to_thread(get_org_notification_email_pg, org_id)

    sent: list[AlertEvent] = []

    for event in events:
        event_type_str = event.event_type  # already a str via StrEnum

        # 1. Dedupe: skip if alert_log already has a row for this review+event_type.
        if review_id is not None:
            already = await asyncio.to_thread(
                is_already_alerted_pg, org_id, review_id, event_type_str
            )
            if already:
                log.debug(
                    "alert.deduped",
                    org_id=org_id,
                    review_id=review_id,
                    event_type=event_type_str,
                )
                continue

        # 2. Preference check (default: enabled=True, frequency="immediate").
        pref = await asyncio.to_thread(get_preference_pg, org_id, event_type_str)
        enabled: bool = pref["enabled"] if pref is not None else True  # type: ignore[assignment]
        frequency: str = pref["frequency"] if pref is not None else "immediate"  # type: ignore[assignment]

        if not enabled:
            log.debug("alert.suppressed_by_pref", org_id=org_id, event_type=event_type_str)
            continue

        # 3. Frequency gate: daily_digest defers send (not yet implemented; skip for now).
        if frequency == "daily_digest":
            log.info(
                "alert.pending_digest",
                org_id=org_id,
                event_type=event_type_str,
                note="digest batching not yet implemented — alert deferred",
            )
            continue

        # 4. Recipient required for real delivery; skip if not configured.
        if not recipient_email:
            log.info(
                "alert.no_recipient_configured",
                org_id=org_id,
                event_type=event_type_str,
            )
            continue

        # 5. Format message and deliver via channel.
        message = AlertMessage(
            org_id=org_id,
            event=event,
            subject=_format_subject(event),
            body_text=_format_body(org_id, review_id, extraction, event),
            recipient_email=recipient_email,
        )
        try:
            await channel.send(message)
        except ChannelError:
            log.error(
                "alert.send_failed",
                org_id=org_id,
                event_type=event_type_str,
                exc_info=True,
            )
            continue

        # 6. Record in alert_log (dedupe source for future calls).
        await asyncio.to_thread(
            record_alert_sent_pg,
            org_id,
            review_id,
            event_type_str,
            dict(event.details),
        )

        sent.append(event)
        log.info(
            "alert.sent",
            org_id=org_id,
            event_type=event_type_str,
            recipient=recipient_email,
        )

    return sent


# ---------------------------------------------------------------------------
# Ingestion-facing wiring
# ---------------------------------------------------------------------------


def _get_default_channel() -> Channel:
    """Resend if configured, else the $0 structured-log fallback (LogChannel).

    Constructing ResendChannel() raises ValueError when RESEND_API_KEY / RESEND_FROM_EMAIL
    are unset (true for local dev and most test environments) — degrade to LogChannel rather
    than skip alerting entirely.
    """
    try:
        return ResendChannel()
    except ValueError:
        return LogChannel()


async def alert_on_review_event(
    *,
    org_id: str,
    review_id: str,
    extraction: ReviewExtraction | None = None,
    auth: AuthenticityResult | None = None,
) -> None:
    """Best-effort wrapper: never raises. Call once per newly-persisted extraction or
    authenticity result from an ingestion call site, passing whichever piece is available.

    Any failure here (missing Resend config surfacing some other way, a transient DB error,
    a Resend API error not already caught inside evaluate_and_alert) is logged and swallowed
    — alerting must never break or roll back ingestion, which has already succeeded by the
    time this is called.
    """
    try:
        channel = _get_default_channel()
        await evaluate_and_alert(
            org_id=org_id,
            review_id=review_id,
            extraction=extraction,
            auth=auth,
            channel=channel,
        )
    except Exception:
        log.error(
            "alert.wiring_failed",
            org_id=org_id,
            review_id=review_id,
            exc_info=True,
        )
