"""Batch-defect (bad manufacturing run) detector -- PRODUCTION FORK.

PRODUCTION FORK of benchmark/phase2_synthetic/detectors/batch_defect.py. Detection logic
(constants, `_is_negative_mention`, `_topics_for_product`, `_window_starts`,
`_best_window_for_topic`, `scan_batch_defects`) is ported unchanged from the synthetic-validated
original -- see that file for the validation methodology and precision/recall findings against
the planted-ground-truth testbed and the later stress test. This fork adds only
`annotated_reviews_from_rows` (a Postgres-row adapter, replacing the benchmark original's
jsonl-file loading path) and drops the file-based CLI plumbing (`write_report`, `main`), which
has no production role. Changes to detection logic made here do not propagate back to the
benchmark copy and vice versa -- keep both docstrings' cross-reference current if the algorithm
changes on either side.

SYNTHETIC-VALIDATED DETECTOR -- proven against a synthetic testbed with PLANTED, KNOWN ground
truth. NOT proven against real seller data -- planted patterns may not match how real batch
defects actually look in production. Do not quote synthetic precision/recall as real-world
accuracy. Gated behind settings.enable_batch_defect_detector (off by default) precisely because
of this — see app/api/v2/insights.py's batch-defects endpoint for the production entry point.

This is a MODERATION-PRIORITIZATION SIGNAL, not a verdict -- it mirrors review-iq's existing
authenticity feature framing: a confidence score plus concrete evidence for a human moderator to
review, never an automated "this product has a defect" label.

What it looks for: a product where the same specific failure TOPIC (e.g. "battery", "screen")
suddenly clusters in a short time window, well above that product's own normal steady-state rate
of complaints about that topic -- as opposed to a product's normal baseline complaint rate. This
is general-purpose: it scans every product for every topic that appears in its reviews, with no
hardcoded product IDs, topic names, or spike windows.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.core.detectors.common import AnnotatedReview

# A (product, topic) pair needs at least this many negative/mixed mentions of the topic in
# total (anywhere in the product's history) before it's even considered for windowing -- below
# this, any "spike" is too small a sample to distinguish from noise.
MIN_TOTAL_MENTIONS = 4

# Deliberately NOT exactly 9 (the width of the planted spike windows in the synthetic testbed)
# -- proves the algorithm generalizes to a window width it wasn't tuned against, rather than
# overfitting to the planted pattern.
WINDOW_DAYS = 10

# The window must contain at least this many negative mentions in absolute terms, on top of
# clearing the ratio threshold below -- guards against a product with almost no baseline
# mentions (e.g. 1 outside the window) turning a tiny count like 2 into an inflated ratio.
MIN_ABSOLUTE_SPIKE_COUNT = 4

# Window rate must be at least this many times the product's own baseline rate for that topic
# to count as a spike.
SPIKE_RATIO_THRESHOLD = 3.0

# A ratio at or above this saturates the confidence score at 1.0 -- same saturating-ratio
# style as the campaign detector's enrichment scoring, so an extreme outlier doesn't need an
# unbounded score to be recognized as maximally anomalous.
SATURATION_RATIO = 10.0

# Floor on the expected (baseline-predicted) count for a window, to avoid a near-zero baseline
# rate producing a division blowup (e.g. baseline rate 0 would otherwise make any single
# mention "infinitely" anomalous).
EXPECTED_COUNT_FLOOR = 0.3

# Floor on "days outside the window" when computing the baseline rate, to avoid division by
# zero on a product whose entire observed range is shorter than one window.
MIN_OUTSIDE_DAYS = 0.5

# The candidate spike window's topic must ALSO be nearly SILENT everywhere else in the product's
# observed history -- not just quiet in the one window right before it. See the benchmark
# original's docstring (benchmark/phase2_synthetic/detectors/batch_defect.py) for the full
# measured-margin justification of this threshold.
MAX_TOTAL_OUTSIDE_COUNT = 3


@dataclass(frozen=True)
class BatchDefectFlag:
    """One flagged (product, topic) batch-defect candidate, with human-reviewable evidence."""

    product_id: str
    topic: str
    confidence: float
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Flatten to the JSON output record shape."""
        return {
            "product_id": self.product_id,
            "topic": self.topic,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }


def _is_negative_mention(review: AnnotatedReview, topic: str) -> bool:
    """A review counts as a negative mention of `topic` if the topic is in its topics list and
    its sentiment is negative or mixed."""
    return topic in review.topics and review.sentiment in ("negative", "mixed")


def _topics_for_product(reviews: list[AnnotatedReview]) -> set[str]:
    """All distinct topic strings appearing in any review's topics list for one product."""
    topics: set[str] = set()
    for review in reviews:
        topics.update(review.topics)
    return topics


def _window_starts(earliest: datetime, latest: datetime) -> list[datetime]:
    """Candidate window start positions: `earliest`, stepping by 1 day, up to and including
    `latest` (a window starting on the last observed day still overlaps observed data)."""
    starts: list[datetime] = []
    current = earliest
    while current <= latest:
        starts.append(current)
        current += timedelta(days=1)
    return starts


