from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip(
    "onnxruntime"
)  # the module imports the serving runtime; the CI venv skips the serving extras together
pytest.importorskip("sklearn")
from engine.intent import s4_llm_check as C  # noqa: E402


def test_mcnemar_exact_counts_discordant_pairs_and_two_sided_p() -> None:
    r = np.array([True, True, True, False, False, True])
    lm = np.array([True, False, False, False, True, True])
    out = C.mcnemar_exact(r, lm)
    assert (out["router_only"], out["llm_only"]) == (2, 1)
    assert out["p"] == pytest.approx(1.0)  # 2 * P(X <= 1 | n=3) = 2 * 4/8, capped at 1


def test_mcnemar_exact_extreme_and_empty() -> None:
    r, lm = np.ones(10, bool), np.zeros(10, bool)
    assert C.mcnemar_exact(r, lm)["p"] == pytest.approx(
        2 / 2**10, abs=1e-6
    )  # p is rounded to 6 places
    assert C.mcnemar_exact(r, r)["p"] == 1.0


def test_paired_boot_is_deterministic_and_brackets_the_point() -> None:
    rng = np.random.default_rng(0)
    r, lm = rng.random(200) < 0.9, rng.random(200) < 0.8
    a = C.paired_cluster_boot(r.astype(float), lm.astype(float), n_boot=500)
    b = C.paired_cluster_boot(r.astype(float), lm.astype(float), n_boot=500)
    assert a == b
    assert a["boot95"][0] <= a["delta"] <= a["boot95"][1]
    assert a["cluster"] == "item"


def test_llm_says_yes_and_empty_rows() -> None:
    yes = {"ans": {"needs_action": {"label": "yes", "confidence": 80}}}
    no = {"ans": {"needs_action": {"label": "no", "confidence": 80}}}
    assert C.llm_says_yes(yes) is True
    assert C.llm_says_yes(no) is False
    assert C.llm_says_yes(None) is None
    for empty in (
        {"ans": None},
        {"ans": {}},
        {"ans": {"needs_action": "yes"}},
        {"ans": {"needs_action": {"label": "maybe"}}},
    ):
        assert C.is_empty(empty)
        assert C.llm_says_yes(empty) is False  # an empty answer never routes
    assert not C.is_empty(yes)


@pytest.mark.parametrize(
    ("ci", "branch"),
    [([-0.04, 0.10], "D1"), ([-0.20, -0.06], "D2"), ([-0.08, 0.02], "D3"), ([-0.05, 0.01], "D3")],
)
def test_decide_branches_on_the_pooled_120b_ci(ci: list[float], branch: str) -> None:
    pooled = {
        "r1_plus_vs_120b": {"paired": {"boot95": ci}, "complete": True},
        "r1_plus_vs_20b": {"paired": {"boot95": [0.5, 0.6]}},
    }
    out = C.decide(pooled)
    assert out["branch"] == branch
    assert out["comparison"] == "r1_plus_vs_120b"
    assert out["deviation"] is None


def test_decide_falls_back_to_20b_only_without_120b_rows() -> None:
    out = C.decide({"r1_plus_vs_20b": {"paired": {"boot95": [-0.01, 0.05]}}})
    assert (out["branch"], out["comparison"]) == ("D1", "r1_plus_vs_20b")
    assert out["deviation"]


def test_positives_order_is_fixed_and_covers_every_s4_positive() -> None:
    a, b = C.positives_order(), C.positives_order()
    assert a == b
    assert (
        len(a) == len(set(a)) == 259
    )  # 126 (fresh16) + 133 (fresh17), the A16 E2 / A17 F2 positives
    assert sum(i.startswith("fresh16") for i in a) == 126
