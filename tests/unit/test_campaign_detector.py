"""Unit tests for app.core.detectors.campaign -- the production fork.

Not a re-run of the synthetic stress-test validation (already proven in
benchmark/phase2_synthetic/detectors/, out of scope here). These tests only prove: (1) the
Postgres-row adapter maps fields correctly and confirms the reviewer_id stub's documented
effect, (2) the ported algorithm still runs end-to-end after the port.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from app.core.detectors.campaign import campaign_reviews_from_rows, scan_corpus

_NOW = datetime(2026, 6, 1, tzinfo=UTC)


def test_campaign_reviews_from_rows_maps_fields() -> None:
    rows = [
        {
            "id": "abc123",
            "product": "Widget Pro",
            "review_text": "Great product, highly recommend",
            "review_date": _NOW,
        }
    ]

    result = campaign_reviews_from_rows(rows)

    assert len(result) == 1
    review = result[0]
    assert review.review_id == "abc123"
    assert review.product_id == "Widget Pro"
    assert review.timestamp == _NOW
    assert review.text == "Great product, highly recommend"
    # The documented stub: reviewer_id = the review's own id, guaranteed unique per review.
    assert review.reviewer_id == "abc123"


def test_campaign_reviews_from_rows_handles_missing_product_and_text() -> None:
    rows = [{"id": "x", "product": None, "review_text": None, "review_date": _NOW}]

    result = campaign_reviews_from_rows(rows)

    assert result[0].product_id == "unknown product"
    assert result[0].text == ""


def test_reviewer_id_stub_forces_reviewer_concentration_to_zero() -> None:
    """Confirms the documented mechanism directly: even 10 reviews landing in the exact same
    burst window score reviewer_concentration_score=0.0, because campaign_reviews_from_rows
    stubs a unique reviewer_id per review -- this is a pure recall reduction, never a
    false-positive risk (a real coordinated-reviewer-identity campaign would be UNDER-detected,
    not falsely flagged)."""
    rows = [
        {
            "id": f"r{i}",
            "product": "Widget",
            "review_text": f"unique organic text number {i} about this product",
            "review_date": _NOW + timedelta(hours=i),
        }
        for i in range(10)
    ]

    reviews = campaign_reviews_from_rows(rows)
    flags = scan_corpus(reviews)

    # Distinct, non-duplicate text + stubbed-unique reviewer_id -> no signal on either axis ->
    # correctly not flagged at all (this specific case), proving the stub doesn't manufacture
    # a false concentration signal.
    assert flags == []


def test_scan_corpus_smoke_after_port_catches_text_based_coordination() -> None:
    """Hand-built burst: many reviews, unique-per-review reviewer_id (as production will
    always provide), but near-identical long TEXT within a tight timing window. Proves the
    port (import path, algorithm execution) still catches text-based coordination -- the one
    signal that survives the reviewer_id stub -- not a re-derivation of the stress-test numbers.
    """
    random.seed(11)
    base = _NOW - timedelta(days=60)
    rows = [
        {
            "id": f"base-{i}",
            "product": "Widget",
            "review_text": f"baseline unique review number {i} about the widget",
            "review_date": base + timedelta(days=random.uniform(0, 50)),
        }
        for i in range(15)
    ]
    template = (
        "This product exceeded my expectations completely and arrived very quickly in "
        "perfect condition"
    )
    for i in range(8):
        rows.append(
            {
                "id": f"burst-{i}",
                "product": "Widget",
                "review_text": template,
                "review_date": _NOW + timedelta(hours=i * 2),
            }
        )

    flags = scan_corpus(campaign_reviews_from_rows(rows))

    assert len(flags) == 1
    flag = flags[0]
    assert flag.product_id == "Widget"
    assert flag.confidence > 0.0
    assert flag.evidence["reviewer_concentration_score"] == 0.0, (
        "reviewer signal must be exactly 0 given the stub -- text_dup_score is the only "
        "possible driver"
    )
    assert flag.evidence["text_dup_score"] > 0.0
