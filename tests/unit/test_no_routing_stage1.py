"""Tests for the prepared (never run live) no-routing stage-1 experiment.

Every provider call is a mock: the budget guard, resume, pacing, ledger and delta math must all be
correct BEFORE anyone spends quota on this. See eval/experiments/no_routing_stage1.py.
"""

from __future__ import annotations

import asyncio
import json
import math
import random
from pathlib import Path
from statistics import mean
from typing import Any

import pytest
from eval.experiments import no_routing_stage1 as exp
from eval.runner import score_fixture

SMALL, LARGE = "small-model", "large-model"
MODELS = [SMALL, LARGE]
T0 = 1_000_000.0


def _fixture(fid: str) -> dict[str, Any]:
    return {
        "id": fid,
        "review_text": f"This is review {fid}. Works fine, would buy again.",
        "ground_truth": {
            "language": "en",
            "stars": None,
            "sentiment": "positive",
            "buy_again": True,
        },
        "scoring_notes": {"exact_match_fields": ["stars", "language", "sentiment", "buy_again"]},
    }


def _extraction(sentiment: str, buy_again: bool | None, language: str = "en") -> dict[str, Any]:
    return {"stars": None, "language": language, "sentiment": sentiment, "buy_again": buy_again}


def _item(fid: str, base_sentiment: str = "positive", base_buy: bool | None = True) -> exp.Item:
    base = _extraction(base_sentiment, base_buy)
    fx = _fixture(fid)
    recorded = {fr.field: fr.score for fr in score_fixture(fx, base)}
    return exp.Item(fid, "ci_gate_en", fx, base, recorded)


class FakeCaller:
    """Records every prompt; returns scripted results; never touches the network."""

    def __init__(self, results: list[exp.CallResult] | None = None, tokens: int = 3_000) -> None:
        self.prompts: list[str] = []
        self.results = results
        self.tokens = tokens

    async def __call__(self, prompt: str) -> exp.CallResult:
        self.prompts.append(prompt)
        if self.results is not None:
            return self.results[len(self.prompts) - 1]
        return exp.CallResult(_extraction("positive", True), LARGE, self.tokens - 500, 500)


