"""Unit tests for app.core.detectors.batch_defect -- the production fork.

Not a re-run of synthetic validation (already proven in
benchmark/phase2_synthetic/detectors/, out of scope here). These tests only prove: (1) the
Postgres-row adapter maps fields correctly, (2) the ported algorithm still runs end-to-end
after the port (import path, no accidental behavior change).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.detectors.batch_defect import annotated_reviews_from_rows, scan_batch_defects
from app.core.detectors.common import AnnotatedReview

_NOW = datetime(2026, 6, 1, tzinfo=UTC)


def test_annotated_reviews_from_rows_maps_fields() -> None:
    rows = [
        {
            "id": "abc123",
            "product": "Widget Pro",
            "topics": ["battery", "screen"],
            "sentiment": "negative",
            "review_date": _NOW,
        }
    ]

    result = annotated_reviews_from_rows(rows)

    assert len(result) == 1
    review = result[0]
    assert review.review_id == "abc123"
    assert review.product_id == "Widget Pro"
    assert review.timestamp == _NOW
    assert review.topics == ["battery", "screen"]
    assert review.sentiment == "negative"
    # Confirmed-unused-by-the-algorithm fields are deliberately stubbed, not populated.
    assert review.reviewer_id == ""
    assert review.rating == 0
    assert review.urgency == ""


def test_annotated_reviews_from_rows_handles_missing_product_and_sentiment() -> None:
    rows = [{"id": "x", "product": None, "topics": [], "sentiment": None, "review_date": _NOW}]

    result = annotated_reviews_from_rows(rows)

    assert result[0].product_id == "unknown product"
    assert result[0].sentiment == ""


def test_scan_batch_defects_smoke_after_port() -> None:
    """Hand-built obvious spike: 6 negative 'battery' mentions clustered in 2 days, against a
    product with no other topic activity. Proves the port (import path, algorithm execution)
    didn't break anything -- not a re-derivation of the synthetic validation results."""
    spike_reviews = [
        AnnotatedReview(
            review_id=f"spike-{i}",
            product_id="Widget Pro",
            reviewer_id="",
            timestamp=_NOW + timedelta(hours=i * 6),
            rating=0,
            topics=["battery"],
            sentiment="negative",
            urgency="",
        )
        for i in range(6)
    ]

    flags = scan_batch_defects(spike_reviews)

    assert len(flags) == 1
    flag = flags[0]
    assert flag.product_id == "Widget Pro"
    assert flag.topic == "battery"
    assert flag.confidence > 0.0
    assert flag.evidence["window_count"] == 6


def test_scan_batch_defects_no_flags_on_steady_baseline() -> None:
    """A steady trickle of the same topic over a long period, well below spike thresholds,
    must not be flagged -- confirms the port didn't accidentally loosen any gate."""
    steady_reviews = [
        AnnotatedReview(
            review_id=f"steady-{i}",
            product_id="Widget Pro",
            reviewer_id="",
            timestamp=_NOW + timedelta(days=i * 20),
            rating=0,
            topics=["battery"],
            sentiment="negative",
            urgency="",
        )
        for i in range(4)
    ]

    flags = scan_batch_defects(steady_reviews)

    assert flags == []


def test_flag_confidence_is_not_rounded_before_threshold_comparisons() -> None:
    """S17 X5b: the flag used to carry round(confidence, 3), so an exact 0.69996 (below the 0.7
    alert threshold and any ?min_confidence=0.7 filter) compared as 0.7 and passed. The dataclass
    keeps full precision; only the serialised dict is rounded for display."""
    from unittest.mock import patch

    from app.core.detectors import batch_defect as bd

    reviews = [
        AnnotatedReview(
            review_id=f"s-{i}",
            product_id="Widget Pro",
            reviewer_id="",
            timestamp=_NOW + timedelta(hours=i * 6),
            rating=0,
            topics=["battery"],
            sentiment="negative",
            urgency="",
        )
        for i in range(6)
    ]
    ratio = bd.SPIKE_RATIO_THRESHOLD + 0.69996 * (bd.SATURATION_RATIO - bd.SPIKE_RATIO_THRESHOLD)
    best = {
        "window_start": _NOW,
        "window_end": _NOW + timedelta(days=bd.WINDOW_DAYS),
        "window_count": 6,
        "baseline_rate_per_day": 0.1,
        "expected_count": 1.0,
        "ratio": ratio,
        "outside_count": 0,
        "review_ids": ["s-0"],
    }
    with patch.object(bd, "_best_window_for_topic", return_value=best):
        (flag,) = scan_batch_defects(reviews)

    assert flag.confidence < 0.7
    assert flag.to_dict()["confidence"] == 0.7  # display rounding unchanged
