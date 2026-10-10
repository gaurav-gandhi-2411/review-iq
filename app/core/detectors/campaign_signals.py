"""Signal computation for the review-pattern detector (see app/core/detectors/campaign.py for the
alert rule and output, and docs/specs/campaign-detection.md for the pre-registered design).

Everything here is evaluated as-of a review's arrival using only earlier reviews (no look-ahead),
against the product's own trailing history. Standard library only.
"""

from __future__ import annotations

import math
import re
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

_PUNCT = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")
_WORD = re.compile(r"[a-z']+")

DAY_S = 86400.0
HOUR_S = 3600.0


@dataclass(frozen=True)
class CampaignParams:
    """Fixed structure plus the tuned thresholds. Tuned values come from the frozen result of
    benchmark/campaign_eval (tuning split only); the rest are pre-registered constants."""

    # tuned on the tuning split, FROZEN in reports/campaign_eval/frozen_params_grid_v2.json (the
    # same row was selected at both false-alert budgets and by grid v1); a test pins this
    similarity: float = 0.4  # char-5-gram Jaccard for "near-identical"
    k_min: int = 3  # smallest near-identical group that counts as a template
    theta_burst: float = 3.0  # -log10 tail probability
    theta_rating: float = 4.0  # |z| of window mean vs history mean
    strong_multiplier: float = 1.5  # one signal alone alerts at this multiple of its threshold
    use_mismatch: bool = False  # only enabled if the tuning ablation justifies it
    # fixed
    theta_mismatch: float = 3.0
    theta_verified: float = 3.0
    n_min: int = 5
    windows_hours: tuple[int, ...] = (6, 24, 72, 168)
    history_days: int = 180
    min_history_reviews: int = 15
    min_history_span_days: int = 30
    min_rated_history: int = 30
    dispersion_days: int = 90
    dispersion_cap: float = 20.0
    rate_floor_count: float = 0.05
    rating_sd_floor: float = 0.6
    min_words_template: int = 6
    min_chars_template: int = 30
    cooldown_days: int = 7
    warmup_reviews: int = 100
    warmup_days: int = 60
    store_min_similarity: float = 0.4  # lowest similarity kept in the pair lists (grid floor)


DEFAULT_PARAMS = CampaignParams()

_POS_WORDS = frozenset(
    [
        "love",
        "loved",
        "great",
        "excellent",
        "amazing",
        "perfect",
        "wonderful",
        "fantastic",
        "awesome",
        "best",
        "good",
        "happy",
        "recommend",
        "delicious",
        "fresh",
        "works",
        "nice",
        "fine",
        "superb",
        "brilliant",
        "satisfied",
        "pleased",
    ]
)
_NEG_WORDS = frozenset(
    [
        "hate",
        "hated",
        "terrible",
        "awful",
        "horrible",
        "worst",
        "bad",
        "poor",
        "disappointed",
        "disappointing",
        "waste",
        "useless",
        "broken",
        "broke",
        "refund",
        "return",
        "returned",
        "disgusting",
        "stale",
        "rancid",
        "defective",
        "cheap",
        "overpriced",
        "regret",
    ]
)