def _best_window_for_topic(
    negative_mentions: list[AnnotatedReview],
    earliest: datetime,
    latest: datetime,
) -> dict[str, Any] | None:
    """Slide a WINDOW_DAYS window across the product's observed date range and return the
    single highest-ratio window for this topic, or None if there are no candidate windows
    (e.g. the observed range collapses to a single instant)."""
    total_observed_days = max((latest - earliest).total_seconds() / 86400.0, 0.0)
    total_negative = len(negative_mentions)

    best: dict[str, Any] | None = None
    for window_start in _window_starts(earliest, latest):
        window_end = window_start + timedelta(days=WINDOW_DAYS)
        in_window = [m for m in negative_mentions if window_start <= m.timestamp < window_end]
        window_count = len(in_window)

        # How much of the observed range does this window itself occupy? Clipped to
        # [earliest, latest] since the window can extend past the last observed review.
        overlap_start = max(window_start, earliest)
        overlap_end = min(window_end, latest)
        overlap_days = max((overlap_end - overlap_start).total_seconds() / 86400.0, 0.0)
        outside_days = max(total_observed_days - overlap_days, MIN_OUTSIDE_DAYS)

        # Excluding baseline: mentions outside this candidate window, not a global average that
        # includes the spike itself -- otherwise the spike would inflate its own comparison
        # point and understate how anomalous it is.
        outside_count = total_negative - window_count
        baseline_rate_per_day = outside_count / outside_days
        expected_count = max(baseline_rate_per_day * WINDOW_DAYS, EXPECTED_COUNT_FLOOR)
        ratio = window_count / expected_count

        # Silence-elsewhere gate: the topic must be nearly silent everywhere OUTSIDE this
        # window, not just relative to a diluted average rate -- see MAX_TOTAL_OUTSIDE_COUNT's
        # comment for why this replaced an earlier, more position-sensitive local-contrast check.
        silence_elsewhere_ok = outside_count <= MAX_TOTAL_OUTSIDE_COUNT

        if silence_elsewhere_ok and (best is None or ratio > best["ratio"]):
            best = {
                "window_start": window_start,
                "window_end": window_end,
                "window_count": window_count,
                "baseline_rate_per_day": baseline_rate_per_day,
                "expected_count": expected_count,
                "ratio": ratio,
                "outside_count": outside_count,
                "review_ids": [m.review_id for m in in_window],
            }
    return best


def scan_batch_defects(reviews: list[AnnotatedReview]) -> list[BatchDefectFlag]:
    """Scan all products for batch-defect topic spikes and return the flagged (product, topic)
    candidates, sorted by confidence descending. General-purpose: no product IDs or topic names
    are hardcoded, every product/topic combination is scanned identically."""
    by_product: dict[str, list[AnnotatedReview]] = defaultdict(list)
    for review in reviews:
        by_product[review.product_id].append(review)

    flags: list[BatchDefectFlag] = []
    for product_id, product_reviews in by_product.items():
        if not product_reviews:
            continue
        earliest = min(r.timestamp for r in product_reviews)
        latest = max(r.timestamp for r in product_reviews)

        for topic in sorted(_topics_for_product(product_reviews)):
            negative_mentions = [m for m in product_reviews if _is_negative_mention(m, topic)]
            if len(negative_mentions) < MIN_TOTAL_MENTIONS:
                continue

            best = _best_window_for_topic(negative_mentions, earliest, latest)
            if best is None:
                continue
            if best["window_count"] < MIN_ABSOLUTE_SPIKE_COUNT:
                continue
            if best["ratio"] < SPIKE_RATIO_THRESHOLD:
                continue

            confidence = min(
                1.0,
                max(
                    0.0,
                    (best["ratio"] - SPIKE_RATIO_THRESHOLD)
                    / (SATURATION_RATIO - SPIKE_RATIO_THRESHOLD),
                ),
            )
            evidence = {
                "window_start": best["window_start"].isoformat(),
                "window_end": best["window_end"].isoformat(),
                "window_days": WINDOW_DAYS,
                "window_count": best["window_count"],
                "baseline_rate_per_day": round(best["baseline_rate_per_day"], 4),
                "expected_count": round(best["expected_count"], 2),
                "ratio_vs_baseline": round(best["ratio"], 2),
                "outside_count": best["outside_count"],
                "review_ids": best["review_ids"],
            }
            flags.append(
                BatchDefectFlag(
                    product_id=product_id,
                    topic=topic,
                    confidence=round(confidence, 3),
                    evidence=evidence,
                )
            )

    flags.sort(key=lambda f: f.confidence, reverse=True)
    return flags


def annotated_reviews_from_rows(rows: list[dict[str, Any]]) -> list[AnnotatedReview]:
    """Adapt app.core.storage_pg.list_dated_extractions_pg's row dicts into AnnotatedReview
    objects -- production's replacement for the benchmark original's jsonl-file loading path.

    `reviewer_id`/`rating`/`urgency` are stubbed ("" / 0 / "") -- confirmed by direct inspection
    that `scan_batch_defects` and its helpers never read these fields (only the benchmark
    original's dropped file-loading helper touched them). `extractions` has no reviewer-identity
    column at all, so there is no honest value to populate for `reviewer_id` even if the
    algorithm wanted one.

    Each row must have `id`, `product`, `topics`, `sentiment`, `review_date` keys (see
    list_dated_extractions_pg's return shape).
    """
    return [
        AnnotatedReview(
            review_id=row["id"],
            product_id=row["product"] or "unknown product",
            reviewer_id="",
            timestamp=row["review_date"],
            rating=0,
            topics=row["topics"],
            sentiment=row["sentiment"] or "",
            urgency="",
        )
        for row in rows
    ]
