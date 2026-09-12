"""P4a/P4b (Session 12): measured tokens and true cost per extraction, per tier.

The single prior cost figure in this repo ($0.00029625, Session 11 H3b) came from ONE real
call (1,678 in / 568 out tokens) -- real cost depends on the review-length distribution, not
one sample. This re-plays the 106 held-out fixtures (the largest real-review corpus this
project has) against committed cassettes -- ZERO quota spent, $0, deterministic -- capturing
model_name/tokens_in/tokens_out per call (which eval/score_held_out_corpus_v2.py's own output
does not retain), classifies each into small/large tier via the same rule
eval/runner.py uses, and reports mean/median/p95 tokens per tier plus the blended cost at the
ACTUAL observed tier-routing mix (not an assumed 100%-small or 100%-large split).

Usage:
    EVAL_CASSETTE_MODE=replay uv run python eval/measure_token_costs.py
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

QUARANTINE_DIR = ROOT / "eval" / "fixtures" / "_held_out_hindi_hinglish"
HELD_OUT_CASSETTES_PATH = ROOT / "eval" / "cassettes" / "held_out_cassettes.json"
OUT_PATH = ROOT / "eval" / "results" / "token_cost_measurement_n106.json"


def load_quarantined_fixtures() -> list[dict[str, Any]]:
    fixtures = []
    for p in sorted(QUARANTINE_DIR.glob("*.json")):
        if p.name.startswith("."):
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        if "review_text" in data:
            fixtures.append(data)
    return fixtures


def _pct(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (no interpolation) -- matches this repo's existing
    bootstrap percentile convention (eval/bootstrap.py) rather than numpy's default."""
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(int(pct / 100 * len(s)), len(s) - 1)
    return s[idx]


async def main() -> None:
    import os

    import app.core.providers.cassette as cassette_module

    os.environ["EVAL_CASSETTE_MODE"] = "replay"
    cassette_module.CASSETTES_PATH = HELD_OUT_CASSETTES_PATH

    from app.core.config import get_settings
    from app.core.language import detect_language
    from app.core.llm import extract_with_llm
    from app.core.pricing import price_extraction
    from app.core.prompts import build_prompt
    from app.core.sanitize import sanitize, wrap_for_llm

    settings = get_settings()
    fixtures = load_quarantined_fixtures()

    per_call: list[dict[str, Any]] = []
    for fx in fixtures:
        text = fx["review_text"]
        detected_lang = detect_language(text)
        sanitized, _ = sanitize(text)
        wrapped = wrap_for_llm(sanitized)
        user_prompt = build_prompt(wrapped, detected_lang)
        (
            _llm_output,
            model_name,
            _latency_ms,
            tokens_in,
            tokens_out,
            _degraded,
        ) = await extract_with_llm(user_prompt, allow_gemini_fallback=False)
        tier = "large" if model_name == settings.groq_model_large else "small"
        cost = price_extraction(model_name, tokens_in, tokens_out)
        per_call.append(
            {
                "id": fx["id"],
                "model": model_name,
                "tier": tier,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "tokens_total": tokens_in + tokens_out,
                "cost_usd": cost.cost_usd,
                "cost_inr": cost.cost_inr,
            }
        )

    per_tier: dict[str, Any] = {}
    for tier in ("small", "large"):
        calls = [c for c in per_call if c["tier"] == tier]
        if not calls:
            per_tier[tier] = {"n": 0}
            continue
        tin = [c["tokens_in"] for c in calls]
        tout = [c["tokens_out"] for c in calls]
        ttot = [c["tokens_total"] for c in calls]
        cost_usd = [c["cost_usd"] for c in calls]
        per_tier[tier] = {
            "n": len(calls),
            "model": calls[0]["model"],
            "tokens_in": {
                "mean": statistics.mean(tin),
                "median": statistics.median(tin),
                "p95": _pct(tin, 95),
            },
            "tokens_out": {
                "mean": statistics.mean(tout),
                "median": statistics.median(tout),
                "p95": _pct(tout, 95),
            },
            "tokens_total": {
                "mean": statistics.mean(ttot),
                "median": statistics.median(ttot),
                "p95": _pct(ttot, 95),
            },
            "cost_usd_per_extraction": {
                "mean": statistics.mean(cost_usd),
                "median": statistics.median(cost_usd),
                "p95": _pct(cost_usd, 95),
            },
        }

    n_total = len(per_call)
    n_small = sum(1 for c in per_call if c["tier"] == "small")
    n_large = n_total - n_small
    blended_cost_usd = sum(c["cost_usd"] for c in per_call) / n_total if n_total else 0.0
    blended_cost_inr = sum(c["cost_inr"] for c in per_call) / n_total if n_total else 0.0

    summary = {
        "n_fixtures": n_total,
        "source": "eval/fixtures/_held_out_hindi_hinglish/ (106 real reviews), cassette-replay, $0",
        "tier_mix_observed": {
            "small": {"n": n_small, "fraction": n_small / n_total if n_total else 0},
            "large": {"n": n_large, "fraction": n_large / n_total if n_total else 0},
        },
        "per_tier": per_tier,
        "blended_cost_per_extraction": {
            "usd": blended_cost_usd,
            "inr": blended_cost_inr,
            "note": "Mean cost across all 106 calls at the ACTUAL observed tier-routing mix "
            "-- not an assumed split. This is the number to use for per-customer cost "
            "projections, not either tier's own mean in isolation.",
        },
    }
    OUT_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"\nWritten: {OUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
