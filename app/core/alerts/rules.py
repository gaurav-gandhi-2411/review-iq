"""Alert rules layer — pure functions over detection output.

No external I/O, no database, no cost. Takes already-computed ReviewExtraction
and AuthenticityResult objects and decides which events are alert-worthy.

Usage:
    events = evaluate_review(extraction, auth)
    cluster_event = check_fake_cluster(recent_auth_results)
    spike_event = check_topic_spike("battery drain", recent_count=5, baseline_per_window=1.0)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.core.authenticity.schema import AuthenticityLabel, AuthenticityResult
from app.core.schemas import ReviewExtraction, Urgency


class AlertEventType(StrEnum):
    HIGH_URGENCY = "high_urgency"
    LIKELY_FAKE = "likely_fake"
    FAKE_CLUSTER = "fake_cluster"
    TOPIC_SPIKE = "topic_spike"


@dataclass(frozen=True)
class AlertThresholds:
    """Conservative defaults — intentionally high bars to avoid alert fatigue.

    Per-org overrides will come from alert_preferences once that table exists.
    """

    fake_cluster_window_hours: int = 48
    fake_cluster_min_count: int = 3
    topic_spike_min_ratio: float = 3.0
    topic_spike_min_absolute: int = 2


DEFAULT_THRESHOLDS: AlertThresholds = AlertThresholds()


@dataclass(frozen=True)
class AlertEvent:
    """A single alert-worthy event produced by the rules layer."""

    event_type: AlertEventType
    details: dict[str, object] = field(default_factory=dict)


def check_high_urgency(
    extraction: ReviewExtraction,
    thresholds: AlertThresholds = DEFAULT_THRESHOLDS,
) -> AlertEvent | None:
    """Return HIGH_URGENCY event when the extraction signals high urgency."""
    if extraction.urgency != Urgency.high:
        return None
    return AlertEvent(
        event_type=AlertEventType.HIGH_URGENCY,
        details={
            "urgency": extraction.urgency.value,
            "topics": list(extraction.topics),
            "cons": list(extraction.cons),
        },
    )


def check_likely_fake(
    auth: AuthenticityResult,
    thresholds: AlertThresholds = DEFAULT_THRESHOLDS,
) -> AlertEvent | None:
    """Return LIKELY_FAKE event when the authenticity result labels the review fake."""
    if auth.label != AuthenticityLabel.LIKELY_FAKE:
        return None
    return AlertEvent(
        event_type=AlertEventType.LIKELY_FAKE,
        details={
            "score": auth.score,
            "flags": [f.value for f in auth.flags],
            "reasons": auth.reasons,
        },
    )


def check_fake_cluster(
    recent: list[AuthenticityResult],
    thresholds: AlertThresholds = DEFAULT_THRESHOLDS,
) -> AlertEvent | None:
    """Return FAKE_CLUSTER event when suspicious/fake results cluster within the time window.

    Uses the max scored_at across all entries as 'now' so the function is
    deterministic (no wall-clock dependency — safe for tests and VCR replay).
    """
    if not recent:
        return None

    from datetime import timedelta

    now = max(r.scored_at for r in recent)
    window_start = now - timedelta(hours=thresholds.fake_cluster_window_hours)

    count = sum(
        1
        for r in recent
        if r.scored_at >= window_start
        and r.label in {AuthenticityLabel.LIKELY_FAKE, AuthenticityLabel.SUSPICIOUS}
    )

    if count < thresholds.fake_cluster_min_count:
        return None

    return AlertEvent(
        event_type=AlertEventType.FAKE_CLUSTER,
        details={
            "count": count,
            "window_hours": thresholds.fake_cluster_window_hours,
        },
    )


def check_topic_spike(
    topic: str,
    recent_count: int,
    baseline_count_per_window: float,
    thresholds: AlertThresholds = DEFAULT_THRESHOLDS,
) -> AlertEvent | None:
    """Return TOPIC_SPIKE when a complaint topic's recent frequency far exceeds baseline.

    Args:
        topic: The topic string (e.g. "battery drain").
        recent_count: Number of occurrences in the current window (e.g. last 7 days).
        baseline_count_per_window: Average occurrences per equivalent window in the
            baseline period (e.g. avg per 7-day window over last 30 days). May be 0.0.
        thresholds: Configurable alert thresholds.

    Returns None when:
      - recent_count < topic_spike_min_absolute (too few to care)
      - baseline > 0 AND ratio < topic_spike_min_ratio (not a spike)
    """
    if recent_count < thresholds.topic_spike_min_absolute:
        return None

    if baseline_count_per_window <= 0.0:
        # No prior baseline — any meeting the absolute minimum is a spike.
        return AlertEvent(
            event_type=AlertEventType.TOPIC_SPIKE,
            details={
                "topic": topic,
                "recent_count": recent_count,
                "baseline": 0.0,
                "ratio": None,
            },
        )

    ratio = recent_count / baseline_count_per_window
    if ratio < thresholds.topic_spike_min_ratio:
        return None

    return AlertEvent(
        event_type=AlertEventType.TOPIC_SPIKE,
        details={
            "topic": topic,
            "recent_count": recent_count,
            "baseline": baseline_count_per_window,
            "ratio": round(ratio, 2),
        },
    )


def evaluate_review(
    extraction: ReviewExtraction,
    auth: AuthenticityResult,
    thresholds: AlertThresholds = DEFAULT_THRESHOLDS,
) -> list[AlertEvent]:
    """Evaluate a single processed review and return all per-review alert events.

    Cluster and spike checks require external context (list of recent results,
    topic frequency stats) — call check_fake_cluster and check_topic_spike
    separately from the engine layer once that context is loaded.
    """
    events: list[AlertEvent] = []
    if event := check_high_urgency(extraction, thresholds):
        events.append(event)
    if event := check_likely_fake(auth, thresholds):
        events.append(event)
    return events
