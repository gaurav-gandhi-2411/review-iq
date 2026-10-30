from __future__ import annotations

import numpy as np
import pytest
from engine.intent import fresh18_eval as E


def _labels(yes: list[str], no: list[str]) -> dict:
    return {"labels": {"needs_action": {**dict.fromkeys(yes, "yes"), **dict.fromkeys(no, "no")}}}


def _row(label: str | None) -> dict:
    return {"ans": {"needs_action": {"label": label}}} if label else {"ans": None}


def test_mcnemar_exact_matches_binomial_and_ties():
    r = np.array([True, True, True, False, False])
    llm = np.array([False, False, True, True, False])
    out = E.mcnemar_exact(r, llm)
    assert (out["router_only"], out["llm_only"]) == (2, 1)
    assert out["p"] == 1.0  # n=3, min=1: 2 * (1 + 3) / 8 = 1.0
    assert E.mcnemar_exact(np.array([True]), np.array([True]))["p"] == 1.0  # no discordant pairs
    assert E.mcnemar_exact(np.array([True] * 6), np.array([False] * 6))["p"] == pytest.approx(
        2 / 64, abs=1e-6
    )


def test_says_yes_and_is_empty_handle_missing_rows():
    assert E.says_yes(_row("yes")) and not E.says_yes(_row("no"))
    assert (
        not E.says_yes(None)
        and E.is_empty(None)
        and E.is_empty(_row(None))
        and not E.is_empty(_row("no"))
    )


def test_subsample_is_registered_rule_and_deterministic():
    yes = [f"fresh18-S-food-{i:04d}" for i in range(250)]
    labels = _labels(list(reversed(yes)), ["x"])
    a, b = E.subsample_120b(labels), E.subsample_120b(labels)
    assert a == b and len(a) == 200 and set(a) <= set(yes) and len(set(a)) == 200
    small = _labels(yes[:5], [])
    assert sorted(E.subsample_120b(small)) == yes[:5]


def test_evaluate_p1_pass_fail_and_sensitivity(monkeypatch):
    ids = [f"i{k}" for k in range(8)]
    items = [{"id": i} for i in ids]
    labels = _labels(ids[:6], ids[6:])
    rp = np.array([True, True, True, True, True, True, True, False])
    monkeypatch.setattr(E, "routes", lambda th, logits, tau: (rp, rp))
    llm20 = {i: _row("yes") for i in ids[:5]} | {
        ids[5]: _row(None),
        ids[6]: _row("no"),
        ids[7]: _row("yes"),
    }
    res = E.evaluate(items, labels, np.zeros((8, 1)), None, 0.07, llm20, {})
    p1 = res["P1_r1_plus_vs_20b"]
    assert (
        p1["router_recall"]["k"] == 6 and p1["llm_recall"]["k"] == 5
    )  # the empty row counts as "no"
    assert res["verdict"]["P1"] is True and p1["paired"]["boot95"][0] > -E.MARGIN
    assert (
        res["llm20"]["empty"] == 1
        and res["sensitivity_empty_20b_excluded"]["router_recall"]["n"] == 5
    )
    assert res["report_only"]["r1_plus_routed_share_gold_no"]["k"] == 1

    worse = np.array([True, False, False, False, False, True, False, False])
    monkeypatch.setattr(E, "routes", lambda th, logits, tau: (worse, worse))
    llm_all = {i: _row("yes") for i in ids}
    assert (
        E.evaluate(items, labels, np.zeros((8, 1)), None, 0.07, llm_all, {})["verdict"]["P1"]
        is False
    )
