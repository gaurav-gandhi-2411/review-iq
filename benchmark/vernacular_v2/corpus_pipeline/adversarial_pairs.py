"""Adversarial authenticity pair generation — held out entirely from any training.

Generates synthetic fake reviews to eventually stress-test the authenticity detector
(`app.core.authenticity.engine`, which calls `settings.groq_model_large` —
`llama-3.3-70b-versatile`, Meta Llama family) against attacks a static-fixture,
perfect-score eval (Wave 1 spec gap register, S1 item 7) can't surface: paraphrase
laundering, template-shift review farms, and outright fabrication.

Model-family constraint (Wave 1 spec §4.H, verbatim: "fake reviews synthesized by
model families DIFFERENT from both the detector and the teacher"):
  - Authenticity detector: `llama-3.3-70b-versatile` → Meta Llama family
    (`app/core/authenticity/engine.py::_call_authenticity_llm` uses
    `settings.groq_model_large`, confirmed by reading the code, not assumed).
  - Teacher (`teacher_labeling.py`): also `llama-3.3-70b-versatile` → same family.
  Both constraints collapse to one exclusion: no Meta Llama family anywhere in
  generation. Generators used here — `openai/gpt-oss-120b` (OpenAI GPT-OSS) and
  `qwen/qwen3.6-27b` (Alibaba Qwen) — are the same two families
  `consensus_validate.py` uses for a different role (judging, not generating); using
  established, already-integrated model IDs here is a smaller-surface-area choice,
  not a licensing/scope requirement to use different models for the two roles.

Three attack types:
  - `fabrication`   — invent a plausible review from scratch (product/rating only,
                       no real review as input) — the classic incentivized-fake pattern.
  - `paraphrase`     — rewrite a REAL corpus review preserving its opinion but changing
                       wording — simulates content laundering (same opinion, "new" post).
  - `template_shift` — generate stylistic variants of a known generic-fake template —
                       simulates review-farm output sharing structural DNA.

HELD OUT: every record here carries `"held_out": true` and
`"label": "synthetic_fake"`. Output lives under
`benchmark/vernacular_v2/adversarial_holdout/`, a directory with its own README
declaring it off-limits for training, kept structurally separate from
`data/processed/` (the real corpus's output tree) so nothing downstream can
accidentally glob/concatenate it into a training set.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

HOLDOUT_DIR = ROOT / "benchmark" / "vernacular_v2" / "adversarial_holdout"

GENERATOR_MODELS: tuple[dict[str, str], ...] = (
    {"id": "openai/gpt-oss-120b", "family": "OpenAI GPT-OSS", "owner": "OpenAI"},
    {"id": "qwen/qwen3.6-27b", "family": "Alibaba Qwen", "owner": "Alibaba Cloud"},
)

AttackType = Literal["fabrication", "paraphrase", "template_shift"]

_SYSTEM_PROMPT = (
    "You are helping build an adversarial test set for a fake-review DETECTOR "
    "(defensive research use only — this output is never posted publicly or used to "
    "deceive real buyers). Follow the task instructions exactly and return ONLY the "
    "review text itself, no commentary, no labels, no quotation marks around it."
)

_FABRICATION_TEMPLATE = """\
Write a single customer review for this product, as if you are a real buyer, but you \
have NOT actually used the product — invent plausible-sounding details.

Product: {product_name}
Star rating to imply: {stars}/5
Language: {language_instruction}

Write ONLY the review text (2-4 sentences), nothing else."""

_PARAPHRASE_TEMPLATE = """\
Rewrite the following customer review so it reads as a DIFFERENT, independently-written \
post — change the wording, sentence structure, and phrasing substantially — while \
preserving the same underlying opinion and the same factual claims about the product.

Original review:
<review>
{original_text}
</review>

Write ONLY the rewritten review text, nothing else."""

_TEMPLATE_SHIFT_TEMPLATE = """\
Here is a generic template commonly used by fake-review farms:
<template>
{template_text}
</template>

Generate a variant #{variant_n} of this template: same generic, low-information, \
overly-positive structure and intent, but with different specific wording so it does \
not look like a copy-paste duplicate.

