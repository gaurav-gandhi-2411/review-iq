"""Unit tests for app.core.detectors.campaign -- the production fork.

Not a re-run of the synthetic stress-test validation (already proven in
benchmark/phase2_synthetic/detectors/, out of scope here). These tests only prove: (1) the
Postgres-row adapter maps fields correctly and confirms the reviewer_id stub's documented
effect, (2) the ported algorithm still runs end-to-end after the port.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from app.core.detectors.campaign import (
    SIGNAL_BURST,
    SIGNAL_RATING,
    SIGNAL_TEMPLATE,
    CampaignParams,
    ProductStream,
    WindowEvidence,
    campaign_reviews_from_rows,
    is_alert,
    scan_corpus,
    scan_stream,
)
from app.core.detectors.campaign_signals import Review

_NOW = datetime(2026, 6, 1, tzinfo=UTC)


def test_campaign_reviews_from_rows_maps_fields() -> None:
    rows = [
        {
            "id": "abc123",
            "product": "Widget Pro",
            "review_text": "Great product, highly recommend",
            "review_date": _NOW,
        }
    ]

    result = campaign_reviews_from_rows(rows)

    assert len(result) == 1
    review = result[0]
    assert review.review_id == "abc123"
    assert review.product_id == "Widget Pro"
    assert review.timestamp == _NOW
    assert review.text == "Great product, highly recommend"
    # The documented stub: reviewer_id = the review's own id, guaranteed unique per review.
    assert review.reviewer_id == "abc123"


def test_campaign_reviews_from_rows_handles_missing_product_and_text() -> None:
    rows = [{"id": "x", "product": None, "review_text": None, "review_date": _NOW}]

    result = campaign_reviews_from_rows(rows)

    assert result[0].product_id == "unknown product"
    assert result[0].text == ""


def test_reviewer_id_stub_forces_reviewer_concentration_to_zero() -> None:
    """Confirms the documented mechanism directly: even 10 reviews landing in the exact same
    burst window score reviewer_concentration_score=0.0, because campaign_reviews_from_rows
    stubs a unique reviewer_id per review -- this is a pure recall reduction, never a
    false-positive risk (a real coordinated-reviewer-identity campaign would be UNDER-detected,
    not falsely flagged)."""
    rows = [
        {
            "id": f"r{i}",
            "product": "Widget",
            "review_text": f"unique organic text number {i} about this product",
            "review_date": _NOW + timedelta(hours=i),
        }
        for i in range(10)
    ]

    reviews = campaign_reviews_from_rows(rows)
    flags = scan_corpus(reviews)

    # Distinct, non-duplicate text + stubbed-unique reviewer_id -> no signal on either axis ->
    # correctly not flagged at all (this specific case), proving the stub doesn't manufacture
    # a false concentration signal.
    assert flags == []


def test_scan_corpus_smoke_after_port_catches_text_based_coordination() -> None:
    """Hand-built burst: many reviews, unique-per-review reviewer_id (as production will
    always provide), but near-identical long TEXT within a tight timing window. Proves the
    port (import path, algorithm execution) still catches text-based coordination -- the one
    signal that survives the reviewer_id stub -- not a re-derivation of the stress-test numbers.
    """
    random.seed(11)
    base = _NOW - timedelta(days=60)
    rows = [
        {
            "id": f"base-{i}",
            "product": "Widget",
            "review_text": f"baseline unique review number {i} about the widget",
            "review_date": base + timedelta(days=random.uniform(0, 50)),
        }
        for i in range(15)
    ]
    template = (
        "This product exceeded my expectations completely and arrived very quickly in "
        "perfect condition"
    )
    for i in range(8):
        rows.append(
            {
                "id": f"burst-{i}",
                "product": "Widget",
                "review_text": template,
                "review_date": _NOW + timedelta(hours=i * 2),
            }
        )

    flags = scan_corpus(campaign_reviews_from_rows(rows))

    assert len(flags) == 1
    flag = flags[0]
    assert flag.product_id == "Widget"
    assert flag.confidence > 0.0
    assert flag.evidence["reviewer_concentration_score"] == 0.0, (
        "reviewer signal must be exactly 0 given the stub -- text_dup_score is the only "
        "possible driver"
    )
    assert flag.evidence["text_dup_score"] > 0.0


def test_flag_confidence_is_not_rounded_before_threshold_comparisons() -> None:
    """S17 X5b: CampaignFlag used to carry round(confidence, 4), so an exact 0.49996 (below the
    0.5 fake-campaign alert threshold) compared as 0.5 and alerted. Full precision is kept on the
    flag; only to_dict() rounds for display."""
    from unittest.mock import patch

    from app.core.detectors import campaign as cp

    reviews = [
        cp.Review(
            review_id=f"r{i}",
            product_id="Widget",
            reviewer_id=f"u{i}",
            timestamp=_NOW + timedelta(hours=i),
            text=f"text {i}",
        )
        for i in range(3)
    ]
    window = cp.BurstWindow(
        start=_NOW,
        end=_NOW + timedelta(hours=cp.BURST_HOURS),
        reviews=reviews,
        ratio_vs_baseline=9.0,
    )
    with patch.object(cp, "find_best_burst_window", return_value=(window, 0.49996, 0.2, 0.2, 0.0)):
        flag = cp.scan_product("Widget", reviews, {})

    assert flag is not None
    assert flag.confidence < 0.5
    assert flag.to_dict()["confidence"] == 0.5  # display rounding unchanged


# ---- alert rule (new review-pattern detector) ----

T0 = datetime(2024, 1, 1, tzinfo=UTC)
VOCAB = [f"word{k}x" for k in range(400)]
TEMPLATE = "this product exceeded my expectations completely and arrived very quickly"


def _background(pid: str = "P", n: int = 240, days: float = 240.0, rated: bool = True):
    rng = random.Random(1)
    return [
        Review(
            f"{pid}-b{i}",
            pid,
            T0 + timedelta(days=days * i / n),
            " ".join(rng.choice(VOCAB) for _ in range(9)),
            rng.choice([4, 5, 5, 5, 5]) if rated else None,
        )
        for i in range(n)
    ]


def _campaign(pid: str, n: int, hours: float, rating: int | None, same_text: bool):
    rng = random.Random(5)
    start = T0 + timedelta(days=200)
    return [
        Review(
            f"{pid}-c{i}",
            pid,
            start + timedelta(hours=hours * i / n),
            TEMPLATE if same_text else " ".join(rng.choice(VOCAB) for _ in range(9)),
            rating,
        )
        for i in range(n)
    ]


def _ev(**kw: object) -> WindowEvidence:
    base: dict[str, object] = dict(
        index=0, width_hours=24, n=10, expected=1.0, burst=0.0, template_k=0,
        template_members=(), rating_z=None, window_mean=None, history_mean=None,
        mismatch_z=None, mismatch_count=0, verified_z=None, verified_window_share=None,
        verified_history_share=None,
    )  # fmt: skip
    base.update(kw)
    return WindowEvidence(**base)  # type: ignore[arg-type]


def test_rule_needs_two_signals_or_one_strong() -> None:
    p = CampaignParams(theta_burst=5.0, k_min=3, theta_rating=3.5, strong_multiplier=2.0)
    assert is_alert(_ev(burst=6.0), p) == (False, [SIGNAL_BURST])  # one moderate signal
    ok, fired = is_alert(_ev(burst=6.0, template_k=3), p)
    assert ok and fired == [SIGNAL_BURST, SIGNAL_TEMPLATE]
    assert is_alert(_ev(burst=10.0), p)[0]  # one signal at 2x its threshold
    assert is_alert(_ev(template_k=6), p)[0]  # template at ceil(2 x 3)
    assert not is_alert(_ev(template_k=5), p)[0]
    assert is_alert(_ev(rating_z=-3.6, burst=5.1), p)[0]
    assert not is_alert(_ev(rating_z=-3.6), p)[0]


def test_mismatch_signal_only_counts_when_enabled_and_verified_works_when_present() -> None:
    off = CampaignParams(use_mismatch=False)
    on = CampaignParams(use_mismatch=True)
    ev = _ev(mismatch_z=4.0, burst=6.0)
    assert not is_alert(ev, off)[0]
    assert is_alert(ev, on)[0]
    assert is_alert(_ev(verified_z=3.5, burst=6.0), off)[0]


def test_template_burst_in_a_quiet_product_alerts_and_explains() -> None:
    s = ProductStream(_background() + _campaign("P", 12, 6, 1, same_text=True))
    alerts = scan_stream(s)
    assert len(alerts) == 1
    a = alerts[0]
    assert SIGNAL_TEMPLATE in a.fired and SIGNAL_RATING in a.fired
    assert a.evidence.template_k >= 3
    assert a.timestamp >= (T0 + timedelta(days=200)).timestamp()


def test_independent_text_burst_with_rating_collapse_alerts_via_burst_and_rating() -> None:
    s = ProductStream(_background() + _campaign("P", 15, 6, 1, same_text=False))
    alerts = scan_stream(s)
    assert alerts and SIGNAL_RATING in alerts[0].fired
    assert SIGNAL_TEMPLATE not in alerts[0].fired


def test_steady_flow_never_alerts() -> None:
    assert scan_stream(ProductStream(_background())) == []


def test_cooldown_and_warmup() -> None:
    s = ProductStream(_background() + _campaign("P", 40, 48, 1, same_text=True))
    alerts = scan_stream(s)
    gaps = [b.timestamp - a.timestamp for a, b in zip(alerts, alerts[1:], strict=False)]
    assert 1 <= len(alerts) <= 2 and all(g >= 7 * 86400 for g in gaps), (
        "cooldown: successive alerts on a product are at least 7 days apart"
    )
    short = ProductStream(_campaign("P", 40, 48, 1, same_text=True))
    assert scan_stream(short) == [], "no history: cold start never alerts"


def test_works_without_ratings() -> None:
    s = ProductStream(
        _background(rated=False) + _campaign("P", 12, 6, None, same_text=True), CampaignParams()
    )
    alerts = scan_stream(s)
    assert alerts and SIGNAL_RATING not in alerts[0].fired