def normalize_cluster_text(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    return _WHITESPACE.sub(" ", _PUNCT.sub("", text)).strip().lower()


def lexicon_net_sentiment(text: str) -> int:
    """Positive-word count minus negative-word count. A heuristic, not a sentiment model."""
    words = _WORD.findall(text.lower())
    return sum(w in _POS_WORDS for w in words) - sum(w in _NEG_WORDS for w in words)


def is_rating_text_mismatch(rating: int | None, text: str) -> bool | None:
    """True/False if decidable, None when there is no rating or no usable wording."""
    if rating is None:
        return None
    net = lexicon_net_sentiment(text)
    return (rating >= 4 and net <= -2) or (rating <= 2 and net >= 2)


def _shingles(text: str, p: CampaignParams) -> frozenset[str] | None:
    """Character 5-gram set, or None when the text is too short/generic to count as a template
    (policy: under min_words_template words or min_chars_template characters is ineligible)."""
    norm = normalize_cluster_text(text)
    if len(norm.split()) < p.min_words_template or len(norm) < p.min_chars_template:
        return None
    return frozenset(norm[i : i + 5] for i in range(len(norm) - 4))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def epoch_seconds(ts: datetime) -> float:
    return (ts if ts.tzinfo else ts.replace(tzinfo=UTC)).timestamp()


def _poisson_evidence(k: int, m: float) -> float:
    """-log10 P(Poisson(m) >= k), clamped to [0, 12]. 0 when k <= m (not above expectation)."""
    if k <= 0 or k <= m:
        return 0.0
    log_pmf = -m + k * math.log(m) - math.lgamma(k + 1)
    term, total = 1.0, 1.0
    for j in range(k, k + 5000):
        term *= m / (j + 1)
        total += term
        if term < 1e-17 * total:
            break
    return min(12.0, max(0.0, -(log_pmf + math.log(total)) / math.log(10)))


@dataclass(frozen=True)
class Review:
    """One review. rating and verified are optional: the detector works without them."""

    review_id: str
    product_id: str
    timestamp: datetime
    text: str
    rating: int | None = None
    verified: bool | None = None


@dataclass(frozen=True)
class WindowEvidence:
    """Signal values for one (arrival, window width). None means the signal was not computable
    (for example no ratings): never a default value."""

    index: int
    width_hours: int
    n: int
    expected: float
    burst: float | None
    template_k: int
    template_members: tuple[int, ...]
    rating_z: float | None
    window_mean: float | None
    history_mean: float | None
    mismatch_z: float | None
    mismatch_count: int
    verified_z: float | None
    verified_window_share: float | None
    verified_history_share: float | None


class ProductStream:
    """One product's reviews with the prefix sums and pair lists needed for O(1)-ish evidence
    queries. Immutable after construction."""

    def __init__(
        self,
        reviews: Sequence[Review],
        params: CampaignParams = DEFAULT_PARAMS,
        shingle_cache: dict[str, frozenset[str] | None] | None = None,
    ):
        """`shingle_cache` (review_id -> shingles) lets an offline harness reuse shingles across
        many excerpts of one stream; production passes nothing."""
        self.p = params
        self.reviews = sorted(reviews, key=lambda r: (epoch_seconds(r.timestamp), r.review_id))
        self.ts = [epoch_seconds(r.timestamp) for r in self.reviews]
        n = len(self.reviews)
        # rating prefix sums over rated reviews
        self.cr, self.sr, self.qr = [0], [0.0], [0.0]
        # mismatch prefix: eligible (decidable) count and mismatch count
        self.ce, self.cm = [0], [0]
        # verified prefix: known count and verified count
        self.ck, self.cv = [0], [0]
        for r in self.reviews:
            rated = r.rating is not None
            x = float(r.rating) if r.rating is not None else 0.0
            self.cr.append(self.cr[-1] + rated)
            self.sr.append(self.sr[-1] + x)
            self.qr.append(self.qr[-1] + x * x)
            mm = is_rating_text_mismatch(r.rating, r.text)
            self.ce.append(self.ce[-1] + (mm is not None))
            self.cm.append(self.cm[-1] + bool(mm))
            self.ck.append(self.ck[-1] + (r.verified is not None))
            self.cv.append(self.cv[-1] + bool(r.verified))
        # daily counts (dense from first to last day) for the dispersion estimate
        self.day0 = int(self.ts[0] // DAY_S) if n else 0
        ndays = (int(self.ts[-1] // DAY_S) - self.day0 + 1) if n else 0
        daily = [0] * ndays
        for t in self.ts:
            daily[int(t // DAY_S) - self.day0] += 1
        self.dc, self.dq = [0], [0]
        for c in daily:
            self.dc.append(self.dc[-1] + c)
            self.dq.append(self.dq[-1] + c * c)
        # template: shingles + similarity pairs within the widest window
        if shingle_cache is None:
            self.sh = [_shingles(r.text, params) for r in self.reviews]
        else:
            self.sh = []
            for r in self.reviews:
                if r.review_id not in shingle_cache:
                    shingle_cache[r.review_id] = _shingles(r.text, params)
                self.sh.append(shingle_cache[r.review_id])
        self.nbrs: list[list[tuple[int, float]]] = [[] for _ in range(n)]
        span = max(params.windows_hours) * HOUR_S
        floor = params.store_min_similarity
        for j in range(n):
            sj = self.sh[j]
            if sj is None:
                continue
            lj = len(sj)
            i = j - 1
            while i >= 0 and self.ts[j] - self.ts[i] < span:
                si = self.sh[i]
                if si is not None:
                    li = len(si)
                    if min(li, lj) / max(li, lj) >= floor:
                        sim = jaccard(si, sj)
                        if sim >= floor:
                            self.nbrs[j].append((i, sim))
                i -= 1

    def __len__(self) -> int:
        return len(self.reviews)

    # ---- evidence -------------------------------------------------------------------------

    def evidence_at(self, i: int, width_hours: int) -> WindowEvidence | None:
        """Evidence for the window (t_i - W, t_i] against the trailing history; None if the
        window is too small or the history too thin to judge (cold start: no default)."""
        p = self.p
        t_i = self.ts[i]
        w_s = width_hours * HOUR_S
        start = t_i - w_s
        lo = bisect_right(self.ts, start)
        n_w = i - lo + 1
        if n_w < p.n_min:
            return None
        hist_lo = bisect_left(self.ts, start - p.history_days * DAY_S)
        n_h = lo - hist_lo
        if (
            n_h < p.min_history_reviews
            or start - self.ts[hist_lo] < p.min_history_span_days * DAY_S
        ):
            return None

        span_days = min(float(p.history_days), (start - self.ts[0]) / DAY_S)
        expected = max(n_h / span_days * (w_s / DAY_S), p.rate_floor_count)
        phi = self._dispersion(start)
        burst = _poisson_evidence(math.ceil(n_w / phi), expected / phi)

        k, members = self.largest_cluster(lo, i)

        rating_z = w_mean = h_mean = None
        n_wr = self.cr[i + 1] - self.cr[lo]
        n_hr = self.cr[lo] - self.cr[hist_lo]
        if n_wr >= p.n_min and n_hr >= p.min_rated_history:
            w_mean = (self.sr[i + 1] - self.sr[lo]) / n_wr
            h_mean = (self.sr[lo] - self.sr[hist_lo]) / n_hr
            var_h = max(0.0, (self.qr[lo] - self.qr[hist_lo]) / n_hr - h_mean * h_mean)
            sd_h = max(math.sqrt(var_h), p.rating_sd_floor)
            rating_z = (w_mean - h_mean) / (sd_h * math.sqrt(1 / n_wr + 1 / n_hr))

        mismatch_z = None
        e_w = self.ce[i + 1] - self.ce[lo]
        e_h = self.ce[lo] - self.ce[hist_lo]
        m_w = self.cm[i + 1] - self.cm[lo]
        if e_w >= p.n_min and e_h >= p.min_rated_history:
            p_h = max((self.cm[lo] - self.cm[hist_lo]) / e_h, 0.02)
            mismatch_z = max(0.0, (m_w / e_w - p_h) / math.sqrt(p_h * (1 - p_h) / e_w))

        verified_z = vw = vh = None
        k_w = self.ck[i + 1] - self.ck[lo]
        k_h = self.ck[lo] - self.ck[hist_lo]
        if k_w >= 0.8 * n_w and k_h >= 0.8 * n_h and k_h >= p.min_rated_history:
            vw = (self.cv[i + 1] - self.cv[lo]) / k_w
            vh = (self.cv[lo] - self.cv[hist_lo]) / k_h
            ph = min(max(vh, 0.02), 0.98)
            verified_z = max(0.0, (ph - vw) / math.sqrt(ph * (1 - ph) / k_w))

        return WindowEvidence(
            index=i,
            width_hours=width_hours,
            n=n_w,
            expected=expected,
            burst=burst,
            template_k=k,
            template_members=members,
            rating_z=rating_z,
            window_mean=w_mean,
            history_mean=h_mean,
            mismatch_z=mismatch_z,
            mismatch_count=m_w,
            verified_z=verified_z,
            verified_window_share=vw,
            verified_history_share=vh,
        )

    def _dispersion(self, start: float) -> float:
        """Variance/mean of daily counts over the dispersion window before `start`, in [1, cap]."""
        p = self.p
        d_end = min(int(start // DAY_S) - self.day0, len(self.dc) - 1)
        d_beg = max(0, d_end - p.dispersion_days)
        m = d_end - d_beg
        if m < 14:
            return 1.0
        total = self.dc[d_end] - self.dc[d_beg]
        sq = self.dq[d_end] - self.dq[d_beg]
        mean = total / m
        if mean <= 0:
            return 1.0
        var = max(0.0, (sq - total * total / m) / (m - 1))
        return min(max(var / mean, 1.0), p.dispersion_cap)

    def largest_cluster(
        self, lo: int, hi: int, similarity: float | None = None
    ) -> tuple[int, tuple[int, ...]]:
        """Largest connected group of reviews in [lo, hi] linked by similarity >= `similarity`
        (default p.similarity; a harness may ask for any value >= p.store_min_similarity)."""
        parent: dict[int, int] = {}

        def find(a: int) -> int:
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        s = self.p.similarity if similarity is None else similarity
        for j in range(lo, hi + 1):
            for i, sim in self.nbrs[j]:
                if i >= lo and sim >= s:
                    parent.setdefault(i, i)
                    parent.setdefault(j, j)
                    ri, rj = find(i), find(j)
                    if ri != rj:
                        parent[ri] = rj
        if not parent:
            return 0, ()
        groups: dict[int, list[int]] = defaultdict(list)
        for a in parent:
            groups[find(a)].append(a)
        best = max(groups.values(), key=lambda g: (len(g), -min(g)))
        return len(best), tuple(sorted(best))
