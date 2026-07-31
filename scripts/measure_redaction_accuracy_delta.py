"""Measure the extraction-accuracy delta of PII redaction (Wave 1 Section E critical gate).

Runs the eval fixture set TWICE against cassette replay -- once with the production
default (PII redaction ON), once with redaction disabled via the eval-only
REVIEW_IQ_DISABLE_PII_REDACTION_FOR_EVAL=1 lever (see app.core.sanitize._pii_redaction_enabled)
-- with everything else (prompt-injection detection, truncation, model, cassette store)
held identical, isolating PII redaction as the only variable.

HARD CONSTRAINT: this script NEVER makes a live LLM call. It forces
EVAL_CASSETTE_MODE=replay for its own process and calls extract_with_llm with
allow_gemini_fallback=False (mirroring the org/v2 path -- avoids eval/runner.py's
documented run_single() KNOWN GAP where a cassette miss silently falls through to a
live Gemini call). A fixture with no cassette entry for a given arm is reported as
BLOCKED for that arm, never silently skipped or treated as a live call.

Paired design: the same 49 fixtures are scored in both arms, so the delta is computed
as a paired difference (per-fixture score_on - score_off), with a bootstrap 95% CI on
the mean paired difference (not two independently-eyeballed CIs). Fixtures blocked in
either arm are excluded from the paired delta and reported separately -- see the
"blocked" section of the report; a delta computed while ignoring blocked fixtures is
NOT the same claim as "the full eval set was measured," and the report says so
explicitly.

Usage:
    uv run python scripts/measure_redaction_accuracy_delta.py
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

REPORT_PATH = REPO_ROOT / "eval" / "fixtures" / "redaction" / "accuracy_delta_report.json"
# Bootstrap resample count and seed -- hardcoded per house convention (seed=42 everywhere
# stochastic, see CLAUDE.md rule 40) for a reproducible CI on the paired difference.
_BOOTSTRAP_RESAMPLES = 10_000
_SEED = 42


@dataclass
class ArmResult:
    fixture_id: str
    language: str
    score: float | None  # None = blocked (no cassette for this arm's prompt)
    error: str | None


async def _score_fixture(fixture: dict[str, Any]) -> ArmResult:
    """Score one fixture under whatever REVIEW_IQ_DISABLE_PII_REDACTION_FOR_EVAL is
    currently set to. Mirrors eval.runner.run_single but pins
    allow_gemini_fallback=False (avoids run_single's documented live-Gemini-fallback gap)
    and treats a cassette miss as BLOCKED (score=None) rather than an ERROR-scored 0.0,
    since "blocked" and "the model genuinely got it wrong" are different findings.
    """
    from app.core.llm import extract_with_llm
    from app.core.prompts import build_prompt
    from app.core.sanitize import sanitize, wrap_for_llm
    from eval.runner import score_fixture

    fixture_id = fixture["id"]
    lang = fixture.get("ground_truth", {}).get("language", "en")

    try:
        sanitized, _is_suspicious, _rmap = sanitize(fixture["review_text"])
        wrapped = wrap_for_llm(sanitized)
        user_prompt = build_prompt(wrapped, lang)
        llm_output, _model, _latency_ms, _tin, _tout, _degraded = await extract_with_llm(
            user_prompt, allow_gemini_fallback=False
        )
    except RuntimeError as exc:
        # extract_with_llm's ONLY RuntimeError-raising path is the final
        # "All LLM providers failed to extract the review." at the end of
        # app.core.llm.extract_with_llm -- reached only once every attempted
        # provider (tiered small, tiered large/escalation, secondary, and
        # -- since allow_gemini_fallback=False -- explicitly NOT Gemini) has
        # been exhausted. With cassette replay the only way every attempt can
        # be exhausted is a missing cassette somewhere in that chain (a Groq
        # quota/API error can't happen in replay mode; nothing calls the
        # network). So ANY RuntimeError here means "this fixture's result
        # cannot be produced without a live call" -- BLOCKED, not a genuine
        # 0.0 extraction failure. (A narrower substring match on the message
        # under-detects: tiered routing swallows the specific inner
        # "no cassette for key" exception into this generic outer message.)
        return ArmResult(fixture_id=fixture_id, language=lang, score=None, error=str(exc))
    except Exception as exc:  # noqa: BLE001 -- record and keep going
        return ArmResult(fixture_id=fixture_id, language=lang, score=0.0, error=str(exc))

    field_results = score_fixture(fixture, llm_output.model_dump())
    scores = [fr.score for fr in field_results]
    overall = sum(scores) / len(scores) if scores else 0.0
    return ArmResult(fixture_id=fixture_id, language=lang, score=overall, error=None)


async def _run_arm(fixtures: list[dict[str, Any]], *, redaction_disabled: bool) -> list[ArmResult]:
    if redaction_disabled:
        os.environ["REVIEW_IQ_DISABLE_PII_REDACTION_FOR_EVAL"] = "1"
    else:
        os.environ.pop("REVIEW_IQ_DISABLE_PII_REDACTION_FOR_EVAL", None)

    results: list[ArmResult] = []
    for fixture in fixtures:
        results.append(await _score_fixture(fixture))
    return results


def _bootstrap_ci(
    diffs: list[float], resamples: int = _BOOTSTRAP_RESAMPLES, seed: int = _SEED
) -> tuple[float, float]:
    """Percentile bootstrap 95% CI on the mean of `diffs`. Empty input -> (0.0, 0.0)."""
    if not diffs:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(diffs)
    means: list[float] = []
    for _ in range(resamples):
        sample = [diffs[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo_idx = int(0.025 * resamples)
    hi_idx = int(0.975 * resamples) - 1
    return (means[lo_idx], means[min(hi_idx, resamples - 1)])


def main() -> int:
    from eval.runner import FIXTURES_DIR, _collect_fixture_paths

    # Set here (not at module import time) so importing this module -- e.g. from a test
    # that only wants `_bootstrap_ci` -- can never mutate the shared pytest process's
    # environment for unrelated tests running in the same session. Must never be
    # "record"/"live" -- this script's entire safety contract is zero live calls.
    os.environ["EVAL_CASSETTE_MODE"] = "replay"

    fixtures = [
        json.loads(p.read_text(encoding="utf-8")) for p in _collect_fixture_paths(FIXTURES_DIR)
    ]
    print(f"Loaded {len(fixtures)} eval fixtures from {FIXTURES_DIR}")
    print("Cassette mode forced to 'replay' -- zero live LLM calls will be made.\n")

    print("=== Arm ON (redaction enabled, production default) ===")
    on_results = asyncio.run(_run_arm(fixtures, redaction_disabled=False))
    on_by_id = {r.fixture_id: r for r in on_results}

    print("=== Arm OFF (redaction disabled, PII+injection guard still active otherwise) ===")
    off_results = asyncio.run(_run_arm(fixtures, redaction_disabled=True))
    off_by_id = {r.fixture_id: r for r in off_results}

    blocked_on = [r for r in on_results if r.score is None]
    blocked_off = [r for r in off_results if r.score is None]

    paired_diffs: list[float] = []
    paired_fixture_ids: list[str] = []
    for fixture_id, on_r in on_by_id.items():
        off_r = off_by_id[fixture_id]
        if on_r.score is None or off_r.score is None:
            continue
        paired_diffs.append(on_r.score - off_r.score)
        paired_fixture_ids.append(fixture_id)

    n_total = len(fixtures)
    n_paired = len(paired_diffs)
    n_blocked = n_total - n_paired

    print(f"\n{n_paired}/{n_total} fixtures had a valid (cassette-hit) score in BOTH arms.")
    print(f"{len(blocked_on)} blocked in arm ON; {len(blocked_off)} blocked in arm OFF.")

    report: dict[str, Any] = {
        "n_fixtures": n_total,
        "n_paired": n_paired,
        "n_blocked_on": len(blocked_on),
        "n_blocked_off": len(blocked_off),
        "blocked_on_fixture_ids": [r.fixture_id for r in blocked_on],
        "blocked_off_fixture_ids": [r.fixture_id for r in blocked_off],
    }

    if n_paired == 0:
        print(
            "\nUNMEASURABLE: zero fixtures had a valid cassette-replayed score in BOTH arms. "
            "The accuracy-delta gate cannot be computed without at least one live LLM call. "
            "STOPPING per the explicit instruction -- not fabricating a number."
        )
        report["measurable"] = False
        report["delta"] = None
        report["delta_ci95"] = None
    else:
        mean_diff = sum(paired_diffs) / n_paired
        lo, hi = _bootstrap_ci(paired_diffs)
        print(f"\nPaired mean delta (ON - OFF): {mean_diff:+.4f} ({mean_diff * 100:+.2f}pp)")
        print(
            f"Bootstrap 95% CI on the mean paired difference: [{lo * 100:+.2f}pp, {hi * 100:+.2f}pp]"
        )
        report["measurable"] = True
        report["delta"] = mean_diff
        report["delta_pp"] = mean_diff * 100
        report["delta_ci95_pp"] = [lo * 100, hi * 100]
        report["paired_fixture_ids"] = paired_fixture_ids

        if n_blocked > 0:
            print(
                f"\nCAVEAT: {n_blocked} fixture(s) excluded from this delta because they were "
                "blocked (no cassette) in at least one arm. This is NOT a full-eval-set "
                "measurement -- see blocked_on/blocked_off_fixture_ids in the JSON report."
            )

    report_path = REPORT_PATH
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport written to {report_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
