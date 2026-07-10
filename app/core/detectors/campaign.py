"""Fake-campaign (coordinated review cluster) detector -- PRODUCTION FORK.

PRODUCTION FORK of benchmark/phase2_synthetic/detectors/campaign_synthetic.py. Detection logic
(constants, `_token_set`, `_near_dup_score`, `_cross_product_score`,
`_timing_concentration_score`, `find_best_burst_window`, `score_burst_window`, `scan_product`,
`scan_corpus`) is ported unchanged from the synthetic-validated, stress-tested original -- see
that file for the validation methodology and precision/recall findings. This fork adds only
`campaign_reviews_from_rows` (a Postgres-row adapter) and drops the jsonl-file CLI plumbing
(`load_reviews`, `write_flags`, `ValidationResult`, `validate_against_ground_truth`,
`render_report`, `main`), which has no production role. Changes to detection logic made here do
not propagate back to the benchmark copy and vice versa.

`normalize_cluster_text` is INLINED directly (not imported via the benchmark original's sys.path
hack into benchmark/phase2_campaign/artifact_filter.py) -- that module also imports
`canonical_product` from a sibling `canonicalize.py`, a dependency campaign_synthetic.py's code
path never actually calls. Inlining the two-regex normalize function avoids dragging in an unused
transitive dependency and the fragile sys.path pattern entirely.

SYNTHETIC-VALIDATED DETECTOR -- proven against a synthetic testbed with PLANTED, KNOWN ground
truth, including a stress test with genuinely unseen patterns. NOT proven against real seller
data. Gated behind settings.enable_fake_campaign_detector (off by default).

ACCEPTED LIMITATION (stated prominently, not papered over): `reviewer_id` is stubbed to each
review's own ID in `campaign_reviews_from_rows` (see that function's docstring) because
`extractions` has no reviewer-identity column yet -- no ingestion path captures it. This forces
BOTH `reviewer_concentration_score` AND `cross_product_score` to always be 0 (traced: distinct
reviewer count always equals window size; every stub ID maps to exactly one product forever), so
confidence can only come from `text_dup_score` (exact/near-duplicate review TEXT within a timing
burst -- a real, available signal since review_text is genuinely stored). This is a pure RECALL
reduction, never a false-positive risk: forcing a signal to 0 can only prevent flags, never
manufacture one. Real reviewer-identity-based coordination (the same accounts posting repeatedly
with genuinely different text) cannot be caught until reviewer-identity ingestion plumbing exists
(same shape as the review_date plumbing that unblocked batch-defect).

This is a MODERATION-PRIORITIZATION SIGNAL, not a verdict -- a confidence score plus concrete
evidence for a human moderator to review, never an automated "this is a fake campaign" label.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

# Same aggressive normalization used in benchmark/phase2_campaign/artifact_filter.py -- inlined,
# see module docstring for why.
_PUNCT = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")


def normalize_cluster_text(text: str) -> str:
    """Aggressively normalize review text for near-duplicate clustering: strip punctuation,
    lowercase, collapse whitespace."""
    stripped = _PUNCT.sub("", text)
    collapsed = _WHITESPACE.sub(" ", stripped)
    return collapsed.strip().lower()


# Burst window width. Chosen to match the spec default and the planted campaigns' shape
# (burst_hours 30-42 in ground truth) while staying short enough that a genuinely organic
# 16-week-long review history can't accidentally accumulate a "burst"-sized cluster.
BURST_HOURS = 48
# A burst window must contain at least this many reviews to be structurally interesting --
# below this, even a large ratio-vs-baseline is too small a sample to act on.
MIN_BURST_COUNT = 5
# A burst window's review count must be at least this many times the product's expected
# baseline count for a window of that width to be flagged as anomalous.
BURST_RATIO_THRESHOLD = 4.0
# Ratio value at which the timing signal alone is treated as "fully saturated" for the
# confidence formula -- ratios beyond this add no further confidence from timing alone.
BURST_SATURATION = 10.0
# A burst window existing (the gate above) is necessary but NOT sufficient to report a flag --
# confidence must also clear this bar. See score_burst_window's docstring for the false-positive
# history this threshold fixes (batch-defect confound).
CONFIDENCE_REPORT_THRESHOLD = 0.2

# Near-identical (not just identical) phrasing only counts as a duplication signal when the text
# is long/specific enough that independent authors converging on it by chance is implausible.
# Short organic praise ("great product!", "really great product") has high token overlap between
# genuinely unrelated reviewers -- gating on length keeps this signal from firing on that.
MIN_WORDS_FOR_NEAR_DUP = 8
# Jaccard token-overlap threshold for two (long-enough) reviews to count as the same near-dup
# cluster -- e.g. a templated review with a swapped noun or adjective.
NEAR_DUP_JACCARD_THRESHOLD = 0.85

# Cross-product reviewer reuse is an AMPLIFIER only -- it can boost an already-nonzero
# reviewer/text concentration signal, but is gated to never manufacture a flag on its own (see
# score_burst_window). Weight controls how much of the remaining headroom to 1.0 it can close.
CROSS_PRODUCT_BOOST_WEIGHT = 0.5

# Number of equal-width sub-buckets a burst window is split into for the evidence-only timing
# concentration signal (see score_burst_window's docstring for why it doesn't drive confidence).
TIMING_CONCENTRATION_BUCKETS = 8


@dataclass(frozen=True)
class Review:
    """One review normalized into the fields this detector needs."""

    review_id: str
    product_id: str
    reviewer_id: str
    timestamp: datetime
    text: str


@dataclass
class BurstWindow:
    """The highest-CONFIDENCE BURST_HOURS-wide window found for one product, if any -- see
    find_best_burst_window's docstring for why confidence, not ratio, is the selection key."""

    start: datetime
    end: datetime
    reviews: list[Review]
    ratio_vs_baseline: float


