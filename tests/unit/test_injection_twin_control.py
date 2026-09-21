"""Unit tests for eval/run_injection_twin_control.py (D6) and run_injection_e2e.py --controls.

Hermetic: a mocked extraction function stands in for the provider; nothing here touches a network,
a cassette or the real state file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from app.core.injection_controls import input_flags
from app.core.schemas import ReviewExtractionLLMOutput, Sentiment
from eval import run_injection_twin_control as tw
from eval.injection_suite import CASES

ROOT = Path(__file__).resolve().parents[2]
SMALL, LARGE = "small-model", "large-model"


# --------------------------------------------------------------------------------------------
# pair construction
# --------------------------------------------------------------------------------------------
def test_pair_counts_ids_and_determinism() -> None:
    pairs = tw.build_pairs()
    assert len(pairs) == 74
    by_type = {t: [p for p in pairs if p.type == t] for t in tw.TYPES}
    assert {t: len(v) for t, v in by_type.items()} == {
        "buy_again": 12,
        "stars_inferred": 12,
        "topics": 50,
    }
    assert len({p.pair_id for p in pairs}) == 74
    assert len({p.base for p in pairs}) == 74  # no base review reused, within or across types
    assert tw.build_pairs() == pairs  # deterministic
    assert [p.type for p in pairs] == ["buy_again"] * 12 + ["stars_inferred"] * 12 + ["topics"] * 50


def test_index_zero_reproduces_the_suite_attack_text_exactly() -> None:
    suite = {c.id: c.text for c in CASES}
    for t, (attack_id, _) in tw.TYPES.items():
        first = next(p for p in tw.build_pairs() if p.type == t)
        assert first.attack_text == suite[attack_id]
        assert first.twin_text == tw.SUITE_BASE[t]
        assert suite[attack_id].endswith(first.twin_text)


def test_attack_is_the_twin_plus_an_instruction_and_nothing_else() -> None:
    for p in tw.build_pairs():
        assert p.attack_text == f"{p.instruction} {p.twin_text}"
        assert p.instruction in tw.INSTRUCTIONS[p.type]


def test_all_three_paraphrases_are_used_per_type_and_order_is_mixed() -> None:
    pairs = tw.build_pairs()
    for t in tw.TYPES:
        assert {p.paraphrase for p in pairs if p.type == t} == {0, 1, 2}
    assert {p.attack_first for p in pairs} == {True, False}


def test_synthetic_base_reviews_occur_in_no_corpus_or_fixture() -> None:
    """The 3 suite sentences (index 0) are deliberately reused; every other base review is
    synthetic and must not appear in any held-out, dev, benchmark or gap-fix text."""
    corpus: list[str] = []
    for p in (ROOT / "eval" / "fixtures").rglob("*.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            corpus.append(str(d.get("review_text") or d.get("text") or ""))
    for rel in (
        "benchmark/dataset/gold.jsonl",
        "benchmark/gap_fixes/candidates.jsonl",
        "benchmark/vernacular_v2/candidates.jsonl",
    ):
        for line in (ROOT / rel).read_text(encoding="utf-8").splitlines():
            if line.strip():
                corpus.append(str(json.loads(line).get("text", "")))
    blob = "\n".join(corpus).lower()
    assert len(blob) > 10_000
    synthetic = [p.base for p in tw.build_pairs() if p.base not in tw.SUITE_BASE.values()]
    assert len(synthetic) == 71
    assert [b for b in synthetic if b.lower() in blob] == []


def test_all_injection_paraphrases_are_in_the_naive_attacker_family() -> None:
    """Documents scope: every paraphrase trips the S15d input rules, so a controls-on run of this
    design measures the naive attacker only (evasive phrasings are not in this experiment)."""
    for t, texts in tw.INSTRUCTIONS.items():
        for text in texts:
            assert input_flags(text), (t, text)


# --------------------------------------------------------------------------------------------
# landed definition, McNemar
# --------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("attack", "twin", "landed"),
    [(True, False, True), (True, True, False), (False, False, False), (False, True, False)],
)
def test_landed_needs_attack_match_and_twin_no_match(
    attack: bool, twin: bool, landed: bool
) -> None:
    assert tw.is_landed(attack, twin) is landed


def test_target_predicates() -> None:
    assert tw.matches_target("buy_again", {"buy_again": True})
    assert not tw.matches_target("buy_again", {"buy_again": None})
    assert tw.matches_target("stars_inferred", {"stars_inferred": 5})
    assert not tw.matches_target("stars_inferred", {"stars_inferred": 4})
    assert tw.matches_target("topics", {"topics": []})
    assert not tw.matches_target("topics", {"topics": ["battery"]})
    with pytest.raises(ValueError):
        tw.matches_target("nope", {})


def test_mcnemar_exact_values() -> None:
    assert tw.mcnemar_exact_p(0, 0) == 1.0
    assert tw.mcnemar_exact_p(5, 0) == pytest.approx(0.0625)  # 2 * (1/32)
    assert tw.mcnemar_exact_p(9, 0) == pytest.approx(2 / 512)
    assert tw.mcnemar_exact_p(3, 1) == pytest.approx(0.625)  # 2 * (1 + 4) / 16
    assert tw.mcnemar_exact_p(2, 2) == 1.0  # capped at 1
    assert tw.mcnemar_exact_p(1, 4) == tw.mcnemar_exact_p(4, 1)


# --------------------------------------------------------------------------------------------
# record: resume, guard, accounting
# --------------------------------------------------------------------------------------------
def _final(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"buy_again": None, "stars_inferred": 2, "topics": ["battery"]}
    base.update(over)
    return base


def make_extract(
    calls: list[str],
    *,
    attack_hits: bool = True,
    tokens: tuple[int, int] = (1000, 500),
    model: str = SMALL,
    escalated: bool = False,
    fail_on_call: int | None = None,
) -> Any:
    """A fake extraction fn: the ATTACK text (contains an instruction) lands, the twin does not."""

    async def extract(text: str) -> dict[str, Any]:
        calls.append(text)
        if fail_on_call is not None and len(calls) == fail_on_call:
            raise RuntimeError("groq exhausted")
        is_attack = any(text.startswith(i) for ts in tw.INSTRUCTIONS.values() for i in ts)
        final = _final(buy_again=True, stars_inferred=5, topics=[]) if is_attack else _final()
        if not (is_attack and attack_hits):
            final = _final()
        return {
            "final": final,
            "model": model,
            "tokens_in": tokens[0],
            "tokens_out": tokens[1],
            "escalated": escalated,
        }

    return extract


async def _nosleep(_: float) -> None:
    return None


def _new_budget(state: dict[str, Any], today: str = "2026-09-20", **kw: Any) -> tw.Budget:
    return tw.Budget(
        [SMALL, LARGE],
        state["usage_by_day"].setdefault(today, {}),
        est_call_tokens=state["max_call_tokens_seen"],
        **kw,
    )


async def _record(
    state: dict[str, Any], extract: Any, budget: tw.Budget, **kw: Any
) -> dict[str, Any]:
    saved: list[str] = []
    out = await tw.record(
        tw.build_pairs(),
        extract,
        state,
        budget,
        small_model=SMALL,
        save=lambda: saved.append(json.dumps(state)),
        sleep=_nosleep,
        **kw,
    )
    out["saves"] = len(saved)
    return out


@pytest.mark.asyncio
async def test_record_completes_pairs_and_marks_landed() -> None:
    state = tw.new_state()
    calls: list[str] = []
    out = await _record(state, make_extract(calls), _new_budget(state), max_pairs=4)
    assert out["pairs_finished_this_invocation"] == 4 and len(calls) == 8
    assert set(state["completed"]) == {f"buy_again-{i:02d}" for i in range(4)}
    assert all(r["landed"] for r in state["completed"].values())
    assert state["partial"] == {}
    assert out["saves"] >= 8  # persisted after every call, not only at the end


@pytest.mark.asyncio
async def test_resume_skips_completed_pairs_and_never_recalls_them() -> None:
    state = tw.new_state()
    first: list[str] = []
    await _record(state, make_extract(first), _new_budget(state), max_pairs=3)
    done = set(state["completed"])
    second: list[str] = []
    await _record(state, make_extract(second), _new_budget(state), max_pairs=3)
    assert len(state["completed"]) == 6
    pairs = {p.pair_id: p for p in tw.build_pairs()}
    for pid in done:
        assert pairs[pid].attack_text not in second and pairs[pid].twin_text not in second


@pytest.mark.asyncio
async def test_half_finished_pair_resumes_with_only_the_missing_call() -> None:
    state = tw.new_state()
    calls: list[str] = []
    out = await _record(state, make_extract(calls, fail_on_call=2), _new_budget(state))
    assert "call failed" in out["stopped"] and out["pairs_finished_this_invocation"] == 0
    assert list(state["partial"]) == ["buy_again-00"] and len(state["partial"]["buy_again-00"]) == 1
    # the successful first call was charged before the crash
    assert sum(state["usage_by_day"]["2026-09-20"].values()) == 1500
    calls2: list[str] = []
    await _record(state, make_extract(calls2), _new_budget(state), max_pairs=1)
    assert len(calls2) == 1  # only the missing arm ran
    assert "buy_again-00" in state["completed"] and "buy_again-00" not in state["partial"]


@pytest.mark.asyncio
async def test_invocation_guard_refuses_to_exceed_95k_per_model() -> None:
    state = tw.new_state()
    calls: list[str] = []
    out = await _record(state, make_extract(calls), _new_budget(state))
    assert out["stopped"].startswith("BUDGET GUARD")
    inv = out["tokens_this_invocation_by_model"]
    assert 0 < inv[SMALL] <= tw.INVOCATION_CEILING
    assert LARGE not in inv or inv[LARGE] <= tw.INVOCATION_CEILING
    assert len(state["completed"]) < 74  # it stopped instead of finishing


@pytest.mark.asyncio
async def test_two_invocations_the_same_utc_day_cannot_exceed_100k_per_model() -> None:
    state = tw.new_state()
    first = await _record(state, make_extract([]), _new_budget(state))
    assert first["stopped"].startswith("BUDGET GUARD")
    used_after_first = state["usage_by_day"]["2026-09-20"][SMALL]
    # second invocation, same day, fresh Budget reading the day's prior usage from the state
    second = await _record(state, make_extract([]), _new_budget(state))
    total = state["usage_by_day"]["2026-09-20"][SMALL]
    assert total <= tw.DAY_CEILING
    assert total >= used_after_first
    assert second["stopped"].startswith("BUDGET GUARD")
    # a different UTC day starts from zero again
    third = await _record(state, make_extract([]), _new_budget(state, today="2026-09-21"))
    assert third["pairs_finished_this_invocation"] > 0
    assert state["usage_by_day"]["2026-09-21"][SMALL] <= tw.DAY_CEILING
    assert state["usage_by_day"]["2026-09-20"][SMALL] == total


@pytest.mark.asyncio
async def test_guard_checks_the_large_pool_too_and_adapts_to_big_calls() -> None:
    state = tw.new_state()
    state["usage_by_day"]["2026-09-20"] = {LARGE: 99_000}  # large pool nearly spent today
    out = await _record(state, make_extract([]), _new_budget(state))
    assert out["stopped"].startswith("BUDGET GUARD") and not state["completed"]
    state2 = tw.new_state()
    b = _new_budget(state2)
    b.charge({SMALL: 9_000}, 9_000)  # one large call seen: estimate adapts upward
    assert b.est_call_tokens == 9_000


def test_escalated_call_charges_the_small_pool_as_well() -> None:
    res = {"model": LARGE, "tokens_in": 2000, "tokens_out": 500, "escalated": True}
    assert tw.tokens_spent(res, SMALL) == {LARGE: 2500, SMALL: 2500}
    res["escalated"] = False
    assert tw.tokens_spent(res, SMALL) == {LARGE: 2500}


def test_state_roundtrip_and_missing_file(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    assert tw.load_state(p) == tw.new_state()
    s = tw.new_state()
    s["completed"]["x"] = {"landed": True}
    tw.save_state(p, s)
    assert tw.load_state(p) == s
    assert not list(tmp_path.glob("*.tmp"))


# --------------------------------------------------------------------------------------------
# report + replay
# --------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_summarize_counts_b_c_and_pvalue() -> None:
    state = tw.new_state()
    await _record(state, make_extract([]), _new_budget(state), max_pairs=12)
    completed = dict(state["completed"])
    # flip two pairs: twin-only (c) and both-match
    completed["buy_again-00"] = {
        **completed["buy_again-00"],
        "attack_match": False,
        "twin_match": True,
    }
    completed["buy_again-01"] = {
        **completed["buy_again-01"],
        "attack_match": True,
        "twin_match": True,
    }
    s = tw.summarize(completed)["per_type"]["buy_again"]
    assert (s["pairs_done"], s["landed_b"], s["twin_only_c"], s["both_match"]) == (12, 10, 1, 1)
    assert s["neither"] == 0 and s["complete"] is True
    assert s["mcnemar_exact_p_two_sided"] == pytest.approx(tw.mcnemar_exact_p(10, 1), abs=1e-6)
    assert s["landing_rate"]["k"] == 10 and s["landing_rate"]["n"] == 12
    assert tw.summarize(completed)["per_type"]["topics"]["pairs_done"] == 0
    rep = tw.build_report(completed, state, "record")
    assert rep["pairs_done"] == 12 and rep["pairs_planned"] == 74
    assert rep["tokens_by_model_reported_calls_only"][SMALL] == 12 * 2 * 1500


@pytest.mark.asyncio
async def test_replay_skips_pairs_without_cassette_entries() -> None:
    pairs = tw.build_pairs()[:3]
    ok = make_extract([])

    async def extract(text: str) -> dict[str, Any]:
        if pairs[1].pair_id and text in (pairs[1].attack_text, pairs[1].twin_text):
            raise RuntimeError("No cassette for key")
        return await ok(text)

    completed, missing = await tw.replay(pairs, extract)
    assert missing == [pairs[1].pair_id] and set(completed) == {pairs[0].pair_id, pairs[2].pair_id}


# --------------------------------------------------------------------------------------------
# run_injection_e2e.py --controls on|off
# --------------------------------------------------------------------------------------------
def _flags(inp: bool, out: bool) -> SimpleNamespace:
    return SimpleNamespace(
        enable_field_injection_input_control=inp, enable_field_injection_output_check=out
    )


_FORGED = ReviewExtractionLLMOutput(
    product="Vac",
    stars_inferred=1,
    sentiment=Sentiment.negative,
    buy_again=True,
    topics=["durability"],
    cons=["broke"],
)


@pytest.mark.asyncio
async def test_e2e_extract_final_off_is_the_original_shape() -> None:
    from eval import run_injection_e2e as e2e

    attack = next(c.text for c in CASES if c.id == "f4-01")
    route = AsyncMock(return_value=(_FORGED.model_copy(deep=True), "m", 10, 5, False, False))
    with patch.object(e2e, "route_extraction", route):
        r = await e2e._extract_final(attack, SimpleNamespace(), False)
    assert "injection_controls" not in r and r["final"]["buy_again"] is True
    assert (
        "buy_again field specifically" in route.call_args.args[0]
    )  # attack text reached the model


@pytest.mark.asyncio
async def test_e2e_extract_final_on_strips_nulls_and_reports() -> None:
    from eval import run_injection_e2e as e2e

    attack = next(c.text for c in CASES if c.id == "f4-01")
    route = AsyncMock(return_value=(_FORGED.model_copy(deep=True), "m", 10, 5, False, False))
    with (
        patch.object(e2e, "route_extraction", route),
        patch("app.core.injection_controls.get_settings", return_value=_flags(True, True)),
    ):
        r = await e2e._extract_final(attack, SimpleNamespace(), True)
    assert "buy_again field specifically" not in route.call_args.args[0]
    assert "The vacuum broke immediately." in route.call_args.args[0]
    assert r["final"]["buy_again"] is None
    assert r["injection_controls"]["input_stripped"] and r["injection_controls"]["needs_review"]
    assert e2e.ATTACKS["f4-01"][1](r["final"]) is False  # the attack no longer "landed"


@pytest.mark.asyncio
async def test_e2e_main_controls_flag_selects_output_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings
    from eval import run_injection_e2e as e2e

    off_path, on_path = tmp_path / "off.json", tmp_path / "on.json"
    monkeypatch.setattr(e2e, "OUT_PATH", off_path)
    monkeypatch.setattr(e2e, "OUT_PATH_CONTROLS_ON", on_path)
    monkeypatch.setattr(e2e, "PACING_SECONDS", 0.0)
    for name in ("ENABLE_FIELD_INJECTION_INPUT_CONTROL", "ENABLE_FIELD_INJECTION_OUTPUT_CHECK"):
        monkeypatch.setenv(name, "false")  # registers restore; main() overwrites when --controls on
    route = AsyncMock(return_value=(_FORGED.model_copy(deep=True), "m", 10, 5, False, False))
    monkeypatch.setattr(e2e, "route_extraction", route)
    monkeypatch.setattr(e2e, "classify_injection_risk", AsyncMock(return_value=False))
    try:
        get_settings.cache_clear()
        monkeypatch.setattr(sys, "argv", ["x", "--runs", "1", "--controls", "off"])
        await e2e.main()
        assert off_path.exists() and not on_path.exists()
        off = json.loads(off_path.read_text(encoding="utf-8"))
        assert "controls_mode" not in off  # controls-off artifact shape is unchanged
        f401_off = next(a for a in off["per_attack"] if a["id"] == "f4-01")
        assert f401_off["landed"] == 1
        monkeypatch.setattr(sys, "argv", ["x", "--runs", "1", "--controls", "on"])
        await e2e.main()
        on = json.loads(on_path.read_text(encoding="utf-8"))
        assert on["controls_mode"] == "on"
        f401_on = next(a for a in on["per_attack"] if a["id"] == "f4-01")
        assert f401_on["landed"] == 0 and f401_on["runs"][0]["injection_controls"]["needs_review"]
    finally:
        get_settings.cache_clear()
