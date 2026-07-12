"""SILVER benchmark labeler — multi-LLM consensus, NOT human ground truth.

╔══════════════════════════════════════════════════════════════════════════╗
║ SILVER BENCHMARK — labels are multi-LLM consensus, NOT human-verified    ║
║ ground truth. Scores computed against this file measure AGREEMENT WITH   ║
║ CONSENSUS, NOT accuracy. DO NOT quote as accuracy externally.            ║
╚══════════════════════════════════════════════════════════════════════════╝

Three genuinely different model families label every candidate independently
(no model sees another's answer), all via Groq's DEDICATED benchmark key:
  1. llama-3.3-70b-versatile   (Meta Llama family)
  2. openai/gpt-oss-120b       (OpenAI open-weight family)
  3. qwen/qwen3-32b            (Alibaba Qwen family)

Why these three: genuinely different training lineages, not 3 sizes of one model
(reduces shared-bias risk a same-family panel would have) — confirmed each is a
distinct owner/family via Groq's /v1/models endpoint, not assumed from naming.

Gemini was the original 3rd choice (a genuinely different PROVIDER, not just
family) but its free tier returned `limit: 0` for gemini-2.0-flash on this
project's key ("check your plan and billing details" — a Google Cloud billing/
provisioning gap, not a code issue). Not chased further — 3 solid cross-family
labelers on one already-isolated key satisfies "3+ different families" without
needing to debug Google Cloud billing for a labeling-diversity nice-to-have.

Consensus rule, PER FIELD (SENT/URG/LANG each scored independently — a review can
be unanimous on LANG but split on SENT):
  - unanimous: all 3 models agree -> that label is silver, agreement="unanimous"
  - majority:  2 of 3 agree        -> that label is silver, agreement="majority"
  - split:     all 3 disagree      -> NO silver label (null), agreement="split"
No 4th model is ever used to "break ties" — split cases are recorded honestly,
not resolved. The disagreement itself is the data (flags hard/ambiguous cases).

Output: benchmark/vernacular_v2/silver_labels.jsonl — first line is a metadata
record (marker: "SILVER_BENCHMARK") carrying the same warning as this docstring,
so the marking travels with the data file itself, not just this script's comments.

Usage:
    uv run python benchmark/vernacular_v2/multi_llm_labeler.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Dedicated benchmark key, injected before any settings-dependent import — same
# isolation mechanism as run_predictions.py. Gemini key is read separately below
# (existing GEMINI_API_KEY; safe per the docstring above, not routed through this).
from benchmark.vernacular_v2.benchmark_groq_key import load_benchmark_groq_key  # noqa: E402

_benchmark_groq_key = load_benchmark_groq_key()
os.environ["GROQ_API_KEY"] = _benchmark_groq_key
print(f"Using dedicated benchmark Groq key ({_benchmark_groq_key[:8]}...{_benchmark_groq_key[-4:]}) — isolated from prod.")

from benchmark.data.llm_labeler import (  # noqa: E402
    LABELING_SYSTEM_PROMPT,
    LABELING_USER_TEMPLATE,
    _parse_labels,
)
from groq import AsyncGroq  # noqa: E402

CANDIDATES_PATH = ROOT / "benchmark" / "vernacular_v2" / "candidates.jsonl"
SILVER_PATH = ROOT / "benchmark" / "vernacular_v2" / "silver_labels.jsonl"

FIELDS = ("SENT", "URG", "LANG")

MODELS = [
    {"id": "llama-3.3-70b-versatile", "provider": "groq", "family": "Meta Llama"},
    {"id": "openai/gpt-oss-120b", "provider": "groq", "family": "OpenAI GPT-OSS"},
    {"id": "qwen/qwen3-32b", "provider": "groq", "family": "Alibaba Qwen"},
]

DELAY_SECONDS = 3  # courtesy pacing on the dedicated key's own budget


async def _label_groq(client: AsyncGroq, model: str, text: str) -> dict[str, str] | None:
    user_prompt = LABELING_USER_TEMPLATE.format(text=text)
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": LABELING_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
        timeout=30,
    )
    raw = response.choices[0].message.content or ""
    return _parse_labels(raw)


async def _label_one_model(model_cfg: dict, text: str, groq_client: AsyncGroq) -> tuple[str, dict[str, str] | None, str | None]:
    try:
        labels = await _label_groq(groq_client, model_cfg["id"], text)
        if labels is None:
            return model_cfg["id"], None, "unparseable_response"
        return model_cfg["id"], labels, None
    except Exception as exc:  # noqa: BLE001
        return model_cfg["id"], None, str(exc)[:200]


def _consensus_for_field(votes: dict[str, str | None], field: str) -> tuple[str | None, str]:
    """Return (silver_label_or_None, agreement_level) for one field.

    agreement_level in {"unanimous", "majority", "split", "insufficient"}.
    "insufficient" = fewer than 2 models returned a usable vote for this field.
    """
    values = [v for v in votes.values() if v is not None]
    if len(values) < 2:
        return None, "insufficient"
    counts = Counter(values)
    top_label, top_count = counts.most_common(1)[0]
    if top_count == len(values):
        return top_label, "unanimous"
    if top_count > len(values) / 2:
        return top_label, "majority"
    return None, "split"


async def main() -> None:
    candidates = [
        json.loads(line) for line in CANDIDATES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()
    ]

    existing: dict[str, dict] = {}
    if SILVER_PATH.exists():
        for line in SILVER_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                if rec.get("_marker") == "SILVER_BENCHMARK_METADATA":
                    continue
                existing[rec["id"]] = rec

    to_label = len(candidates) - len(existing)
    print(f"Candidates: {len(candidates)}  Already labeled: {len(existing)}  To label: {to_label}")
    print(f"Models: {[m['id'] for m in MODELS]}")

    groq_client = AsyncGroq(api_key=_benchmark_groq_key)

    metadata_record = {
        "_marker": "SILVER_BENCHMARK_METADATA",
        "_warning": (
            "SILVER BENCHMARK — labels are multi-LLM consensus, NOT human-verified "
            "ground truth. Scores computed against this file measure AGREEMENT WITH "
            "CONSENSUS, NOT accuracy. DO NOT quote as accuracy externally."
        ),
        "_labeler_models": [{"id": m["id"], "family": m["family"], "provider": m["provider"]} for m in MODELS],
        "_consensus_rule": "unanimous=3/3, majority=2/3, split=no majority (no silver label assigned)",
        "_generated_at_note": "see git history / file mtime for generation date",
    }

    results = list(existing.values())
    # Rewrite with metadata first + existing successes; new labels append after.
    with SILVER_PATH.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(metadata_record, ensure_ascii=False) + "\n")
        for rec in results:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()

        for i, cand in enumerate(candidates, 1):
            if cand["id"] in existing:
                continue
            t0 = time.monotonic()
            # All 3 models labeled CONCURRENTLY (different keys/endpoints — no
            # contention with each other), one model never sees another's answer.
            model_results = await asyncio.gather(
                *[_label_one_model(m, cand["text"], groq_client) for m in MODELS]
            )
            latency_ms = int((time.monotonic() - t0) * 1000)

            votes: dict[str, dict[str, str | None]] = {f: {} for f in FIELDS}
            errors: dict[str, str] = {}
            for model_id, labels, err in model_results:
                if err:
                    errors[model_id] = err
                for f in FIELDS:
                    votes[f][model_id] = labels.get(f) if labels else None

            silver: dict[str, str | None] = {}
            agreement: dict[str, str] = {}
            for f in FIELDS:
                label, level = _consensus_for_field(votes[f], f)
                silver[f] = label
                agreement[f] = level

            rec = {
                "id": cand["id"],
                "slice": cand["slice"],
                "text": cand["text"],
                "votes": votes,
                "silver": silver,
                "agreement": agreement,
                "model_errors": errors or None,
                "latency_ms": latency_ms,
            }
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            results.append(rec)

            summary = "  ".join(f"{f}={silver[f]}({agreement[f]})" for f in FIELDS)
            print(f"  [{i}/{len(candidates)}] {cand['id']} ({cand['slice']}): {summary}  {latency_ms}ms")
            if errors:
                print(f"    model errors: {errors}")
            await asyncio.sleep(DELAY_SECONDS)

    print(f"\nDone. Total silver-labeled: {len(results)}")
    print(f"Written: {SILVER_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
