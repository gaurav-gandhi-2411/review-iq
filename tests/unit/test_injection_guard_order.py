"""Offline tests for eval/injection_guard_order.py (no network, no Groq calls)."""

from __future__ import annotations

import pytest
from app.core.sanitize import pii_redacted_text
from eval import injection_guard_order as ig

# ---------------------------------------------------------------- statistics


def test_wilson_known_values() -> None:
    lo, hi = ig.wilson(5, 20)  # standard Wilson 95% for 5/20: (0.1119, 0.4687)
    assert lo == pytest.approx(0.11186, abs=1e-4)
    assert hi == pytest.approx(0.46870, abs=1e-4)
    assert ig.wilson(0, 10)[1] == pytest.approx(0.2775, abs=1e-3)
    assert ig.wilson(0, 0) == (0.0, 1.0)


def test_mcnemar_exact_hand_computed() -> None:
    # b=7, c=1: n=8, P(X<=1) = (C(8,0)+C(8,1))/2^8 = 9/256; two-sided = 18/256.
    assert ig.mcnemar_exact(7, 1) == pytest.approx(18 / 256)
    # b=8, c=0: 2 * 1/256.
    assert ig.mcnemar_exact(8, 0) == pytest.approx(2 / 256)
    assert ig.mcnemar_exact(0, 0) == 1.0
    assert ig.mcnemar_exact(5, 5) == 1.0  # capped at 1


def test_newcombe_phi_hand_computed() -> None:
    # a=8,b=2,c=1,d=9, n=20: A = 8*9 - 2*1 = 70 > n/2=10 so C = 70-10 = 60;
    # margins r=10,s=10,t=9,u=11: sqrt(10*10*9*11) = sqrt(9900) = 99.4987; phi = 60/99.4987.
    assert ig._newcombe_phi(8, 2, 1, 9) == pytest.approx(0.60302, abs=1e-4)
    # a=1,b=4,c=3,d=12: A = 12 - 12 = 0, inside [0, n/2] so C = 0.
    assert ig._newcombe_phi(1, 4, 3, 12) == 0.0
    assert ig._newcombe_phi(0, 0, 3, 7) == 0.0  # empty margin


def test_newcombe_method10_hand_computed() -> None:
    # a=1 (both), b=4 (first only), c=3 (second only), d=12, n=20 -> phi = 0 (see above).
    # p1=5/20=.25, p2=4/20=.20; Wilson(5/20)=(.11186,.46870), Wilson(4/20)=(.08064,.41600).
    # delta = p1-p2 = .05
    # lower = .05 - sqrt(.13814^2 + .21600^2) = .05 - sqrt(.019083+.046656) = .05 - .25640 = -.20640
    # upper = .05 + sqrt(.21870^2 + .11936^2) = .05 + sqrt(.047830+.014247) = .05 + .24915 = .29915
    # Function returns second-minus-first, i.e. negated: diff=-.05, CI=(-.29915, .20640).
    diff, lo, hi = ig.newcombe_paired_diff(1, 4, 3, 12)
    assert diff == pytest.approx(-0.05)
    assert lo == pytest.approx(-0.2992, abs=1e-3)
    assert hi == pytest.approx(0.2064, abs=1e-3)


def test_newcombe_phi_narrows_the_interval() -> None:
    # Strong positive agreement (phi > 0) must give a narrower interval than phi = 0 would.
    _, lo, hi = ig.newcombe_paired_diff(8, 2, 1, 9)
    width_corr = hi - lo
    p1, p2 = 10 / 20, 9 / 20
    (l1, u1), (l2, u2) = ig.wilson(10, 20), ig.wilson(9, 20)
    width_indep = (p1 - p2 + ((u1 - p1) ** 2 + (p2 - l2) ** 2) ** 0.5) - (
        p1 - p2 - ((p1 - l1) ** 2 + (u2 - p2) ** 2) ** 0.5
    )
    assert width_corr < width_indep


def test_paired_stats_counts() -> None:
    pairs = [(True, True)] * 5 + [(True, False)] * 2 + [(False, True)] + [(False, False)] * 12
    s = ig.paired_stats(pairs)
    assert (s["both"], s["b_current_only"], s["c_new_only"], s["neither"]) == (5, 2, 1, 12)
    assert s["k_current"] == 7 and s["k_new"] == 6 and s["n"] == 20
    assert s["diff_new_minus_current"] == pytest.approx(-0.05)


# ---------------------------------------------------------------- PII generator


def test_pii_generator_is_deterministic_and_input_dependent() -> None:
    text = "Great phone. Battery is good. Camera is poor."
    a = ig.make_pii_variant(42, "x1", text)
    assert a == ig.make_pii_variant(42, "x1", text)
    others = {ig.make_pii_variant(42, f"id{i}", text)[0] for i in range(30)}
    assert len(others) > 10  # different ids give different insertions
    by_seed = {ig.make_pii_variant(s, "x1", text)[0] for s in range(30)}
    assert len(by_seed) > 10  # the seed participates in the hash


