"""SYNTHETIC TESTBED -- planted ground truth for LOCAL DETECTOR VALIDATION ONLY.

Review TEXT is real (reused from the licensed corpus); timestamps/reviewer-IDs/product
assignment are FABRICATED to create known, verifiable patterns. Passing validation against
this data proves the detector CODE works correctly against patterns shaped like these -- it
does NOT prove the patterns match how real defects/trends/campaigns actually look on live
seller data. NEVER quote synthetic detection results as real-world accuracy.

Why this exists: the real licensed corpus (data/processed/flipkart_deduped.jsonl) has no
timestamps and no reviewer identity, so batch-defect / trend / fake-campaign detectors that
depend on those fields can't even be exercised against it. This generator builds a synthetic
review stream that reuses REAL review text verbatim and only synthesizes the metadata
(timestamp, reviewer_id, product_id) the public dataset never had in the first place.

Reproducibility: `random.seed(SEED)` is set once at module load and never reseeded. All "current
time" concepts are derived from the fixed WINDOW_START constant, never wall-clock time. Given an
unchanged source corpus, re-running this script produces byte-identical output.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SEED = 42
random.seed(SEED)

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = REPO_ROOT / "data" / "processed" / "flipkart_deduped.jsonl"
OUT_DIR = Path(__file__).resolve().parent
REVIEWS_PATH = OUT_DIR / "reviews.jsonl"
GROUND_TRUTH_PATH = OUT_DIR / "ground_truth.json"
REPORT_PATH = OUT_DIR / "GENERATION_REPORT.md"

# Exact 16-week window: Monday 2026-01-05 00:00 UTC (inclusive) to Monday 2026-04-27 00:00 UTC
# (exclusive). 16 * 7 = 112 days exactly -- verified via datetime diff, not assumed.
WINDOW_START = datetime(2026, 1, 5)
WINDOW_END_EXCLUSIVE = datetime(2026, 4, 27)
WINDOW_DAYS = (WINDOW_END_EXCLUSIVE - WINDOW_START).days
assert WINDOW_DAYS == 112, f"expected exactly 16 weeks (112 days), got {WINDOW_DAYS}"
N_WEEKS = WINDOW_DAYS // 7
N_PHASES = 4
PHASE_WEEKS = N_WEEKS // N_PHASES

BANNER = (
    "SYNTHETIC TESTBED -- planted ground truth for LOCAL DETECTOR VALIDATION ONLY. "
    "Review TEXT is real (reused from the licensed corpus); timestamps/reviewer-IDs/product "
    "assignment are FABRICATED to create known, verifiable patterns. Passing validation "
    "against this data proves the detector CODE works correctly against patterns shaped like "
    "these -- it does NOT prove the patterns match how real defects/trends/campaigns actually "
    "look on live seller data. NEVER quote synthetic detection results as real-world accuracy."
)


def fmt_ts(dt: datetime) -> str:
    """Format a naive UTC datetime as an ISO8601 string with a trailing 'Z'."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def week_range(week_idx: int, n_weeks: int = 1) -> tuple[datetime, datetime]:
    """Return the [start, end) datetime bounds for `n_weeks` starting at 0-indexed `week_idx`."""
    start = WINDOW_START + timedelta(days=7 * week_idx)
    end = start + timedelta(days=7 * n_weeks)
    return start, end


def phase_range(phase_idx: int) -> tuple[datetime, datetime]:
    """Return the [start, end) datetime bounds for 0-indexed phase `phase_idx` (4-week chunks)."""
    return week_range(phase_idx * PHASE_WEEKS, PHASE_WEEKS)


def random_timestamp(start: datetime, end: datetime) -> datetime:
    """Return a uniform-random second-resolution timestamp in [start, end)."""
    span_seconds = int((end - start).total_seconds())
    offset = random.randint(0, max(span_seconds - 1, 0))
    return start + timedelta(seconds=offset)


