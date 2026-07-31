from __future__ import annotations

from benchmark.vernacular_v2.corpus_pipeline.run_corpus_pipeline import (
    HARD_CALL_CAP,
    build_plan,
    estimate_cost,
)


def test_estimate_cost_scales_linearly_with_calls() -> None:
    cost_10 = estimate_cost("llama-3.3-70b-versatile", 10)
    cost_20 = estimate_cost("llama-3.3-70b-versatile", 20)
    assert cost_20 == cost_10 * 2


def test_estimate_cost_zero_calls_is_zero() -> None:
    assert estimate_cost("llama-3.3-70b-versatile", 0) == 0.0


def test_build_plan_default_sample_is_within_hard_call_cap() -> None:
    plan = build_plan(20)
    assert plan["within_hard_call_cap"] is True
    assert plan["total_live_groq_calls"] <= HARD_CALL_CAP


def test_build_plan_call_counts_match_documented_stages() -> None:
    plan = build_plan(20, n_adversarial_per_type_per_model=2)
    assert plan["teacher_labeling_calls"] == 20
    assert plan["consensus_validation_calls"] == 20 * 2  # 2 judges
    assert plan["adversarial_generation_calls"] == 3 * 2 * 2  # 3 attacks * n * 2 models
    assert plan["total_live_groq_calls"] == (
        plan["teacher_labeling_calls"]
        + plan["consensus_validation_calls"]
        + plan["adversarial_generation_calls"]
    )


def test_build_plan_total_cost_is_trivially_small_for_documented_sample() -> None:
    """Sanity bound proving this is a 'small documented sample', not a large run --
    the exact escalation trigger this pipeline is built to never hit by construction."""
    plan = build_plan(20)
    assert plan["estimated_cost_usd"]["total"] < 1.0


def test_build_plan_large_n_exceeds_hard_call_cap() -> None:
    plan = build_plan(1000)
    assert plan["within_hard_call_cap"] is False
