"""LLM provider pricing constants and per-extraction cost calculation.

Every model that can actually serve org-path extraction traffic (per
app/core/config.py's groq_model / groq_model_small / groq_model_large /
gemini_model) needs an entry here. An unrecognized model is a deploy-time bug
(a new model was wired into config/providers without a matching price added
here) — ``price_extraction`` raises ``UnknownModelError`` rather than silently
recording a $0 cost, so the gap surfaces immediately instead of corrupting the
Wave 2 COGS numbers this module exists to produce.

Source of truth for the numbers below: fetched live from each provider's own
pricing page during this task (2026-07-31) — see ``source``/``as_of`` on each
``ModelPricing`` entry. Re-verify before any Wave 2 pricing decision if the
``as_of`` date is more than ~90 days old; provider pricing changes without
notice.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

log = structlog.get_logger(__name__)

# USD → INR conversion rate. Fetched live 2026-09-05 from
# https://open.er-api.com/v6/latest/USD (provider: exchangerate-api.com),
# reported update timestamp 2026-09-05T00:02:32Z. This is a point-in-time
# market snapshot, not a live feed — FX drifts day to day. Re-fetch before
# using this constant in any customer-facing price quote; fine as-is for
# COGS/telemetry purposes where day-to-day drift doesn't matter.
USD_TO_INR_RATE = 94.51761


class UnknownModelError(ValueError):
    """Raised when a model has no pricing entry.

    Deliberately a distinct exception type (not a bare ValueError) so callers
    can catch it specifically without swallowing unrelated ValueErrors.
    """


@dataclass(frozen=True)
class ModelPricing:
    """USD price per 1,000,000 tokens, input and output priced separately.

    ``tier`` mirrors the routing tiers in app/core/routing_policy.py
    ("small"/"large") for models on the tiered router; "fallback" for models
    only reachable via non-tiered failover paths (secondary/Gemini).
    """

    provider: str
    tier: str
    usd_per_million_input: float
    usd_per_million_output: float
    source: str
    as_of: str  # ISO date the price was last verified against a live page
    verified: bool  # False only if a number could not be confirmed live
    note: str = ""


# ---------------------------------------------------------------------------
# Pricing table — one entry per model reachable on any extraction path.
# ---------------------------------------------------------------------------
PRICING_TABLE: dict[str, ModelPricing] = {
    # Groq — org-path production models (app/core/config.py groq_model_small/_large).
    # Groq deprecated llama-3.1-8b-instant and llama-3.3-70b-versatile on 2026-08-16
    # (see app/core/config.py's comments) -- current live defaults are the two
    # openai/gpt-oss-* entries below. The two llama-* entries are kept (not deleted)
    # only so a stray cassette or historical extraction_costs row referencing them by
    # model name doesn't hit UnknownModelError; they can no longer serve live traffic.
    # Verified live 2026-09-05 against https://console.groq.com/docs/model/openai/
    # gpt-oss-20b and .../gpt-oss-120b (server-rendered per-model pricing pages).
    "openai/gpt-oss-20b": ModelPricing(
        provider="groq",
        tier="small",
        usd_per_million_input=0.075,
        usd_per_million_output=0.30,
        source="https://console.groq.com/docs/model/openai/gpt-oss-20b",
        as_of="2026-09-05",
        verified=True,
    ),
    "openai/gpt-oss-120b": ModelPricing(
        provider="groq",
        tier="large",
        usd_per_million_input=0.15,
        usd_per_million_output=0.60,
        source="https://console.groq.com/docs/model/openai/gpt-oss-120b",
        as_of="2026-09-05",
        verified=True,
    ),
    # Deprecated 2026-08-16 by Groq -- can no longer serve live traffic. Kept only so
    # historical cost rows / cassettes referencing these model names don't 404 into
    # UnknownModelError. Prices as last verified (2026-07-31), not re-verified since --
    # a deprecated model's price page may no longer even exist.
    "llama-3.1-8b-instant": ModelPricing(
        provider="groq",
        tier="small",
        usd_per_million_input=0.05,
        usd_per_million_output=0.08,
        source="https://groq.com/pricing",
        as_of="2026-07-31",
        verified=True,
        note="Deprecated by Groq 2026-08-16; cannot serve live traffic, kept for historical rows.",
    ),
    "llama-3.3-70b-versatile": ModelPricing(
        provider="groq",
        tier="large",
        usd_per_million_input=0.59,
        usd_per_million_output=0.79,
        source="https://groq.com/pricing",
        as_of="2026-07-31",
        verified=True,
        note="Deprecated by Groq 2026-08-16; cannot serve live traffic, kept for historical rows.",
    ),
    # Gemini — v1/demo-path fallback only; NEVER reachable on the org-key path
    # (app/core/providers/base.py assert_privacy_safe bans train-on-input
    # providers there). Verified live 2026-07-31 against
    # https://ai.google.dev/gemini-api/docs/pricing, which also surfaced an
    # operationally relevant fact: Gemini 2.0 Flash is marked deprecated and
    # was shut down 2026-06-01 by Google. app/core/config.py's gemini_model
    # default still points at it — this is a live fallback-path outage, not a
    # pricing gap, and is out of scope for this cost-telemetry task (belongs
    # to Section F/reliability). Price kept here for cost-math completeness
    # and so a stray call doesn't hit UnknownModelError on top of whatever
    # error Google's API itself now returns for a shut-down model.
    "gemini-2.0-flash": ModelPricing(
        provider="gemini",
        tier="fallback",
        usd_per_million_input=0.10,
        usd_per_million_output=0.40,
        source="https://ai.google.dev/gemini-api/docs/pricing",
        as_of="2026-07-31",
        verified=True,
        note="Model deprecated + shut down by Google 2026-06-01; flagged, not fixed here.",
    ),
}


@dataclass(frozen=True)
class ExtractionCost:
    """Priced result for one extraction call."""

    provider: str
    tier: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    cost_inr: float


def _lookup_pricing(model: str) -> ModelPricing:
    """Return the pricing entry for *model*, raising loudly if absent.

    Never returns a zero-cost placeholder — an unrecognized model must not
    silently corrupt the cost-per-1k aggregate with a free row.
    """
    pricing = PRICING_TABLE.get(model)
    if pricing is None:
        log.error("pricing.unknown_model", model=model)
        raise UnknownModelError(
            f"No pricing entry for model {model!r}. Add it to "
            "app/core/pricing.PRICING_TABLE before this model can serve "
            "extraction traffic — cost telemetry must never silently record $0."
        )
    return pricing


def compute_cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    """Return the USD cost of one extraction call for *model*.

    Raises:
        UnknownModelError: *model* has no entry in PRICING_TABLE.
    """
    pricing = _lookup_pricing(model)
    return (tokens_in / 1_000_000) * pricing.usd_per_million_input + (
        tokens_out / 1_000_000
    ) * pricing.usd_per_million_output


def compute_cost_inr(cost_usd: float) -> float:
    """Convert a USD cost to INR using the pinned snapshot rate (see USD_TO_INR_RATE)."""
    return cost_usd * USD_TO_INR_RATE


def price_extraction(model: str, tokens_in: int, tokens_out: int) -> ExtractionCost:
    """Price one extraction call end to end: USD, INR, provider, and tier.

    This is the single entry point callers should use — it bundles the
    pricing-table lookup with the USD/INR cost math so call sites never
    duplicate the ``compute_cost_usd`` + ``compute_cost_inr`` pair.

    Raises:
        UnknownModelError: *model* has no entry in PRICING_TABLE.
    """
    pricing = _lookup_pricing(model)
    cost_usd = compute_cost_usd(model, tokens_in, tokens_out)
    cost_inr = compute_cost_inr(cost_usd)
    return ExtractionCost(
        provider=pricing.provider,
        tier=pricing.tier,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
        cost_inr=cost_inr,
    )
