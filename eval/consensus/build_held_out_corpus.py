"""Build the QUARANTINED held-out Hindi/Hinglish corpus -- P3 (Session 9), see ADR 0016.

Distinct from run_consensus.py, which grows the MAIN, prompt-visible eval/fixtures/{,hi-en/,hi/}
set (used for eval.yml regression gating and prompt development). This script writes ONLY into
eval/fixtures/_held_out_hindi_hinglish/ -- a directory `eval/runner.py` never walks and
`scripts/check_no_heldout_leakage.py` mechanically forbids referencing from any prompt file (see
that directory's own README for the quarantine mechanism). Its entire purpose is giving
production models a FIRST measurement no prompt was ever tuned against -- growing the main set
with the same text would defeat that purpose.

Source: eval/data/flipkart_candidates.jsonl, language in {"hi", "hi-en"} -- 108 candidates total
(2 hi + 106 hi-en, ADR 0014). Hindi-first ordering per P3d: the 2 Hindi candidates are always
labeled before any Hinglish one, in every batch, because Hindi sentiment (n=6 in the main,
non-quarantined set) is this product's thinnest-evidence, highest-risk measurement.

Batched, resumable, quota-bounded (P3c): pass --max-items to cap how many NEW candidates this
invocation labels; already-labeled ids (existing files in the quarantine dir) are always
skipped. Every item is written regardless of consensus agreement level (unanimous/majority/
split) -- discarding low-consensus items would bias this corpus toward the "easy" cases, which
is exactly wrong for a corpus whose purpose is an honest, uncontaminated difficulty measurement.
Split-consensus items get `"ground_truth": null` for the affected field(s) and are counted
separately in the run summary; P4's scoring must treat them as "no defensible silver label for
this field" rather than silently dropping them from the corpus's own accounting.

Labels every item with LLM-consensus silver ONLY -- P3f, never human ground truth.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.consensus import calibration, panel  # noqa: E402
from eval.consensus.candidates import FLIPKART_CANDIDATES_PATH, load_jsonl  # noqa: E402
from eval.consensus.voting import consensus_for_item  # noqa: E402

QUARANTINE_DIR = ROOT / "eval" / "fixtures" / "_held_out_hindi_hinglish"
RESULTS_DIR = ROOT / "eval" / "consensus" / "results"
CALIBRATION_REPORT_PATH = RESULTS_DIR / "calibration_report.json"
BATCH_LOG_PATH = RESULTS_DIR / "held_out_batch_log.jsonl"

DELAY_SECONDS = 2.0  # same courtesy pacing as run_consensus.py

GROWTH_GATE_FIELDS: tuple[str, ...] = ("sentiment", "urgency", "buy_again", "language")


def _text_key(text: str) -> str:
    import re

    return re.sub(r"\s+", " ", text.strip().lower())[:100]


def get_active_panel() -> list[dict[str, str]]:
    panel.assert_no_self_judging()
    report = json.loads(CALIBRATION_REPORT_PATH.read_text(encoding="utf-8"))
    active_ids = set(report["active_panel"])
    active = [m for m in panel.JUDGE_MODELS if m["id"] in active_ids]
    print(f"Active panel (from existing calibration_report.json): {[m['id'] for m in active]}")
    if report["dropped"]:
        for d in report["dropped"]:
            print(f"  DROPPED (calibration): {d['model_id']} -- {d['reason']}")
    return active


def load_already_labeled_keys() -> set[str]:
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    keys: set[str] = set()
    for p in QUARANTINE_DIR.glob("*.json"):
        data = json.loads(p.read_text(encoding="utf-8"))
        if "review_text" in data:
            keys.add(_text_key(data["review_text"]))
    return keys


def build_candidate_queue() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (hindi_candidates, hinglish_candidates), both excluding already-labeled text."""
    all_candidates = load_jsonl(FLIPKART_CANDIDATES_PATH)
    used = load_already_labeled_keys()

    def pool(language: str) -> list[dict[str, Any]]:
        p = [
            c
            for c in all_candidates
            if c.get("language") == language and _text_key(c["text"]) not in used
        ]
        p.sort(key=lambda c: _text_key(c["text"]))
        return p

    return pool("hi"), pool("hi-en")


async def _label_one_judge(client: Any, model_cfg: dict[str, str], text: str) -> dict[str, Any] | None:
    try:
        raw = await panel.call_judge(client, model_cfg["id"], text)
    except Exception as exc:  # noqa: BLE001 -- record the error, don't crash the whole batch
        print(f"    ERROR [{model_cfg['id']}]: {str(exc)[:200]}")
        return None
    parsed = panel.parse_judge_response(raw)
    return parsed.model_dump() if parsed else None


def _next_index(language: str) -> int:
    existing = list(QUARANTINE_DIR.glob(f"{'hi' if language == 'hi' else 'hien'}-*.json"))
    if not existing:
        return 1
    nums = [int(p.stem.split("-")[-1]) for p in existing]
    return max(nums) + 1


