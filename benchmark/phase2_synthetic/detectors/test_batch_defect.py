"""Unit tests for batch_defect.py's spike-detection algorithm.

Uses hand-built `AnnotatedReview` fixtures with fixed dates (no randomness) so results are
deterministic and the spike/baseline shapes are known exactly, rather than relying on the
synthetic testbed files (validated separately in validate_interim.py).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from batch_defect import (
    MIN_TOTAL_MENTIONS,
    SPIKE_RATIO_THRESHOLD,
    BatchDefectFlag,
    scan_batch_defects,
)
from common import AnnotatedReview

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def _review(
    review_id: str,
    product_id: str,
    day_offset: int,
    topics: list[str],
    sentiment: str = "negative",
    rating: int = 2,
) -> AnnotatedReview:
    """Build one AnnotatedReview at EPOCH + day_offset days."""
    return AnnotatedReview(
        review_id=review_id,
        product_id=product_id,
        reviewer_id=f"rev-{review_id}",
        timestamp=EPOCH + timedelta(days=day_offset),
        rating=rating,
        topics=topics,
        sentiment=sentiment,
        urgency="medium",
    )


def _flag_for(flags: list[BatchDefectFlag], product_id: str, topic: str) -> BatchDefectFlag | None:
    for flag in flags:
        if flag.product_id == product_id and flag.topic == topic:
            return flag
    return None


def test_flags_clustered_spike_against_sparse_baseline() -> None:
    """A topic with a couple of scattered baseline mentions plus a tight burst of 6 in a
    10-day window should be flagged, with the burst's own review_ids in the evidence.

    Baseline is 2 scattered mentions (not, e.g., 10) -- MAX_TOTAL_OUTSIDE_COUNT requires the
    topic to be nearly silent outside the spike window (measured from real data: true positives
    have 0-1 total mentions outside their window, trend-pattern products have 11-18). A denser
    "sparse" baseline than that is genuinely ambiguous under the real threshold, not a case this
    detector is meant to catch -- see MAX_TOTAL_OUTSIDE_COUNT's comment in batch_defect.py.
    """
    reviews = []
    # Sparse baseline: 2 negative "battery" mentions, well outside the burst window.
    for i, day in enumerate([10, 140]):
        reviews.append(_review(f"base-{i}", "P-SPIKE", day, ["battery"]))
    # Tight burst: 6 negative "battery" mentions within a 5-day span around day 80.
    burst_ids = []
    for i, day in enumerate([80, 81, 82, 83, 84, 84]):
        rid = f"burst-{i}"
        burst_ids.append(rid)
        reviews.append(_review(rid, "P-SPIKE", day, ["battery"]))
    # Unrelated positive noise shouldn't affect the count.
    reviews.append(_review("pos-1", "P-SPIKE", 40, ["battery"], sentiment="positive", rating=5))

    flags = scan_batch_defects(reviews)
    flag = _flag_for(flags, "P-SPIKE", "battery")

    assert flag is not None
    assert flag.evidence["ratio_vs_baseline"] >= SPIKE_RATIO_THRESHOLD
    assert 0.0 < flag.confidence <= 1.0
    assert set(burst_ids).issubset(set(flag.evidence["review_ids"]))


def test_does_not_flag_dense_tail_of_a_continuous_trend() -> None:
    """A topic active continuously across the whole observed range, with gradually shrinking
    gaps between mentions (a rising-trend shape), must NOT be flagged even though its densest
    late-window sub-period alone would clear the ratio/count thresholds.

    Regression pin for the real false positive found during validation on the synthetic
    testbed (SYN-TREND-01): the topic was never silent, so no window position should pass the
    MAX_TOTAL_OUTSIDE_COUNT gate. Mirrors the real shape found by direct inspection of the
    failing case's mention timestamps -- mentions throughout months 1-4, several landing close
    together near the end purely by chance.
    """
    reviews = [
        _review(f"trend-{i}", "P-TREND-TAIL", day, ["delivery"])
        for i, day in enumerate(
            [10, 25, 40, 50, 65, 70, 75, 80, 85, 90, 91, 91, 91, 92, 95, 105, 108]
        )
    ]

    flags = scan_batch_defects(reviews)

    assert _flag_for(flags, "P-TREND-TAIL", "delivery") is None


def test_does_not_flag_evenly_scattered_topic() -> None:
    """The same total mention count spread evenly across the observed range (no clustering)
    must not be flagged -- this is the matched-control shape."""
    reviews = [
        _review(f"scatter-{i}", "P-CONTROL", day, ["battery"])
        for i, day in enumerate(range(0, 121, 15))  # 9 mentions, evenly spaced
    ]

    flags = scan_batch_defects(reviews)

    assert _flag_for(flags, "P-CONTROL", "battery") is None


def test_does_not_flag_below_min_total_mentions() -> None:
    """Even a tight cluster is ignored if the topic has fewer than MIN_TOTAL_MENTIONS mentions
    in total -- too small a sample to distinguish from noise."""
    assert MIN_TOTAL_MENTIONS >= 2  # sanity: test below assumes this constant is > 2
    reviews = [
        _review(f"tiny-{i}", "P-TINY", day, ["battery"])
        for i, day in enumerate([10, 11, 12])  # below MIN_TOTAL_MENTIONS (default 4)
    ]

    flags = scan_batch_defects(reviews)

    assert _flag_for(flags, "P-TINY", "battery") is None


def test_only_negative_or_mixed_sentiment_counts_as_mention() -> None:
    """Positive/neutral mentions of a topic don't contribute to the spike count, even if
    clustered in time."""
    reviews = [
        _review(f"pos-{i}", "P-POSITIVE-CLUSTER", 80 + i, ["battery"], sentiment="positive", rating=5)
        for i in range(6)
    ]

    flags = scan_batch_defects(reviews)

    assert _flag_for(flags, "P-POSITIVE-CLUSTER", "battery") is None


def test_mixed_sentiment_counts_as_negative_mention() -> None:
    """'mixed' sentiment mentions count toward the spike, same as 'negative'."""
    reviews = []
    for i, day in enumerate([10, 140]):
        reviews.append(_review(f"base-{i}", "P-MIXED", day, ["battery"]))
    for i, day in enumerate([80, 81, 82, 83, 84, 84]):
        reviews.append(
            _review(f"burst-{i}", "P-MIXED", day, ["battery"], sentiment="mixed", rating=3)
        )

    flags = scan_batch_defects(reviews)

    assert _flag_for(flags, "P-MIXED", "battery") is not None


def test_review_can_contribute_to_multiple_topics() -> None:
    """A single review's topics list can trigger spikes on more than one topic independently."""
    reviews = []
    for i, day in enumerate([10, 140]):
        reviews.append(_review(f"base-battery-{i}", "P-MULTI", day, ["battery"]))
        reviews.append(_review(f"base-screen-{i}", "P-MULTI", day, ["screen"]))
    for i, day in enumerate([80, 81, 82, 83, 84, 84]):
        reviews.append(_review(f"burst-{i}", "P-MULTI", day, ["battery", "screen"]))

    flags = scan_batch_defects(reviews)

    assert _flag_for(flags, "P-MULTI", "battery") is not None
    assert _flag_for(flags, "P-MULTI", "screen") is not None


def test_empty_input_returns_no_flags() -> None:
    """Hostile/boundary input: an empty review list must not raise."""
    assert scan_batch_defects([]) == []


@pytest.mark.parametrize("bad_topics", [[], None])
def test_review_with_no_topics_is_ignored(bad_topics: list[str] | None) -> None:
    """A review with no topics contributes to no (product, topic) pair and cannot crash the
    topic-collection step."""
    reviews = [
        AnnotatedReview(
            review_id="r1",
            product_id="P-NO-TOPICS",
            reviewer_id="rev-1",
            timestamp=EPOCH,
            rating=1,
            topics=bad_topics or [],
            sentiment="negative",
            urgency="high",
        )
    ]

    assert scan_batch_defects(reviews) == []
