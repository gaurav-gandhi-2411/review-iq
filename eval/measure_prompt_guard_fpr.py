"""Session 13 P4b: measure llama-prompt-guard-2-86m's false-positive rate on real reviews.

"A guard that flags real customer reviews as attacks is worse than no guard." Runs the
classifier LIVE (real API calls -- this model has its own separate 500K tokens/day / 14.4K
requests/day budget, confirmed live and via Groq's own published docs, NOT shared with the
extraction models' 200K TPD pools -- see P4c/docs/architecture/adr/0015-*.md) over all 106
held-out real marketplace reviews (eval/fixtures/_held_out_hindi_hinglish/) -- text no one wrote
as an attack, so any score above threshold here is a genuine false positive.

Cost: 106 short classification calls (~20-40 tokens each) against a 500,000 tokens/day budget --
trivial, and does not touch the extraction models' quota at all.

Usage:
    uv run python eval/measure_prompt_guard_fpr.py
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from statistics import mean

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "eval" / "fixtures" / "_held_out_hindi_hinglish"
OUT_PATH = ROOT / "eval" / "results" / "prompt_guard_fpr_n106.json"

load_dotenv(ROOT / ".env")

import sys  # noqa: E402

sys.path.insert(0, str(ROOT))
from app.core.injection_guard import INJECTION_GUARD_THRESHOLD  # noqa: E402
from groq import AsyncGroq  # noqa: E402


async def _raw_score(client: AsyncGroq, text: str) -> float:
    response = await client.chat.completions.create(
        model="meta-llama/llama-prompt-guard-2-86m",
        messages=[{"role": "user", "content": text}],
        timeout=10.0,
    )
    return float(response.choices[0].message.content or "0")


async def main() -> None:
    api_key = os.environ["GROQ_API_KEY"]
    client = AsyncGroq(api_key=api_key)

    fixtures = sorted(FIXTURES_DIR.glob("hien-*.json"))
    scores: list[dict[str, object]] = []
    for path in fixtures:
        data = json.loads(path.read_text(encoding="utf-8"))
        text = data["review_text"]
        score = await _raw_score(client, text)
        scores.append({"id": data["id"], "score": score})
        await asyncio.sleep(0.1)  # polite pacing, well under 30 RPM

    flagged = [s for s in scores if s["score"] >= INJECTION_GUARD_THRESHOLD]  # type: ignore[operator]
    result = {
        "n_fixtures": len(scores),
        "threshold": INJECTION_GUARD_THRESHOLD,
        "n_false_positives": len(flagged),
        "false_positive_rate": len(flagged) / len(scores) if scores else 0.0,
        "score_mean": mean(s["score"] for s in scores),  # type: ignore[misc]
        "score_max": max(s["score"] for s in scores),  # type: ignore[type-var]
        "false_positives": flagged,
        "all_scores": scores,
        "note": (
            "Every one of these 106 fixtures is a real marketplace review, never written as "
            "an attack -- any score >= threshold here is a genuine false positive, not a "
            "borderline judgment call."
        ),
    }
    OUT_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Written: {OUT_PATH}")
    print(
        f"\nFalse-positive rate at threshold {INJECTION_GUARD_THRESHOLD}: "
        f"{result['n_false_positives']}/{result['n_fixtures']} "
        f"({result['false_positive_rate']:.2%}). "
        f"Score mean={result['score_mean']:.5f}, max={result['score_max']:.5f}."
    )
    if flagged:
        print("Flagged fixtures:")
        for f in flagged:
            print(f"  {f['id']}: {f['score']}")


if __name__ == "__main__":
    asyncio.run(main())
