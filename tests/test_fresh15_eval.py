from __future__ import annotations

import numpy as np
import pytest
from engine.intent.fresh15_eval import evaluate, paired_delta


def _task(na: str, urg: str = "low") -> dict:
    return {
        "needs_action": {"label": na, "candidate_label": na},
        "urgency": {"label": urg, "candidate_label": urg},
    }


def _fixture(n_pos: int, routed: int, llm_caught: int):
    items, votes, tasks, llm = [], {}, [], {}
    for k in range(n_pos):
        i = f"fresh15-food-{k:04d}"
        items.append({"id": i, "stratum": "food"})
        votes[i] = {"a": "yes", "b": "yes", "c": "yes", "d": "yes" if k % 5 else "no"}  # 4-0 or 3-1
        tasks.append(_task("yes" if k < routed else "no"))
        llm[i] = {"id": i, "ans": {"needs_action": {"label": "yes" if k < llm_caught else "no"}}}
    j = "fresh15-food-9999"  # a negative and a no-consensus item
    items += [{"id": j, "stratum": "food"}, {"id": j + "x", "stratum": "food"}]
    votes[j] = {"a": "no", "b": "no", "c": "no", "d": "no"}
    votes[j + "x"] = {"a": "yes", "b": "yes", "c": "no", "d": "no"}  # 2-2: no consensus
    tasks += [_task("no"), _task("no")]
    llm[j] = {"id": j, "ans": {"needs_action": {"label": "no"}}}
    return items, votes, tasks, llm


def test_both_criteria_met_when_router_is_strong_and_llm_is_weaker() -> None:
    res = evaluate(*_fixture(300, routed=290, llm_caught=255))
    assert res["n_gold_yes"] == 300 and res["n_no_consensus"] == 1 and res["size_rule"]["met"]
    assert res["A_router_routing_recall"]["met"] is True  # 290/300 = 0.967, Wilson lower > 0.90
    assert res["B_non_inferiority"]["delta"] > 0 and res["B_non_inferiority"]["met"] is True
    assert res["criteria_met"] is True
    assert (
        res["llm"]["empty_or_unparseable_or_missing"] == 1
    )  # the no-consensus item has no LLM row


def test_absolute_criterion_fails_with_a_low_lower_bound_even_if_non_inferior() -> None:
    res = evaluate(*_fixture(300, routed=270, llm_caught=260))  # 0.90 point, lower bound below 0.90
    assert res["A_router_routing_recall"]["met"] is False
    assert res["B_non_inferiority"]["met"] is True
    assert res["criteria_met"] is False


def test_non_inferiority_fails_when_the_router_is_clearly_worse() -> None:
    res = evaluate(*_fixture(300, routed=250, llm_caught=295))
    assert res["B_non_inferiority"]["met"] is False and res["criteria_met"] is False


def test_paired_delta_is_deterministic() -> None:
    r, lm = np.array([1, 1, 0, 1.0]), np.array([1, 0, 0, 1.0])
    assert paired_delta(r, lm) == paired_delta(r, lm)
    assert paired_delta(r, lm)["delta"] == pytest.approx(0.25)