def build_fixture(
    fixture_id: str, review_text: str, consensus: dict[str, dict[str, Any]], source: str
) -> dict[str, Any]:
    def silver_or_none(field: str) -> Any:
        c = consensus.get(field, {})
        return c.get("silver") if c.get("agreement") in ("unanimous", "majority") else None

    ground_truth = {
        "product": silver_or_none("product") or "unknown",
        "stars": silver_or_none("stars"),
        "stars_inferred": silver_or_none("stars_inferred"),
        "pros": silver_or_none("pros") or [],
        "cons": silver_or_none("cons") or [],
        "buy_again": silver_or_none("buy_again"),
        "sentiment": silver_or_none("sentiment"),
        "topics": silver_or_none("topics") or [],
        "competitor_mentions": silver_or_none("competitor_mentions") or [],
        "urgency": silver_or_none("urgency"),
        "feature_requests": silver_or_none("feature_requests") or [],
        "language": silver_or_none("language"),
    }
    agreement_levels = {field: c["agreement"] for field, c in consensus.items()}
    return {
        "id": fixture_id,
        "review_text": review_text,
        "ground_truth": ground_truth,
        "scoring_notes": {
            "exact_match_fields": ["product", "stars", "buy_again", "sentiment", "language"],
            "set_overlap_fields": ["topics", "competitor_mentions"],
            "fuzzy_fields": ["pros", "cons"],
            "tolerance_fields": {"stars_inferred": 1},
        },
        "labeling_meta": {
            "labeled_by": "multi-llm-consensus",
            "method": "eval/consensus/build_held_out_corpus.py (Session 9 P3)",
            "source": source,
            "quarantined": True,
            "agreement_per_field": agreement_levels,
        },
    }


async def run_batch(max_items: int) -> dict[str, Any]:
    active_panel = get_active_panel()
    if len(active_panel) < 2:
        print(f"FATAL: only {len(active_panel)} judge(s) passed calibration -- need >= 2.")
        sys.exit(1)

    hindi, hinglish = build_candidate_queue()
    print(f"Remaining unlabeled: hi={len(hindi)}  hi-en={len(hinglish)}")

    queue = (hindi + hinglish)[:max_items]  # Hindi-first per P3d
    print(f"This batch: {len(queue)} items ({sum(1 for c in queue if c['language'] == 'hi')} hi, "
          f"{sum(1 for c in queue if c['language'] == 'hi-en')} hi-en)")

    client = panel.make_groq_client()
    written = {"hi": 0, "hi-en": 0}
    split_on_gate_fields = 0
    n_groq_calls = 0

    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    with BATCH_LOG_PATH.open("a", encoding="utf-8") as log_fh:
        for i, cand in enumerate(queue, 1):
            t0 = time.monotonic()
            outputs = await asyncio.gather(
                *[_label_one_judge(client, m, cand["text"]) for m in active_panel]
            )
            n_groq_calls += sum(1 for m in active_panel if m["provider"] == "groq")
            judge_outputs = {m["id"]: out for m, out in zip(active_panel, outputs, strict=True)}
            consensus = consensus_for_item(judge_outputs)
            latency_ms = int((time.monotonic() - t0) * 1000)

            lang = cand["language"]
            fixture_id = f"{'hi' if lang == 'hi' else 'hien'}-{_next_index(lang):04d}"
            out_path = QUARANTINE_DIR / f"{fixture_id}.json"
            fixture = build_fixture(fixture_id, cand["text"], consensus, cand.get("source", ""))
            out_path.write_text(json.dumps(fixture, indent=2, ensure_ascii=False), encoding="utf-8")
            written[lang] += 1

            if not all(
                consensus.get(f, {}).get("agreement") in ("unanimous", "majority")
                for f in GROWTH_GATE_FIELDS
            ):
                split_on_gate_fields += 1

            log_fh.write(
                json.dumps(
                    {
                        "fixture_id": fixture_id,
                        "language": lang,
                        "judge_outputs": judge_outputs,
                        "consensus": consensus,
                        "latency_ms": latency_ms,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            log_fh.flush()

            summary = "  ".join(
                f"{f}={consensus[f]['silver']}({consensus[f]['agreement']})"
                for f in ("sentiment", "urgency", "buy_again", "language")
            )
            print(f"  [{i}/{len(queue)}] {fixture_id} ({lang}): {summary}  {latency_ms}ms")
            await asyncio.sleep(DELAY_SECONDS)

    remaining_hindi, remaining_hinglish = build_candidate_queue()
    return {
        "batch_size": len(queue),
        "written": written,
        "split_on_growth_gate_fields": split_on_gate_fields,
        "estimated_groq_requests": n_groq_calls,
        "remaining_after_batch": {"hi": len(remaining_hindi), "hi-en": len(remaining_hinglish)},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-items", type=int, required=True, help="P3c batch ceiling, in items")
    args = parser.parse_args()
    result = asyncio.run(run_batch(args.max_items))
    print("\n" + "=" * 60)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
