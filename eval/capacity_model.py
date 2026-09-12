"""Session 13 P3a/P3b: the real production capacity ceiling, computed from measured data.

Reads eval/results/token_cost_measurement_n106.json (real, measured tokens/extraction per
tier, PR #169) and Groq's published per-model free-tier limits (hardcoded below, each cited
to its verification method -- see docs/architecture/adr/0015-*.md's Session 13 correction
section for the full methodology). ZERO quota spent running this script -- pure arithmetic
over already-committed data.

The point of this script: the binding daily/per-minute constraint is NOT "1,000 requests/day"
(RPD) the way a naive reading suggests -- at real measured token sizes, TPD (tokens/day) binds
first, and binds MUCH tighter than RPD does. This script makes that arithmetic reproducible
and auditable rather than asserted in prose.

Usage:
    uv run python eval/capacity_model.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TOKEN_COST_PATH = ROOT / "eval" / "results" / "token_cost_measurement_n106.json"
OUT_PATH = ROOT / "eval" / "results" / "capacity_model.json"

# Per-model Groq free-tier limits. VERIFIED two independent ways this session:
# (1) a real, live POST /openai/v1/chat/completions call against each model, response
#     headers captured directly (x-ratelimit-limit-requests, x-ratelimit-limit-tokens,
#     plus reset-time arithmetic: reset_requests = (used/limit_requests) * 86400s
#     reconciles exactly to a 24h window, reset_tokens reconciles to a 60s window);
# (2) Groq's own published rate-limits doc (console.groq.com/docs/rate-limits), fetched
#     directly, same session.
# TPD (tokens/day) is NOT independently re-derivable from a single live response (no header
# exposes it directly) -- confirmed via (2) only, not (1). Still treated as VERIFIED because
# (2) is Groq's own primary documentation, not a blended/third-party search summary.
GROQ_FREE_TIER_LIMITS: dict[str, dict[str, int]] = {
    "openai/gpt-oss-20b": {"rpm": 30, "rpd": 1_000, "tpm": 8_000, "tpd": 200_000},
    "openai/gpt-oss-120b": {"rpm": 30, "rpd": 1_000, "tpm": 8_000, "tpd": 200_000},
}

# Maps this project's tier labels (from token_cost_measurement_n106.json) to Groq model IDs.
TIER_TO_MODEL: dict[str, str] = {
    "small": "openai/gpt-oss-20b",
    "large": "openai/gpt-oss-120b",
}


def compute_capacity_model(token_cost_data: dict[str, Any]) -> dict[str, Any]:
    """Compute the real per-minute and per-day extraction ceiling at the observed tier mix.

    Model: the two Groq models have INDEPENDENT quota pools (Session 13's correction to the
    prior "shared org-wide budget" mental model -- see ADR 0015). If overall extraction rate
    is R (per minute or per day), tier t's pool receives `fraction[t] * R` extractions, each
    costing `mean_tokens[t]` tokens. Tier t's own limit binds at:
        R <= limit[t] / (fraction[t] * mean_tokens[t])
    The real achievable R is the MINIMUM across all tiers' bindings (the tightest one).
    """
    fractions = {
        tier: info["fraction"] for tier, info in token_cost_data["tier_mix_observed"].items()
    }
    mean_tokens = {
        tier: info["tokens_total"]["mean"] for tier, info in token_cost_data["per_tier"].items()
    }

    per_tier_bindings: dict[str, dict[str, float]] = {}
    for tier, model in TIER_TO_MODEL.items():
        limits = GROQ_FREE_TIER_LIMITS[model]
        frac = fractions[tier]
        tok = mean_tokens[tier]
        per_tier_bindings[tier] = {
            "model": model,
            "fraction_of_traffic": frac,
            "mean_tokens_per_extraction": tok,
            # Extractions/minute this tier's TPM pool alone permits, at this tier's own
            # share of total traffic.
            "max_total_rate_per_minute_from_tpm": limits["tpm"] / (frac * tok),
            "max_total_rate_per_minute_from_rpm": limits["rpm"] / frac,
            "max_total_extractions_per_day_from_tpd": limits["tpd"] / (frac * tok),
            "max_total_extractions_per_day_from_rpd": limits["rpd"] / frac,
        }

    # The real ceiling is the tightest (minimum) binding across tiers and across dimensions.
    per_minute_candidates = {
        tier: min(b["max_total_rate_per_minute_from_tpm"], b["max_total_rate_per_minute_from_rpm"])
        for tier, b in per_tier_bindings.items()
    }
    per_day_candidates = {
        tier: min(
            b["max_total_extractions_per_day_from_tpd"], b["max_total_extractions_per_day_from_rpd"]
        )
        for tier, b in per_tier_bindings.items()
    }

    binding_tier_per_minute = min(per_minute_candidates, key=lambda t: per_minute_candidates[t])
    binding_tier_per_day = min(per_day_candidates, key=lambda t: per_day_candidates[t])

    real_per_minute_ceiling = per_minute_candidates[binding_tier_per_minute]
    real_per_day_ceiling = per_day_candidates[binding_tier_per_day]

    return {
        "method": (
            "Two independent per-model Groq quota pools (not one shared org-wide pool -- "
            "see ADR 0015's Session 13 correction). Real ceiling = min across tiers of "
            "(that tier's own limit) / (that tier's share of total traffic * that tier's "
            "mean tokens/extraction), at the OBSERVED tier-routing mix."
        ),
        "per_tier_bindings": per_tier_bindings,
        "real_extractions_per_minute_ceiling": real_per_minute_ceiling,
        "binding_tier_per_minute": binding_tier_per_minute,
        "real_extractions_per_day_ceiling": real_per_day_ceiling,
        "binding_tier_per_day": binding_tier_per_day,
        "real_extractions_per_month_ceiling_30d": real_per_day_ceiling * 30,
        "note": (
            "This ceiling is SHARED across every consumer of the single production "
            "GROQ_API_KEY: real customer /v2/extract and /v2/extract/batch and "
            "/v2/ingest/csv traffic, the public /demo/extract endpoint, and any live "
            "(non-cassette-replay) eval/CI call against these two models. It is not "
            "per-customer -- it is the entire business's total daily capacity on the "
            "free tier, at the current tier-routing mix."
        ),
    }


def main() -> None:
    data = json.loads(TOKEN_COST_PATH.read_text(encoding="utf-8"))
    result = compute_capacity_model(data)
    OUT_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Written: {OUT_PATH}")
    print(
        f"\nReal ceiling: {result['real_extractions_per_minute_ceiling']:.2f} extractions/minute "
        f"(binding: {result['binding_tier_per_minute']} tier's TPM/RPM), "
        f"{result['real_extractions_per_day_ceiling']:.1f} extractions/day "
        f"(binding: {result['binding_tier_per_day']} tier's TPD/RPD), "
        f"~{result['real_extractions_per_month_ceiling_30d']:.0f} extractions/month (30d)."
    )


if __name__ == "__main__":
    main()
