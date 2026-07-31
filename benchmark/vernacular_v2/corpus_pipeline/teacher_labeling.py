"""Teacher-labeling pipeline — the 70B teacher produces extraction targets.

Teacher = review-iq's own production "large" tier model
(`app.core.config.Settings.groq_model_large`, default `llama-3.3-70b-versatile`) —
this is the model the spec means by "70B teacher," not a separately-chosen model.
This model is FORCED explicitly on every item (bypasses the tiered router entirely —
this is a labeling run, not a customer-traffic simulation, and every item needs the
same teacher for labels to be comparable across the sample).

Reuses the real production extraction path — `app.core.sanitize.sanitize()` +
`app.core.prompts.build_prompt()` + `app.core.llm._SYSTEM_PROMPT` (the actual system
prompt, imported directly rather than hand-copied so this can never silently drift
from what prod sends) + `app.core.providers.groq.GroqProvider` (cassette-aware) +
`app.core.schemas.ReviewExtractionLLMOutput` — not a reimplementation. Precedent for
importing a leading-underscore internal from another module for exact-fidelity reuse
already exists in this directory: `multi_llm_labeler.py` imports
`benchmark.data.llm_labeler._parse_labels`.

IMPORTANT — Groq key isolation: every live call in this module goes through whatever
`GroqProvider` is constructed with; callers (this module's own `main()`, and
`run_corpus_pipeline.py`) MUST pass the dedicated benchmark key
(`benchmark_groq_key.load_benchmark_groq_key()`), never `GROQ_API_KEY` (prod) — same
isolation this whole directory already enforces, see `benchmark_groq_key.py`'s
docstring for the 2026-07-07 incident this exists to prevent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app.core.llm import _SYSTEM_PROMPT, _parse_response  # noqa: E402
from app.core.prompts import PROMPT_VERSION, build_prompt  # noqa: E402
from app.core.providers.groq import GroqProvider  # noqa: E402
from app.core.sanitize import sanitize, wrap_for_llm  # noqa: E402
from app.core.schemas import ReviewExtractionLLMOutput  # noqa: E402

TEACHER_MODEL_ID = "llama-3.3-70b-versatile"  # must match Settings.groq_model_large's
# default — asserted at runtime in main(), not just assumed, so a config drift
# (someone changes the prod default) doesn't silently make "the teacher" mean
# something different from what this constant says it means.

DELAY_SECONDS = 3.0  # courtesy pacing on the dedicated benchmark key's own budget


async def label_review(
    provider: GroqProvider,
    review_text: str,
    *,
    language: str = "en",
) -> tuple[ReviewExtractionLLMOutput | None, str | None]:
    """Run one review through the real extraction path with the teacher model.

    Returns (parsed_extraction_or_None, error_or_None). Never raises — a labeling
    run over N items must not die on one bad item.
    """
    sanitized, _is_suspicious = sanitize(review_text)
    wrapped = wrap_for_llm(sanitized)
    user_prompt = build_prompt(wrapped, language)
    try:
        raw, _tokens_in, _tokens_out = await provider.complete(
            user_prompt, system_prompt=_SYSTEM_PROMPT
        )
    except Exception as exc:  # noqa: BLE001 — record and continue, don't crash the run
        return None, f"provider_error: {str(exc)[:180]}"
    try:
        parsed = _parse_response(raw)
    except (ValidationError, json.JSONDecodeError) as exc:
        return None, f"parse_error: {str(exc)[:180]}"
    return parsed, None


async def label_sample(
    provider: GroqProvider,
    records: list[dict],
    *,
    text_field: str = "text",
    language_field: str = "detected_language",
    delay_seconds: float = DELAY_SECONDS,
    on_progress: object = None,
) -> list[dict]:
    """Label every record in `records`; returns one result dict per input record.

    Each result: {"id", "language_hint", "teacher_model", "prompt_version",
    "extraction": <dict or None>, "error": <str or None>, "latency_ms"}.
    """
    results: list[dict] = []
    for i, rec in enumerate(records):
        t0 = time.monotonic()
        extraction, error = await label_review(
            provider, rec.get(text_field, ""), language=rec.get(language_field, "en")
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        result = {
            "id": rec.get("id", i),
            "language_hint": rec.get(language_field, "en"),
            "teacher_model": provider.model,
            "prompt_version": PROMPT_VERSION,
            "extraction": extraction.model_dump() if extraction else None,
            "error": error,
            "latency_ms": latency_ms,
        }
        results.append(result)
        if callable(on_progress):
            on_progress(i + 1, len(records), result)
        if i < len(records) - 1:
            await asyncio.sleep(delay_seconds)
    return results


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--n", type=int, default=20, help="Sample size — small and bounded, not the full corpus."
    )
    args = parser.parse_args()

    # Dedicated benchmark key, injected before the provider is constructed — same
    # isolation mechanism as run_predictions.py / multi_llm_labeler.py.
    from benchmark.vernacular_v2.benchmark_groq_key import load_benchmark_groq_key

    benchmark_key = load_benchmark_groq_key()
    print(f"Using dedicated benchmark Groq key ({benchmark_key[:8]}...{benchmark_key[-4:]}).")

    from app.core.config import get_settings

    settings = get_settings()
    if settings.groq_model_large != TEACHER_MODEL_ID:
        print(
            f"WARNING: Settings.groq_model_large={settings.groq_model_large!r} no longer "
            f"matches this module's TEACHER_MODEL_ID={TEACHER_MODEL_ID!r} — 'the teacher' "
            "has drifted from review-iq's actual production large-tier model. Update "
            "TEACHER_MODEL_ID to match before proceeding.",
            file=sys.stderr,
        )
        sys.exit(1)

    all_records = _load_jsonl(args.input)
    sample = all_records[: args.n]
    print(f"Corpus: {len(all_records)} records. Labeling sample: {len(sample)} (n={args.n}).")

    provider = GroqProvider(model=TEACHER_MODEL_ID, api_key=benchmark_key, timeout=30)

    def _progress(i: int, total: int, result: dict) -> None:
        status = "ERROR: " + result["error"] if result["error"] else "OK"
        print(f"  [{i}/{total}] {result['id']}: {status}  {result['latency_ms']}ms")

    results = asyncio.run(
        label_sample(provider, sample, delay_seconds=DELAY_SECONDS, on_progress=_progress)
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    n_ok = sum(1 for r in results if r["error"] is None)
    print(f"\nDone. {n_ok}/{len(results)} labeled successfully. Written: {args.output}")


if __name__ == "__main__":
    main()
