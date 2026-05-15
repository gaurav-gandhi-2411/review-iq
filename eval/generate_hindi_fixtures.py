"""
One-shot script: generate 6 synthetic Hindi fixtures + verify with production extractor.

Generates Devanagari-script Hindi reviews via Claude Sonnet, writes fixtures to
eval/fixtures/hi/, then runs each through extract_with_llm (Groq Llama 3.3 70B)
to confirm the current English-language prompt scores >=85% before language-branching.

Usage:
    uv run python eval/generate_hindi_fixtures.py

Env required: ANTHROPIC_API_KEY (for generation), GROQ_API_KEY (for verification).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "hi"
MODEL = "claude-sonnet-4-5"
INPUT_COST_PER_M = 3.0
OUTPUT_COST_PER_M = 15.0

SYSTEM_PROMPT = """\
You are creating SYNTHETIC Hindi product reviews for an NLP evaluation suite.
These reviews test an extraction model that reads Hindi text and outputs structured JSON.

CRITICAL RULES for review_text:
1. Write mostly in Devanagari Hindi script — authentic, natural Indian consumer language
2. The product BRAND or MODEL NAME must appear in English/Roman script in the review text
   (e.g. "mera Boat earphone", "yeh Sony speaker", "Amazon se liya charger"). This is
   extremely common in real Indian reviews — people write brand names in English.
3. Keep the review short and focused (60-200 chars). One or two clear points max.
4. State pros and cons in simple, direct language so they are unambiguous.

Ground truth fields (ALL in English — this is what the extraction model outputs):
- "review_text": the synthetic Hindi review containing an English brand/product name
- "product": English name matching what appears in the review (e.g. "Boat earphone", "Sony speaker")
- "stars": integer 1-5 ONLY if explicitly stated in review text (e.g. "4 star diya"), else null
- "stars_inferred": integer 1-5 (your read of satisfaction)
- "pros": 0-2 SHORT English phrases (2-5 words each) for positives — keep VERY simple
- "cons": 0-2 SHORT English phrases (2-5 words each) for negatives — keep VERY simple
- "buy_again": true / false / null
- "sentiment": "positive" | "negative" | "mixed" | "neutral"
- "urgency": "high" (safety/defect/return) | "medium" (notable dissatisfaction) | "low"
- "topics": 1-3 simple English words/phrases that are CLEARLY in the review
- "competitor_mentions": brand names only if mentioned, else []
- "feature_requests": English feature requests if any, else []
- "language": always "hi"

