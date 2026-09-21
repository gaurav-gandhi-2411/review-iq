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
    CORRECTED in Session 15d: over all fields that delta is almost entirely the `language`
    field itself (100% by echo when forced), NOT an extraction effect -- excluding `language`
    the paired delta is -0.63pp [-2.77, +1.51] (eval/results/routing_cost_n106.json; ADR
    0021 / ADR 0023 "Correction" sections). The published headline therefore excludes it.

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
from eval.free_text_scoring import SCORER_VERSION  # noqa: E402
from eval.heldout_exposure import held_out_exposure, unresolved_fields  # noqa: E402
from eval.provenance import get_git_sha, now_iso  # noqa: E402
from eval.runner import score_fixture  # noqa: E402
from eval.wilson import wilson_ci  # noqa: E402

QUARANTINE_DIR = ROOT / "eval" / "fixtures" / "_held_out_hindi_hinglish"
HELD_OUT_CASSETTES_PATH = ROOT / "eval" / "cassettes" / "held_out_cassettes.json"
OUT_PATH = ROOT / "eval" / "results" / "held_out_scoring_v2.json"

DELAY_SECONDS = 2.0  # courtesy pacing on production's own shared org quota

# Session 15d (D7): fields excluded from the headline for being echo/label-noise rather than
# extraction signal (see summarize()).
HEADLINE_ECHO_FIELDS = ("language",)
# Inter-rater Krippendorff alpha on `language` in this corpus. QUOTED from ADR 0023, NOT
# recomputed here -- carried in the artifact with its source so the renderer never hand-types it.
LANGUAGE_LABEL_ALPHA = 0.380
LANGUAGE_LABEL_ALPHA_SOURCE = (
    "docs/architecture/adr/0023-language-boundary-abstraction-recommendation.md (quoted, "
    "not recomputed)"
)


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


def _strict_overall(fixture: dict[str, Any], extraction: dict[str, Any]) -> float:
    """Overall score under the pre-Session-15c exact-string comparator (strict=True), computed
    on the SAME prediction, so the published number and the disclosure of what the comparator
    change did to it both come from one artifact rather than a hand-typed before/after."""
    scores = [fr.score for fr in score_fixture(fixture, extraction, strict=True)]
    return sum(scores) / len(scores) if scores else 0.0


async def score_one(fixture: dict[str, Any]) -> dict[str, Any]:
    from app.core.language import detect_language

    text = fixture["review_text"]
    gt_lang = fixture.get("ground_truth", {}).get("language", "en")
    detected_lang = detect_language(text)

    record: dict[str, Any] = {
        "id": fixture["id"],
        "gt_language": gt_lang,
        "detected_language": detected_lang,
        # Session 16 (V2): gold pairs that are a panel split stored as a default, not a label.
        "unresolved_fields": list(unresolved_fields(fixture)),
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
        record["as_deployed"]["overall_score_strict"] = _strict_overall(fixture, as_deployed)
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
                "overall_score_strict": _strict_overall(fixture, forced),
            }
        except Exception as exc:  # noqa: BLE001
            record["language_forced"] = {"error": str(exc)}
        record["forced_call_made"] = True

    return record


def _headline_cell(
    records: list[dict[str, Any]],
    fields: list[str],
    *,
    exclude_split: bool,
    exclude_exposed: bool,
) -> dict[str, Any]:
    """Headline over `fields` for one cell of the exposure x split-gold grid.

    Per-record mean over the fields that survive for THAT record (the unit the bootstrap
    resamples: a whole review moves together). A record whose every headline field is unresolved
    contributes nothing, and `n` says how many did.
    """
    kept = [r for r in records if not (exclude_exposed and r.get("exposure"))]
    cell: dict[str, Any] = {
        "n": 0,
        "n_reviews_excluded_as_exposed": len(records) - len(kept),
        "n_split_pairs_excluded": 0,
    }
    for cond in ("as_deployed", "language_forced"):
        per_record: list[float] = []
        split_pairs = 0
        for r in kept:
            skip = set(r.get("unresolved_fields", ())) if exclude_split else set()
            used = [f for f in fields if f not in skip]
            split_pairs += len(fields) - len(used)
            if used:
                per_record.append(mean(r[cond]["field_scores"][f] for f in used))
        lo, hi = bootstrap_ci(per_record) if per_record else (0.0, 0.0)
        cell["n"] = len(per_record)
        cell["n_split_pairs_excluded"] = split_pairs
        cell[cond] = {
            "score": mean(per_record) if per_record else 0.0,
            "ci_95": {"lower": lo, "upper": hi, "n": len(per_record)},
        }
    return cell


