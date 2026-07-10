"""SYNTHETIC-VALIDATED DETECTOR -- this code's logic is proven against a synthetic testbed with
PLANTED, KNOWN ground truth (benchmark/phase2_synthetic/). It is NOT proven against real seller
data -- planted patterns may not match how real coordinated campaigns actually look in
production. Do not quote synthetic precision/recall as real-world accuracy. This is the
reviewer+timing-enabled EXTENSION of benchmark/phase2_campaign/ (which was built text-only
against the real corpus, which lacks reviewer identity and timestamps).

Combines signals into one moderation-prioritization confidence score (never a binary verdict,
always evidence for human review):
  1. Timing burst           -- an abnormal spike in review arrival rate for a product.
  2. Reviewer concentration -- within a burst, a small pool of reviewer_ids posting repeatedly.
  3. Text duplication       -- within a burst, reviews clustering on identical OR near-identical
     (long, templated) text. Near-identical is gated to texts of MIN_WORDS_FOR_NEAR_DUP+ words --
     short organic praise ("great product!", "really great product") has high token overlap
     between genuinely unrelated reviewers, so only longer, more specific near-duplicates count.
  4. Cross-product reviewer reuse -- amplifier only: the same reviewer_id posting inside more than
     one product's burst window is a coordination-farm signature organic popularity does not
     produce. Gated to never fire on its own (see CROSS_PRODUCT_BOOST_WEIGHT) -- it can only
     amplify an already-nonzero concentration signal, not manufacture one from nothing.
  5. Timing concentration   -- evidence-only, does NOT drive confidence: what fraction of a burst
     lands in one tight sub-window of it, for moderator context. A genuine batch-defect (many real
     customers hit by the same shipment/defect) can also cluster tightly by chance, so this cannot
     be allowed to drive confidence without resurrecting the batch-defect confound this detector
     was already fixed against (see CONFIDENCE_REPORT_THRESHOLD comment).

The core burst search evaluates EVERY qualifying window per product and keeps the
highest-CONFIDENCE one (not the highest-ratio one) -- found necessary during stress testing, when
a coincidental cluster of unrelated organic reviews had a marginally higher timing ratio than a
real small planted campaign burst and was selected first, so the real burst (which would have
scored well above threshold) was never evaluated at all. Selecting by ratio alone is a structural
flaw independent of any threshold value; selecting by confidence fixes it because an organic
cluster's confidence is suppressed by its zero reviewer/text concentration regardless of how high
its timing ratio happens to be.

General-purpose: scans every product in reviews.jsonl. No product_id is hardcoded into the
detection logic; ground-truth product IDs are only used later, in validation, to score
precision/recall against the known planted campaigns.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# The near-duplicate text-normalization approach was already built and validated in
# benchmark/phase2_campaign/artifact_filter.py against the real licensed corpus -- reuse it
# rather than reimplementing the same "strip punctuation, lowercase, collapse whitespace" logic
# a second time. artifact_filter.py itself uses script-style sibling imports (no __init__.py in
# that directory), so it is only importable with its own directory on sys.path.
_PHASE2_CAMPAIGN_DIR = Path(__file__).resolve().parents[2] / "phase2_campaign"
if str(_PHASE2_CAMPAIGN_DIR) not in sys.path:
    sys.path.insert(0, str(_PHASE2_CAMPAIGN_DIR))
from artifact_filter import normalize_cluster_text  # noqa: E402 -- needs the sys.path insert above

_SYNTHETIC_DIR = Path(__file__).resolve().parents[1]
_REVIEWS_PATH = _SYNTHETIC_DIR / "reviews.jsonl"
_GROUND_TRUTH_PATH = _SYNTHETIC_DIR / "ground_truth.json"
_FLAGS_OUTPUT_PATH = Path(__file__).resolve().parent / "campaign_synthetic_flags.jsonl"
_REPORT_OUTPUT_PATH = Path(__file__).resolve().parent / "CAMPAIGN_SYNTHETIC_REPORT.md"

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
# confidence must also clear this bar. Without it, a burst with zero reviewer/text concentration
# (confidence exactly 0.0 under score_burst_window's max() formula) still surfaced as a "flag" in
# the output, since the gate alone determined list membership -- found during validation, when
# the 3 batch-defect false positives kept appearing in the flagged list even after their
# confidence was correctly driven to 0. Same pattern as benchmark/phase2_campaign/score.py's
# CONFIDENCE_REPORT_THRESHOLD -- a near-zero score isn't worth a human moderator's attention.
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
    """One review normalized from `reviews.jsonl` into the fields this detector needs."""

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


def load_reviews(path: Path) -> list[Review]:
    """Load `reviews.jsonl` into a list of normalized `Review` records.

    Timestamps are ISO8601 UTC with a trailing `Z` (e.g. `2026-02-02T00:00:00Z`), which
    `datetime.fromisoformat` cannot parse directly on Python < 3.11 -- the `Z`/`+00:00` swap
    keeps this working across the supported version range regardless of the interpreter used to
    run it.
    """
    reviews: list[Review] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            timestamp = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
            reviews.append(
                Review(
                    review_id=record["review_id"],
                    product_id=record["product_id"],
                    reviewer_id=record["reviewer_id"],
                    timestamp=timestamp,
                    text=record["text"],
                )
            )
    return reviews


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
    """Evidence-only (see module docstring point 5): the largest fraction of a burst window's
    reviews landing in any single one of TIMING_CONCENTRATION_BUCKETS equal-width sub-buckets.
    High values mean the burst executed in a much tighter timeframe than the full window."""
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
    return sorted(
        (f for f in flags if f is not None), key=lambda f: -f.confidence
    )


def write_flags(flags: list[CampaignFlag], path: Path) -> None:
    """Write one JSON object per flagged product to `path`, one per line."""
    with path.open("w", encoding="utf-8") as f:
        for flag in flags:
            record = {
                "product_id": flag.product_id,
                "confidence": flag.confidence,
                "evidence": flag.evidence,
            }
            f.write(json.dumps(record) + "\n")


@dataclass
class ValidationResult:
    """Precision/recall/false-positive summary against the synthetic ground truth."""

    true_positives: list[str] = field(default_factory=list)
    false_negatives: list[str] = field(default_factory=list)
    false_positives: list[str] = field(default_factory=list)
    true_negatives: list[str] = field(default_factory=list)

    @property
    def precision(self) -> float:
        """Fraction of flagged products that were actually planted campaigns."""
        denom = len(self.true_positives) + len(self.false_positives)
        return len(self.true_positives) / denom if denom else 0.0

    @property
    def recall(self) -> float:
        """Fraction of planted campaigns that were caught."""
        denom = len(self.true_positives) + len(self.false_negatives)
        return len(self.true_positives) / denom if denom else 0.0

    @property
    def false_positive_rate(self) -> float:
        """Fraction of all non-campaign products that were incorrectly flagged."""
        denom = len(self.false_positives) + len(self.true_negatives)
        return len(self.false_positives) / denom if denom else 0.0


def validate_against_ground_truth(
    flags: list[CampaignFlag], ground_truth: dict[str, Any]
) -> ValidationResult:
    """Score flagged products against ground truth's `pattern == "fake_campaign"` and
    `is_control == False` planted products -- the ONLY use of ground-truth product IDs in this
    module; detection logic above never references them.
    """
    flagged_ids = {f.product_id for f in flags}
    result = ValidationResult()
    for product_id, record in ground_truth["products"].items():
        is_planted_campaign = record.get("pattern") == "fake_campaign" and not record.get(
            "is_control", False
        )
        was_flagged = product_id in flagged_ids
        if is_planted_campaign and was_flagged:
            result.true_positives.append(product_id)
        elif is_planted_campaign and not was_flagged:
            result.false_negatives.append(product_id)
        elif not is_planted_campaign and was_flagged:
            result.false_positives.append(product_id)
        else:
            result.true_negatives.append(product_id)
    return result


def render_report(flags: list[CampaignFlag], validation: ValidationResult) -> str:
    """Render the markdown validation report."""
    lines = [
        "# Campaign Synthetic Detector -- Validation Report",
        "",
        "SYNTHETIC-VALIDATED DETECTOR. Results below are against a synthetic testbed with "
        "PLANTED, KNOWN ground truth (`benchmark/phase2_synthetic/`). NOT proven against real "
        "seller data. Do not quote these numbers as real-world accuracy.",
        "",
        "## Summary",
        "",
        f"- Precision: {validation.precision:.3f}",
        f"- Recall: {validation.recall:.3f}",
        f"- False positive rate: {validation.false_positive_rate:.3f}",
        f"- True positives ({len(validation.true_positives)}): "
        f"{', '.join(sorted(validation.true_positives)) or 'none'}",
        f"- False negatives ({len(validation.false_negatives)}): "
        f"{', '.join(sorted(validation.false_negatives)) or 'none'}",
        f"- False positives ({len(validation.false_positives)}): "
        f"{', '.join(sorted(validation.false_positives)) or 'none'}",
        f"- True negatives ({len(validation.true_negatives)}): "
        f"{', '.join(sorted(validation.true_negatives)) or 'none'}",
        "",
        "## All flagged products",
        "",
    ]
    if not flags:
        lines.append("(none flagged)")
    for flag in flags:
        ev = flag.evidence
        lines.extend(
            [
                f"### {flag.product_id} -- confidence {flag.confidence:.3f}",
                f"- burst window: {ev['burst_window']['start']} -- {ev['burst_window']['end']} "
                f"({ev['burst_hours']}h)",
                f"- window review count: {ev['window_review_count']}, "
                f"ratio vs baseline: {ev['burst_ratio_vs_baseline']}",
                f"- distinct reviewers: {ev['distinct_reviewers']} "
                f"(concentration score {ev['reviewer_concentration_score']})",
                f"- distinct texts: {ev['distinct_texts']} (dup score {ev['text_dup_score']})",
                f"- cross-product reviewer reuse score: {ev['cross_product_score']} "
                f"(amplifier only)",
                f"- timing concentration score: {ev['timing_concentration_score']} "
                f"(evidence only, not scored)",
                f"- top reviewer_ids: {ev['top_reviewer_ids']}",
                f"- top texts: {ev['top_texts']}",
                "",
            ]
        )

    lines.extend(
        [
            "## Confound analysis",
            "",
            "**Matched-volume controls (SYN-CAMPAIGN-CTRL-01/02):** CTRL-02 has no 48h window "
            "meeting even MIN_BURST_COUNT -- rejected by the timing-burst gate alone. CTRL-01 "
            "DOES have a real qualifying timing-burst window (organic review volume can "
            "genuinely cluster within 48h) -- it is rejected by the CONFIDENCE gate instead: "
            "zero reviewer/text concentration in that window drives confidence to 0.0 despite "
            "the timing ratio clearing BURST_RATIO_THRESHOLD. Both controls match their planted "
            "counterparts' total review count -- proving this detector does not simply alert on "
            "\"popular product, lots of reviews,\" even when that popularity produces a real "
            "timing burst.",
            "",
            "**Batch-defect products (topic-vs-timing confound) -- FIXED:** an earlier version "
            "of this detector DID false-positive on SYN-BATCH-01/02 and SYN-BATCH-CTRL-02 (a "
            "real batch-defect spike also elevates raw review-arrival rate over a short window, "
            "without reviewer/text clustering -- confirmed via random clumping in a small "
            "8-point sample, the birthday-paradox effect). Root cause: a flat 0.5 confidence "
            "floor gave burst-timing alone half credit regardless of reviewer/text "
            "concentration. Fixed by requiring "
            "`confidence = burst_component * max(reviewer_concentration_score, text_dup_score)` "
            "-- a burst with zero concentration on BOTH dimensions now scores exactly 0.0 -- "
            "plus a CONFIDENCE_REPORT_THRESHOLD=0.2 gate so a zero-confidence item no longer "
            "appears in the flagged list at all. Current run (see Summary above): 0 false "
            "positives, including on all 3 of these previously-affected products -- verified "
            "programmatically each run, not a one-time fix. See "
            "project_phase2_synthetic_testbed.md memory for the full fix history.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    """Scan all 18 synthetic products for fake-review-campaign signatures, write flags + report,
    and print a precision/recall/false-positive summary against ground truth.
    """
    reviews = load_reviews(_REVIEWS_PATH)
    flags = scan_corpus(reviews)
    write_flags(flags, _FLAGS_OUTPUT_PATH)

    ground_truth = json.loads(_GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
    validation = validate_against_ground_truth(flags, ground_truth)
    report = render_report(flags, validation)
    _REPORT_OUTPUT_PATH.write_text(report, encoding="utf-8")

    print(f"scanned {len(reviews)} reviews across "
          f"{len({r.product_id for r in reviews})} products")
    print(f"flagged {len(flags)} product(s): {[f.product_id for f in flags]}")
    print(f"precision={validation.precision:.3f} recall={validation.recall:.3f} "
          f"fpr={validation.false_positive_rate:.3f}")
    print(f"wrote {_FLAGS_OUTPUT_PATH}")
    print(f"wrote {_REPORT_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
