"""Amendment 16: a panel status file that does not record the pinned Ollama build and the judge digests is rejected."""

from __future__ import annotations

from engine.labelling import panel_pin as P

GOOD = {
    "ollama_sha256": P.PINNED_SHA256,
    "ollama_version": "ollama version is 0.40.6",
    "model_digests": {j: "sha256:" + "ab" * 32 for j in P.JUDGES},
    "budget_hit": False,
}


def test_a_complete_pinned_status_is_accepted() -> None:
    assert P.check(GOOD) == []


def test_the_s28_panel_status_is_rejected_as_the_amendment_requires() -> None:
    # reports/intent/fresh15/panel_run_status.json: no version, no digest, an unpinned build
    s28 = {
        "stage": "P",
        "elapsed_s": 15487,
        "budget_hit": False,
        "note": "ollama build NOT recorded",
    }
    problems = P.check(s28)
    assert any("ollama_sha256" in p for p in problems) and any(
        "ollama_version" in p for p in problems
    )
    assert sum("judge model" in p for p in problems) == 4


def test_a_wrong_digest_a_wrong_version_or_a_missing_judge_digest_is_rejected() -> None:
    assert P.check({**GOOD, "ollama_sha256": "0" * 64})
    assert P.check({**GOOD, "ollama_version": "ollama version is 0.41.0"})
    missing = {
        **GOOD,
        "model_digests": {j: d for j, d in GOOD["model_digests"].items() if j != "gemma2:9b"},
    }
    assert P.check(missing) == ["no recorded digest for judge model gemma2:9b"]
    assert P.check({**GOOD, "model_digests": {**GOOD["model_digests"], "qwen3:8b": ""}})


def test_a_file_that_is_not_a_panel_status_is_rejected() -> None:
    bad = {k: v for k, v in GOOD.items() if k != "budget_hit"}
    assert any("not a panel status" in p for p in P.check(bad))