def _exposure_sensitivity(records: list[dict[str, Any]], fields: list[str]) -> dict[str, Any]:
    """Do reviews the development process had seen score higher than ones it had not?

    A contamination signature would be exposed > unexposed. Same fields, split pairs excluded on
    both sides so only exposure differs; as deployed. The difference CI is an unpaired bootstrap
    (10,000 resamples, seed 42) -- exposed and unexposed reviews are different reviews.
    """
    import random

    def per_record(rs: list[dict[str, Any]]) -> list[float]:
        out = []
        for r in rs:
            used = [f for f in fields if f not in r.get("unresolved_fields", ())]
            if used:
                out.append(mean(r["as_deployed"]["field_scores"][f] for f in used))
        return out

    exposed = per_record([r for r in records if r.get("exposure")])
    unexposed = per_record([r for r in records if not r.get("exposure")])
    if not exposed or not unexposed:
        return {"n_exposed": len(exposed), "n_unexposed": len(unexposed)}
    rng = random.Random(42)  # noqa: S311 -- resampling, not security
    diffs = sorted(
        mean(rng.choices(exposed, k=len(exposed))) - mean(rng.choices(unexposed, k=len(unexposed)))
        for _ in range(10_000)
    )
    return {
        "n_exposed": len(exposed),
        "n_unexposed": len(unexposed),
        "exposed_score": mean(exposed),
        "unexposed_score": mean(unexposed),
        "difference": mean(exposed) - mean(unexposed),
        "difference_ci_95": {"lower": diffs[249], "upper": diffs[9749]},
    }