class Clock:
    def __init__(self, t: float = T0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture(autouse=True)
def _no_provenance_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(exp, "get_git_sha", lambda: "testsha")


def _run(
    items: list[exp.Item],
    caller: FakeCaller,
    tmp_path: Path,
    *,
    mode: str = "record",
    clock: Clock | None = None,
    sleeps: Sleeps | None = None,
    ceiling: int = 10_000,
    est: int = 3_200,
) -> exp.StageOutcome:
    return asyncio.run(
        exp.run_stage(
            items,
            caller,
            mode=mode,
            rows_path=tmp_path / "rows.json",
            ledger_path=tmp_path / "ledger.json",
            models=MODELS,
            now_fn=clock or Clock(),
            sleep_fn=sleeps or Sleeps(),
            ceiling=ceiling,
            est=est,
            pacing=28.0,
        )
    )


# ---------------------------------------------------------------- plan / prompt


def test_real_plan_is_27_ci_gate_plus_3_held_out_and_baselines_have_not_drifted() -> None:
    items = exp.load_plan()
    assert sum(1 for i in items if i.stratum == "ci_gate_en") == 27
    held = [i for i in items if i.stratum == "held_out_en"]
    assert len(held) == 3
    # The 3 are exactly the held-out fixtures whose hi-en prompt result is NOT recorded.
    art = json.loads(exp.HELD_OUT_ARTIFACT.read_text(encoding="utf-8"))
    unrecorded = {
        r["id"]
        for r in art["records"]
        if r["gt_language"] == "en" and r["detected_language"] == "en"
    }
    assert {i.fid for i in held} == unrecorded
    routing = json.loads((exp.ROOT / "eval/results/routing_cost_n106.json").read_text("utf-8"))
    assert routing["policy_always_hi_en_prompt_recorded_subset"]["n_not_recorded"] == 3
    assert exp.baseline_drift(items) == []


def test_plan_size_mismatch_fails_loudly(tmp_path: Path) -> None:
    latest = {"fixtures": [{"id": "x1", "fields": [], "error": None}]}
    (tmp_path / "latest.json").write_text(json.dumps(latest), encoding="utf-8")
    (tmp_path / "held.json").write_text(json.dumps({"records": []}), encoding="utf-8")
    (tmp_path / "x1.json").write_text(json.dumps(_fixture("x1")), encoding="utf-8")
    (tmp_path / "_held_out_hindi_hinglish").mkdir()
    with pytest.raises(ValueError, match="spec says"):
        exp.load_plan(tmp_path, tmp_path / "latest.json", tmp_path / "held.json")


def test_stage_prompt_is_the_production_hi_en_prompt() -> None:
    from app.core.prompts import build_prompt
    from app.core.sanitize import sanitize, wrap_for_llm

    text = "Great phone, battery is fine."
    clean, _ = sanitize(text)
    assert exp.build_stage_prompt(text) == build_prompt(wrap_for_llm(clean), "hi-en")
    assert exp.build_stage_prompt(text) != build_prompt(wrap_for_llm(clean), "en")


# ---------------------------------------------------------------- ledger


def test_ledger_window_is_rolling_24h_and_per_model(tmp_path: Path) -> None:
    ledger = exp.DayLedger(tmp_path / "l.json")
    ledger.add(SMALL, 1_000, T0, "a")
    ledger.add(LARGE, 500, T0 + 100, "b")
    assert ledger.used(SMALL, T0 + 200) == 1_000
    assert ledger.used(LARGE, T0 + 200) == 500
    assert ledger.used(SMALL, T0 + exp.LEDGER_WINDOW_SECONDS + 1) == 0  # aged out
    assert ledger.used(LARGE, T0 + exp.LEDGER_WINDOW_SECONDS + 1) == 500  # 'b' still inside
    # Persisted: a fresh object (a new invocation) sees the same spend.
    assert exp.DayLedger(tmp_path / "l.json").used(SMALL, T0 + 200) == 1_000


# ---------------------------------------------------------------- budget guard / resume


def test_budget_guard_stops_before_the_ceiling_and_reports_the_next_id(tmp_path: Path) -> None:
    items = [_item(f"f{i}") for i in range(6)]
    out = _run(items, FakeCaller(tokens=3_000), tmp_path, ceiling=10_000, est=3_200)
    # used: 0 -> 3000 -> 6000 -> 9000; a 4th call would need 9000 + 3200 > 10000.
    assert out.n_run == 3
    assert out.stopped_by_guard is True
    assert out.next_id == "f3"
    assert out.used_by_model_window[LARGE] == 9_000


def test_guard_holds_across_invocations_until_the_window_frees(tmp_path: Path) -> None:
    items = [_item(f"f{i}") for i in range(6)]
    clock = Clock()
    _run(items, FakeCaller(tokens=3_000), tmp_path, clock=clock)  # spends 9,000
    second = FakeCaller(tokens=3_000)
    out2 = _run(items, second, tmp_path, clock=clock)  # same instant: ledger still full
    assert out2.n_run == 0 and out2.stopped_by_guard and second.prompts == []
    clock.t += exp.LEDGER_WINDOW_SECONDS + 1  # window has rolled
    third = FakeCaller(tokens=3_000)
    out3 = _run(items, third, tmp_path, clock=clock)
    assert out3.n_already_done == 3  # resume: the first 3 are not re-run
    assert out3.n_run == 3
    assert [p for p in third.prompts if "review f3" in p], "resumed at the next id"
    assert not [p for p in third.prompts if "review f0" in p], "done ids are not re-called"


def test_escalated_call_charges_both_pools(tmp_path: Path) -> None:
    res = exp.CallResult(_extraction("positive", True), LARGE, 2_000, 400, escalated=True)
    out = _run([_item("f0")], FakeCaller([res]), tmp_path)
    assert out.used_by_model_window[LARGE] == 2_400
    assert out.used_by_model_window[SMALL] == 2_400  # the small-tier attempt is charged too


def test_guard_checks_every_pool_not_just_the_last_model_used(tmp_path: Path) -> None:
    ledger = exp.DayLedger(tmp_path / "ledger.json")
    ledger.add(SMALL, 9_000, T0, "earlier")  # small pool nearly full, large pool empty
    out = _run([_item("f0")], FakeCaller(), tmp_path, ceiling=10_000, est=3_200)
    assert out.stopped_by_guard and out.n_run == 0


def test_pacing_between_calls_only_in_record_mode(tmp_path: Path) -> None:
    items = [_item(f"f{i}") for i in range(3)]
    sleeps = Sleeps()
    _run(items, FakeCaller(tokens=1_000), tmp_path, sleeps=sleeps, ceiling=95_000)
    assert sleeps.calls == [28.0, 28.0]  # between calls, none after the last

    replay_sleeps = Sleeps()
    replay_tmp = tmp_path / "replay"
    replay_tmp.mkdir()
    _run(items, FakeCaller(), replay_tmp, mode="replay", sleeps=replay_sleeps)
    assert replay_sleeps.calls == []
    assert not (replay_tmp / "ledger.json").exists(), "replay must not touch the quota ledger"


def test_replay_ignores_the_budget_guard(tmp_path: Path) -> None:
    items = [_item(f"f{i}") for i in range(6)]
    out = _run(items, FakeCaller(tokens=9_000), tmp_path, mode="replay", ceiling=1, est=1)
    assert out.n_run == 6 and not out.stopped_by_guard


def test_provider_error_stops_cleanly_and_keeps_finished_rows(tmp_path: Path) -> None:
    ok = exp.CallResult(_extraction("positive", True), LARGE, 1_000, 200)

    class Boom(FakeCaller):
        async def __call__(self, prompt: str) -> exp.CallResult:
            if len(self.prompts) == 1:
                self.prompts.append(prompt)
                raise RuntimeError("tokens per day exceeded")
            return await super().__call__(prompt)

    out = _run(
        [_item("f0"), _item("f1"), _item("f2")], Boom([ok, ok, ok]), tmp_path, ceiling=95_000
    )
    assert out.n_run == 1
    assert out.stopped_by_error == "tokens per day exceeded"
    assert out.next_id == "f1"
    rows = json.loads((tmp_path / "rows.json").read_text(encoding="utf-8"))["rows"]
    assert [r["id"] for r in rows] == ["f0"]


def test_rows_file_is_written_after_every_call(tmp_path: Path) -> None:
    seen: list[int] = []

    class Peek(FakeCaller):
        async def __call__(self, prompt: str) -> exp.CallResult:
            p = tmp_path / "rows.json"
            seen.append(len(json.loads(p.read_text(encoding="utf-8"))["rows"]) if p.exists() else 0)
            return await super().__call__(prompt)

    _run([_item(f"f{i}") for i in range(3)], Peek(tokens=1_000), tmp_path, ceiling=95_000)
    assert seen == [0, 1, 2]


# ---------------------------------------------------------------- delta math


def test_headline_excludes_stars_and_language() -> None:
    fx = _fixture("f0")
    # Wrong language and null stars must not matter; only sentiment and buy_again count.
    ex = _extraction("positive", False, language="hi-en")
    assert exp.headline_score(fx, ex) == 0.5  # sentiment right (1.0), buy_again wrong (0.0)


def test_paired_delta_is_new_minus_recorded_per_fixture() -> None:
    items = [
        _item("a", base_sentiment="negative", base_buy=False),  # baseline scores 0.0
        _item("b", base_sentiment="positive", base_buy=True),  # baseline scores 1.0
    ]
    rows = {
        "a": {"predicted": _extraction("positive", True)},  # new 1.0 -> +1.0
        "b": {"predicted": _extraction("negative", False)},  # new 0.0 -> -1.0
    }
    deltas = exp.english_deltas(items, rows)
    assert deltas["ci_gate_en"] == [1.0, -1.0]
    assert deltas["held_out_en"] == []


def test_unfinished_items_are_skipped_in_the_delta() -> None:
    items = [_item("a"), _item("b")]
    deltas = exp.english_deltas(items, {"a": {"predicted": _extraction("positive", True)}})
    assert deltas["ci_gate_en"] == [0.0]


def test_paired_bootstrap_is_seed_42_10000_resamples_and_unclamped() -> None:
    deltas = [-0.10, -0.05, -0.02, 0.0, 0.0, 0.01, -0.08, -0.03]
    lo, hi = exp.paired_bootstrap_ci(deltas)
    rng = random.Random(42)
    means = sorted(mean(rng.choices(deltas, k=len(deltas))) for _ in range(10_000))
    assert (lo, hi) == (means[250], means[9750 - 1])
    assert hi < 0.05 and lo < 0.0, "a negative-effect CI must not be clamped at 0"
    assert lo < mean(deltas) < hi


def test_delta_stats_counts_and_se() -> None:
    s = exp.delta_stats([0.1, -0.1, 0.0, 0.2])
    assert (s["n"], s["n_up"], s["n_down"], s["n_zero"]) == (4, 2, 1, 1)
    assert s["mean"] == pytest.approx(0.05)
    sd = math.sqrt(sum((d - 0.05) ** 2 for d in [0.1, -0.1, 0.0, 0.2]) / 3)
    assert s["sd"] == pytest.approx(sd)
    assert s["se"] == pytest.approx(sd / 2)


def test_hinglish_stratum_comes_from_recorded_data_only() -> None:
    def rec(gt: str, dep: float, forced: float) -> dict[str, Any]:
        def fs(x: float) -> dict[str, float]:
            return {"stars": 1.0, "language": 0.0, "product": x, "sentiment": x}

        return {
            "gt_language": gt,
            "as_deployed": {"field_scores": fs(dep)},
            "language_forced": {"field_scores": fs(forced)},
        }

    art = {"records": [rec("hi-en", 0.5, 0.75), rec("hi-en", 1.0, 0.5), rec("en", 0.0, 1.0)]}
    assert exp.hinglish_recorded_deltas(art) == [0.25, -0.5]  # the gt-en record is excluded


# ---------------------------------------------------------------- decision rule


def _stats(mean_: float, lower: float, n: int = 30) -> dict[str, Any]:
    return {"n": n, "mean": mean_, "ci95": [lower, lower + 0.08]}


def test_rule_futility_stops_below_minus_5pp() -> None:
    d = exp.decide(_stats(-0.051, -0.12), _stats(0.0, -0.01, 101), 30)
    assert d["verdict"].startswith("STOP (futility)")
    d = exp.decide(_stats(-0.05, -0.12), _stats(0.0, -0.01, 101), 30)  # exactly -5pp: not below
    assert not d["verdict"].startswith("STOP")


def test_rule_non_inferiority_needs_both_strata_and_is_never_a_licence_to_adopt() -> None:
    both = exp.decide(_stats(0.01, -0.029), _stats(0.0, -0.029, 101), 30)
    assert both["verdict"].startswith("NON-INFERIORITY NUMERICALLY MET")
    assert "NOT sufficient to delete routing" in both["verdict"]
    en_only = exp.decide(_stats(0.01, -0.029), _stats(0.0, -0.031, 101), 30)
    assert en_only["verdict"].startswith("NOT DECIDED")
    hi_only = exp.decide(_stats(0.0, -0.031), _stats(0.0, -0.01, 101), 30)
    assert hi_only["verdict"].startswith("NOT DECIDED")


def test_rule_margin_boundary_is_inclusive() -> None:
    at_margin = exp.decide(_stats(0.0, -0.03), _stats(0.0, -0.03, 101), 30)
    assert at_margin["verdict"].startswith("NON-INFERIORITY NUMERICALLY MET")


def test_rule_incomplete_and_empty_are_not_conclusions() -> None:
    assert exp.decide(_stats(0.0, -0.01, n=12), _stats(0.0, -0.01, 101), 30)["verdict"].startswith(
        "INTERIM"
    )
    assert exp.decide({"n": 0}, _stats(0.0, -0.01, 101), 30)["verdict"].startswith("NO DATA")


def test_decision_states_honestly_that_stage1_cannot_certify_the_hinglish_stratum() -> None:
    d = exp.decide(_stats(0.0, -0.01), _stats(0.0, -0.01, 101), 30)
    assert "Hinglish stratum" in d["stage1_cannot_certify"]
    assert "does not exist yet" in d["stage1_cannot_certify"]


def test_verdict_probabilities_are_a_partition_and_monotone() -> None:
    at_futility = exp.verdict_probabilities(-0.05, 0.11, 30)
    assert at_futility["futility"] == pytest.approx(0.5)
    prev = -1.0
    for mu in (-0.08, -0.04, 0.0, 0.03):
        p = exp.verdict_probabilities(mu, 0.11, 30)
        assert sum(p.values()) == pytest.approx(1.0)
        assert p["non_inferior"] >= prev
        prev = p["non_inferior"]


def test_report_end_to_end_with_mock_rows() -> None:
    items = [_item(f"f{i}") for i in range(4)]
    rows = {
        it.fid: {
            "id": it.fid,
            "predicted": _extraction("positive", True),
            "model": LARGE,
            "escalated": False,
            "degraded": False,
            "tokens_in": 2_000,
            "tokens_out": 500,
        }
        for it in items
    }
    art = json.loads(exp.HELD_OUT_ARTIFACT.read_text(encoding="utf-8"))
    rep = exp.build_report(items, rows, art)
    assert rep["english_pooled_hi_en_prompt_minus_en_prompt"]["mean"] == 0.0
    assert rep["tokens_spent_by_this_experiment"]["tokens_total"] == 10_000
    assert rep["decision"]["verdict"].startswith("INTERIM")  # 4 of 30 planned
    assert rep["hinglish_recorded_hi_en_prompt_minus_as_deployed"]["n"] == 101


# ---------------------------------------------------------------- CLI safety


def test_record_mode_refuses_without_the_explicit_quota_flag(tmp_path: Path) -> None:
    before = exp.ROWS_PATH.exists()
    with pytest.raises(SystemExit) as e:
        exp.main(["record"])
    assert e.value.code == 2
    assert exp.ROWS_PATH.exists() == before  # nothing was recorded or created
    assert not exp.LEDGER_PATH.exists()


def test_budget_constants_match_the_spec() -> None:
    assert exp.TOKEN_CEILING_PER_MODEL == 95_000  # 5% inside the 100K/model/day budget
    assert exp.PACING_SECONDS == 28.0
    assert exp.N_CI_GATE_EN + exp.N_HELD_OUT_EN == 30


def test_design_expected_tokens_and_sd_come_from_recorded_artifacts() -> None:
    art = json.loads(exp.HELD_OUT_ARTIFACT.read_text(encoding="utf-8"))
    tc = json.loads((exp.ROOT / "eval/results/token_cost_measurement_n106.json").read_text("utf-8"))
    d = exp.design(art, tc)
    small = tc["per_tier"]["small"]["tokens_total"]["mean"]
    large = tc["per_tier"]["large"]["tokens_total"]["mean"]
    f_large = tc["tier_mix_observed"]["large"]["fraction"]
    assert d["n_calls"] == 30
    assert d["expected_tokens"]["small_pool"] == pytest.approx(30 * small)
    assert d["expected_tokens"]["large_pool"] == pytest.approx(30 * f_large * large)
    # The busiest pool must sit inside the per-model ceiling, else the experiment cannot finish.
    assert d["expected_tokens"]["small_pool"] < exp.TOKEN_CEILING_PER_MODEL
    # The spec quotes an 11.2pp per-fixture SD of the held-out delta.
    assert d["delta_sd_held_out"]["all_106"] == pytest.approx(0.112, abs=0.001)
    assert d["delta_sd_held_out"]["nonzero_only"] > d["delta_sd_held_out"]["all_106"]
