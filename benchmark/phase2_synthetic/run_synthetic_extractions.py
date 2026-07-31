"""Run real production extraction (v2.3, tiered Groq routing) against every synthetic
testbed review — the same _predict_full wrapper used in benchmark/gap_fixes/, on the
DEDICATED benchmark Groq key (never prod's). This makes the detectors consume the same
topics/sentiment/urgency fields a real tenant's reviews would have in prod, not a
parallel keyword-tagging system, so a detector validated here is genuinely the same
code path that would run against live data.

SYNTHETIC TESTBED — see generate_synthetic_testbed.py's banner. Review TEXT is real;
timestamps/reviewer-IDs/product assignment are fabricated. Extraction output on this
data validates the extraction CODE runs correctly on realistic language — it says
nothing about real-world defect/trend/campaign accuracy, only about detector-vs-planted-
ground-truth performance.

Usage:
    uv run python benchmark/phase2_synthetic/run_synthetic_extractions.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from benchmark.vernacular_v2.benchmark_groq_key import load_benchmark_groq_key  # noqa: E402

_benchmark_key = load_benchmark_groq_key()
os.environ["GROQ_API_KEY"] = _benchmark_key
print(
    f"Using dedicated benchmark Groq key ({_benchmark_key[:8]}...{_benchmark_key[-4:]}) — isolated from prod."
)

import asyncio  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402

REVIEWS_PATH = ROOT / "benchmark" / "phase2_synthetic" / "reviews.jsonl"
EXTRACTIONS_PATH = ROOT / "benchmark" / "phase2_synthetic" / "extractions.jsonl"

# Bumped from 5s after the first run showed sustained TPM-style throttling under a ~800+ call
# bulk workload (many silent-until-checked failures, not just the isolated late-stage error
# initially spotted) -- matches the project's own documented precedent for sustained Groq
# free-tier bulk work (PROMPTS.md's v2.3 cassette re-record used a 35-45s inter-call delay for
# the same reason). Isolated single calls succeeded fine even during the failure window,
# confirming this is sustained-load TPM pressure, not a hard daily quota exhaustion.
DELAY_SECONDS = 35


async def _predict_full(text: str) -> dict[str, object]:
    """Full-field extraction — same real pipeline as app.api.v2.extract._run_extraction_v2
    (sanitize -> wrap -> language-routed prompt -> extract_with_llm -> language override)."""
    from app.core.language import detect_language
    from app.core.llm import extract_with_llm
    from app.core.prompts import build_prompt
    from app.core.sanitize import sanitize, wrap_for_llm

    detected_lang = detect_language(text)
    clean_text, _is_suspicious, _redaction_map = sanitize(text)
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


async def main() -> None:
    import app.core.providers.cassette as cassette_module
    import benchmark.systems.review_iq  # noqa: F401

    cassette_module.CASSETTES_PATH = ROOT / "benchmark" / "phase2_synthetic" / "cassettes.json"

    reviews = [
        json.loads(line)
        for line in REVIEWS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    existing: dict[str, dict] = {}
    if EXTRACTIONS_PATH.exists():
        for line in EXTRACTIONS_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                if not rec.get("extraction", {}).get("_error"):
                    existing[rec["review_id"]] = rec

    to_run = len(reviews) - len(existing)
    eta_min = round(to_run * DELAY_SECONDS / 60, 1)
    print(
        f"Reviews: {len(reviews)}  Already done: {len(existing)}  To run: {to_run}  ETA: ~{eta_min} min"
    )

    results = list(existing.values())
    with EXTRACTIONS_PATH.open("w", encoding="utf-8") as fh:
        for rec in results:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()

        for i, review in enumerate(reviews, 1):
            if review["review_id"] in existing:
                continue
            t0 = time.monotonic()
            extraction = await _predict_full(review["text"])
            latency_ms = int((time.monotonic() - t0) * 1000)
            rec = {
                "review_id": review["review_id"],
                "product_id": review["product_id"],
                "reviewer_id": review["reviewer_id"],
                "timestamp": review["timestamp"],
                "rating": review["rating"],
                "product_category": review.get("product_category"),
                "extraction": extraction,
                "latency_ms": latency_ms,
            }
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            results.append(rec)
            err = extraction.get("_error", "")
            status = f"sent={extraction.get('sentiment')} topics={extraction.get('topics')}"
            if err:
                status = f"ERROR: {err[:80]}"
            print(
                f"  [{i}/{len(reviews)}] {review['review_id']} ({review['product_id']}): {status}  {latency_ms}ms"
            )
            await asyncio.sleep(DELAY_SECONDS)

    print(f"\nDone. Total: {len(results)}  Written: {EXTRACTIONS_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
