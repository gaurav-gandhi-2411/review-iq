"""
LLM-based auto-labeler for Hinglish eval fixtures.

Uses Claude Sonnet (claude-sonnet-4-5) as an independent labeling model
(different from production Groq Llama 3.3 70B) to keep the eval honest.

Usage:
    uv run python eval/label-helper-llm.py

Output:
    eval/fixtures/hi-en/001.json … 015.json

Hard budget: $2.50 (stops before $3.00 cap).
Pricing (as of 2026-05): input $3/M tokens, output $15/M tokens.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

CANDIDATES_FILE = Path(__file__).parent / "data" / "flipkart_candidates.jsonl"
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "hi-en"

TARGET = 15
COST_HARD_STOP = 2.50  # USD

MODEL = "claude-sonnet-4-5"
INPUT_COST_PER_M = 3.0
OUTPUT_COST_PER_M = 15.0

SYSTEM_PROMPT = """\
You are a careful data annotator creating GROUND TRUTH labels for an NLP evaluation suite.

The model under evaluation is Groq Llama 3.3 70B. You are Claude Sonnet — an independent model.
Your labels become the reference answers used to score the production model. Label honestly.

Rules:
- stars: set ONLY if an explicit numeric rating is given in the text (e.g. "4/5", "★★★★☆", "4 stars"). Otherwise null.
- stars_inferred: your best read of the implied satisfaction (1–5). null if truly ambiguous.
- pros/cons: short phrases, 2–6 words each. Empty list [] if none.
- buy_again: true if the reviewer recommends the product, false if they don't, null if ambiguous.
- sentiment: exactly one of "positive", "negative", "mixed", "neutral".
- urgency: "low" for general feedback, "medium" for notable dissatisfaction, "high" for safety/defect/return intent.
- topics: short snake_case or space-separated topic words (e.g. "battery", "price", "sound quality").
- competitor_mentions: brand names only. [] if none.
- feature_requests: things the reviewer wishes the product had. [] if none.
- product: the product category or name as best you can infer. Use a generic category (e.g. "earphones", "phone") if no brand/model is stated.
- language: always "hi-en" for these reviews (Hinglish — Roman-script Hindi/English code-mix).

If a field is genuinely ambiguous, return null rather than guessing. Accuracy > completeness.

