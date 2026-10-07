"""S19 (mirrors #265 for the production detectors): the research forks must keep full-precision
confidence on the flag and round only for serialised/display output, so ordering and any
threshold comparison see the exact value (0.69996 is below 0.7; rounded to 3 dp it was 0.7).

Run locally: `pytest benchmark/phase2_synthetic/detectors/test_confidence_precision.py
--no-cov -p no:cacheprovider` -- CI's unit job runs `tests/` only and never collects benchmark/.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import batch_defect as bd
import campaign_synthetic as cs
import pytest
import trend as tr
from common import AnnotatedReview

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_batch_defect_flag_confidence_not_rounded_before_threshold() -> None:
    reviews = [
        AnnotatedReview(
            review_id=f"s-{i}",
            product_id="Widget Pro",
            reviewer_id="",
            timestamp=_NOW + timedelta(hours=i * 6),
            rating=0,
            topics=["battery"],
            sentiment="negative",
            urgency="",
        )
        for i in range(6)
    ]
    ratio = bd.SPIKE_RATIO_THRESHOLD + 0.69996 * (bd.SATURATION_RATIO - bd.SPIKE_RATIO_THRESHOLD)
    best = {
        "window_start": _NOW,
        "window_end": _NOW + timedelta(days=bd.WINDOW_DAYS),
        "window_count": 6,
        "baseline_rate_per_day": 0.1,
        "expected_count": 1.0,
        "ratio": ratio,
        "outside_count": 0,
        "review_ids": ["s-0"],
    }
    with patch.object(bd, "_best_window_for_topic", return_value=best):
        (flag,) = bd.scan_batch_defects(reviews)

    assert flag.confidence < 0.7
    assert flag.confidence == pytest.approx(0.69996)
    assert flag.to_dict()["confidence"] == 0.7  # display rounding unchanged


def test_campaign_flag_confidence_not_rounded_before_threshold() -> None:
    reviews = [
        cs.Review(
            review_id=f"r{i}",
            product_id="Widget",
            reviewer_id=f"u{i}",
            timestamp=_NOW + timedelta(hours=i),
            text=f"text {i}",
        )
        for i in range(3)
    ]
    window = cs.BurstWindow(
        start=_NOW,
        end=_NOW + timedelta(hours=cs.BURST_HOURS),
        reviews=reviews,
        ratio_vs_baseline=9.0,
    )
    with patch.object(cs, "find_best_burst_window", return_value=(window, 0.49996, 0.2, 0.2, 0.0)):
        flag = cs.scan_product("Widget", reviews, {})

    assert flag is not None
    assert flag.confidence < 0.5
    flag_dict = {"confidence": round(flag.confidence, 4)}  # what write_flags serialises
    assert flag_dict["confidence"] == 0.5


def _trend_records() -> list[tr._trend_ReviewRecord]:
    # 'a' rises across the 4 phases; the patched _sustained_rise supplies the exact rise, so only
    # the confidence arithmetic and its rounding are under test here.
    records = []
    for topic, per_phase in (("a", [1, 2, 3, 4]), ("b", [1, 2, 3, 4])):
        for phase, n in enumerate(per_phase):
            for j in range(n):
                records.append(
                    tr._trend_ReviewRecord(
                        review_id=f"{topic}-{phase}-{j}",
                        product_id="P",
                        timestamp=_NOW + timedelta(days=phase * 10 + j),
                        topics=(topic,),
                        sentiment="negative",
                    )
                )
    return records


def test_trend_flag_confidence_not_rounded_and_sorts_on_exact_value() -> None:
    saturation = 10.0
    rises = iter([0.6999 * saturation, 0.69996 * saturation])  # topic 'a' then 'b'

    def fake_rise(*_args: object, **_kwargs: object) -> tuple[bool, float, float]:
        return True, 1.0, next(rises)

    with (
        patch.object(tr, "_sustained_rise", side_effect=fake_rise),
        patch.object(tr, "_scan_aggregate_polarity", return_value=None),
    ):
        flags = tr.scan_for_trends(
            _trend_records(), min_total_mentions=1, rise_saturation=saturation
        )

    topic_flags = [f for f in flags if f.trend_type == "topic_negative"]
    assert [f.topic for f in topic_flags] == ["b", "a"]  # 0.69996 outranks 0.6999
    assert all(f.confidence < 0.7 for f in topic_flags)
    # both display as 0.7 -- before the fix they were stored as 0.7 and tied (insertion order)
    assert [tr._trend_flag_to_dict(f)["confidence"] for f in topic_flags] == [0.7, 0.7]