def week_index_of(dt: datetime) -> int:
    """Return the 0-indexed week number of `dt` within the generation window (clipped to N_WEEKS-1)."""
    day_offset = (dt - WINDOW_START).days
    return min(day_offset // 7, N_WEEKS - 1)


_reviewer_counter = 0


def alloc_reviewer_id() -> str:
    """Allocate a fresh, globally-unique synthetic reviewer id (never reused unless a caller
    deliberately reuses the returned string, e.g. inside a fake-campaign reviewer pool)."""
    global _reviewer_counter
    _reviewer_counter += 1
    return f"rev-{_reviewer_counter:04d}"


@dataclass
class RawReview:
    """A synthetic review record prior to global chronological review_id assignment."""

    product_id: str
    reviewer_id: str
    timestamp: datetime
    rating: str
    text: str
    source_review_id: str
    product_category: str
    review_id: str | None = field(default=None)

    def to_json(self) -> dict[str, Any]:
        """Serialize to the reviews.jsonl record shape (requires review_id to be assigned)."""
        assert self.review_id is not None, "review_id must be assigned before serialization"
        return {
            "review_id": self.review_id,
            "product_id": self.product_id,
            "reviewer_id": self.reviewer_id,
            "timestamp": fmt_ts(self.timestamp),
            "rating": self.rating,
            "text": self.text,
            "source_review_id": self.source_review_id,
            "product_category": self.product_category,
        }


class SourcePool:
    """Wraps the real review corpus and tracks which source rows have been consumed, so no
    single real review is silently reused across two different synthetic products (deliberate
    reuse, e.g. fake-campaign templates, is done explicitly by the caller instead)."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._records = records
        self._used_ids: set[str] = set()

    def _available(self) -> list[dict[str, Any]]:
        # Iterates the stable file-order list and filters via set membership (O(1) check only --
        # never iterates the set itself, so output order never depends on PYTHONHASHSEED).
        return [r for r in self._records if r["id"] not in self._used_ids]

    def mark_used(self, records: list[dict[str, Any]]) -> None:
        """Mark the given source records as consumed so they are not drawn again elsewhere."""
        for r in records:
            self._used_ids.add(r["id"])

    def sample_diverse(self, n: int) -> list[dict[str, Any]]:
        """Draw n distinct not-yet-used records uniformly at random from the whole corpus."""
        avail = self._available()
        chosen = random.sample(avail, n)
        self.mark_used(chosen)
        return chosen

    def find_matching(
        self,
        keyword_re: str,
        neg_re: str | None = None,
        max_rate: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return all not-yet-used records whose text matches keyword_re (optionally also
        neg_re and rate<=max_rate), in stable corpus order. Does not mark anything used."""
        kw = re.compile(keyword_re, re.IGNORECASE)
        neg = re.compile(neg_re, re.IGNORECASE) if neg_re else None
        out = []
        for r in self._available():
            t = r["text"]
            if not kw.search(t):
                continue
            if neg and not neg.search(t):
                continue
            if max_rate is not None and int(r["rate"]) > max_rate:
                continue
            out.append(r)
        return out

    def find_short_praise(self, max_len: int = 25, max_words: int = 3) -> list[dict[str, Any]]:
        """Return not-yet-used 5-star records whose normalized text is short and template-like
        (letters/spaces/basic punctuation only, no digits, <= max_words words)."""
        pattern = re.compile(r"^[A-Za-z][A-Za-z .!]{1,22}[A-Za-z.!]$")
        out = []
        for r in self._available():
            if r["rate"] != "5":
                continue
            norm = re.sub(r"\s+", " ", r["text"].strip())
            if len(norm) > max_len or len(norm.split()) > max_words:
                continue
            if not pattern.match(norm):
                continue
            out.append(r)
        return out

    def take_sample(self, matches: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
        """Randomly select up to n records from `matches`, mark them used, return the chosen set
        (fewer than n if `matches` is shorter -- callers must check and report honestly)."""
        n = min(n, len(matches))
        chosen = random.sample(matches, n)
        self.mark_used(chosen)
        return chosen


def load_pool() -> SourcePool:
    """Load the licensed corpus (data/processed/flipkart_deduped.jsonl) into a SourcePool."""
    records = []
    with CORPUS_PATH.open(encoding="utf-8") as f:
        for line in f:
            records.append(json.loads(line))
    return SourcePool(records)


# --------------------------------------------------------------------------------------------
# Batch-defect: 2 planted + 2 matched controls
# --------------------------------------------------------------------------------------------

BATCH_DEFECT_SPECS: list[dict[str, Any]] = [
    {
        "product_id": "SYN-BATCH-01",
        "control_id": "SYN-BATCH-CTRL-01",
        "topic": "battery",
        "keyword_re": r"battery",
        "neg_re": r"die|dies|died|drain|drains|draining|weak|poor|bad|not\s+last|discharg",
        "spike_week": 9,
    },
    {
        "product_id": "SYN-BATCH-02",
        "control_id": "SYN-BATCH-CTRL-02",
        "topic": "screen",
        "keyword_re": r"screen",
        "neg_re": r"crack|cracked|broken|shatter",
        "spike_week": 6,
    },
]

BATCH_BASELINE_COUNT = 40
BATCH_SPIKE_TARGET = 8
BATCH_SPIKE_WINDOW_DAYS = 9
BATCH_CONTROL_SCATTER_TARGET = 4


def gen_batch_defect_planted(
    pool: SourcePool, spec: dict[str, Any]
) -> tuple[list[RawReview], dict[str, Any]]:
    """Generate one planted batch-defect product: a diffuse baseline plus a tight, real-text
    defect-complaint spike starting at spec['spike_week']."""
    product_id = spec["product_id"]

    baseline = pool.sample_diverse(BATCH_BASELINE_COUNT)
    baseline_reviews = [
        RawReview(
            product_id=product_id,
            reviewer_id=alloc_reviewer_id(),
            timestamp=random_timestamp(WINDOW_START, WINDOW_END_EXCLUSIVE),
            rating=r["rate"],
            text=r["text"],
            source_review_id=r["id"],
            product_category="batch_defect_planted",
        )
        for r in baseline
    ]

    baseline_topic_matches = sum(
        1 for r in baseline if re.search(spec["keyword_re"], r["text"], re.IGNORECASE)
    )

    matches = pool.find_matching(spec["keyword_re"], neg_re=spec["neg_re"], max_rate=2)
    spike_start, _ = week_range(spec["spike_week"])
    spike_end = spike_start + timedelta(days=BATCH_SPIKE_WINDOW_DAYS)
    spike_source = pool.take_sample(matches, BATCH_SPIKE_TARGET)
    spike_reviews = [
        RawReview(
            product_id=product_id,
            reviewer_id=alloc_reviewer_id(),
            timestamp=random_timestamp(spike_start, spike_end),
            rating=r["rate"],
            text=r["text"],
            source_review_id=r["id"],
            product_category="batch_defect_planted",
        )
        for r in spike_source
    ]

    gt = {
        "product_id": product_id,
        "pattern": "batch_defect",
        "is_control": False,
        "topic_keyword": spec["topic"],
        "keyword_regex": spec["keyword_re"],
        "defect_negative_regex": spec["neg_re"],
        "spike_window": {"start": fmt_ts(spike_start), "end": fmt_ts(spike_end)},
        "spike_target_count": BATCH_SPIKE_TARGET,
        "spike_actual_count": len(spike_reviews),
        "spike_available_matches": len(matches),
        "baseline_review_count": len(baseline_reviews),
        "baseline_topic_keyword_matches": baseline_topic_matches,
        "baseline_topic_keyword_rate": baseline_topic_matches / len(baseline_reviews),
        "_planted_review_objs": spike_reviews,
    }
    return baseline_reviews + spike_reviews, gt


def gen_batch_defect_control(
    pool: SourcePool, spec: dict[str, Any], matched_volume: int
) -> tuple[list[RawReview], dict[str, Any]]:
    """Generate the matched control for a planted batch-defect product: same total review
    volume, uniformly spread across the full window, with a few scattered (not clustered)
    real topic mentions so the topic isn't at literal zero baseline rate."""
    product_id = spec["control_id"]

    topic_matches = pool.find_matching(spec["keyword_re"])
    scatter_source = pool.take_sample(topic_matches, BATCH_CONTROL_SCATTER_TARGET)
    remaining = matched_volume - len(scatter_source)
    diverse_source = pool.sample_diverse(remaining)

    all_reviews = [
        RawReview(
            product_id=product_id,
            reviewer_id=alloc_reviewer_id(),
            timestamp=random_timestamp(WINDOW_START, WINDOW_END_EXCLUSIVE),
            rating=r["rate"],
            text=r["text"],
            source_review_id=r["id"],
            product_category="batch_defect_control",
        )
        for r in scatter_source + diverse_source
    ]
    random.shuffle(all_reviews)  # de-correlate insertion order from any downstream sort ties

    scatter_objs = all_reviews[: len(scatter_source)]

    gt = {
        "product_id": product_id,
        "pattern": "batch_defect",
        "is_control": True,
        "linked_planted_product_id": spec["product_id"],
        "topic_keyword": spec["topic"],
        "keyword_regex": spec["keyword_re"],
        "total_review_count": len(all_reviews),
        "scattered_topic_mention_target": BATCH_CONTROL_SCATTER_TARGET,
        "scattered_topic_mention_actual": len(scatter_source),
        "_scatter_review_objs": scatter_objs,
    }
    return all_reviews, gt


# --------------------------------------------------------------------------------------------
# Trend: 2 planted + 2 matched controls
# --------------------------------------------------------------------------------------------

TREND_SPECS: list[dict[str, Any]] = [
    {
        "product_id": "SYN-TREND-01",
        "control_id": "SYN-TREND-CTRL-01",
        "topic": "delivery",
        "keyword_re": r"delivery|shipping|late|delayed",
        # 2026-07-10 fix: originally keyword-only (no sentiment filter), which sourced mostly
        # positive delivery PRAISE ("delivery guy attitude is excellent") since "delivery"/
        # "late"/etc. appear in both compliments and complaints. GG's spec explicitly wants a
        # "rising COMPLAINT theme" -- found via the trend detector's own validation (0/2 recall
        # traced to this exact root cause, confirmed via corpus text inspection). 876 real
        # negative+rate<=2 matches available, comfortable surplus over the 17-review target.
        "neg_re": r"late|delay|damaged?|broken|poor|bad|never\s+arriv|lost|wrong|worst|not\s+deliver",
        "phase_targets": [1, 3, 5, 8],
    },
    {
        "product_id": "SYN-TREND-02",
        "control_id": "SYN-TREND-CTRL-02",
        "topic": "packaging",
        "keyword_re": r"packaging|box|damaged\s+in\s+transit|poorly\s+packed",
        "neg_re": r"damag|broken|poor|bad|torn|crush|worst|cheap|flimsy",
        "phase_targets": [2, 4, 6, 10],
    },
]

TREND_BASELINE_PER_WEEK_CHOICES = [3, 4]


def _stable_baseline(pool: SourcePool, product_id: str, category: str) -> list[RawReview]:
    """Build the ~3-4 reviews/week diverse, non-topic baseline used by both trend variants."""
    reviews: list[RawReview] = []
    for week_idx in range(N_WEEKS):
        n = random.choice(TREND_BASELINE_PER_WEEK_CHOICES)
        start, end = week_range(week_idx)
        source = pool.sample_diverse(n)
        for r in source:
            reviews.append(
                RawReview(
                    product_id=product_id,
                    reviewer_id=alloc_reviewer_id(),
                    timestamp=random_timestamp(start, end),
                    rating=r["rate"],
                    text=r["text"],
                    source_review_id=r["id"],
                    product_category=category,
                )
            )
    return reviews


def gen_trend_planted(
    pool: SourcePool, spec: dict[str, Any]
) -> tuple[list[RawReview], dict[str, Any]]:
    """Generate one planted trend product: a stable non-topic baseline plus topic-matching
    reviews with strictly-rising phase-over-phase density (4 phases of 4 weeks each)."""
    product_id = spec["product_id"]
    baseline_reviews = _stable_baseline(pool, product_id, "trend_planted")

    matches = pool.find_matching(spec["keyword_re"], neg_re=spec["neg_re"], max_rate=2)
    random.shuffle(matches)

    topic_reviews: list[RawReview] = []
    phase_actual_counts = []
    cursor = 0
    for phase_idx, target in enumerate(spec["phase_targets"]):
        start, end = phase_range(phase_idx)
        take = matches[cursor : cursor + target]
        cursor += len(take)
        pool.mark_used(take)
        for r in take:
            topic_reviews.append(
                RawReview(
                    product_id=product_id,
                    reviewer_id=alloc_reviewer_id(),
                    timestamp=random_timestamp(start, end),
                    rating=r["rate"],
                    text=r["text"],
                    source_review_id=r["id"],
                    product_category="trend_planted",
                )
            )
        phase_actual_counts.append(len(take))

    weekly_counts = [0] * N_WEEKS
    for r in topic_reviews:
        weekly_counts[week_index_of(r.timestamp)] += 1

    gt = {
        "product_id": product_id,
        "pattern": "trend",
        "is_control": False,
        "topic_keyword": spec["topic"],
        "keyword_regex": spec["keyword_re"],
        "defect_negative_regex": spec["neg_re"],
        "phase_targets": spec["phase_targets"],
        "phase_actual_counts": phase_actual_counts,
        "phase_windows": [
            {"start": fmt_ts(s), "end": fmt_ts(e)}
            for s, e in (phase_range(i) for i in range(N_PHASES))
        ],
        "weekly_topic_counts": weekly_counts,
        "phase_over_phase_strictly_increasing": all(
            phase_actual_counts[i] < phase_actual_counts[i + 1]
            for i in range(len(phase_actual_counts) - 1)
        ),
        "baseline_review_count": len(baseline_reviews),
        "_topic_review_objs": topic_reviews,
    }
    return baseline_reviews + topic_reviews, gt


def gen_trend_control(
    pool: SourcePool, spec: dict[str, Any], matched_total: int
) -> tuple[list[RawReview], dict[str, Any]]:
    """Generate the matched control for a planted trend product: same topic keyword and same
    total topic-mention count, but distributed flat/uniform across all 16 weeks."""
    product_id = spec["control_id"]
    baseline_reviews = _stable_baseline(pool, product_id, "trend_control")

    base_per_week = matched_total // N_WEEKS
    remainder = matched_total - base_per_week * N_WEEKS
    per_week_counts = [base_per_week] * N_WEEKS
    for week_idx in random.sample(range(N_WEEKS), remainder):
        per_week_counts[week_idx] += 1

    matches = pool.find_matching(spec["keyword_re"], neg_re=spec["neg_re"], max_rate=2)
    random.shuffle(matches)

    topic_reviews: list[RawReview] = []
    cursor = 0
    for week_idx, n in enumerate(per_week_counts):
        start, end = week_range(week_idx)
        take = matches[cursor : cursor + n]
        cursor += len(take)
        pool.mark_used(take)
        for r in take:
            topic_reviews.append(
                RawReview(
                    product_id=product_id,
                    reviewer_id=alloc_reviewer_id(),
                    timestamp=random_timestamp(start, end),
                    rating=r["rate"],
                    text=r["text"],
                    source_review_id=r["id"],
                    product_category="trend_control",
                )
            )

    weekly_counts = [0] * N_WEEKS
    for r in topic_reviews:
        weekly_counts[week_index_of(r.timestamp)] += 1

    gt = {
        "product_id": product_id,
        "pattern": "trend",
        "is_control": True,
        "linked_planted_product_id": spec["product_id"],
        "topic_keyword": spec["topic"],
        "keyword_regex": spec["keyword_re"],
        "matched_total_topic_count": matched_total,
        "actual_total_topic_count": len(topic_reviews),
        "weekly_topic_counts": weekly_counts,
        "baseline_review_count": len(baseline_reviews),
        "_topic_review_objs": topic_reviews,
    }
    return baseline_reviews + topic_reviews, gt


# --------------------------------------------------------------------------------------------
# Fake-campaign: 2 planted + 2 matched controls
# --------------------------------------------------------------------------------------------

CAMPAIGN_SPECS: list[dict[str, Any]] = [
    {
        "product_id": "SYN-CAMPAIGN-01",
        "control_id": "SYN-CAMPAIGN-CTRL-01",
        "burst_week": 4,
        "burst_hours": 30,
        "n_templates": 4,
        "n_reviewers": 5,
        "target_total": 11,
    },
    {
        "product_id": "SYN-CAMPAIGN-02",
        "control_id": "SYN-CAMPAIGN-CTRL-02",
        "burst_week": 12,
        "burst_hours": 42,
        "n_templates": 4,
        "n_reviewers": 6,
        "target_total": 12,
    },
]

CAMPAIGN_BASELINE_COUNT = 30


def _distribute(total: int, n_bins: int, lo: int, hi: int) -> list[int]:
    """Distribute `total` units across `n_bins` bins, each in [lo, hi], filling round-robin.
    Requires n_bins*lo <= total <= n_bins*hi."""
    assert n_bins * lo <= total <= n_bins * hi, "target_total out of reachable [lo,hi] range"
    counts = [lo] * n_bins
    remaining = total - lo * n_bins
    i = 0
    while remaining > 0:
        idx = i % n_bins
        if counts[idx] < hi:
            counts[idx] += 1
            remaining -= 1
        i += 1
    return counts


def gen_campaign_planted(
    pool: SourcePool, spec: dict[str, Any]
) -> tuple[list[RawReview], dict[str, Any]]:
    """Generate one planted fake-campaign product: organic baseline plus a tight-window burst
    of real, short 5-star praise text reused across a small pool of reviewer ids."""
    product_id = spec["product_id"]

    baseline = pool.sample_diverse(CAMPAIGN_BASELINE_COUNT)
    baseline_reviews = [
        RawReview(
            product_id=product_id,
            reviewer_id=alloc_reviewer_id(),
            timestamp=random_timestamp(WINDOW_START, WINDOW_END_EXCLUSIVE),
            rating=r["rate"],
            text=r["text"],
            source_review_id=r["id"],
            product_category="campaign_planted",
        )
        for r in baseline
    ]

    short_praise = pool.find_short_praise()
    templates = random.sample(short_praise, spec["n_templates"])
    pool.mark_used(templates)
    for t in templates:
        assert t["rate"] == "5", "campaign templates must be genuinely 5-star, never overridden"

    template_counts = _distribute(spec["target_total"], spec["n_templates"], 2, 4)
    template_slots = []
    for idx, count in enumerate(template_counts):
        template_slots.extend([idx] * count)
    random.shuffle(template_slots)

    reviewer_ids = [alloc_reviewer_id() for _ in range(spec["n_reviewers"])]
    reviewer_counts = _distribute(spec["target_total"], spec["n_reviewers"], 2, 3)
    reviewer_slots = []
    for idx, count in enumerate(reviewer_counts):
        reviewer_slots.extend([idx] * count)
    random.shuffle(reviewer_slots)

    burst_start, _ = week_range(spec["burst_week"])
    burst_end = burst_start + timedelta(hours=spec["burst_hours"])

    burst_reviews: list[RawReview] = []
    review_to_template: dict[int, str] = {}  # index into burst_reviews -> template text
    for template_idx, reviewer_idx in zip(template_slots, reviewer_slots, strict=True):
        template = templates[template_idx]
        rr = RawReview(
            product_id=product_id,
            reviewer_id=reviewer_ids[reviewer_idx],
            timestamp=random_timestamp(burst_start, burst_end),
            rating=template["rate"],
            text=template["text"],
            source_review_id=template["id"],
            product_category="campaign_planted",
        )
        burst_reviews.append(rr)
        review_to_template[len(burst_reviews) - 1] = template["text"]

    gt = {
        "product_id": product_id,
        "pattern": "fake_campaign",
        "is_control": False,
        "burst_window": {"start": fmt_ts(burst_start), "end": fmt_ts(burst_end)},
        "burst_hours": spec["burst_hours"],
        "burst_review_count": len(burst_reviews),
        "reviewer_ids_used": reviewer_ids,
        "template_texts": [t["text"] for t in templates],
        "template_usage_counts": dict(
            zip([t["text"] for t in templates], template_counts, strict=True)
        ),
        "baseline_review_count": len(baseline_reviews),
        "_burst_review_objs": burst_reviews,
        "_review_to_template": review_to_template,
    }
    return baseline_reviews + burst_reviews, gt


def gen_campaign_control(
    pool: SourcePool, spec: dict[str, Any], matched_total: int
) -> tuple[list[RawReview], dict[str, Any]]:
    """Generate the matched control for a planted fake-campaign product: same total review
    count, but fully organic (unique text, unique reviewer ids, mixed ratings, spread across
    the full window -- the adversarial 'genuinely popular product' case)."""
    product_id = spec["control_id"]
    source = pool.sample_diverse(matched_total)
    reviews = [
        RawReview(
            product_id=product_id,
            reviewer_id=alloc_reviewer_id(),
            timestamp=random_timestamp(WINDOW_START, WINDOW_END_EXCLUSIVE),
            rating=r["rate"],
            text=r["text"],
            source_review_id=r["id"],
            product_category="campaign_control",
        )
        for r in source
    ]

    gt = {
        "product_id": product_id,
        "pattern": "fake_campaign",
        "is_control": True,
        "linked_planted_product_id": spec["product_id"],
        "total_review_count": len(reviews),
        "unique_reviewer_count": len({r.reviewer_id for r in reviews}),
        "unique_text_count": len({r.text for r in reviews}),
    }
    return reviews, gt


# --------------------------------------------------------------------------------------------
# Pure noise: 6 products, negative examples for all three detectors
# --------------------------------------------------------------------------------------------

NOISE_PRODUCT_IDS = [f"SYN-NOISE-{i:02d}" for i in range(1, 7)]
NOISE_COUNT_RANGE = (25, 35)


def gen_noise(pool: SourcePool, product_id: str) -> tuple[list[RawReview], dict[str, Any]]:
    """Generate one pure-noise product: fully organic real reviews, no planted pattern."""
    n = random.randint(*NOISE_COUNT_RANGE)
    source = pool.sample_diverse(n)
    reviews = [
        RawReview(
            product_id=product_id,
            reviewer_id=alloc_reviewer_id(),
            timestamp=random_timestamp(WINDOW_START, WINDOW_END_EXCLUSIVE),
            rating=r["rate"],
            text=r["text"],
            source_review_id=r["id"],
            product_category="noise",
        )
        for r in source
    ]
    gt = {
        "product_id": product_id,
        "pattern": None,
        "is_control": False,
        "review_count": len(reviews),
    }
    return reviews, gt


# --------------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------------


def _resolve_object_refs(gt: dict[str, Any]) -> dict[str, Any]:
    """Replace any `_..._objs` / `_review_to_template` internal keys in a ground-truth dict with
    their final, post-sort review_id-based equivalents. Must run after global ID assignment."""
    out = dict(gt)
    if "_planted_review_objs" in out:
        out["planted_review_ids"] = [o.review_id for o in out.pop("_planted_review_objs")]
    if "_scatter_review_objs" in out:
        out["scattered_topic_review_ids"] = [o.review_id for o in out.pop("_scatter_review_objs")]
    if "_topic_review_objs" in out:
        out["topic_review_ids"] = [o.review_id for o in out.pop("_topic_review_objs")]
    if "_burst_review_objs" in out:
        burst_objs = out.pop("_burst_review_objs")
        review_to_template = out.pop("_review_to_template")
        out["burst_review_ids"] = [o.review_id for o in burst_objs]
        out["review_id_to_template_text"] = {
            burst_objs[i].review_id: text for i, text in review_to_template.items()
        }
    return out


def generate() -> tuple[list[RawReview], dict[str, Any]]:
    """Run the full synthetic generation pipeline and return (all_reviews, ground_truth_dict)."""
    pool = load_pool()
    all_reviews: list[RawReview] = []
    gt_entries: dict[str, dict[str, Any]] = {}

    for spec in BATCH_DEFECT_SPECS:
        reviews, gt = gen_batch_defect_planted(pool, spec)
        all_reviews.extend(reviews)
        gt_entries[spec["product_id"]] = gt

        matched_volume = gt["baseline_review_count"] + gt["spike_actual_count"]
        ctrl_reviews, ctrl_gt = gen_batch_defect_control(pool, spec, matched_volume)
        all_reviews.extend(ctrl_reviews)
        gt_entries[spec["control_id"]] = ctrl_gt

    for spec in TREND_SPECS:
        reviews, gt = gen_trend_planted(pool, spec)
        all_reviews.extend(reviews)
        gt_entries[spec["product_id"]] = gt

        matched_total = sum(gt["phase_actual_counts"])
        ctrl_reviews, ctrl_gt = gen_trend_control(pool, spec, matched_total)
        all_reviews.extend(ctrl_reviews)
        gt_entries[spec["control_id"]] = ctrl_gt

    for spec in CAMPAIGN_SPECS:
        reviews, gt = gen_campaign_planted(pool, spec)
        all_reviews.extend(reviews)
        gt_entries[spec["product_id"]] = gt

        matched_total = gt["baseline_review_count"] + gt["burst_review_count"]
        ctrl_reviews, ctrl_gt = gen_campaign_control(pool, spec, matched_total)
        all_reviews.extend(ctrl_reviews)
        gt_entries[spec["control_id"]] = ctrl_gt

    for product_id in NOISE_PRODUCT_IDS:
        reviews, gt = gen_noise(pool, product_id)
        all_reviews.extend(reviews)
        gt_entries[product_id] = gt

    # Global chronological sort + sequential review_id assignment, done once at the end so
    # every pattern generator above can be written without knowing final IDs in advance.
    all_reviews.sort(key=lambda r: (r.timestamp, r.product_id, r.source_review_id))
    for i, r in enumerate(all_reviews, start=1):
        r.review_id = f"syn-{i:06d}"

    resolved_gt = {pid: _resolve_object_refs(gt) for pid, gt in gt_entries.items()}

    # Cross-product reviewer-id reuse check: by construction, alloc_reviewer_id() always
    # returns a fresh id, and the only deliberate reuse is within a single campaign product's
    # reviewer pool (same product_id both times) -- so cross-*product* reuse should be exactly
    # zero. Verified here rather than assumed.
    reviewer_to_products: dict[str, set[str]] = {}
    for r in all_reviews:
        reviewer_to_products.setdefault(r.reviewer_id, set()).add(r.product_id)
    cross_product_reviewers = {
        rid: sorted(pids) for rid, pids in reviewer_to_products.items() if len(pids) > 1
    }

    ground_truth = {
        "_marker": "SYNTHETIC_TESTBED_GROUND_TRUTH",
        "_warning": BANNER,
        "_seed": SEED,
        "_window": {"start": fmt_ts(WINDOW_START), "end_exclusive": fmt_ts(WINDOW_END_EXCLUSIVE)},
        "_cross_product_reviewer_reuse": cross_product_reviewers,
        "products": resolved_gt,
    }
    return all_reviews, ground_truth


def write_outputs(all_reviews: list[RawReview], ground_truth: dict[str, Any]) -> None:
    """Write reviews.jsonl and ground_truth.json to OUT_DIR."""
    with REVIEWS_PATH.open("w", encoding="utf-8") as f:
        for r in all_reviews:
            f.write(json.dumps(r.to_json(), ensure_ascii=False))
            f.write("\n")

    with GROUND_TRUTH_PATH.open("w", encoding="utf-8") as f:
        json.dump(ground_truth, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main() -> None:
    """Entry point: generate the synthetic testbed and write reviews.jsonl + ground_truth.json."""
    all_reviews, ground_truth = generate()
    write_outputs(all_reviews, ground_truth)
    print(f"Wrote {len(all_reviews)} reviews to {REVIEWS_PATH}")
    print(f"Wrote ground truth for {len(ground_truth['products'])} products to {GROUND_TRUTH_PATH}")


if __name__ == "__main__":
    main()
