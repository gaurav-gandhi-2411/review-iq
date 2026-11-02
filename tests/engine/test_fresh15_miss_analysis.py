from __future__ import annotations

from engine.intent import fresh15_miss_analysis as M


def test_rate_reports_k_n_and_a_wilson_interval_inside_0_1():
    r = M.rate(58, 67)
    assert (r["k"], r["n"], r["rate"]) == (58, 67, 0.8657)
    lo, hi = r["wilson95"]
    assert 0 < lo < r["rate"] < hi < 1


def test_every_reader_tag_has_a_signal_and_a_frame_and_no_review_text_is_embedded():
    assert len(M.READ) == 14
    for i, t in M.READ.items():
        assert i.startswith("fresh15-")
        assert set(t) == {"signal", "frame"} and all(t.values())
        assert len(t["signal"]) < 80  # a short tag, never a copy of the review
