"""P1/P3 (Session 11): reproducible, cassette-backed scoring of the held-out corpus, plus a
language-routing isolation experiment.

Supersedes eval/score_held_out_corpus.py (Session 10), which hit the LIVE demo endpoint over
HTTP with no cassette -- unreplayable, and vulnerable to exactly the kind of in-session
collision that happened (the demo cap zeroed for an unrelated batch while that measurement was
still in flight). This version calls `extract_with_llm` IN-PROCESS, the same function
`app/api/demo.py` and `app/api/v2/extract.py` both call, using the SAME cassette mechanism
(`app.core.providers.cassette`, controlled by EVAL_CASSETTE_MODE) the 49-fixture CI-gate set
already uses -- just pointed at a separate cassette file, so this never touches or depends on
`eval/cassettes/cassettes.json`.

Two conditions per fixture, matching a real methodology gap found this session: neither
`eval/runner.py::run_single` nor `run_single_routed` (the functions behind every published
CI-gate number) ever calls `detect_language()` -- both force-feed the fixture's own
ground-truth language into `build_prompt`. Real traffic (`app/api/demo.py`, `app/api/
v2/extract.py`) calls `detect_language()` first and routes on WHATEVER IT RETURNS. This script
measures both:

  - "as_deployed": detect_language() picks the prompt, exactly like real traffic. This is the
    number that matters for P1 (what a customer actually experiences).
  - "language_forced": the fixture's own ground-truth language is forced into build_prompt,
    exactly like the CI-gate set's own methodology. This isolates contamination/prompt-quality
    from language-misrouting: the delta between the two conditions is misrouting cost, not
    contamination (P3b).

Only fixtures where detect_language() disagrees with the ground-truth language need a SEPARATE
call for the language_forced condition (same-language items reuse the as_deployed result --
same prompt, same cassette key, no reason to call twice).

Quarantine discipline unchanged: this script is never imported by eval/runner.py and never
walks eval/fixtures/{,hi-en/,hi/} -- only eval/fixtures/_held_out_hindi_hinglish/.

Usage:
    EVAL_CASSETTE_MODE=record uv run python eval/score_held_out_corpus_v2.py --mode record
    EVAL_CASSETTE_MODE=replay uv run python eval/score_held_out_corpus_v2.py --mode replay
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.bootstrap import bootstrap_ci  # noqa: E402
from eval.provenance import get_git_sha, now_iso  # noqa: E402
from eval.runner import score_fixture  # noqa: E402

QUARANTINE_DIR = ROOT / "eval" / "fixtures" / "_held_out_hindi_hinglish"
HELD_OUT_CASSETTES_PATH = ROOT / "eval" / "cassettes" / "held_out_cassettes.json"
OUT_PATH = ROOT / "eval" / "results" / "held_out_scoring_v2.json"

DELAY_SECONDS = 2.0  # courtesy pacing on production's own shared org quota


def load_quarantined_fixtures() -> list[dict[str, Any]]:
    fixtures = []
    for p in sorted(QUARANTINE_DIR.glob("*.json")):
        if p.name.startswith("."):
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        if "review_text" in data:
            fixtures.append(data)
    return fixtures


async def _extract(text: str, lang: str) -> dict[str, Any] | None:
    from app.core.llm import extract_with_llm
    from app.core.prompts import build_prompt
    from app.core.sanitize import sanitize, wrap_for_llm

    sanitized, _ = sanitize(text)
    wrapped = wrap_for_llm(sanitized)
    user_prompt = build_prompt(wrapped, lang)
    llm_output, _model, _latency_ms, _tin, _tout, _degraded = await extract_with_llm(
        user_prompt, allow_gemini_fallback=False
    )
    return llm_output.model_dump()


async def score_one(fixture: dict[str, Any]) -> dict[str, Any]:
    from app.core.language import detect_language

    text = fixture["review_text"]
    gt_lang = fixture.get("ground_truth", {}).get("language", "en")
    detected_lang = detect_language(text)

    record: dict[str, Any] = {
        "id": fixture["id"],
        "gt_language": gt_lang,
        "detected_language": detected_lang,
    }

    try:
        as_deployed = await _extract(text, detected_lang)
        record["as_deployed"] = {
            "field_scores": {fr.field: fr.score for fr in score_fixture(fixture, as_deployed)},
            "predicted": as_deployed,
        }
        as_deployed_scores = [fr.score for fr in score_fixture(fixture, as_deployed)]
        record["as_deployed"]["overall_score"] = (
            sum(as_deployed_scores) / len(as_deployed_scores) if as_deployed_scores else 0.0
        )
    except Exception as exc:  # noqa: BLE001
        record["as_deployed"] = {"error": str(exc)}

    if detected_lang == gt_lang:
        record["language_forced"] = record["as_deployed"]
        record["forced_call_made"] = False
    else:
        try:
            forced = await _extract(text, gt_lang)
            forced_scores = [fr.score for fr in score_fixture(fixture, forced)]
            record["language_forced"] = {
                "field_scores": {fr.field: fr.score for fr in score_fixture(fixture, forced)},
                "predicted": forced,
                "overall_score": sum(forced_scores) / len(forced_scores) if forced_scores else 0.0,
            }
        except Exception as exc:  # noqa: BLE001
            record["language_forced"] = {"error": str(exc)}
        record["forced_call_made"] = True

    return record


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    def cond_summary(cond: str) -> dict[str, Any]:
        scored = [r[cond]["overall_score"] for r in records if "error" not in r[cond]]
        errors = sum(1 for r in records if "error" in r[cond])
        ci = bootstrap_ci(scored) if scored else (0.0, 0.0)
        return {
            "n": len(scored),
            "errors": errors,
            "overall_score": mean(scored) if scored else 0.0,
            "ci_95": {"lower": ci[0], "upper": ci[1]},
        }

    from app.core.config import get_settings

    settings = get_settings()
    n_mismatched = sum(1 for r in records if r["detected_language"] != r["gt_language"])
    return {
        "generated_at": now_iso(),
        "git_sha": get_git_sha(),
        "groq_model_small": settings.groq_model_small,
        "groq_model_large": settings.groq_model_large,
        "n_fixtures": len(records),
        "n_language_mismatched": n_mismatched,
        "language_detection_accuracy": 1 - (n_mismatched / len(records)) if records else None,
        "as_deployed": cond_summary("as_deployed"),
        "language_forced": cond_summary("language_forced"),
    }


async def main() -> None:
    import app.core.providers.cassette as cassette_module

    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["record", "replay"], required=True)
    args = parser.parse_args()

    import os

    os.environ["EVAL_CASSETTE_MODE"] = args.mode
    cassette_module.CASSETTES_PATH = HELD_OUT_CASSETTES_PATH

    fixtures = load_quarantined_fixtures()

    existing: dict[str, dict[str, Any]] = {}
    if OUT_PATH.exists() and args.mode == "record":
        prior = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        existing = {r["id"]: r for r in prior.get("records", [])}

    records: list[dict[str, Any]] = []
    for i, fx in enumerate(fixtures, 1):
        if fx["id"] in existing and "error" not in existing[fx["id"]].get("as_deployed", {}):
            records.append(existing[fx["id"]])
            continue
        rec = await score_one(fx)
        records.append(rec)
        print(
            f"  [{i}/{len(fixtures)}] {fx['id']}: gt={rec['gt_language']} "
            f"detected={rec['detected_language']} as_deployed="
            f"{rec['as_deployed'].get('overall_score', 'ERR')} "
            f"forced={rec['language_forced'].get('overall_score', 'ERR')} "
            f"(extra_call={rec['forced_call_made']})"
        )
        if args.mode == "record":
            await asyncio.sleep(DELAY_SECONDS)

    summary = summarize(records)
    summary["records"] = records
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 60)
    print(json.dumps({k: v for k, v in summary.items() if k != "records"}, indent=2))
    print(f"Written: {OUT_PATH}")
    print(f"Cassettes: {HELD_OUT_CASSETTES_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
