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
