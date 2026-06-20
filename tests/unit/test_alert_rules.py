"""Unit tests for app.core.alerts.rules — pure-function alert rule layer.

No external I/O, no database, no async. All deterministic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, UTC

import pytest

from app.core.alerts.rules import (
    AlertEvent,
    AlertEventType,
    AlertThresholds,
    check_fake_cluster,
    check_high_urgency,
    check_likely_fake,
    check_topic_spike,
    evaluate_review,
)
from app.core.authenticity.schema import AuthenticityFlag, AuthenticityLabel, AuthenticityResult
from app.core.schemas import ReviewExtraction, Urgency

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

_STABLE_TS = datetime(2026, 6, 21, 12, 0, 0)
_REVIEW_HASH = "abc123"


def _auth(
    label: AuthenticityLabel,
    score: float = 0.5,
    flags: list[AuthenticityFlag] | None = None,
    reasons: str = "",
    scored_at: datetime = _STABLE_TS,
) -> AuthenticityResult:
    """Helper to build AuthenticityResult without going through from_signals."""
    return AuthenticityResult(
        score=score,
        label=label,
        flags=flags or [],
        reasons=reasons,
        review_hash=_REVIEW_HASH,
        scored_at=scored_at,
    )


def _extraction(
    urgency: Urgency = Urgency.low,
    topics: list[str] | None = None,
    cons: list[str] | None = None,
) -> ReviewExtraction:
    """Helper to build ReviewExtraction with minimal required fields."""
    return ReviewExtraction(
        product="Test Product",
        urgency=urgency,
        topics=topics or [],
        cons=cons or [],
    )


# ---------------------------------------------------------------------------
# check_high_urgency
# ---------------------------------------------------------------------------


def test_check_high_urgency_fires_on_high() -> None:
    event = check_high_urgency(_extraction(urgency=Urgency.high, topics=["crash"], cons=["broken"]))
    assert event is not None
    assert event.event_type == AlertEventType.HIGH_URGENCY
    assert event.details["urgency"] == "high"
    assert event.details["topics"] == ["crash"]
    assert event.details["cons"] == ["broken"]


def test_check_high_urgency_silent_on_medium() -> None:
    event = check_high_urgency(_extraction(urgency=Urgency.medium))
    assert event is None


def test_check_high_urgency_silent_on_low() -> None:
    event = check_high_urgency(_extraction(urgency=Urgency.low))
    assert event is None


# ---------------------------------------------------------------------------
# check_likely_fake
# ---------------------------------------------------------------------------


def test_check_likely_fake_fires_on_likely_fake() -> None:
    auth = _auth(
        label=AuthenticityLabel.LIKELY_FAKE,
        score=0.1,
        flags=[AuthenticityFlag.PROMOTIONAL_TONE],
        reasons="Looks like spam",
    )
    event = check_likely_fake(auth)
    assert event is not None
    assert event.event_type == AlertEventType.LIKELY_FAKE
    assert event.details["score"] == 0.1
    assert event.details["flags"] == ["promotional_tone"]
    assert event.details["reasons"] == "Looks like spam"


def test_check_likely_fake_silent_on_suspicious() -> None:
    event = check_likely_fake(_auth(label=AuthenticityLabel.SUSPICIOUS))
    assert event is None


def test_check_likely_fake_silent_on_genuine() -> None:
    event = check_likely_fake(_auth(label=AuthenticityLabel.GENUINE, score=0.9))
    assert event is None


# ---------------------------------------------------------------------------
# check_fake_cluster
# ---------------------------------------------------------------------------


def test_check_fake_cluster_fires_at_min_count() -> None:
    """Exactly at min_count=3 should fire."""
    results = [
        _auth(AuthenticityLabel.LIKELY_FAKE, scored_at=_STABLE_TS),
        _auth(AuthenticityLabel.SUSPICIOUS, scored_at=_STABLE_TS),
        _auth(AuthenticityLabel.LIKELY_FAKE, scored_at=_STABLE_TS),
    ]
    event = check_fake_cluster(results)
    assert event is not None
    assert event.event_type == AlertEventType.FAKE_CLUSTER
    assert event.details["count"] == 3


def test_check_fake_cluster_silent_one_below_min_count() -> None:
    """Two results (one below default min_count=3) should not fire."""
    results = [
        _auth(AuthenticityLabel.LIKELY_FAKE, scored_at=_STABLE_TS),
        _auth(AuthenticityLabel.SUSPICIOUS, scored_at=_STABLE_TS),
    ]
    event = check_fake_cluster(results)
    assert event is None


def test_check_fake_cluster_outside_window_not_counted() -> None:
    """Entries older than window_hours should be excluded from count."""
    old_ts = _STABLE_TS - timedelta(hours=100)
    results = [
        _auth(AuthenticityLabel.LIKELY_FAKE, scored_at=old_ts),
        _auth(AuthenticityLabel.LIKELY_FAKE, scored_at=old_ts),
        _auth(AuthenticityLabel.LIKELY_FAKE, scored_at=old_ts),
        # Only one entry within the 48-hour window (the most recent = now for max())
        _auth(AuthenticityLabel.LIKELY_FAKE, scored_at=_STABLE_TS),
    ]
    # max scored_at = _STABLE_TS; window_start = _STABLE_TS - 48h
    # old_ts = _STABLE_TS - 100h < window_start, so only 1 in-window entry
    event = check_fake_cluster(results)
    assert event is None


def test_check_fake_cluster_empty_list_returns_none() -> None:
    assert check_fake_cluster([]) is None


def test_check_fake_cluster_genuine_not_counted() -> None:
    """GENUINE labels should not contribute to the cluster count."""
    results = [
        _auth(AuthenticityLabel.GENUINE, scored_at=_STABLE_TS),
        _auth(AuthenticityLabel.GENUINE, scored_at=_STABLE_TS),
        _auth(AuthenticityLabel.GENUINE, scored_at=_STABLE_TS),
    ]
    event = check_fake_cluster(results)
    assert event is None


def test_check_fake_cluster_custom_thresholds_lower_min_count() -> None:
    """Custom thresholds with min_count=2 should fire on 2 entries."""
    thresholds = AlertThresholds(fake_cluster_min_count=2)
    results = [
        _auth(AuthenticityLabel.LIKELY_FAKE, scored_at=_STABLE_TS),
        _auth(AuthenticityLabel.SUSPICIOUS, scored_at=_STABLE_TS),
    ]
    event = check_fake_cluster(results, thresholds=thresholds)
    assert event is not None
    assert event.details["count"] == 2


def test_check_fake_cluster_window_hours_in_details() -> None:
    """Alert details should reflect the configured window_hours."""
    thresholds = AlertThresholds(fake_cluster_min_count=2, fake_cluster_window_hours=24)
    results = [
        _auth(AuthenticityLabel.LIKELY_FAKE, scored_at=_STABLE_TS),
        _auth(AuthenticityLabel.LIKELY_FAKE, scored_at=_STABLE_TS),
    ]
    event = check_fake_cluster(results, thresholds=thresholds)
    assert event is not None
    assert event.details["window_hours"] == 24


# ---------------------------------------------------------------------------
# check_topic_spike
# ---------------------------------------------------------------------------


def test_check_topic_spike_zero_baseline_absolute_met_fires() -> None:
    """Zero baseline with absolute minimum met → spike with ratio=None."""
    event = check_topic_spike("battery drain", recent_count=3, baseline_count_per_window=0.0)
    assert event is not None
    assert event.event_type == AlertEventType.TOPIC_SPIKE
    assert event.details["topic"] == "battery drain"
    assert event.details["recent_count"] == 3
    assert event.details["baseline"] == 0.0
    assert event.details["ratio"] is None


def test_check_topic_spike_zero_baseline_absolute_not_met_returns_none() -> None:
    """Zero baseline but recent_count=1 < default min_absolute=2 → None."""
    event = check_topic_spike("battery drain", recent_count=1, baseline_count_per_window=0.0)
    assert event is None


def test_check_topic_spike_high_ratio_absolute_met_fires() -> None:
    """ratio=6.0 >= default 3.0, recent_count=6 >= 2 → fires with correct ratio."""
    event = check_topic_spike("overheating", recent_count=6, baseline_count_per_window=1.0)
    assert event is not None
    assert event.details["ratio"] == 6.0
    assert event.details["recent_count"] == 6
    assert event.details["baseline"] == 1.0


def test_check_topic_spike_high_ratio_absolute_not_met_returns_none() -> None:
    """Ratio is high but recent_count=1 < min_absolute=2 → None."""
    event = check_topic_spike("overheating", recent_count=1, baseline_count_per_window=0.1)
    assert event is None


def test_check_topic_spike_below_ratio_returns_none() -> None:
    """ratio=1.5 < default 3.0 → None even if absolute is met."""
    event = check_topic_spike("noise", recent_count=3, baseline_count_per_window=2.0)
    assert event is None


def test_check_topic_spike_exactly_at_ratio_threshold_fires() -> None:
    """ratio exactly equal to threshold (3.0) should fire."""
    event = check_topic_spike("crack", recent_count=6, baseline_count_per_window=2.0)
    assert event is not None
    assert event.details["ratio"] == 3.0


def test_check_topic_spike_ratio_rounded_to_two_decimal_places() -> None:
    """Ratio in details should be rounded to 2 decimal places."""
    # 7 / 2 = 3.5 exactly — check rounding doesn't corrupt it
    event = check_topic_spike("dim screen", recent_count=7, baseline_count_per_window=2.0)
    assert event is not None
    assert event.details["ratio"] == 3.5


def test_check_topic_spike_custom_thresholds_respected() -> None:
    """Custom thresholds with lower ratio should fire where defaults would not."""
    thresholds = AlertThresholds(topic_spike_min_ratio=2.0, topic_spike_min_absolute=2)
    # ratio=2.0 equals custom threshold, absolute=4 >= 2 → fires with custom thresholds
    event = check_topic_spike(
        "slow charging", recent_count=4, baseline_count_per_window=2.0, thresholds=thresholds
    )
    assert event is not None

    # Same inputs without custom thresholds: ratio=2.0 < default 3.0 → None
    assert check_topic_spike("slow charging", recent_count=4, baseline_count_per_window=2.0) is None


# ---------------------------------------------------------------------------
# evaluate_review (integration of per-review checks)
# ---------------------------------------------------------------------------


def test_evaluate_review_high_urgency_genuine_yields_high_urgency_only() -> None:
    extraction = _extraction(urgency=Urgency.high, topics=["crash"])
    auth = _auth(label=AuthenticityLabel.GENUINE, score=0.9)
    events = evaluate_review(extraction, auth)
    assert len(events) == 1
    assert events[0].event_type == AlertEventType.HIGH_URGENCY


def test_evaluate_review_low_urgency_likely_fake_yields_likely_fake_only() -> None:
    extraction = _extraction(urgency=Urgency.low)
    auth = _auth(label=AuthenticityLabel.LIKELY_FAKE, score=0.1)
    events = evaluate_review(extraction, auth)
    assert len(events) == 1
    assert events[0].event_type == AlertEventType.LIKELY_FAKE


def test_evaluate_review_high_urgency_likely_fake_yields_both() -> None:
    extraction = _extraction(urgency=Urgency.high)
    auth = _auth(label=AuthenticityLabel.LIKELY_FAKE, score=0.05)
    events = evaluate_review(extraction, auth)
    assert len(events) == 2
    event_types = {e.event_type for e in events}
    assert AlertEventType.HIGH_URGENCY in event_types
    assert AlertEventType.LIKELY_FAKE in event_types


def test_evaluate_review_high_urgency_likely_fake_order() -> None:
    """HIGH_URGENCY should appear before LIKELY_FAKE in the returned list."""
    extraction = _extraction(urgency=Urgency.high)
    auth = _auth(label=AuthenticityLabel.LIKELY_FAKE, score=0.05)
    events = evaluate_review(extraction, auth)
    assert events[0].event_type == AlertEventType.HIGH_URGENCY
    assert events[1].event_type == AlertEventType.LIKELY_FAKE


def test_evaluate_review_low_urgency_genuine_yields_empty() -> None:
    extraction = _extraction(urgency=Urgency.low)
    auth = _auth(label=AuthenticityLabel.GENUINE, score=0.85)
    events = evaluate_review(extraction, auth)
    assert events == []


def test_evaluate_review_medium_urgency_suspicious_yields_empty() -> None:
    """SUSPICIOUS is not alert-worthy at the per-review level — only cluster is."""
    extraction = _extraction(urgency=Urgency.medium)
    auth = _auth(label=AuthenticityLabel.SUSPICIOUS, score=0.45)
    events = evaluate_review(extraction, auth)
    assert events == []


# ---------------------------------------------------------------------------
# AlertThresholds dataclass
# ---------------------------------------------------------------------------


def test_alert_thresholds_defaults() -> None:
    t = AlertThresholds()
    assert t.fake_cluster_window_hours == 48
    assert t.fake_cluster_min_count == 3
    assert t.topic_spike_min_ratio == 3.0
    assert t.topic_spike_min_absolute == 2


def test_alert_thresholds_custom_values_are_frozen() -> None:
    """AlertThresholds is frozen — mutation should raise AttributeError."""
    t = AlertThresholds(fake_cluster_min_count=5)
    with pytest.raises(AttributeError):
        t.fake_cluster_min_count = 10  # type: ignore[misc]
