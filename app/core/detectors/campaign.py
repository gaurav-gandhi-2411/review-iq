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

import math
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.detectors.campaign_signals import (
    DEFAULT_PARAMS,
    CampaignParams,
    ProductStream,
    Review,
    WindowEvidence,
    epoch_seconds,
)

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


@dataclass
class BurstWindow:
    """The highest-CONFIDENCE BURST_HOURS-wide window found for one product, if any -- see
    find_best_burst_window's docstring for why confidence, not ratio, is the selection key."""

    start: datetime
    end: datetime
    reviews: list[Review]
    ratio_vs_baseline: float


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


def _cross_product_score(
    window_reviews: list[Review], reviewer_products: dict[str, set[str]]
) -> float:
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
    confidence = (
        base_confidence + (1.0 - base_confidence) * CROSS_PRODUCT_BOOST_WEIGHT * cross_product_score
    )

    return confidence, reviewer_concentration_score, text_dup_score, cross_product_score


SIGNAL_BURST = "burst"
SIGNAL_TEMPLATE = "template"
SIGNAL_RATING = "rating_shift"
SIGNAL_MISMATCH = "rating_text_mismatch"
SIGNAL_VERIFIED = "verified_share"

DAY_S = 86400.0


# ---- alert rule ---------------------------------------------------------------------------


def fired_signals(ev: WindowEvidence, p: CampaignParams) -> tuple[list[str], bool]:
    """(fired signal names, whether any single signal is beyond the strong multiple)."""
    fired: list[str] = []
    strong = False
    c = p.strong_multiplier
    if ev.burst is not None and ev.burst >= p.theta_burst:
        fired.append(SIGNAL_BURST)
        strong |= ev.burst >= c * p.theta_burst
    if ev.template_k >= p.k_min:
        fired.append(SIGNAL_TEMPLATE)
        strong |= ev.template_k >= math.ceil(c * p.k_min)
    if ev.rating_z is not None and abs(ev.rating_z) >= p.theta_rating:
        fired.append(SIGNAL_RATING)
        strong |= abs(ev.rating_z) >= c * p.theta_rating
    if p.use_mismatch and ev.mismatch_z is not None and ev.mismatch_z >= p.theta_mismatch:
        fired.append(SIGNAL_MISMATCH)
        strong |= ev.mismatch_z >= c * p.theta_mismatch
    if ev.verified_z is not None and ev.verified_z >= p.theta_verified:
        fired.append(SIGNAL_VERIFIED)
        strong |= ev.verified_z >= c * p.theta_verified
    return fired, strong


def is_alert(ev: WindowEvidence, p: CampaignParams) -> tuple[bool, list[str]]:
    fired, strong = fired_signals(ev, p)
    return (len(fired) >= 2 or strong), fired


@dataclass(frozen=True)
class Alert:
    index: int
    timestamp: float
    width_hours: int
    fired: tuple[str, ...]
    evidence: WindowEvidence


def scan_stream(
    stream: ProductStream, start_index: int = 0, apply_warmup: bool = True
) -> list[Alert]:
    """Alerts opened over the stream (cooldown applied), evaluating each arrival from
    `start_index` and the narrowest window that satisfies the alert rule."""
    p = stream.p
    alerts: list[Alert] = []
    last_t = -math.inf
    t0 = stream.ts[0] if len(stream) else 0.0
    for i in range(max(start_index, 0), len(stream)):
        t = stream.ts[i]
        if apply_warmup and (i < p.warmup_reviews or t - t0 < p.warmup_days * DAY_S):
            continue
        if t - last_t < p.cooldown_days * DAY_S:
            continue
        for w in sorted(p.windows_hours):
            ev = stream.evidence_at(i, w)
            if ev is None:
                continue
            ok, fired = is_alert(ev, p)
            if ok:
                alerts.append(Alert(i, t, w, tuple(fired), ev))
                last_t = t
                break
    return alerts


# ---- user-facing output (counts, windows, shared text; never a verdict on one review) ------


def window_label(hours: int) -> str:
    if hours % 24 == 0:
        days = hours // 24
        return f"{days} day" + ("" if days == 1 else "s")
    return f"{hours} hours"


