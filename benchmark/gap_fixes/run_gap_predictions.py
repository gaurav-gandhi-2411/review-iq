"""Run current prod extraction against real GAP1 (sarcastic-Hinglish) / GAP2
(short-review-drops-fields) candidates sourced from the licensed Flipkart corpus.
Uses benchmark.systems.review_iq.predict — the same production pipeline wrapper as
every other benchmark run — on the DEDICATED benchmark Groq key (never prod's).

Output path is parameterized so this same script produces both the pre-fix baseline
(predictions_baseline.jsonl) and the post-fix re-run (predictions_postfix.jsonl) for
an honest before/after comparison — call with an explicit --out to pick which.

Usage:
    uv run python benchmark/gap_fixes/run_gap_predictions.py --out predictions_baseline.jsonl
    uv run python benchmark/gap_fixes/run_gap_predictions.py --out predictions_postfix.jsonl
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Must happen before ANY import that could transitively trigger app.core.config.get_settings()
# — see benchmark/vernacular_v2/run_predictions.py for the full incident writeup this
# pattern exists to prevent (an unpaced/paced run against prod's key degraded prod twice
# on 2026-07-07). This env var is scoped to this one local script process only.
from benchmark.vernacular_v2.benchmark_groq_key import load_benchmark_groq_key  # noqa: E402

_benchmark_key = load_benchmark_groq_key()
os.environ["GROQ_API_KEY"] = _benchmark_key
print(f"Using dedicated benchmark Groq key ({_benchmark_key[:8]}...{_benchmark_key[-4:]}) — isolated from prod.")

import asyncio  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402

DELAY_SECONDS = 5


async def _predict_full(text: str) -> dict[str, object]:
    """Full-field extraction — mirrors app.api.v2.extract._run_extraction_v2's real
    pipeline exactly (sanitize -> wrap -> language-routed prompt -> extract_with_llm
    -> language-field override), unlike benchmark.systems.review_iq.predict() which
    only returns the narrow 3-field {SENT, URG, LANG} benchmark mapping. GAP1/GAP2
    need the full extraction (pros, cons, topics, feature_requests, etc.) to inspect
    field-dropping and sentiment correctness on real cases.
    """
    from app.core.language import detect_language
    from app.core.llm import extract_with_llm
    from app.core.prompts import build_prompt
    from app.core.sanitize import sanitize, wrap_for_llm

    detected_lang = detect_language(text)
    clean_text, _is_suspicious = sanitize(text)
    wrapped = wrap_for_llm(clean_text)
    user_prompt = build_prompt(wrapped, detected_lang)
    try:
        llm_output, model_name, latency_ms, tin, tout, degraded = await extract_with_llm(
            user_prompt, allow_gemini_fallback=False
        )
        llm_output.language = detected_lang
        extraction = llm_output.model_dump()
        extraction["_model"] = model_name
        extraction["_degraded"] = degraded
        return extraction
    except Exception as exc:  # noqa: BLE001
        return {"_error": str(exc), "language": detected_lang}


async def main(out_name: str, candidates_name: str) -> None:
    import app.core.providers.cassette as cassette_module

    # Import review_iq's predict once so the cassette-path override below isn't
    # clobbered by its own module-level reassignment (the exact bug found + fixed
    # in vernacular_v2's runners on 2026-07-09 after it leaked recordings into the
    # wrong cassette file) — even though this script uses _predict_full() above,
    # not review_iq.predict(), the import still triggers that module-level reassign.
    import benchmark.systems.review_iq  # noqa: F401

    cassette_module.CASSETTES_PATH = ROOT / "benchmark" / "gap_fixes" / f"cassettes_{out_name.replace('.jsonl', '')}.json"

    out_path = ROOT / "benchmark" / "gap_fixes" / out_name
    candidates_path = ROOT / "benchmark" / "gap_fixes" / candidates_name

    candidates = [
        json.loads(line) for line in candidates_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]

    existing: dict[str, dict] = {}
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                if not rec.get("pred", {}).get("_error"):
                    existing[rec["id"]] = rec

    to_run = len(candidates) - len(existing)
    print(f"Candidates: {len(candidates)}  Already done: {len(existing)}  To run: {to_run}")
    print(f"Output: {out_path.relative_to(ROOT)}  Pacing: {DELAY_SECONDS}s/call")

    results = list(existing.values())
    with out_path.open("w", encoding="utf-8") as fh:
        for rec in results:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()

        for i, cand in enumerate(candidates, 1):
            if cand["id"] in existing:
                continue
            t0 = time.monotonic()
            pred = await _predict_full(cand["text"])
            latency_ms = int((time.monotonic() - t0) * 1000)
            rec = {
                "id": cand["id"],
                "gap": cand["gap"],
                "rate": cand.get("rate"),
                "text": cand["text"],
                "pred": pred,
                "latency_ms": latency_ms,
            }
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            results.append(rec)
            err = pred.get("_error", "")
            status = (
                f"SENT={pred.get('SENT') or pred.get('sentiment')} "
                f"URG={pred.get('URG') or pred.get('urgency')}"
            )
            if err:
                status = f"ERROR: {err[:80]}"
            print(f"  [{i}/{len(candidates)}] {cand['id']} ({cand['gap']}): {status}  {latency_ms}ms")
            await asyncio.sleep(DELAY_SECONDS)

    print(f"\nDone. Total: {len(results)}  Written: {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="Output filename, e.g. predictions_baseline.jsonl")
    parser.add_argument("--candidates", default="candidates.jsonl", help="Candidates filename")
    args = parser.parse_args()
    asyncio.run(main(args.out, args.candidates))