Write ONLY the variant review text, nothing else."""

# A handful of known generic-fake templates (the kind a review-farm reuses verbatim
# across many listings) — small, documented, hand-authored for this research purpose,
# not scraped from anywhere.
GENERIC_FAKE_TEMPLATES: tuple[str, ...] = (
    "Excellent product, fast delivery, highly recommend! 5 stars, would buy again.",
    "Good quality product at this price range. Packaging was nice. Very satisfied "
    "with my purchase, will definitely order again.",
    "Product is amazing, exactly as described. Seller is very responsive. Five stars all the way!",
)

_LANGUAGE_INSTRUCTIONS = {
    "en": "Plain English.",
    "hi-en": "Hinglish (Latin-script, code-mixed Hindi/English, like real Indian "
    "e-commerce reviews — e.g. 'bahut accha product hai').",
}


async def _generate(client: Any, model_id: str, user_prompt: str, timeout: int = 30) -> str:
    response = await client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.9,  # generation, not extraction — some variation is the point
        max_completion_tokens=300,
        timeout=timeout,
        extra_body={"reasoning_effort": "none"} if "qwen" in model_id else None,
    )
    return (response.choices[0].message.content or "").strip()


def build_fabrication_prompt(product_name: str, stars: int, language: str = "en") -> str:
    return _FABRICATION_TEMPLATE.format(
        product_name=product_name,
        stars=stars,
        language_instruction=_LANGUAGE_INSTRUCTIONS.get(language, _LANGUAGE_INSTRUCTIONS["en"]),
    )


def build_paraphrase_prompt(original_text: str) -> str:
    return _PARAPHRASE_TEMPLATE.format(original_text=original_text)


def build_template_shift_prompt(template_text: str, variant_n: int) -> str:
    return _TEMPLATE_SHIFT_TEMPLATE.format(template_text=template_text, variant_n=variant_n)


def make_record(
    *,
    text: str,
    attack_type: AttackType,
    generator_model: str,
    generator_family: str,
    source_note: str,
) -> dict:
    """Build one adversarial holdout record — every field a downstream consumer needs
    to filter this OUT of anything training/eval-related without re-deriving intent."""
    return {
        "text": text,
        "label": "synthetic_fake",
        "held_out": True,
        "attack_type": attack_type,
        "generator_model": generator_model,
        "generator_family": generator_family,
        "source_note": source_note,
        "excluded_families": ["Meta Llama"],  # detector + teacher family, for audit
    }


async def generate_sample(
    client: Any,
    real_reviews: list[dict],
    *,
    n_per_type_per_model: int = 2,
    delay_seconds: float = 2.0,
    text_field: str = "text",
    on_progress: object = None,
) -> list[dict]:
    """Generate a small, documented adversarial sample across all 3 attack types x
    both generator models. Total records = 3 attack types * len(GENERATOR_MODELS) *
    n_per_type_per_model (paraphrase is additionally capped by len(real_reviews))."""
    tasks: list[tuple[AttackType, dict[str, str], str]] = []  # (attack_type, model, prompt)

    for model in GENERATOR_MODELS:
        for i in range(n_per_type_per_model):
            product = real_reviews[i % len(real_reviews)].get("product_name") or "a product"
            stars = 5 if i % 2 == 0 else 1
            tasks.append(
                (
                    "fabrication",
                    model,
                    build_fabrication_prompt(product, stars, language="en"),
                )
            )
        for i in range(min(n_per_type_per_model, len(real_reviews))):
            original = real_reviews[i][text_field]
            tasks.append(("paraphrase", model, build_paraphrase_prompt(original)))
        for i in range(n_per_type_per_model):
            template = GENERIC_FAKE_TEMPLATES[i % len(GENERIC_FAKE_TEMPLATES)]
            tasks.append(("template_shift", model, build_template_shift_prompt(template, i + 1)))

    results: list[dict] = []
    for idx, (attack_type, model, prompt) in enumerate(tasks):
        t0 = time.monotonic()
        try:
            text = await _generate(client, model["id"], prompt)
            error = None
        except Exception as exc:  # noqa: BLE001
            text = ""
            error = str(exc)[:180]
        latency_ms = int((time.monotonic() - t0) * 1000)
        rec = make_record(
            text=text,
            attack_type=attack_type,
            generator_model=model["id"],
            generator_family=model["family"],
            source_note=f"generated {time.strftime('%Y-%m-%d')}",
        )
        rec["error"] = error
        rec["latency_ms"] = latency_ms
        results.append(rec)
        if callable(on_progress):
            on_progress(idx + 1, len(tasks), rec)
        if idx < len(tasks) - 1:
            await asyncio.sleep(delay_seconds)
    return results


def write_holdout(records: list[dict], out_path: Path) -> None:
    """Write records + ensure the holdout directory's DO-NOT-TRAIN README exists."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    readme = out_path.parent / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Adversarial authenticity holdout — DO NOT TRAIN ON THIS DIRECTORY\n\n"
            "Every file here is synthetic fake-review content generated by "
            "`benchmark/vernacular_v2/corpus_pipeline/adversarial_pairs.py` for "
            "authenticity-detector STRESS-TESTING only. Every record carries "
            '`"held_out": true` and `"label": "synthetic_fake"`.\n\n'
            "Held out entirely from:\n"
            "- the main corpus pipeline's train/eval outputs "
            "(`data/processed/**`, never merged with this directory)\n"
            "- any future fine-tuning run\n\n"
            "See `docs/architecture/adr/0004-corpus-mining-pipeline-and-target-volume.md` "
            "for how this is meant to be used (authenticity-detector adversarial eval, "
            "not training data).\n",
            encoding="utf-8",
        )
    with out_path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-reviews-input", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=HOLDOUT_DIR / "adversarial_sample.jsonl")
    parser.add_argument("--n-per-type-per-model", type=int, default=2)
    args = parser.parse_args()

    from benchmark.vernacular_v2.benchmark_groq_key import load_benchmark_groq_key
    from groq import AsyncGroq

    benchmark_key = load_benchmark_groq_key()
    print(f"Using dedicated benchmark Groq key ({benchmark_key[:8]}...{benchmark_key[-4:]}).")
    print(f"Generator families: {[m['family'] for m in GENERATOR_MODELS]} (Meta Llama excluded)")

    real_reviews = _load_jsonl(args.real_reviews_input)
    client = AsyncGroq(api_key=benchmark_key)

    def _progress(i: int, total: int, rec: dict) -> None:
        status = "ERROR: " + rec["error"] if rec["error"] else f"{len(rec['text'])} chars"
        print(f"  [{i}/{total}] {rec['attack_type']}/{rec['generator_model']}: {status}")

    results = asyncio.run(
        generate_sample(
            client,
            real_reviews,
            n_per_type_per_model=args.n_per_type_per_model,
            on_progress=_progress,
        )
    )
    write_holdout(results, args.output)
    n_ok = sum(1 for r in results if not r["error"])
    print(f"\nDone. {n_ok}/{len(results)} generated successfully. Written: {args.output}")


if __name__ == "__main__":
    main()