def _per_field_split_effect(records: list[dict[str, Any]], fields: list[str]) -> dict[str, Any]:
    """As deployed, all reviews: each field's score with and without the split-gold pairs.

    Symmetric by construction: an empty/default gold scores 0 against a non-empty prediction
    (raising pros/cons/topics when excluded) but can score 1 against an abstaining `product`
    (lowering it when excluded). Reported per field so neither direction is hidden.
    """
    out: dict[str, Any] = {}
    for f in fields:
        all_scores = [r["as_deployed"]["field_scores"][f] for r in records]
        kept = [
            r["as_deployed"]["field_scores"][f]
            for r in records
            if f not in r.get("unresolved_fields", ())
        ]
        out[f] = {
            "n": len(all_scores),
            "n_split_gold": len(all_scores) - len(kept),
            "score_all_pairs": mean(all_scores) if all_scores else 0.0,
            "score_excluding_split": mean(kept) if kept else None,
        }
    return out


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    def cond_summary(cond: str) -> dict[str, Any]:
        scored = [r[cond]["overall_score"] for r in records if "error" not in r[cond]]
        errors = sum(1 for r in records if "error" in r[cond])
        ci = bootstrap_ci(scored) if scored else (0.0, 0.0)
        strict = [r[cond]["overall_score_strict"] for r in records if "error" not in r[cond]]
        return {
            "n": len(scored),
            "errors": errors,
            "overall_score": mean(scored) if scored else 0.0,
            "ci_95": {"lower": ci[0], "upper": ci[1]},
            "overall_score_strict_exact_match": mean(strict) if strict else 0.0,
        }

    from app.core.config import get_settings

    # A field that scores exactly 1.0 on every record in both conditions is constant, not
    # informative -- e.g. `stars` (an explicit star rating stated in the review text) is null
    # in gold AND prediction for all 106 reviews, so it adds a free 1.0 to every overall score.
    # Report the overall without such fields alongside the headline so it can't flatter it.
    conditions = ("as_deployed", "language_forced")
    field_names = list(records[0]["as_deployed"]["field_scores"]) if records else []
    constant_fields = [
        f
        for f in field_names
        if all(r[c]["field_scores"][f] == 1.0 for r in records for c in conditions)
    ]
    informative = [f for f in field_names if f not in constant_fields]
    # Per-record mean over informative fields only: the unit the bootstrap resamples, exactly as
    # for the all-fields CI (a whole review moves together). This is the published headline.
    excl_per_record = {
        c: [mean(r[c]["field_scores"][f] for f in informative) for r in records] for c in conditions
    }
    overall_excl = {c: mean(v) for c, v in excl_per_record.items()}
    overall_excl_ci: dict[str, dict[str, float]] = {}
    for c, v in excl_per_record.items():
        lo, hi = bootstrap_ci(v)
        overall_excl_ci[c] = {"lower": lo, "upper": hi, "n": len(v)}

    # Session 15d (D7): the published headline ALSO drops `language`. Not "constant" in the
    # sense above (as deployed it varies), but it carries no extraction signal: in the forced
    # condition the prompt says `language: always "hi-en"` so the field is 100% by echo, and as
    # deployed it equals detector-vs-corpus-label agreement exactly, against a label whose
    # inter-rater alpha is 0.380 (scripts/measure_routing_cost.py asserts both per fixture;
    # eval/results/routing_cost_n106.json). Kept in the all-fields and stars-only figures below,
    # which stay disclosed beside the headline.
    echo_fields = [f for f in HEADLINE_ECHO_FIELDS if f in informative]
    headline_fields = [f for f in informative if f not in echo_fields]
    # The published headline (Session 16): over reviews the prompt-development process has NOT
    # seen (see eval/heldout_exposure.py) and with panel-split gold pairs excluded. The grid keeps
    # every other cell so no direction is hidden; `all_reviews_all_pairs` is the D7 headline.
    headline_grid = {
        "all_reviews_all_pairs": _headline_cell(
            records, headline_fields, exclude_split=False, exclude_exposed=False
        ),
        "all_reviews_split_excluded": _headline_cell(
            records, headline_fields, exclude_split=True, exclude_exposed=False
        ),
        "unexposed_all_pairs": _headline_cell(
            records, headline_fields, exclude_split=False, exclude_exposed=True
        ),
        "unexposed_split_excluded": _headline_cell(
            records, headline_fields, exclude_split=True, exclude_exposed=True
        ),
    }
    published = headline_grid["unexposed_split_excluded"] if headline_fields else None
    headline = {c: published[c]["score"] for c in conditions} if published is not None else {}
    headline_ci: dict[str, dict[str, float]] = {}
    if published is not None:
        headline_ci = {c: published[c]["ci_95"] for c in conditions}

    settings = get_settings()
    n_mismatched = sum(1 for r in records if r["detected_language"] != r["gt_language"])
    n = len(records)
    agreement = 1 - (n_mismatched / n) if n else None
    agreement_ci = wilson_ci(agreement, n) if agreement is not None else None
    return {
        "generated_at": now_iso(),
        "git_sha": get_git_sha(),
        "scorer_version": SCORER_VERSION,
        "groq_model_small": settings.groq_model_small,
        "groq_model_large": settings.groq_model_large,
        "n_fixtures": len(records),
        "n_language_mismatched": n_mismatched,
        # Renamed from `language_detection_accuracy` (Session 15d): the value is the detector's
        # AGREEMENT with the corpus's language label, not accuracy. That label is itself noisy
        # (inter-rater alpha 0.380), so this partly measures label noise, not detector error.
        "language_label_agreement": agreement,
        "language_label_agreement_wilson_95": (
            {"lower": agreement_ci[0], "upper": agreement_ci[1], "n": n} if agreement_ci else None
        ),
        "language_label_alpha": LANGUAGE_LABEL_ALPHA,
        "language_label_alpha_source": LANGUAGE_LABEL_ALPHA_SOURCE,
        "as_deployed": cond_summary("as_deployed"),
        "language_forced": cond_summary("language_forced"),
        "constant_fields": constant_fields,
        # Stars-only-excluded figure (the pre-D7 headline), kept as a disclosed, not published,
        # number: it still counts the `language` field.
        "overall_score_excluding_constant_fields": overall_excl,
        # Bootstrap CI (10,000 resamples, seed 42, eval/bootstrap.py defaults) over the
        # per-review means excluding constant fields.
        "overall_score_excluding_constant_fields_ci_95": overall_excl_ci,
        # The published headline (D7): excludes constant fields AND the echo/label-noise fields.
        "headline_echo_fields": echo_fields,
        "headline_fields": headline_fields,
        "overall_score_headline": headline,
        "overall_score_headline_ci_95": headline_ci,
        "headline_policy": {
            "cell": "unexposed_split_excluded",
            "exclude_exposed_reviews": True,
            "exclude_split_gold_pairs": True,
        },
        "headline_grid": headline_grid,
        "n_exposed_reviews": sum(1 for r in records if r.get("exposure")),
        "exposure_sensitivity": _exposure_sensitivity(records, headline_fields),
        "exposure_reasons": {
            reason: sum(1 for r in records if reason in r.get("exposure", ()))
            for reason in sorted({x for r in records for x in r.get("exposure", ())})
        },
        "per_field_split_effect": _per_field_split_effect(records, headline_fields),
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

    exposure = held_out_exposure()
    for rec in records:
        rec["exposure"] = exposure.get(rec["id"], [])

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