def build_explanation(ev: WindowEvidence, fired: Sequence[str]) -> str:
    """Counts, window and shared text only. Names no individual review and gives no verdict."""
    parts = [
        f"{ev.n} reviews in the last {window_label(ev.width_hours)}"
        f" (about {ev.expected:.1f} expected from this product's last 180 days)."
    ]
    if SIGNAL_TEMPLATE in fired:
        parts.append(f"{ev.template_k} of them share near-identical text.")
    if SIGNAL_RATING in fired and ev.window_mean is not None and ev.history_mean is not None:
        parts.append(
            f"Their average rating is {ev.window_mean:.1f} versus {ev.history_mean:.1f} "
            "over the previous 180 days."
        )
    if SIGNAL_MISMATCH in fired:
        parts.append(
            f"{ev.mismatch_count} of them have a star rating that points the opposite way "
            "from the wording."
        )
    if SIGNAL_VERIFIED in fired and ev.verified_window_share is not None:
        parts.append(
            f"{ev.verified_window_share:.0%} are marked as verified purchases versus "
            f"{(ev.verified_history_share or 0.0):.0%} earlier."
        )
    parts.append("Worth reviewing together.")
    return " ".join(parts)


@dataclass
class CampaignFlag:
    """One alert on one product: the pattern, which signals fired, and the counts."""

    product_id: str
    alert_time: datetime
    width_hours: int
    signals_fired: list[str]
    review_count: int
    explanation: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "review_count": self.review_count,
            "window_label": window_label(self.width_hours),
            "signals_fired": list(self.signals_fired),
            "explanation": self.explanation,
            "evidence": self.evidence,
        }


def flag_from_alert(stream: ProductStream, product_id: str, alert: Alert) -> CampaignFlag:
    ev = alert.evidence
    t_end = datetime.fromtimestamp(alert.timestamp, tz=UTC)
    t_start = t_end - timedelta(hours=alert.width_hours)
    lo = alert.index - ev.n + 1
    evidence: dict[str, Any] = {
        "window": {
            "start": t_start.isoformat().replace("+00:00", "Z"),
            "end": t_end.isoformat().replace("+00:00", "Z"),
        },
        "window_hours": alert.width_hours,
        "review_count": ev.n,
        "expected_count": round(ev.expected, 2),
        "burst_strength": None if ev.burst is None else round(ev.burst, 2),
        "near_identical_group_size": ev.template_k,
        "window_mean_rating": None if ev.window_mean is None else round(ev.window_mean, 2),
        "history_mean_rating": None if ev.history_mean is None else round(ev.history_mean, 2),
        "review_ids": [stream.reviews[j].review_id for j in range(lo, alert.index + 1)],
        "near_identical_review_ids": [stream.reviews[j].review_id for j in ev.template_members],
    }
    return CampaignFlag(
        product_id=product_id,
        alert_time=t_end,
        width_hours=alert.width_hours,
        signals_fired=list(alert.fired),
        review_count=ev.n,
        explanation=build_explanation(ev, alert.fired),
        evidence=evidence,
    )


def scan_corpus(
    reviews: Sequence[Review],
    params: CampaignParams = DEFAULT_PARAMS,
    since: datetime | None = None,
) -> list[CampaignFlag]:
    """Group reviews by product, scan each product's stream, return alerts (newest first).
    `since` keeps only alerts opened at or after it (the sweep passes a recent cutoff so that
    enabling the detector does not replay a product's whole history as alerts)."""
    by_product: dict[str, list[Review]] = defaultdict(list)
    for r in reviews:
        by_product[r.product_id].append(r)
    flags: list[CampaignFlag] = []
    for pid, prs in by_product.items():
        if len(prs) < params.warmup_reviews:
            continue
        stream = ProductStream(prs, params)
        for alert in scan_stream(stream):
            if since is None or alert.timestamp >= epoch_seconds(since):
                flags.append(flag_from_alert(stream, pid, alert))
    return sorted(flags, key=lambda f: f.alert_time, reverse=True)


def campaign_reviews_from_rows(rows: list[dict[str, Any]]) -> list[Review]:
    """Adapt app.core.storage_pg.list_dated_extractions_pg rows. Those rows carry no rating and
    no verified flag today, so only the burst and template signals are active in production
    until ingestion plumbs them (a rating or verified key is used when a row has one)."""
    return [
        Review(
            review_id=row["id"],
            product_id=row["product"] or "unknown product",
            timestamp=row["review_date"],
            text=row["review_text"] or "",
            rating=row.get("rating"),
            verified=row.get("verified"),
        )
        for row in rows
    ]
