"""Multi-family consensus validation of the teacher's extraction targets.

Two judge models, both genuinely different lineages from the teacher
(`llama-3.3-70b-versatile`, Meta Llama) AND from each other:
  1. `openai/gpt-oss-120b`  — OpenAI GPT-OSS family
  2. `qwen/qwen3.6-27b`     — Alibaba Qwen family

This is the SAME 2-judge active panel Wave 1 Section B's `eval/consensus/panel.py`
already calibrated and shipped (on `origin/feat/wave1-b-llm-consensus-labeling`,
not yet on `main`) — `allam-2-7b` was tried as a 3rd family there and FAILED
calibration reproducibly (9/33 control-set misses, twice), so it was dropped, not
silently kept. Reusing that already-validated 2-judge panel here rather than
re-running the same calibration and re-discovering the same negative result.

Why this module does NOT `import eval.consensus.panel` directly: this branch
(`feat/wave1-h-corpus-mining`) was cut from `main` before Section B merged, so
`eval/consensus/` does not exist in this worktree. The judge prompt/parsing logic
below is adapted from that package (field definitions and the JSON-schema
instructions are the same semantics as `ReviewExtractionLLMOutput`, reworded from
panel.py's own text, read via `git show origin/feat/wave1-b-llm-consensus-labeling:
eval/consensus/panel.py`) rather than a fresh design. TODO once Section B merges to
`main`: replace this module's judge-calling code with a direct import of
`eval.consensus.panel`, and replace `_consensus_for_field`/`compute_agreement_report`
below with `eval.consensus.voting` + `eval.agreement` (Krippendorff's alpha / Fleiss'
kappa) — this module's own agreement metric is a much simpler pairwise
percent-agreement, not a substitute for that statistically-grounded implementation,
see `compute_agreement_report`'s docstring below for exactly what it does and doesn't
measure.

Uses raw `groq.AsyncGroq` (not `app.core.providers.groq.GroqProvider`) for the same
reason `eval/consensus/panel.py` and `multi_llm_labeler.py` both do: Qwen's Groq
deployment needs a per-model `extra_body={"reasoning_effort": "none"}` override and a
larger `max_completion_tokens` budget (its hybrid "thinking" mode otherwise exhausts
the completion budget before emitting JSON) — `GroqProvider.complete()` has no
extra-body passthrough, and this is judge-panel code, not the production extraction
path (unlike `teacher_labeling.py`, which deliberately DOES use `GroqProvider` for
maximum fidelity to what prod actually sends).

CLOSED-CATEGORY FIELDS ONLY: agreement is computed over `sentiment`, `urgency`,
`buy_again`, and `language` — the same choice `eval/consensus/run_consensus.py`
makes (open-list fields like `pros`/`cons`/`topics` have no well-defined "agreement"
without a much harder set-similarity metric; excluded here for the same reason).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app.core.schemas import ReviewExtractionLLMOutput  # noqa: E402

CLOSED_CATEGORY_FIELDS: tuple[str, ...] = ("sentiment", "urgency", "buy_again", "language")

JUDGE_MODELS: tuple[dict[str, Any], ...] = (
    {"id": "openai/gpt-oss-120b", "family": "OpenAI GPT-OSS", "owner": "OpenAI"},
    {
        "id": "qwen/qwen3.6-27b",
        "family": "Alibaba Qwen",
        "owner": "Alibaba Cloud",
        "extra_params": {"reasoning_effort": "none"},
    },
)

JUDGE_SYSTEM_PROMPT = (
    "You are labeling a customer product review for a research ground-truth dataset. "
    "Return ONLY a valid JSON object. No markdown, no commentary, no code fences."
)

JUDGE_USER_TEMPLATE = """\
Read this customer review and extract structured fields.

Field definitions:
- product: the primary product name mentioned, or your best plain description if none stated.
- stars: ONLY if the review explicitly states a numeric rating (e.g. "4/5", "3 stars").
  null otherwise — never infer from tone.
- stars_inferred: your own 1-5 holistic estimate of reviewer satisfaction. Always populate.
- pros: distinct positive points, short phrases. Empty list if none.
- cons: distinct negative points/complaints/defects, short phrases. Empty list if none.
- buy_again: true/false if repurchase intent is stated or implied; null if not stated/ambiguous.
- sentiment: one of "positive", "negative", "neutral", "mixed" ("mixed" only if both a clear
  positive and a clear negative element are present).
- topics: short snake_case tags for aspects discussed (e.g. battery, price, sound_quality).
- competitor_mentions: other brand/product names explicitly named. Empty list if none.
- urgency: "high" = any physical harm/safety risk, OR explicit refund/return/legal demand, OR a
  repeated/systemic failure. "medium" = a concrete fixable defect, no harm, no escalation demand.
  "low" = no concrete defect — praise, neutral commentary, or subjective preference only.
- feature_requests: explicit improvement suggestions/wishes. Empty list if none.
- language: one of "en", "hi-en" (Latin-script Hindi/English code-mix), "hi" (Devanagari Hindi).

Review:
<review>
{text}
</review>

Return a JSON object with exactly these keys: product, stars, stars_inferred, pros, cons,
buy_again, sentiment, topics, competitor_mentions, urgency, feature_requests, language."""


def build_judge_user_prompt(text: str) -> str:
    return JUDGE_USER_TEMPLATE.format(text=text)


def parse_judge_response(raw: str) -> ReviewExtractionLLMOutput | None:
    """Parse+validate a judge's raw JSON. Returns None on any failure (never raises) —
    an unparseable judge response is treated identically to a judge that errored:
    absent from that item's votes."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    try:
        return ReviewExtractionLLMOutput.model_validate(obj)
    except ValidationError:
        return None


