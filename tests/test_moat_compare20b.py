"""Unit tests for engine/intent/moat_compare20b.py on synthetic data (hermetic; no private rows)."""

from __future__ import annotations

import numpy as np
from engine.intent import moat_compare20b as C


def test_wilson_known_value() -> None:
    lo, hi = C.wilson(50, 100)
    assert abs(lo - 0.4038) < 1e-3 and abs(hi - 0.5962) < 1e-3
    assert np.isnan(C.wilson(0, 0)[0])


def test_op_point_picks_highest_coverage_and_whole_tie_groups() -> None:
    # 200 perfect items at confidence 0.9, then 50 wrong at 0.5: the 0.9 group qualifies, adding the 0.5 group does not.
    correct = np.r_[np.ones(200, bool), np.zeros(50, bool)]
    conf = np.r_[np.full(200, 0.9), np.full(50, 0.5)]
    p = C.op_point(correct, conf)
    assert p["qualifies"] and p["answered"] == 200 and p["threshold"] == 0.9
    assert abs(p["coverage"] - 0.8) < 1e-12


def test_op_point_no_qualifying_threshold_is_zero() -> None:
    # 10 perfect items: Wilson lower bound of 10/10 is about 0.72 < 0.95, so nothing qualifies (conservative).
    p = C.op_point(np.ones(10, bool), np.full(10, 0.99))
    assert not p["qualifies"] and p["coverage"] == 0.0 and p["answered"] == 0


def test_tie_group_is_never_split() -> None:
    # One tie group of 100 with 2 errors can't be cut: it either qualifies whole or not at all.
    correct = np.r_[np.ones(98, bool), np.zeros(2, bool)]
    p = C.op_point(correct, np.full(100, 0.8))
    assert p["answered"] in (0, 100)


def test_at_count_largest_group_under_cap_and_overshoot() -> None:
    correct = np.ones(10, bool)
    conf = np.r_[np.full(3, 0.9), np.full(7, 0.8)]
    assert C.at_count(correct, conf, 5)["answered"] == 3
    assert C.at_count(correct, conf, 10)["answered"] == 10
    o = C.at_count(correct, conf, 2)
    assert o["overshoot"] and o["answered"] == 3


def test_matched_ids_respects_order_cap_and_all_tables() -> None:
    t1 = {"a": (True, 1.0), "b": (True, 1.0), "c": (True, 1.0)}
    t2 = {"a": (True, 1.0), "c": (True, 1.0)}
    assert C.matched_ids(["a", "b", "c", "d"], {"x": t1, "y": t2}, 3) == ["a", "c"]
    assert C.matched_ids(["a", "b", "c"], {"x": t1, "y": t2}, 1) == ["a"]


def test_verdict_needs_both_criteria() -> None:
    assert C.verdict(0.1, 0.3, 0.2, "A", "B")["verdict"] == "A beats B"
    assert C.verdict(-0.3, -0.1, -0.2, "A", "B")["verdict"] == "B beats A"
    # interval excludes 0 for A but operating points point the other way (or tie): not separated
    assert C.verdict(0.1, 0.3, -0.1, "A", "B")["verdict"] == "not separated"
    assert C.verdict(0.1, 0.3, 0.0, "A", "B")["verdict"] == "not separated"
    # operating points favour A but the interval includes 0: not separated
    assert C.verdict(-0.1, 0.3, 0.2, "A", "B")["verdict"] == "not separated"


def _synthetic(n: int, p_ok: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    correct = rng.random(n) < p_ok
    conf = np.where(correct, rng.uniform(0.7, 1.0, n), rng.uniform(0.3, 0.8, n))
    return correct, np.round(conf, 2)


def test_compare_task_separates_strong_from_weak_and_is_deterministic() -> None:
    strong = _synthetic(300, 0.99, 1)
    weak = _synthetic(300, 0.70, 2)
    arms = {"strong": strong, "weak": weak}
    r1 = C.compare_task(arms, [("strong", "weak")], n_boot=200)
    r2 = C.compare_task(arms, [("strong", "weak")], n_boot=200)
    assert r1 == r2  # seed 42, no hidden state
    pair = r1["pairs"]["strong minus weak"]
    assert pair["accuracy"]["verdict"] == "strong beats weak"
    assert pair["coverage_at_op_point"]["verdict"] == "strong beats weak"
    assert r1["matched_coverage"]["computed"]


def test_compare_task_identical_arms_not_separated() -> None:
    a = _synthetic(300, 0.9, 3)
    r = C.compare_task({"x": a, "y": (a[0].copy(), a[1].copy())}, [("x", "y")], n_boot=200)
    for m in r["pairs"]["x minus y"].values():
        assert m["verdict"] == "not separated"


def test_bootstrap_shares_one_index_set_across_arms() -> None:
    # Identical arrays must give a degenerate paired difference of exactly 0 in every replicate; independent resamples would not.
    a = _synthetic(120, 0.9, 4)
    r = C.compare_task({"x": a, "y": a}, [("x", "y")], n_boot=100)
    assert r["pairs"]["x minus y"]["accuracy"]["ci95"] == [0.0, 0.0]
    assert r["pairs"]["x minus y"]["coverage_at_op_point"]["ci95"] == [0.0, 0.0]