@dataclass
class CampaignFlag:
    """One flagged product with its combined confidence score and human-readable evidence."""

    product_id: str
    confidence: float
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Flatten to the JSON output record shape."""
        return {
            "product_id": self.product_id,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }


def _token_set(text: str) -> frozenset[str]:
    """Normalized whitespace-token set of a review's text, for Jaccard near-dup comparison."""
    return frozenset(normalize_cluster_text(text).split())


def _near_dup_score(reviews: list[Review]) -> float:
    """Fraction of long-enough reviews that fall into a near-duplicate (Jaccard-clustered) group,
    beyond exact-text duplication -- catches templated campaigns with minor word substitutions
    that `normalize_cluster_text` alone treats as distinct. Gated to MIN_WORDS_FOR_NEAR_DUP+ words
    (see that constant) so short organic praise can't trigger it via incidental token overlap.

    Clustering is union-find over pairwise Jaccard >= NEAR_DUP_JACCARD_THRESHOLD -- cheap at the
    small (<=few dozen reviews per window) scale this runs at.
    """
    long_reviews = [r for r in reviews if len(r.text.split()) >= MIN_WORDS_FOR_NEAR_DUP]
    if len(long_reviews) < 2:
        return 0.0

    token_sets = [_token_set(r.text) for r in long_reviews]
    parent = list(range(len(long_reviews)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(token_sets)):
        for j in range(i + 1, len(token_sets)):
            a, b = token_sets[i], token_sets[j]
            if not a or not b:
                continue
            jaccard = len(a & b) / len(a | b)
            if jaccard >= NEAR_DUP_JACCARD_THRESHOLD:
                root_i, root_j = find(i), find(j)
                if root_i != root_j:
                    parent[root_i] = root_j

    n_clusters = len({find(i) for i in range(len(token_sets))})
    return 1.0 - (n_clusters / len(long_reviews))


def _cross_product_score(window_reviews: list[Review], reviewer_products: dict[str, set[str]]) -> float:
    """Fraction of a burst window's DISTINCT reviewers who also appear elsewhere in the corpus
    under a different product_id -- a coordination-farm signature (the same accounts posting
    across multiple sellers' burst windows) that organic popularity has no reason to produce.
    `reviewer_products` is corpus-wide: reviewer_id -> set of every product_id they reviewed.
    """
    distinct_reviewer_ids = {r.reviewer_id for r in window_reviews}
    if not distinct_reviewer_ids:
        return 0.0
    reused = sum(1 for rid in distinct_reviewer_ids if len(reviewer_products.get(rid, ())) > 1)
    return reused / len(distinct_reviewer_ids)


def _timing_concentration_score(window: BurstWindow) -> float:
    """Evidence-only (does NOT drive confidence): the largest fraction of a burst window's
    reviews landing in any single one of TIMING_CONCENTRATION_BUCKETS equal-width sub-buckets.
    High values mean the burst executed in a much tighter timeframe than the full window -- but a
    genuine batch-defect can also cluster tightly by chance, so this cannot be allowed to drive
    confidence without resurrecting the batch-defect confound this detector was already fixed
    against (see score_burst_window's docstring)."""
    if not window.reviews:
        return 0.0
    bucket_width = (window.end - window.start) / TIMING_CONCENTRATION_BUCKETS
    bucket_seconds = bucket_width.total_seconds()
    if bucket_seconds <= 0:
        return 1.0
    counts = [0] * TIMING_CONCENTRATION_BUCKETS
    for r in window.reviews:
        idx = int((r.timestamp - window.start).total_seconds() / bucket_seconds)
        idx = min(max(idx, 0), TIMING_CONCENTRATION_BUCKETS - 1)
        counts[idx] += 1
    return max(counts) / len(window.reviews)


def find_best_burst_window(
    product_reviews: list[Review], reviewer_products: dict[str, set[str]]
) -> tuple[BurstWindow, float, float, float, float] | None:
    """Find the highest-CONFIDENCE BURST_HOURS-wide window in one product's review timeline, and
    return it pre-scored as (window, confidence, reviewer_concentration_score, text_dup_score,
    cross_product_score).

    Candidate window starts are each review's own timestamp (rather than a fixed step across the
    timeline) -- this guarantees the search never straddles/misses a tight real cluster due to
    step-alignment, and is cheap since a product has at most a few dozen reviews. Baseline arrival
    rate is `total_reviews / total_observed_days` for that product (its own first-to-last review
    span); a product with a zero-width span (all reviews at the exact same timestamp, not seen in
    this dataset but guarded for) falls back to a 1-day span so the rate stays finite.

    Selection is by CONFIDENCE, not by timing ratio -- an earlier version picked the single
    highest-ratio window and scored it afterward. Found broken during stress testing: a
    coincidental cluster of unrelated organic reviews can have a marginally higher ratio than a
    real, smaller coordinated burst elsewhere in the same product's history, so the real burst was
    never even evaluated (0/1 recall on that stress case). Scoring every qualifying window and
    keeping the best-CONFIDENCE one fixes this structurally: an organic cluster's confidence is
    suppressed by its own zero reviewer/text concentration regardless of how high its ratio is, so
    it can no longer out-rank a genuinely coordinated (lower-ratio) burst.

    Returns None if no candidate window meets both MIN_BURST_COUNT and BURST_RATIO_THRESHOLD.
    """
    if len(product_reviews) < MIN_BURST_COUNT:
        return None

    sorted_reviews = sorted(product_reviews, key=lambda r: r.timestamp)
    first_ts = sorted_reviews[0].timestamp
    last_ts = sorted_reviews[-1].timestamp
    observed_days = max((last_ts - first_ts).total_seconds() / 86400, 1.0)
    baseline_rate_per_day = len(sorted_reviews) / observed_days
    expected_count = baseline_rate_per_day * (BURST_HOURS / 24)
    # No expected volume at all (e.g. a single-review product) means "ratio" is undefined --
    # there is nothing to compare a burst against, so no window can be flagged.
    if expected_count <= 0:
        return None

    window_delta = timedelta(hours=BURST_HOURS)
    best: tuple[BurstWindow, float, float, float, float] | None = None
    for candidate in sorted_reviews:
        window_start = candidate.timestamp
        window_end = window_start + window_delta
        window_reviews = [r for r in sorted_reviews if window_start <= r.timestamp < window_end]
        window_count = len(window_reviews)
        ratio = window_count / expected_count
        if window_count < MIN_BURST_COUNT or ratio < BURST_RATIO_THRESHOLD:
            continue

        window = BurstWindow(
            start=window_start, end=window_end, reviews=window_reviews, ratio_vs_baseline=ratio
        )
        confidence, reviewer_score, text_score, cross_score = score_burst_window(
            window, reviewer_products
        )
        if best is None or confidence > best[1]:
            best = (window, confidence, reviewer_score, text_score, cross_score)

    return best


def score_burst_window(
    window: BurstWindow, reviewer_products: dict[str, set[str]]
) -> tuple[float, float, float, float]:
    """Compute reviewer-concentration, text-dup, and cross-product scores for a burst window, plus
    combined confidence. Returns (confidence, reviewer_concentration_score, text_dup_score,
    cross_product_score).

    reviewer_concentration_score and text_dup_score share the same shape: 0.0 means "every review
    is unique on that dimension" (organic, not suspicious on its own -- e.g. a genuine flash-sale
    spike would look like this), 1.0 means "heavily repeated" (few reviewers or few distinct texts
    driving many reviews -- the actual campaign signature). text_dup_score is the max of exact
    duplication (identical after normalize_cluster_text) and near-duplication (Jaccard-clustered,
    long texts only -- see _near_dup_score) so a templated campaign with minor word substitutions
    still registers even when no two reviews are byte-identical.

    Base confidence REQUIRES at least one of reviewer-concentration or text-dup to be genuinely
    elevated -- timing burst alone is NOT sufficient. An earlier version gave burst timing a flat
    0.5 floor regardless of reviewer/text signals; validated against the synthetic testbed, that
    produced 3 false positives (precision 0.4) on the two planted batch-defect products plus one
    control -- a real batch defect is many DIFFERENT genuine customers independently complaining
    about the same issue within days of each other, which elevates review-arrival rate exactly
    like a campaign burst does, but with zero reviewer/text concentration (both scored 0.0 in
    every false-positive case). Using max() instead of a weighted sum with a floor means a burst
    with zero concentration on BOTH dimensions scores exactly 0 for the base confidence.

    Cross-product reviewer reuse then AMPLIFIES (never replaces) that base confidence: gated to
    apply only when base confidence is already > 0, so it cannot resurrect the batch-defect
    confound by manufacturing a flag purely from coincidental reviewer overlap on a
    zero-concentration burst.
    """
    window_count = len(window.reviews)
    distinct_reviewers = len({r.reviewer_id for r in window.reviews})
    distinct_texts = len({normalize_cluster_text(r.text) for r in window.reviews})

    reviewer_concentration_score = 1.0 - (distinct_reviewers / window_count)
    exact_dup_score = 1.0 - (distinct_texts / window_count)
    near_dup_score = _near_dup_score(window.reviews)
    text_dup_score = max(exact_dup_score, near_dup_score)

    burst_component = min(1.0, window.ratio_vs_baseline / BURST_SATURATION)
    base_confidence = burst_component * max(reviewer_concentration_score, text_dup_score)

    cross_product_score = 0.0
    if base_confidence > 0:
        cross_product_score = _cross_product_score(window.reviews, reviewer_products)
    confidence = base_confidence + (1.0 - base_confidence) * CROSS_PRODUCT_BOOST_WEIGHT * cross_product_score

    return confidence, reviewer_concentration_score, text_dup_score, cross_product_score


def scan_product(
    product_id: str, product_reviews: list[Review], reviewer_products: dict[str, set[str]]
) -> CampaignFlag | None:
    """Run the full scan for one product; returns a flag only if a burst was found AND its
    confidence clears CONFIDENCE_REPORT_THRESHOLD -- the burst gate alone is not sufficient
    (see that constant's comment for why)."""
    found = find_best_burst_window(product_reviews, reviewer_products)
    if found is None:
        return None
    window, confidence, reviewer_concentration_score, text_dup_score, cross_product_score = found
    if confidence < CONFIDENCE_REPORT_THRESHOLD:
        return None
    window_count = len(window.reviews)
    distinct_reviewers = len({r.reviewer_id for r in window.reviews})
    distinct_texts = len({normalize_cluster_text(r.text) for r in window.reviews})

    reviewer_counts: dict[str, int] = defaultdict(int)
    text_counts: dict[str, int] = defaultdict(int)
    for review in window.reviews:
        reviewer_counts[review.reviewer_id] += 1
        text_counts[normalize_cluster_text(review.text)] += 1
    top_reviewer_ids = [
        rid for rid, _ in sorted(reviewer_counts.items(), key=lambda kv: -kv[1])[:5]
    ]
    top_texts = [text for text, _ in sorted(text_counts.items(), key=lambda kv: -kv[1])[:5]]

    evidence = {
        "burst_window": {
            "start": window.start.isoformat().replace("+00:00", "Z"),
            "end": window.end.isoformat().replace("+00:00", "Z"),
        },
        "burst_hours": BURST_HOURS,
        "window_review_count": window_count,
        "burst_ratio_vs_baseline": round(window.ratio_vs_baseline, 3),
        "distinct_reviewers": distinct_reviewers,
        "distinct_texts": distinct_texts,
        "reviewer_concentration_score": round(reviewer_concentration_score, 3),
        "text_dup_score": round(text_dup_score, 3),
        "cross_product_score": round(cross_product_score, 3),
        "timing_concentration_score": round(_timing_concentration_score(window), 3),
        "review_ids": [r.review_id for r in window.reviews],
        "top_reviewer_ids": top_reviewer_ids,
        "top_texts": top_texts,
    }
    return CampaignFlag(product_id=product_id, confidence=round(confidence, 4), evidence=evidence)


def scan_corpus(reviews: list[Review]) -> list[CampaignFlag]:
    """Group reviews by product and scan each product independently; no product is hardcoded.

    Builds a corpus-wide reviewer_id -> {product_ids reviewed} map once, used only by the
    cross-product-reuse amplifier signal -- the only place any product's scan depends on data
    outside its own reviews.
    """
    by_product: dict[str, list[Review]] = defaultdict(list)
    reviewer_products: dict[str, set[str]] = defaultdict(set)
    for review in reviews:
        by_product[review.product_id].append(review)
        reviewer_products[review.reviewer_id].add(review.product_id)

    flags = [scan_product(pid, prs, reviewer_products) for pid, prs in by_product.items()]
    return sorted((f for f in flags if f is not None), key=lambda f: -f.confidence)


def campaign_reviews_from_rows(rows: list[dict[str, Any]]) -> list[Review]:
    """Adapt app.core.storage_pg.list_dated_extractions_pg's row dicts into Review objects.

    `reviewer_id` is stubbed to the review's own `id` (guaranteed unique per review) --
    `extractions` has no reviewer-identity column to populate honestly (no ingestion path
    captures it yet). This forces BOTH `reviewer_concentration_score` AND `cross_product_score`
    to always be 0 (distinct_reviewers == window_count always; every stub id maps to exactly one
    product forever), leaving `text_dup_score` (real review_text, exact + near-dup) as the only
    confidence driver. Pure recall reduction, never a false-positive risk -- see module docstring
    for the full explanation.

    Each row must have `id`, `product`, `review_text`, `review_date` keys (see
    list_dated_extractions_pg's return shape).
    """
    return [
        Review(
            review_id=row["id"],
            product_id=row["product"] or "unknown product",
            reviewer_id=row["id"],
            timestamp=row["review_date"],
            text=row["review_text"] or "",
        )
        for row in rows
    ]