def _extra_params_for(model_id: str) -> dict[str, Any]:
    for m in JUDGE_MODELS:
        if m["id"] == model_id:
            return dict(m.get("extra_params") or {})
    return {}


async def call_judge(client: Any, model_id: str, text: str, timeout: int = 30) -> str:
    """Call one judge; returns raw response content. Raises on API-level failure —
    callers must catch per-judge so one judge's outage doesn't kill the whole run."""
    response = await client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": build_judge_user_prompt(text)},
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
        max_completion_tokens=2000,
        timeout=timeout,
        extra_body=_extra_params_for(model_id) or None,
    )
    return response.choices[0].message.content or ""


def _consensus_for_field(votes: dict[str, Any | None]) -> tuple[Any | None, str]:
    """(silver_label_or_None, agreement_level) for one field's votes across judges.

    agreement_level in {"unanimous", "majority", "split", "insufficient"}.
    With exactly 2 judges, "majority" and "unanimous" collapse to the same case (both
    agree, or it's split) — this generic vote-counting logic handles any judge count
    correctly without special-casing, same design choice `eval/consensus/voting.py`
    (unavailable on this branch) documents making.
    """
    values = [v for v in votes.values() if v is not None]
    if len(values) < 2:
        return None, "insufficient"
    counts = Counter(str(v) for v in values)
    top_label, top_count = counts.most_common(1)[0]
    if top_count == len(values):
        return top_label, "unanimous"
    if top_count > len(values) / 2:
        return top_label, "majority"
    return None, "split"


def consensus_for_item(judge_outputs: dict[str, ReviewExtractionLLMOutput | None]) -> dict:
    """Per-field consensus for one item across CLOSED_CATEGORY_FIELDS."""
    result: dict[str, dict[str, Any]] = {}
    for field in CLOSED_CATEGORY_FIELDS:
        votes = {
            judge_id: (getattr(out, field) if out is not None else None)
            for judge_id, out in judge_outputs.items()
        }
        silver, level = _consensus_for_field(votes)
        result[field] = {
            "votes": {k: (str(v) if v is not None else None) for k, v in votes.items()},
            "silver": silver,
            "agreement": level,
        }
    return result


def compute_agreement_report(records: list[dict]) -> dict:
    """Simple pairwise percent-agreement per field across all labeled records.

    NOT Krippendorff's alpha or Fleiss' kappa — those correct for chance agreement
    and require >=3 raters (or a specific 2-rater formula) to be statistically
    meaningful; `eval/agreement.py` (unmerged Section B branch) already implements
    them properly. This function reports the plain "how often did the panel reach a
    non-split consensus, and how often was consensus reached" rate — a useful, honest,
    much simpler number for a small documented sample, explicitly not a replacement
    for the real reliability statistics once eval/agreement.py is available here.
    """
    report: dict[str, dict[str, Any]] = {}
    for field in CLOSED_CATEGORY_FIELDS:
        levels = [r["consensus"][field]["agreement"] for r in records]
        n = len(levels)
        report[field] = {
            "n": n,
            "unanimous": levels.count("unanimous"),
            "majority": levels.count("majority"),
            "split": levels.count("split"),
            "insufficient": levels.count("insufficient"),
            "non_split_consensus_rate_pct": (
                round(100 * (levels.count("unanimous") + levels.count("majority")) / n, 1)
                if n
                else None
            ),
        }
    return report


async def validate_sample(
    client: Any,
    records: list[dict],
    *,
    text_field: str = "text",
    delay_seconds: float = 2.0,
    on_progress: object = None,
) -> list[dict]:
    """Label every record with every JUDGE_MODELS entry; compute per-item consensus."""
    results: list[dict] = []
    for i, rec in enumerate(records):
        t0 = time.monotonic()
        outputs: dict[str, ReviewExtractionLLMOutput | None] = {}
        errors: dict[str, str] = {}
        for judge in JUDGE_MODELS:
            try:
                raw = await call_judge(client, judge["id"], rec.get(text_field, ""))
                outputs[judge["id"]] = parse_judge_response(raw)
                if outputs[judge["id"]] is None:
                    errors[judge["id"]] = "unparseable_response"
            except Exception as exc:  # noqa: BLE001
                outputs[judge["id"]] = None
                errors[judge["id"]] = str(exc)[:180]
        consensus = consensus_for_item(outputs)
        latency_ms = int((time.monotonic() - t0) * 1000)
        result = {
            "id": rec.get("id", i),
            "consensus": consensus,
            "judge_errors": errors or None,
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
    parser.add_argument("--n", type=int, default=20)
    args = parser.parse_args()

    from benchmark.vernacular_v2.benchmark_groq_key import load_benchmark_groq_key
    from groq import AsyncGroq

    benchmark_key = load_benchmark_groq_key()
    print(f"Using dedicated benchmark Groq key ({benchmark_key[:8]}...{benchmark_key[-4:]}).")
    print(f"Judge panel: {[m['id'] for m in JUDGE_MODELS]}")

    records = _load_jsonl(args.input)[: args.n]
    print(f"Validating {len(records)} records (n={args.n}).")

    client = AsyncGroq(api_key=benchmark_key)

    def _progress(i: int, total: int, result: dict) -> None:
        summary = "  ".join(
            f"{f}={result['consensus'][f]['silver']}" for f in CLOSED_CATEGORY_FIELDS
        )
        print(f"  [{i}/{total}] {result['id']}: {summary}  {result['latency_ms']}ms")

    results = asyncio.run(validate_sample(client, records, on_progress=_progress))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    report = compute_agreement_report(results)
    print("\nAgreement report (simple pairwise, NOT Krippendorff/Fleiss — see module docstring):")
    print(json.dumps(report, indent=2))
    print(f"\nWritten: {args.output}")


if __name__ == "__main__":
    main()