Return ONLY valid JSON matching this exact schema — no commentary, no markdown fences:
{
  "product": string,
  "stars": integer | null,
  "stars_inferred": integer | null,
  "pros": [string, ...],
  "cons": [string, ...],
  "buy_again": boolean | null,
  "sentiment": "positive" | "negative" | "mixed" | "neutral",
  "urgency": "low" | "medium" | "high",
  "topics": [string, ...],
  "competitor_mentions": [string, ...],
  "feature_requests": [string, ...],
  "language": "hi-en"
}
"""

_SENTIMENTS = {"positive", "negative", "mixed", "neutral"}
_URGENCIES = {"low", "medium", "high"}


def _load_candidates() -> list[dict]:
    if not CANDIDATES_FILE.exists():
        print(f"ERROR: {CANDIDATES_FILE} not found.")
        print("  Run first: uv run python eval/data/sample_flipkart.py")
        sys.exit(1)
    candidates = []
    with CANDIDATES_FILE.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    candidates.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return candidates


def _rank_candidates(candidates: list[dict]) -> list[dict]:
    hinglish = [
        c for c in candidates
        if c.get("language") == "hi-en"
        and 30 <= c.get("char_len", 0) <= 600
    ]

    def score(c: dict) -> float:
        n = c["char_len"]
        length_score = 1.0 - abs(n - 200) / 400
        product_bonus = 0.1 if c.get("product", "unknown") not in ("unknown", "nan") else 0.0
        return length_score + product_bonus

    ranked = sorted(hinglish, key=score, reverse=True)

    seen_prefix: set[str] = set()
    diverse = []
    for c in ranked:
        prefix = re.sub(r"\s+", " ", c["text"][:50].lower())
        if prefix not in seen_prefix:
            seen_prefix.add(prefix)
            diverse.append(c)

    return diverse


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _token_cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * INPUT_COST_PER_M / 1_000_000) + (output_tokens * OUTPUT_COST_PER_M / 1_000_000)


def _validate_ground_truth(gt: dict) -> bool:
    if not isinstance(gt.get("product"), str) or not gt["product"].strip():
        return False
    if gt.get("sentiment") not in _SENTIMENTS:
        return False
    if gt.get("urgency") not in _URGENCIES:
        return False
    if not isinstance(gt.get("pros"), list):
        return False
    if not isinstance(gt.get("cons"), list):
        return False
    if not isinstance(gt.get("topics"), list):
        return False
    if not isinstance(gt.get("competitor_mentions"), list):
        return False
    if not isinstance(gt.get("feature_requests"), list):
        return False
    stars = gt.get("stars")
    if stars is not None and not (isinstance(stars, int) and 1 <= stars <= 5):
        return False
    stars_inferred = gt.get("stars_inferred")
    if stars_inferred is not None and not (isinstance(stars_inferred, int) and 1 <= stars_inferred <= 5):
        return False
    return True


def _fixture_path(n: int) -> Path:
    return FIXTURES_DIR / f"{n:03d}.json"


def _next_fixture_number() -> int:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(FIXTURES_DIR.glob("*.json"))
    if not existing:
        return 1
    last = int(existing[-1].stem)
    return last + 1


def _write_fixture(
    n: int,
    candidate: dict,
    ground_truth: dict,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
) -> Path:
    fixture = {
        "id": f"hi-en-{n:03d}",
        "review_text": candidate["text"],
        "ground_truth": ground_truth,
        "scoring_notes": {
            "exact_match_fields": ["product", "stars", "buy_again", "sentiment", "language"],
            "set_overlap_fields": ["topics", "competitor_mentions"],
            "fuzzy_fields": ["pros", "cons"],
            "tolerance_fields": {"stars_inferred": 1},
        },
        "labeling_meta": {
            "labeled_by": "claude-sonnet-4-5",
            "labeled_at": datetime.now(timezone.utc).isoformat(),
            "model_version": model_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    }
    path = _fixture_path(n)
    path.write_text(json.dumps(fixture, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # Try loading from .env
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    break
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in environment or .env")
        sys.exit(1)

    try:
        import anthropic
    except ImportError:
        print("ERROR: anthropic not installed. Run: uv add anthropic")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 70)
    print("  Review-IQ Hinglish Auto-Labeler (Claude Sonnet)")
    print("=" * 70)
    print(f"  Model:       {MODEL}")
    print(f"  Hard budget: ${COST_HARD_STOP:.2f}")
    print(f"  Target:      {TARGET} fixtures")
    print()

    candidates = _load_candidates()
    ranked = _rank_candidates(candidates)
    print(f"  Candidates available: {len(ranked)} hi-en (30-600 chars, de-duped)")
    print()

    existing_hashes: set[str] = set()
    existing_fixtures = sorted(FIXTURES_DIR.glob("*.json"))
    for fp in existing_fixtures:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            existing_hashes.add(_text_hash(data.get("review_text", "")))
        except Exception:
            pass
    accepted_count = len(existing_fixtures)
    if accepted_count > 0:
        print(f"  Resuming: {accepted_count} fixtures already exist")

    total_cost = 0.0
    total_input_tokens = 0
    total_output_tokens = 0
    considered = 0
    skipped_quality = 0
    skipped_duplicate = 0
    skipped_parse = 0

    for candidate in ranked:
        if accepted_count >= TARGET:
            break

        # Budget check
        if total_cost >= COST_HARD_STOP:
            print(f"\n  HARD STOP: accumulated cost ${total_cost:.4f} >= ${COST_HARD_STOP:.2f}")
            print(f"  Accepted {accepted_count}/{TARGET} fixtures before budget exhausted.")
            break

        # Duplicate check
        h = _text_hash(candidate["text"])
        if h in existing_hashes:
            skipped_duplicate += 1
            continue

        considered += 1
        print(f"  [{considered}] Labeling ({candidate['char_len']} chars) ...", end="", flush=True)

        user_msg = f"Label this Hinglish product review:\n\n{candidate['text']}"

        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=512,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
        except Exception as exc:
            print(f" ERROR: {exc}")
            skipped_parse += 1
            continue

        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        step_cost = _token_cost(input_tokens, output_tokens)
        total_cost += step_cost
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens

        raw_text = response.content[0].text.strip()
        model_id = response.model

        # Parse JSON
        try:
            # Strip markdown fences if Sonnet added them despite instructions
            cleaned = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.MULTILINE)
            cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE).strip()
            gt = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            print(f" PARSE_ERROR (${step_cost:.5f})")
            print(f"    Raw: {raw_text[:120]}")
            skipped_parse += 1
            continue

        # Quality validation
        if not _validate_ground_truth(gt):
            print(f" QUALITY_FAIL (${step_cost:.5f})")
            skipped_quality += 1
            continue

        # Ensure language is always hi-en
        gt["language"] = "hi-en"

        fixture_n = _next_fixture_number()
        path = _write_fixture(fixture_n, candidate, gt, model_id, input_tokens, output_tokens)
        existing_hashes.add(h)
        accepted_count += 1

        print(f" OK -> {path.name}  in={input_tokens} out={output_tokens}  ${step_cost:.5f}  total=${total_cost:.4f}")

    print()
    print("=" * 70)
    print(f"  DONE: {accepted_count}/{TARGET} fixtures written to {FIXTURES_DIR}")
    print(f"  Candidates considered: {considered}")
    print(f"  Skipped (parse error): {skipped_parse}")
    print(f"  Skipped (quality fail): {skipped_quality}")
    print(f"  Skipped (duplicate): {skipped_duplicate}")
    print()
    print(f"  Total input tokens:  {total_input_tokens:,}")
    print(f"  Total output tokens: {total_output_tokens:,}")
    print(f"  Total Anthropic cost: ${total_cost:.6f}")
    if accepted_count > 0:
        print(f"  Average cost/fixture: ${total_cost / accepted_count:.6f}")
    print("=" * 70)

    # Write cost info to a sidecar for README generation
    sidecar = FIXTURES_DIR / ".labeling_run.json"
    sidecar.write_text(json.dumps({
        "model": MODEL,
        "labeled_at": datetime.now(timezone.utc).isoformat(),
        "fixtures_written": accepted_count,
        "candidates_considered": considered,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_cost_usd": total_cost,
        "avg_cost_per_fixture_usd": total_cost / accepted_count if accepted_count else 0,
    }, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
