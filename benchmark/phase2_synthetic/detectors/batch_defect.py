"""Batch-defect (bad manufacturing run) detector.

SYNTHETIC-VALIDATED DETECTOR -- this code's logic is proven against a synthetic testbed with
PLANTED, KNOWN ground truth (benchmark/phase2_synthetic/). It is NOT proven against real seller
data -- planted patterns may not match how real batch defects actually look in production. Do
not quote synthetic precision/recall as real-world accuracy. Ready to run against live tenant
data, not yet validated against it.

This is a MODERATION-PRIORITIZATION SIGNAL, not a verdict -- it mirrors review-iq's existing
authenticity feature framing and the Phase 2 fake-campaign detector
(benchmark/phase2_campaign/score.py): a confidence score plus concrete evidence for a human
moderator to review, never an automated "this product has a defect" label.

What it looks for: a product where the same specific failure TOPIC (e.g. "battery", "screen")
suddenly clusters in a short time window, well above that product's own normal steady-state rate
of complaints about that topic -- as opposed to a product's normal baseline complaint rate. This
is general-purpose: it scans every product for every topic that appears in its reviews, with no
hardcoded product IDs, topic names, or spike windows.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from common import AnnotatedReview, load_extractions, to_annotated

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

# The candidate window's count must ALSO be a jump relative to the WINDOW_DAYS-wide period
# immediately preceding it, not just the all-history average. Found via validation, after
# fixing the trend detector's synthetic data to source genuine complaints: the all-history-
# average baseline alone flagged the tail of a genuinely RISING complaint trend as a "spike",
# because a smooth ramp's later (denser) phase looks anomalous against its own diluted early-
# phase average. A real batch defect is a discontinuity -- the period right before it should
# look like normal baseline, not already-elevated. A rising trend's immediately-preceding
# window is itself elevated (it's mid-ramp), so this local contrast check rejects it while
# leaving genuine step-change spikes (baseline near zero right up until the spike) untouched.
# Threshold set from measured separation, not guessed: the two planted batch-defect spikes
# have a preceding-window count of 0 (margin 16.0x against the floor) -- a clean discontinuity
# -- while a rising-trend false positive found during validation (SYN-TREND-01's final phase,
# where 5 of 8 reviews randomly clumped within ~34 hours by chance) only reaches margin 3.0x,
# since its preceding window is already elevated mid-ramp. 5.0 sits with headroom on both
# sides (3.1x below the true positives, 1.9x above the false positive), not tuned to force one
# specific case.
LOCAL_CONTRAST_RATIO = 5.0
LOCAL_CONTRAST_FLOOR = 0.5


@dataclass(frozen=True)
class BatchDefectFlag:
    """One flagged (product, topic) batch-defect candidate, with human-reviewable evidence."""

    product_id: str
    topic: str
    confidence: float
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Flatten to the JSONL output record shape."""
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

        # Local before/after contrast: the window must also jump relative to the period right
        # before it, not just the all-history average -- see LOCAL_CONTRAST_RATIO's comment.
        preceding_start = window_start - timedelta(days=WINDOW_DAYS)
        preceding_count = sum(
            1 for m in negative_mentions if preceding_start <= m.timestamp < window_start
        )
        local_contrast_ok = window_count >= LOCAL_CONTRAST_RATIO * max(
            preceding_count, LOCAL_CONTRAST_FLOOR
        )

        if local_contrast_ok and (best is None or ratio > best["ratio"]):
            best = {
                "window_start": window_start,
                "window_end": window_end,
                "window_count": window_count,
                "baseline_rate_per_day": baseline_rate_per_day,
                "expected_count": expected_count,
                "ratio": ratio,
                "preceding_window_count": preceding_count,
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
                "preceding_window_count": best["preceding_window_count"],
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


def write_report(flags: list[BatchDefectFlag], path: Path) -> None:
    """Write a human-readable markdown report of all flagged (product, topic) candidates."""
    lines = [
        "# Phase 2 Batch-Defect Detector -- Flagged Products",
        "",
        "> SYNTHETIC-VALIDATED DETECTOR -- see batch_defect.py module docstring. Moderation-"
        "prioritization signal only, never a verdict.",
        "",
        f"Total flagged (product, topic) pairs: {len(flags)}",
        "",
    ]
    for i, flag in enumerate(flags, start=1):
        ev = flag.evidence
        lines.append(f"## {i}. confidence={flag.confidence} -- {flag.product_id} / {flag.topic}")
        lines.append("")
        lines.append(
            f"- {ev['window_count']} reviews in {ev['window_days']} days cite '{flag.topic}' "
            f"failure, {ev['ratio_vs_baseline']}x baseline"
        )
        lines.append(f"- window: {ev['window_start']} to {ev['window_end']}")
        lines.append(
            f"- baseline_rate_per_day: {ev['baseline_rate_per_day']}, "
            f"expected_count: {ev['expected_count']}"
        )
        lines.append(f"- review_ids: {ev['review_ids']}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    """Load `extractions.jsonl`, scan for batch-defect spikes, and write the flags JSONL + report.

    Exits cleanly (no traceback) if `extractions.jsonl` does not exist yet -- it is produced by
    a separate, long-running background extraction process.
    """
    extractions_path = Path(__file__).resolve().parents[1] / "extractions.jsonl"
    if not extractions_path.exists():
        print(f"{extractions_path} does not exist yet -- nothing to scan. Exiting cleanly.")
        return

    records = load_extractions(extractions_path)
    reviews = [to_annotated(r) for r in records]
    flags = scan_batch_defects(reviews)

    out_dir = Path(__file__).resolve().parent
    jsonl_path = out_dir / "batch_defect_flags.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for flag in flags:
            f.write(json.dumps(flag.to_dict(), ensure_ascii=False) + "\n")

    report_path = out_dir / "BATCH_DEFECT_REPORT.md"
    write_report(flags, report_path)

    print(f"loaded {len(reviews)} annotated reviews (of {len(records)} extraction records)")
    print(f"flagged {len(flags)} (product, topic) pairs")
    print(f"wrote {jsonl_path}")
    print(f"wrote {report_path}")


if __name__ == "__main__":
    main()