Return ONLY valid JSON — no commentary, no markdown fences.
"""

PERSONAS = [
    {
        "id": "001",
        "name": "frustrated_customer",
        "prompt": (
            "Frustrated customer whose product broke within days. "
            "NOT buying again. Brand: Syska charger. "
            "Review must say 'Syska charger' in English. Short, 60-120 chars Hindi. "
            "Cons: 'stopped working in two days'. Topics: ['quality']. Sentiment: negative. Urgency: medium."
        ),
    },
    {
        "id": "002",
        "name": "happy_customer",
        "prompt": (
            "Happy customer, very satisfied. Will recommend. Brand: Boat earphones. "
            "Review must say 'Boat earphone' in English. Short, 60-120 chars Hindi. "
            "Pros: 'excellent sound quality'. Topics: ['sound quality']. Sentiment: positive. Urgency: low."
        ),
    },
    {
        "id": "003",
        "name": "ambiguous_buy_again",
        "prompt": (
            "Mixed feelings. Display is good but battery drains fast. Unsure about buying again. "
            "Brand: Noise smartwatch. Review must say 'Noise smartwatch' in English. 80-150 chars Hindi. "
            "Pros: 'good display'. Cons: 'battery drains fast'. Topics: ['battery', 'display']. "
            "Sentiment: mixed. Buy_again: null. Urgency: low."
        ),
    },
    {
        "id": "004",
        "name": "urgent_safety",
        "prompt": (
            "Safety emergency. Product gave electric shock. Wants refund. NOT buying again. "
            "Brand: generic adapter (say 'Amazon se liya adapter' in English). 60-130 chars Hindi. "
            "Cons: 'electric shock'. Topics: ['safety']. Sentiment: negative. Urgency: high."
        ),
    },
    {
        "id": "005",
        "name": "feature_request",
        "prompt": (
            "Satisfied but wishes product had Bluetooth support. Otherwise fine. "
            "Brand: Portronics speaker (say 'Portronics speaker' in English). 80-150 chars Hindi. "
            "Pros: 'good sound'. Feature requests: ['bluetooth support']. "
            "Topics: ['sound quality', 'bluetooth']. Sentiment: mixed. Buy_again: null. Urgency: low."
        ),
    },
    {
        "id": "006",
        "name": "neutral_review",
        "prompt": (
            "Neutral review. Average product, no strong feelings. Neither positive nor negative. "
            "Brand: Skybags bag (say 'Skybags bag' in English). 60-120 chars Hindi. "
            "Topics: ['quality']. Sentiment: neutral. Buy_again: null. Urgency: low. "
            "Pros: [], Cons: []."
        ),
    },
]

SCORING_NOTES = {
    "exact_match_fields": ["product", "stars", "buy_again", "sentiment", "language"],
    "set_overlap_fields": ["topics", "competitor_mentions"],
    "fuzzy_fields": ["pros", "cons"],
    "tolerance_fields": {"stars_inferred": 1},
}

SENTIMENTS = {"positive", "negative", "mixed", "neutral"}
URGENCIES = {"low", "medium", "high"}


def _load_api_key(key_name: str) -> str:
    val = os.environ.get(key_name)
    if val:
        return val
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key_name}="):
                return line.split("=", 1)[1].strip()
    return ""


def _token_cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * INPUT_COST_PER_M / 1_000_000) + (output_tokens * OUTPUT_COST_PER_M / 1_000_000)


def _validate(gt: dict) -> list[str]:
    errors = []
    if not isinstance(gt.get("review_text"), str) or len(gt["review_text"]) < 20:
        errors.append("review_text too short or missing")
    if not isinstance(gt.get("product"), str) or not gt["product"].strip():
        errors.append("product missing")
    if gt.get("sentiment") not in SENTIMENTS:
        errors.append(f"invalid sentiment: {gt.get('sentiment')}")
    if gt.get("urgency") not in URGENCIES:
        errors.append(f"invalid urgency: {gt.get('urgency')}")
    if gt.get("language") != "hi":
        errors.append("language must be 'hi'")
    for lst_field in ("pros", "cons", "topics", "competitor_mentions", "feature_requests"):
        if not isinstance(gt.get(lst_field), list):
            errors.append(f"{lst_field} must be list")
    return errors


def generate_fixtures(client) -> tuple[list[dict], float]:
    """Call Sonnet for each persona, return list of fixture dicts + total cost."""
    total_cost = 0.0
    fixtures = []

    for persona in PERSONAS:
        print(f"  Generating {persona['id']} ({persona['name']})...", end="", flush=True)
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=600,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": persona["prompt"]}],
            )
        except Exception as exc:
            print(f" ERROR: {exc}")
            sys.exit(1)

        inp = response.usage.input_tokens
        out = response.usage.output_tokens
        cost = _token_cost(inp, out)
        total_cost += cost

        raw = response.content[0].text.strip()
        try:
            cleaned = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
            cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE).strip()
            gt = json.loads(cleaned)
        except json.JSONDecodeError:
            print(f" PARSE_ERROR — raw: {raw[:100]}")
            sys.exit(1)

        gt["language"] = "hi"

        errors = _validate(gt)
        if errors:
            print(f" VALIDATION_FAIL: {errors}")
            sys.exit(1)

        review_text = gt.pop("review_text")

        fixture = {
            "id": f"hi-{persona['id']}",
            "review_text": review_text,
            "ground_truth": gt,
            "scoring_notes": SCORING_NOTES,
            "labeling_meta": {
                "labeled_by": "claude-sonnet-4-5",
                "labeled_at": datetime.now(timezone.utc).isoformat(),
                "model_version": response.model,
                "input_tokens": inp,
                "output_tokens": out,
                "persona": persona["name"],
                "note": "synthetic — review text and ground truth both generated by labeling model",
            },
        }
        fixtures.append(fixture)
        print(f" OK  ${cost:.5f}")

    return fixtures, total_cost


async def verify_fixtures(fixtures: list[dict]) -> list[tuple[str, float, str | None]]:
    """Run each fixture through extract_with_llm, score against ground truth, return results."""
    from eval.runner import run_single, score_fixture

    results = []
    for fixture in fixtures:
        print(f"  Verifying {fixture['id']}...", end="", flush=True)
        result = await run_single(fixture)
        if result.error:
            print(f" ERROR: {result.error}")
            results.append((fixture["id"], 0.0, result.error))
        else:
            scores = [fr.score for fr in result.field_results]
            score = sum(scores) / len(scores) if scores else 0.0
            pct = f"{score:.0%}"
            field_detail = "  ".join(f"{fr.field}={fr.score:.2f}" for fr in result.field_results)
            print(f" {pct}  [{field_detail}]")
            results.append((fixture["id"], score, None))

    return results


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-only", action="store_true",
                        help="Skip generation; only re-score existing fixtures in FIXTURES_DIR")
    args = parser.parse_args()

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 70)
    print("  Review-IQ Hindi Fixture Generator (Claude Sonnet)")
    print("=" * 70)
    print(f"  6 personas, {MODEL}, output -> {FIXTURES_DIR}")
    print()

    gen_cost = 0.0

    if args.verify_only:
        existing = sorted(FIXTURES_DIR.glob("*.json"))
        if not existing:
            print("ERROR: --verify-only but no fixtures found in", FIXTURES_DIR)
            sys.exit(1)
        fixtures = [json.loads(p.read_text(encoding="utf-8")) for p in existing]
        print(f"  Loaded {len(fixtures)} existing fixtures (skipping generation)")
    else:
        anthropic_key = _load_api_key("ANTHROPIC_API_KEY")
        if not anthropic_key:
            print("ERROR: ANTHROPIC_API_KEY not set")
            sys.exit(1)
        try:
            import anthropic
        except ImportError:
            print("ERROR: run: uv add anthropic")
            sys.exit(1)
        client = anthropic.Anthropic(api_key=anthropic_key)
        print("Phase 1: Generating synthetic Hindi fixtures")
        print("-" * 50)
        fixtures, gen_cost = generate_fixtures(client)
        print(f"\n  Generated {len(fixtures)} fixtures  cost=${gen_cost:.5f}")
        for fixture in fixtures:
            num = fixture["id"].split("-")[1]
            path = FIXTURES_DIR / f"{num}.json"
            path.write_text(json.dumps(fixture, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"  Wrote {path.name}")

    print()
    print("Phase 2: Verifying with Groq Llama 3.3 70B (current prompt, no language branching)")
    print("-" * 50)

    verify_results = asyncio.run(verify_fixtures(fixtures))

    passed = [r for r in verify_results if r[1] >= 0.85]
    overall = sum(r[1] for r in verify_results) / len(verify_results) if verify_results else 0.0

    print()
    print("=" * 70)
    print(f"  Hindi fixture verification: {len(passed)}/{len(verify_results)} pass (>=85%)")
    print(f"  Overall accuracy: {overall:.1%}  {'PASS' if overall >= 0.85 else 'FAIL'}")
    if gen_cost > 0:
        print(f"  Generation cost: ${gen_cost:.5f}")
    print("=" * 70)

    if overall < 0.85:
        print("\n  WARNING: Hindi fixtures scored below 85% with current prompt.")
        print("  Step 6 (language-branching prompts) will be needed to meet the gate.")
        print("  Committing fixtures regardless -- this gap motivates Step 6.")


if __name__ == "__main__":
    main()
