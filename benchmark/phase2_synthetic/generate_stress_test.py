"""Stress test for the Phase 2 detectors -- harder, subtler planted patterns and AMBIGUOUS
controls the detectors have never seen, built AFTER all detector thresholds were finalized.

SYNTHETIC TESTBED, STRESS-TEST VARIANT -- same real-text-fabricated-metadata construction as
generate_synthetic_testbed.py, but deliberately harder: weak/borderline planted signals instead
of extreme ones, plus controls specifically designed to tempt a false alarm (organic bursts,
chronic-but-flat complaint volume, popular products with genuinely similar organic praise).
This is the actual test of whether detector thresholds generalize, not just fit the original
18-product set -- see project_phase2_synthetic_testbed.md memory for why this was necessary
(perfect 1.0/1.0 on N=2 planted is not evidence of a working detector).

Separate file, separate ground truth -- does NOT touch or regenerate the original
reviews.jsonl/ground_truth.json.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

random.seed(1337)  # different seed from the original set -- genuinely new draws, not a replay

ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = ROOT / "data" / "processed" / "flipkart_deduped.jsonl"
OUT_DIR = Path(__file__).resolve().parent
REVIEWS_PATH = OUT_DIR / "stress_reviews.jsonl"
GT_PATH = OUT_DIR / "stress_ground_truth.json"

WINDOW_START = datetime(2026, 7, 6, tzinfo=None)
N_WEEKS = 16
WINDOW_END_EXCLUSIVE = WINDOW_START + timedelta(weeks=N_WEEKS)


def fmt_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def random_timestamp(start: datetime, end: datetime) -> datetime:
    delta = (end - start).total_seconds()
    return start + timedelta(seconds=random.uniform(0, delta))


def week_range(week_idx: int) -> tuple[datetime, datetime]:
    s = WINDOW_START + timedelta(weeks=week_idx)
    return s, s + timedelta(weeks=1)


_reviewer_counter = 0


def alloc_reviewer_id() -> str:
    global _reviewer_counter
    _reviewer_counter += 1
    return f"stress-rev-{_reviewer_counter:04d}"


@dataclass
class RawReview:
    product_id: str
    reviewer_id: str
    timestamp: datetime
    rating: str
    text: str
    source_review_id: str
    product_category: str

    def to_json(self) -> dict[str, Any]:
        return {
            "review_id": "",  # filled in at write time (global sequential numbering)
            "product_id": self.product_id,
            "reviewer_id": self.reviewer_id,
            "timestamp": fmt_ts(self.timestamp),
            "rating": self.rating,
            "text": self.text,
            "source_review_id": self.source_review_id,
            "product_category": self.product_category,
        }


class SourcePool:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._records = records
        self._used_ids: set[str] = set()

    def _available(self) -> list[dict[str, Any]]:
        return [r for r in self._records if r["id"] not in self._used_ids]

    def mark_used(self, records: list[dict[str, Any]]) -> None:
        for r in records:
            self._used_ids.add(r["id"])

    def sample_diverse(self, n: int) -> list[dict[str, Any]]:
        chosen = random.sample(self._available(), n)
        self.mark_used(chosen)
        return chosen

    def find_matching(
        self,
        keyword_re: str,
        neg_re: str | None = None,
        max_rate: int | None = None,
        min_rate: int | None = None,
    ) -> list[dict[str, Any]]:
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
            if min_rate is not None and int(r["rate"]) < min_rate:
                continue
            out.append(r)
        return out

    def sample_by_rating(
        self, n: int, max_rate: int | None = None, min_rate: int | None = None
    ) -> list[dict[str, Any]]:
        """Sample n reviews filtered only by rating (any topic/text) -- used to construct
        aggregate, not-tied-to-one-topic sentiment-ratio patterns. A handful of corpus records
        have a malformed (non-numeric) `rate` field -- skipped, same data-quality issue every
        other rating-filtered path in this pool implicitly avoids by filtering on keyword_re
        first (which this method deliberately doesn't)."""
        candidates = []
        for r in self._available():
            if not str(r["rate"]).isdigit():
                continue
            rate = int(r["rate"])
            if max_rate is not None and rate > max_rate:
                continue
            if min_rate is not None and rate < min_rate:
                continue
            candidates.append(r)
        n = min(n, len(candidates))
        chosen = random.sample(candidates, n)
        self.mark_used(chosen)
        return chosen

    def find_short_praise(self, max_len: int = 25, max_words: int = 4) -> list[dict[str, Any]]:
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
        n = min(n, len(matches))
        chosen = random.sample(matches, n)
        self.mark_used(chosen)
        return chosen


def load_pool() -> SourcePool:
    records = []
    with CORPUS_PATH.open(encoding="utf-8") as f:
        for line in f:
            records.append(json.loads(line))
    return SourcePool(records)


all_reviews: list[RawReview] = []
ground_truth: dict[str, Any] = {}


# ============================================================================================
# 1. WEAK batch-defect: 4 reviews (bare minimum), ~2x baseline -- below SPIKE_RATIO_THRESHOLD,
#    tests whether the detector's sensitivity is real or only works on extreme cases.
# ============================================================================================
def gen_weak_batch_defect(pool: SourcePool) -> None:
    product_id = "STRESS-BATCH-WEAK"
    keyword_re = r"charger|charging"
    neg_re = r"stop|stopped|not\s+work|dead|fail|slow|damag"
    baseline = pool.sample_diverse(40)
    baseline_reviews = [
        RawReview(product_id, alloc_reviewer_id(), random_timestamp(WINDOW_START, WINDOW_END_EXCLUSIVE),
                  r["rate"], r["text"], r["id"], "stress_batch_weak")
        for r in baseline
    ]
    matches = pool.find_matching(keyword_re, neg_re=neg_re, max_rate=2)
    spike_start, _ = week_range(8)
    spike_end = spike_start + timedelta(days=10)  # spread across the FULL window width, not tight
    spike_source = pool.take_sample(matches, 4)  # bare MIN_ABSOLUTE_SPIKE_COUNT
    spike_reviews = [
        RawReview(product_id, alloc_reviewer_id(), random_timestamp(spike_start, spike_end),
                  r["rate"], r["text"], r["id"], "stress_batch_weak")
        for r in spike_source
    ]
    all_reviews.extend(baseline_reviews + spike_reviews)
    ground_truth[product_id] = {
        "product_id": product_id, "pattern": "batch_defect", "is_control": False,
        "difficulty": "weak", "topic_keyword": "charger", "keyword_regex": keyword_re,
        "spike_target_count": 4, "spike_actual_count": len(spike_reviews),
        "spike_available_matches": len(matches),
        "planted_review_ids": [], "_planted_objs": spike_reviews,
    }


# ============================================================================================
# 2. MODERATE batch-defect: 5 reviews, moderate concentration -- between the weak case and the
#    original extreme case (8 reviews, 26x ratio).
# ============================================================================================
def gen_moderate_batch_defect(pool: SourcePool) -> None:
    product_id = "STRESS-BATCH-MODERATE"
    keyword_re = r"hinge|zipper"
    neg_re = r"broke|broken|crack|tear|torn|loose|damag"
    baseline = pool.sample_diverse(45)
    baseline_reviews = [
        RawReview(product_id, alloc_reviewer_id(), random_timestamp(WINDOW_START, WINDOW_END_EXCLUSIVE),
                  r["rate"], r["text"], r["id"], "stress_batch_moderate")
        for r in baseline
    ]
    matches = pool.find_matching(keyword_re, neg_re=neg_re, max_rate=2)
    spike_start, _ = week_range(11)
    spike_end = spike_start + timedelta(days=10)
    spike_source = pool.take_sample(matches, 5)
    spike_reviews = [
        RawReview(product_id, alloc_reviewer_id(), random_timestamp(spike_start, spike_end),
                  r["rate"], r["text"], r["id"], "stress_batch_moderate")
        for r in spike_source
    ]
    all_reviews.extend(baseline_reviews + spike_reviews)
    ground_truth[product_id] = {
        "product_id": product_id, "pattern": "batch_defect", "is_control": False,
        "difficulty": "moderate", "topic_keyword": "hinge_zipper", "keyword_regex": keyword_re,
        "spike_target_count": 5, "spike_actual_count": len(spike_reviews),
        "spike_available_matches": len(matches),
        "planted_review_ids": [], "_planted_objs": spike_reviews,
    }


# ============================================================================================
# 3. SLOW trend: rise of exactly MIN_RISE (3), gentle slope [3,4,5,6] instead of [1,3,5,8].
# ============================================================================================
def gen_slow_trend(pool: SourcePool) -> None:
    product_id = "STRESS-TREND-SLOW"
    keyword_re = r"customer\s+service|support|helpline"
    neg_re = r"rude|slow|unhelpful|no\s+response|ignor|poor|bad|worst"
    baseline_reviews: list[RawReview] = []
    for week_idx in range(N_WEEKS):
        n = random.choice([3, 4])
        start, end = week_range(week_idx)
        for r in pool.sample_diverse(n):
            baseline_reviews.append(
                RawReview(product_id, alloc_reviewer_id(), random_timestamp(start, end),
                          r["rate"], r["text"], r["id"], "stress_trend_slow")
            )
    matches = pool.find_matching(keyword_re, neg_re=neg_re, max_rate=2)
    random.shuffle(matches)
    phase_targets = [3, 4, 5, 6]
    topic_reviews: list[RawReview] = []
    phase_actual = []
    cursor = 0
    for phase_idx, target in enumerate(phase_targets):
        s = WINDOW_START + timedelta(weeks=phase_idx * 4)
        e = s + timedelta(weeks=4)
        take = matches[cursor:cursor + target]
        cursor += len(take)
        pool.mark_used(take)
        for r in take:
            topic_reviews.append(
                RawReview(product_id, alloc_reviewer_id(), random_timestamp(s, e),
                          r["rate"], r["text"], r["id"], "stress_trend_slow")
            )
        phase_actual.append(len(take))
    all_reviews.extend(baseline_reviews + topic_reviews)
    ground_truth[product_id] = {
        "product_id": product_id, "pattern": "trend", "is_control": False,
        "difficulty": "slow", "topic_keyword": "customer_service", "keyword_regex": keyword_re,
        "phase_targets": phase_targets, "phase_actual_counts": phase_actual,
        "planted_review_ids": [r.reviewer_id for r in topic_reviews],
    }


# ============================================================================================
# 4. SMALL fake-campaign: 5 reviews (bare MIN_BURST_COUNT), 3 reviewers, 2 distinct texts.
# ============================================================================================
def gen_small_campaign(pool: SourcePool) -> None:
    product_id = "STRESS-CAMPAIGN-SMALL"
    baseline = pool.sample_diverse(30)
    baseline_reviews = [
        RawReview(product_id, alloc_reviewer_id(), random_timestamp(WINDOW_START, WINDOW_END_EXCLUSIVE),
                  r["rate"], r["text"], r["id"], "stress_campaign_small")
        for r in baseline
    ]
    templates = pool.take_sample(pool.find_short_praise(), 2)
    burst_start, _ = week_range(9)
    burst_end = burst_start + timedelta(hours=48)
    reviewer_pool = [alloc_reviewer_id() for _ in range(3)]
    burst_reviews: list[RawReview] = []
    idx = 0  # 5 reviews from 3 reviewers, 2 templates (3+2 split)
    for t_idx, template in enumerate(templates):
        n_uses = 3 if t_idx == 0 else 2
        for _ in range(n_uses):
            rid = reviewer_pool[idx % 3]
            idx += 1
            burst_reviews.append(
                RawReview(product_id, rid, random_timestamp(burst_start, burst_end),
                          template["rate"], template["text"], template["id"], "stress_campaign_small")
            )
    all_reviews.extend(baseline_reviews + burst_reviews)
    ground_truth[product_id] = {
        "product_id": product_id, "pattern": "fake_campaign", "is_control": False,
        "difficulty": "small", "burst_review_count": len(burst_reviews),
        "reviewer_ids_used": reviewer_pool,
        "burst_review_ids": [], "_burst_objs": burst_reviews,
    }


# ============================================================================================
# 5. AMBIGUOUS CONTROL: chronic high-but-FLAT complaint volume about the SAME topic every week
#    -- tests batch-defect (no spike should fire) and trend (no rise should fire) against a
#    genuinely bad product that is NOT worsening and NOT a discrete defect event.
# ============================================================================================
def gen_chronic_complaint_control(pool: SourcePool) -> None:
    product_id = "STRESS-CTRL-CHRONIC"
    keyword_re = r"battery"
    neg_re = r"die|dies|died|drain|drains|draining|weak|poor|bad|not\s+last|discharg"
    matches = pool.find_matching(keyword_re, neg_re=neg_re, max_rate=2)
    random.shuffle(matches)
    reviews: list[RawReview] = []
    # 2 complaints/week, every week, no rise, no spike -- chronic, not acute.
    per_week = 2
    cursor = 0
    for week_idx in range(N_WEEKS):
        s, e = week_range(week_idx)
        take = matches[cursor:cursor + per_week]
        cursor += len(take)
        pool.mark_used(take)
        for r in take:
            reviews.append(
                RawReview(product_id, alloc_reviewer_id(), random_timestamp(s, e),
                          r["rate"], r["text"], r["id"], "stress_ctrl_chronic")
            )
    # plus diverse non-topic baseline
    baseline = pool.sample_diverse(20)
    for r in baseline:
        reviews.append(
            RawReview(product_id, alloc_reviewer_id(), random_timestamp(WINDOW_START, WINDOW_END_EXCLUSIVE),
                      r["rate"], r["text"], r["id"], "stress_ctrl_chronic")
        )
    all_reviews.extend(reviews)
    ground_truth[product_id] = {
        "product_id": product_id, "pattern": None, "is_control": True,
        "difficulty": "ambiguous_chronic_complaint", "topic_keyword": "battery",
        "note": "genuinely bad product, steady complaint rate, NOT a batch defect or a trend",
    }


# ============================================================================================
# 6. AMBIGUOUS CONTROL: organic burst -- many DIFFERENT real reviewers post UNIQUE text in a
#    tight time window (simulating a real flash-sale/viral moment), tests fake-campaign doesn't
#    mistake "organically popular right now" for "coordinated".
# ============================================================================================
def gen_organic_burst_control(pool: SourcePool) -> None:
    product_id = "STRESS-CTRL-ORGANIC-BURST"
    baseline = pool.sample_diverse(25)
    reviews = [
        RawReview(product_id, alloc_reviewer_id(), random_timestamp(WINDOW_START, WINDOW_END_EXCLUSIVE),
                  r["rate"], r["text"], r["id"], "stress_ctrl_organic_burst")
        for r in baseline
    ]
    burst_start, _ = week_range(10)
    burst_end = burst_start + timedelta(hours=48)
    burst_source = pool.sample_diverse(9)  # a real burst, but every reviewer + text unique
    for r in burst_source:
        reviews.append(
            RawReview(product_id, alloc_reviewer_id(), random_timestamp(burst_start, burst_end),
                      r["rate"], r["text"], r["id"], "stress_ctrl_organic_burst")
        )
    all_reviews.extend(reviews)
    ground_truth[product_id] = {
        "product_id": product_id, "pattern": None, "is_control": True,
        "difficulty": "ambiguous_organic_burst",
        "note": "9 reviews in 48h from 9 unique reviewers, 9 unique texts -- a real flash-sale-"
                "shaped spike, not coordination",
    }


# ============================================================================================
# 7. AMBIGUOUS CONTROL: popular product, many organic SHORT similar-sounding positive reviews
#    (real people independently saying "great product"/"love it"), spread over time (no burst).
# ============================================================================================
def gen_popular_similar_control(pool: SourcePool) -> None:
    product_id = "STRESS-CTRL-POPULAR-SIMILAR"
    short_praise_pool = pool.find_short_praise()
    chosen = pool.take_sample(short_praise_pool, 25)
    reviews = [
        RawReview(product_id, alloc_reviewer_id(), random_timestamp(WINDOW_START, WINDOW_END_EXCLUSIVE),
                  r["rate"], r["text"], r["id"], "stress_ctrl_popular_similar")
        for r in chosen
    ]
    diverse = pool.sample_diverse(20)
    for r in diverse:
        reviews.append(
            RawReview(product_id, alloc_reviewer_id(), random_timestamp(WINDOW_START, WINDOW_END_EXCLUSIVE),
                      r["rate"], r["text"], r["id"], "stress_ctrl_popular_similar")
        )
    all_reviews.extend(reviews)
    ground_truth[product_id] = {
        "product_id": product_id, "pattern": None, "is_control": True,
        "difficulty": "ambiguous_popular_similar",
        "note": "45 reviews incl. 25 organic short-praise texts (unique reviewers, spread over "
                "16 weeks, no timing burst) -- textually repetitive but not coordinated",
    }


# ============================================================================================
# 8. AMBIGUOUS CONTROL (hardest one): a REAL burst (e.g. post-flash-sale) where many DIFFERENT,
#    independent reviewers each write DIFFERENT-but-similar short praise -- the genuinely hard
#    case for the fake-campaign detector's text-dup signal, since it combines both a qualifying
#    timing burst AND textually-similar-sounding reviews, without any actual coordination. Neither
#    gen_organic_burst_control (unique text) nor gen_popular_similar_control (no burst timing)
#    covers this combination on its own.
# ============================================================================================
def gen_organic_burst_similar_text_control(pool: SourcePool) -> None:
    product_id = "STRESS-CTRL-ORGANIC-BURST-SIMILAR-TEXT"
    baseline = pool.sample_diverse(22)
    reviews = [
        RawReview(product_id, alloc_reviewer_id(), random_timestamp(WINDOW_START, WINDOW_END_EXCLUSIVE),
                  r["rate"], r["text"], r["id"], "stress_ctrl_organic_burst_similar_text")
        for r in baseline
    ]
    burst_start, _ = week_range(13)
    burst_end = burst_start + timedelta(hours=48)
    burst_source = pool.take_sample(pool.find_short_praise(), 11)
    for r in burst_source:
        reviews.append(
            RawReview(product_id, alloc_reviewer_id(), random_timestamp(burst_start, burst_end),
                      r["rate"], r["text"], r["id"], "stress_ctrl_organic_burst_similar_text")
        )
    all_reviews.extend(reviews)
    ground_truth[product_id] = {
        "product_id": product_id, "pattern": None, "is_control": True,
        "difficulty": "ambiguous_organic_burst_similar_text",
        "note": f"{len(burst_source)} reviews in a real 48h burst window, each from a unique "
                "reviewer, each independently short-praise text (similar tone/length, not "
                "coordinated) -- the hardest control: a genuine timing burst COMBINED with "
                "textually similar-sounding reviews, with zero actual coordination.",
    }


# ============================================================================================
# 9. RISING POSITIVE trend -- entirely outside batch-defect's negatives-only scope. A specific
#    topic's POSITIVE mentions rise over the observation window (e.g. a product that genuinely
#    improved). Same gentle shape as gen_slow_trend, mirrored to the positive polarity.
# ============================================================================================
def gen_rising_positive_trend(pool: SourcePool) -> None:
    product_id = "STRESS-TREND-POSITIVE"
    keyword_re = r"warranty|replacement|support\s+team"
    pos_re = r"quick|fast|resolved|helpful|great\s+service|excellent|responsive|sorted"
    baseline_reviews: list[RawReview] = []
    for week_idx in range(N_WEEKS):
        n = random.choice([3, 4])
        start, end = week_range(week_idx)
        for r in pool.sample_diverse(n):
            baseline_reviews.append(
                RawReview(product_id, alloc_reviewer_id(), random_timestamp(start, end),
                          r["rate"], r["text"], r["id"], "stress_trend_positive")
            )
    matches = pool.find_matching(keyword_re, neg_re=pos_re, min_rate=4)
    random.shuffle(matches)
    phase_targets = [2, 3, 4, 6]
    topic_reviews: list[RawReview] = []
    phase_actual = []
    cursor = 0
    for phase_idx, target in enumerate(phase_targets):
        s = WINDOW_START + timedelta(weeks=phase_idx * 4)
        e = s + timedelta(weeks=4)
        take = matches[cursor:cursor + target]
        cursor += len(take)
        pool.mark_used(take)
        for r in take:
            topic_reviews.append(
                RawReview(product_id, alloc_reviewer_id(), random_timestamp(s, e),
                          r["rate"], r["text"], r["id"], "stress_trend_positive")
            )
        phase_actual.append(len(take))
    all_reviews.extend(baseline_reviews + topic_reviews)
    ground_truth[product_id] = {
        "product_id": product_id, "pattern": "trend", "is_control": False,
        "difficulty": "rising_positive", "topic_keyword": "warranty_support",
        "keyword_regex": keyword_re, "trend_polarity": "positive",
        "phase_targets": phase_targets, "phase_actual_counts": phase_actual,
        "planted_review_ids": [r.reviewer_id for r in topic_reviews],
    }


# ============================================================================================
# 10. AGGREGATE slow negative drift -- NOT tied to any single topic (diverse categories/text each
#     phase), only the overall negative-review PROPORTION climbs gradually. Tests the aggregate
#     scan in isolation: no individual topic should reach MIN_TOTAL_MENTIONS since the negative
#     reviews are drawn from unrelated products/topics.
# ============================================================================================
def gen_aggregate_negative_drift(pool: SourcePool) -> None:
    product_id = "STRESS-TREND-AGGREGATE-DRIFT"
    reviews: list[RawReview] = []
    phase_neg_target = [2, 3, 5, 7]  # rising negative share
    phase_pos_target = [8, 7, 5, 3]  # falling positive share, steady-ish total volume per phase
    for phase_idx, (n_neg, n_pos) in enumerate(
        zip(phase_neg_target, phase_pos_target, strict=True)
    ):
        s = WINDOW_START + timedelta(weeks=phase_idx * 4)
        e = s + timedelta(weeks=4)
        neg = pool.sample_by_rating(n_neg, max_rate=2)
        pos = pool.sample_by_rating(n_pos, min_rate=4)
        for r in neg + pos:
            reviews.append(
                RawReview(product_id, alloc_reviewer_id(), random_timestamp(s, e),
                          r["rate"], r["text"], r["id"], "stress_trend_aggregate_drift")
            )
    all_reviews.extend(reviews)
    ground_truth[product_id] = {
        "product_id": product_id, "pattern": "trend", "is_control": False,
        "difficulty": "aggregate_drift", "trend_polarity": "negative",
        "trend_scope": "aggregate",
        "phase_neg_target": phase_neg_target, "phase_pos_target": phase_pos_target,
        "note": "no single topic dominates -- only the aggregate scan should catch this",
    }


# ============================================================================================
# 11. AMBIGUOUS CONTROL: random fluctuation -- negative-review share bounces up and down across
#     phases with no sustained direction. Tests that noise doesn't spuriously correlate into a
#     "trend" (the MIN_NON_DECREASING_STEPS / correlation guards should reject this).
# ============================================================================================
def gen_random_fluctuation_control(pool: SourcePool) -> None:
    product_id = "STRESS-CTRL-RANDOM-FLUCTUATION"
    reviews: list[RawReview] = []
    # up, down, up, down -- no monotonic direction, but each phase individually plausible.
    phase_neg_target = [6, 2, 7, 3]
    phase_pos_target = [4, 8, 3, 7]
    for phase_idx, (n_neg, n_pos) in enumerate(
        zip(phase_neg_target, phase_pos_target, strict=True)
    ):
        s = WINDOW_START + timedelta(weeks=phase_idx * 4)
        e = s + timedelta(weeks=4)
        neg = pool.sample_by_rating(n_neg, max_rate=2)
        pos = pool.sample_by_rating(n_pos, min_rate=4)
        for r in neg + pos:
            reviews.append(
                RawReview(product_id, alloc_reviewer_id(), random_timestamp(s, e),
                          r["rate"], r["text"], r["id"], "stress_ctrl_random_fluctuation")
            )
    all_reviews.extend(reviews)
    ground_truth[product_id] = {
        "product_id": product_id, "pattern": None, "is_control": True,
        "difficulty": "ambiguous_random_fluctuation",
        "note": "negative-review share bounces up/down across phases (no sustained direction) "
                "-- neither batch_defect, trend, nor campaign should fire",
    }


def main() -> None:
    pool = load_pool()
    gen_weak_batch_defect(pool)
    gen_moderate_batch_defect(pool)
    gen_slow_trend(pool)
    gen_small_campaign(pool)
    gen_chronic_complaint_control(pool)
    gen_organic_burst_control(pool)
    gen_popular_similar_control(pool)
    gen_organic_burst_similar_text_control(pool)
    gen_rising_positive_trend(pool)
    gen_aggregate_negative_drift(pool)
    gen_random_fluctuation_control(pool)

    all_reviews.sort(key=lambda r: (r.product_id, r.timestamp))
    with REVIEWS_PATH.open("w", encoding="utf-8") as f:
        for i, r in enumerate(all_reviews, start=1):
            rec = r.to_json()
            rec["review_id"] = f"stress-{i:06d}"
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # Backfill review_ids into ground truth's planted-object lists now that IDs are assigned.
    id_by_identity = {}
    with REVIEWS_PATH.open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            id_by_identity[(rec["product_id"], rec["timestamp"], rec["source_review_id"])] = rec["review_id"]

    for pid, entry in ground_truth.items():
        for key in ("_planted_objs", "_burst_objs"):
            if key in entry:
                ids = [
                    id_by_identity[(pid, fmt_ts(r.timestamp), r.source_review_id)]
                    for r in entry[key]
                ]
                if key == "_planted_objs":
                    entry["planted_review_ids"] = ids
                else:
                    entry["burst_review_ids"] = ids
                del entry[key]

    GT_PATH.write_text(
        json.dumps(
            {
                "_marker": "STRESS_TEST",
                "_warning": (
                    "STRESS TEST -- harder/subtler patterns + adversarial controls the "
                    "detectors have NEVER been tuned against. Built AFTER all detector "
                    "thresholds were finalized. Real text from the licensed corpus; "
                    "fabricated timestamps/reviewer-IDs. NOT proven on real seller data."
                ),
                "_seed": 1337,
                "products": ground_truth,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {len(all_reviews)} reviews to {REVIEWS_PATH}")
    print(f"Wrote ground truth for {len(ground_truth)} products to {GT_PATH}")
    for pid, e in ground_truth.items():
        print(f"  {pid}: pattern={e['pattern']} is_control={e['is_control']} difficulty={e['difficulty']}")


if __name__ == "__main__":
    main()
