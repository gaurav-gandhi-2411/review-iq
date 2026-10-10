"""Unit tests for app.core.detectors.campaign_signals (signal evidence, no alert rule)."""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta

import pytest
from app.core.detectors.campaign_signals import (
    CampaignParams,
    ProductStream,
    Review,
    _poisson_evidence,
    is_rating_text_mismatch,
    jaccard,
)

T0 = datetime(2024, 1, 1, tzinfo=UTC)
VOCAB = [f"word{k}x" for k in range(400)]


def _background(n: int = 240, days: float = 240.0, seed: int = 1, rating: int | None = 5):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        text = " ".join(rng.choice(VOCAB) for _ in range(9))
        r = None if rating is None else rng.choice([4, 5, 5, 5, 5])
        out.append(Review(f"b{i}", "P", T0 + timedelta(days=days * i / n), text, r))
    return out


def _burst(start: datetime, n: int, hours: float, rating: int | None, text: str | None = None):
    rng = random.Random(99)
    return [
        Review(
            f"c{i}",
            "P",
            start + timedelta(hours=hours * i / n),
            text if text is not None else " ".join(rng.choice(VOCAB) for _ in range(9)),
            rating,
        )
        for i in range(n)
    ]


def _last_index(stream: ProductStream, review_id: str) -> int:
    return next(i for i, r in enumerate(stream.reviews) if r.review_id == review_id)


def test_poisson_evidence_matches_closed_form() -> None:
    # P(Poisson(1) >= 5) = 0.0036598 -> 2.4366
    assert _poisson_evidence(5, 1.0) == pytest.approx(-math.log10(0.00365985), abs=1e-3)
    assert _poisson_evidence(1, 3.0) == 0.0  # at or below expectation: no evidence
    assert _poisson_evidence(100, 0.05) == 12.0  # clamped


def test_burst_evidence_high_for_a_burst_and_low_for_steady_flow() -> None:
    reviews = _background() + _burst(T0 + timedelta(days=200), 12, 5, 5)
    s = ProductStream(reviews)
    i = _last_index(s, "c11")
    ev = s.evidence_at(i, 6)
    assert ev is not None and ev.n >= 12
    assert ev.burst is not None and ev.burst > 8
    steady = s.evidence_at(_last_index(s, "b150"), 24)
    assert steady is None or (steady.burst is not None and steady.burst < 3)


def test_cold_start_returns_none_not_a_default() -> None:
    s = ProductStream(_burst(T0, 12, 5, 5))  # no history at all
    assert s.evidence_at(11, 24) is None
    thin = _background(n=10, days=100) + _burst(T0 + timedelta(days=120), 8, 3, 5)
    s2 = ProductStream(thin)
    assert s2.evidence_at(len(s2) - 1, 24) is None  # history under 15 reviews


def test_no_look_ahead_appending_later_reviews_does_not_change_earlier_evidence() -> None:
    base = _background() + _burst(T0 + timedelta(days=200), 8, 4, 1)
    later = base + _burst(T0 + timedelta(days=230), 20, 2, 1)
    a, b = ProductStream(base), ProductStream(later)
    i = _last_index(a, "c7")
    ev_a, ev_b = a.evidence_at(i, 24), b.evidence_at(_last_index(b, "c7"), 24)
    assert ev_a is not None and ev_b is not None
    assert (ev_a.burst, ev_a.template_k, ev_a.rating_z) == (
        ev_b.burst,
        ev_b.template_k,
        ev_b.rating_z,
    )


def test_template_counts_near_identical_text_and_ignores_short_generic_text() -> None:
    tmpl = "this product exceeded my expectations completely and arrived very quickly"
    reviews = _background() + [
        Review(f"t{i}", "P", T0 + timedelta(days=200, hours=i), tmpl + (" ok" if i % 2 else ""), 5)
        for i in range(7)
    ]
    s = ProductStream(reviews)
    ev = s.evidence_at(_last_index(s, "t6"), 24)
    assert ev is not None and ev.template_k == 7

    generic = _background() + [
        Review(f"g{i}", "P", T0 + timedelta(days=200, hours=i), "great product", 5)
        for i in range(9)
    ]
    sg = ProductStream(generic)
    evg = sg.evidence_at(_last_index(sg, "g8"), 24)
    assert evg is not None and evg.template_k == 0, "short generic text must never form a template"


def test_independent_texts_do_not_form_a_template() -> None:
    s = ProductStream(_background() + _burst(T0 + timedelta(days=200), 10, 5, 5))
    ev = s.evidence_at(_last_index(s, "c9"), 24)
    assert ev is not None and ev.template_k <= 1


def test_rating_shift_sign_and_absence_of_ratings() -> None:
    s = ProductStream(_background() + _burst(T0 + timedelta(days=200), 10, 5, 1))
    ev = s.evidence_at(_last_index(s, "c9"), 24)
    assert ev is not None and ev.rating_z is not None and ev.rating_z < -5
    assert ev.window_mean is not None and ev.window_mean < 2 and ev.history_mean > 4

    unrated = ProductStream(
        _background(rating=None) + _burst(T0 + timedelta(days=200), 10, 5, None)
    )
    ev2 = unrated.evidence_at(_last_index(unrated, "c9"), 24)
    assert ev2 is not None and ev2.rating_z is None and ev2.mismatch_z is None


def test_verified_share_is_optional_and_only_used_when_mostly_present() -> None:
    plain = ProductStream(_background() + _burst(T0 + timedelta(days=200), 10, 5, 5))
    ev = plain.evidence_at(_last_index(plain, "c9"), 24)
    assert ev is not None and ev.verified_z is None

    hist = [
        Review(r.review_id, r.product_id, r.timestamp, r.text, r.rating, verified=True)
        for r in _background()
    ]
    burst = [
        Review(r.review_id, r.product_id, r.timestamp, r.text, r.rating, verified=False)
        for r in _burst(T0 + timedelta(days=200), 10, 5, 5)
    ]
    s = ProductStream(hist + burst)
    ev2 = s.evidence_at(_last_index(s, "c9"), 24)
    assert ev2 is not None and ev2.verified_z is not None and ev2.verified_z > 5
    assert ev2.verified_window_share < 0.2


def test_rating_text_mismatch_heuristic() -> None:
    assert is_rating_text_mismatch(5, "terrible awful waste") is True
    assert is_rating_text_mismatch(1, "love it, excellent and perfect") is True
    assert is_rating_text_mismatch(5, "love it, excellent") is False
    assert is_rating_text_mismatch(None, "terrible awful waste") is None


def test_jaccard_identity_and_disjoint() -> None:
    a = frozenset({"abcde", "bcdef"})
    assert jaccard(a, a) == 1.0
    assert jaccard(a, frozenset({"zzzzz"})) == 0.0
    assert jaccard(a, frozenset()) == 0.0


def test_params_are_frozen_and_windows_are_the_pre_registered_ones() -> None:
    p = CampaignParams()
    assert p.windows_hours == (6, 24, 72, 168) and p.history_days == 180 and p.n_min == 5
    with pytest.raises(AttributeError):
        p.n_min = 3  # type: ignore[misc]
