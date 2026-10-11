"""Unit tests for app.core.detectors.campaign: the alert rule, output shape and corpus scan.

Signal arithmetic is covered in test_campaign_signals.py; wording rules in
test_campaign_wording.py. The performance claim is NOT made here: it comes from the
pre-registered evaluation in benchmark/campaign_eval (docs/specs/campaign-detection.md).
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
    Review,
    WindowEvidence,
    campaign_reviews_from_rows,
    is_alert,
    scan_corpus,
    scan_stream,
)

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
    ev = _ev(mismatch_z=4.0, burst=3.5)  # 3.5: fired, but below the 1.5x strong level
    assert not is_alert(ev, off)[0]
    assert is_alert(ev, on)[0]
    assert is_alert(_ev(verified_z=3.5, burst=3.5), off)[0]


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


def test_scan_corpus_groups_by_product_and_respects_since() -> None:
    reviews = _background("A") + _campaign("A", 12, 6, 1, True) + _background("B", n=150, days=150)
    flags = scan_corpus(reviews)
    assert [f.product_id for f in flags] == ["A"]
    assert scan_corpus(reviews, since=T0 + timedelta(days=400)) == []
    d = flags[0].to_dict()
    assert d["review_count"] >= 5 and d["signals_fired"] and d["explanation"]
    assert d["evidence"]["window"]["start"] < d["evidence"]["window"]["end"]
    assert set(d["evidence"]["near_identical_review_ids"]) <= set(d["evidence"]["review_ids"])


def test_rows_adapter_maps_fields_and_tolerates_missing_ones() -> None:
    now = T0
    rows = [
        {"id": "a", "product": "Widget", "review_text": "Great", "review_date": now, "rating": 4},
        {"id": "b", "product": None, "review_text": None, "review_date": now},
    ]
    r = campaign_reviews_from_rows(rows)
    assert (r[0].review_id, r[0].product_id, r[0].rating, r[0].verified) == ("a", "Widget", 4, None)
    assert (r[1].product_id, r[1].text, r[1].rating) == ("unknown product", "", None)