def test_pii_generator_inserts_only_patterns_that_get_redacted() -> None:
    text = "Battery lasts a day. Screen is fine. Would not buy again."
    seen_kinds: set[str] = set()
    for i in range(200):
        out, kinds = ig.make_pii_variant(42, f"item{i}", text)
        assert 1 <= len(kinds) <= 2 and len(set(kinds)) == len(kinds)
        seen_kinds.update(kinds)
        assert out != text and text.split(".")[0] in out  # original content preserved
        red = pii_redacted_text(out)
        assert red != out, (kinds, out)  # the insertion is always something redact_pii removes
        for kind, marker in (
            ("email", "[EMAIL]"),
            ("card", "[CARD]"),
            ("name_intro", "[NAME]"),
        ):
            if kind in kinds:
                assert marker in red, (kind, out, red)
        if "in_mobile" in kinds or "us_phone" in kinds:
            assert "[PHONE]" in red or "[CARD]" in red
    assert seen_kinds == set(ig.PII_KINDS)


def test_pii_generator_handles_single_sentence_and_empty_boundaries() -> None:
    for i in range(30):
        out, _ = ig.make_pii_variant(42, f"s{i}", "no punctuation here")
        assert "no punctuation here" in out


def test_pii_values_are_synthetic() -> None:
    for i in range(100):
        out, _ = ig.make_pii_variant(42, f"v{i}", "Fine product.")
        assert "@" not in out or "@example.com" in out


# ---------------------------------------------------------------- decision rule


def _stats(cur_only: int, new_only: int, both: int, neither: int) -> dict[str, object]:
    pairs = (
        [(True, True)] * both
        + [(True, False)] * cur_only
        + [(False, True)] * new_only
        + [(False, False)] * neither
    )
    return ig.paired_stats(pairs)


def _decide(inj_p, inj_pool, ben_p, ben_pool, **kw):  # type: ignore[no-untyped-def]
    args = {"n_injection_base": 344, "n_benign_base": 400, "missing_frac": 0.0}
    args.update(kw)
    return ig.decide(inj_p, inj_pool, ben_p, ben_pool, **args)


def test_decision_ships_when_nothing_changes() -> None:
    inj = _stats(0, 0, 300, 44)
    ben = _stats(0, 0, 5, 395)
    res = _decide(inj, inj, ben, ben)
    assert res["decision"] == "SHIP" and all(res["conditions"].values())


def test_decision_fails_condition_1_on_recall_drop() -> None:
    drop = _stats(40, 0, 260, 44)  # 40 of 344 injections lost
    ok = _stats(0, 0, 300, 44)
    ben = _stats(0, 0, 5, 395)
    res = _decide(drop, ok, ben, ben)
    assert res["decision"] == "DO NOT SHIP"
    assert not res["conditions"]["1_recall_drop_ub_le_3pp_INJ-P"]
    res = _decide(ok, drop, ben, ben)
    assert not res["conditions"]["1_recall_drop_ub_le_3pp_INJ_pooled"]


def test_decision_fails_condition_2_on_significant_fpr_rise() -> None:
    inj = _stats(0, 0, 300, 44)
    ok = _stats(0, 0, 5, 395)
    rise = _stats(0, 20, 0, 380)  # FPR 0 -> 5%, 20 discordant all one way
    res = _decide(inj, inj, rise, ok)
    assert res["decision"] == "DO NOT SHIP" and not res["conditions"]["2_fpr_not_raised_BEN-P"]
    res = _decide(inj, inj, ok, rise)
    assert not res["conditions"]["2_fpr_not_raised_BEN_pooled"]


def test_decision_allows_fpr_rise_inside_ci_but_not_significant() -> None:
    inj = _stats(0, 0, 300, 44)
    small = _stats(1, 3, 10, 386)  # b=1, c=3: p=0.625, rate_new .035 <= wilson upper of .0275
    res = _decide(inj, inj, small, small)
    assert res["conditions"]["2_fpr_not_raised_BEN-P"]


def test_decision_inconclusive_on_completeness() -> None:
    inj = _stats(0, 0, 300, 44)
    ben = _stats(0, 0, 5, 395)
    assert _decide(inj, inj, ben, ben, missing_frac=0.0201)["decision"] == "INCONCLUSIVE"
    assert _decide(inj, inj, ben, ben, missing_frac=0.02)["decision"] == "SHIP"  # boundary
    assert _decide(inj, inj, ben, ben, n_injection_base=299)["decision"] == "INCONCLUSIVE"
    assert _decide(inj, inj, ben, ben, n_benign_base=299)["decision"] == "INCONCLUSIVE"
    # a failing efficacy condition does not mask incompleteness
    drop = _stats(40, 0, 260, 44)
    assert _decide(drop, drop, ben, ben, missing_frac=0.5)["decision"] == "INCONCLUSIVE"
