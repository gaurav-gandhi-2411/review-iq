"""Unit tests for app.core.pricing — cost math and pricing-table lookups."""

from __future__ import annotations

import pytest
from app.core.pricing import (
    PRICING_TABLE,
    USD_TO_INR_RATE,
    UnknownModelError,
    compute_cost_inr,
    compute_cost_usd,
    price_extraction,
)

# ---------------------------------------------------------------------------
# compute_cost_usd — known model, known token counts
# ---------------------------------------------------------------------------


def test_compute_cost_usd_small_model_known_value() -> None:
    """1000 tokens in + 1000 tokens out at $0.05/$0.08 per 1M tokens."""
    cost = compute_cost_usd("llama-3.1-8b-instant", tokens_in=1000, tokens_out=1000)
    expected = (1000 / 1_000_000) * 0.05 + (1000 / 1_000_000) * 0.08
    assert cost == pytest.approx(expected)
    assert cost == pytest.approx(0.00013)


def test_compute_cost_usd_large_model_known_value() -> None:
    """1M tokens in + 1M tokens out at $0.59/$0.79 per 1M tokens — exact price."""
    cost = compute_cost_usd("llama-3.3-70b-versatile", tokens_in=1_000_000, tokens_out=1_000_000)
    assert cost == pytest.approx(0.59 + 0.79)


def test_compute_cost_usd_zero_tokens_is_zero_cost() -> None:
    cost = compute_cost_usd("llama-3.1-8b-instant", tokens_in=0, tokens_out=0)
    assert cost == 0.0


def test_compute_cost_usd_only_input_tokens() -> None:
    cost = compute_cost_usd("llama-3.1-8b-instant", tokens_in=1_000_000, tokens_out=0)
    assert cost == pytest.approx(0.05)


def test_compute_cost_usd_current_small_model_gpt_oss_20b() -> None:
    """The model actually live in production today (config.py's groq_model_small
    default, post-2026-08-16 deprecation) — $0.075/$0.30 per 1M tokens."""
    cost = compute_cost_usd("openai/gpt-oss-20b", tokens_in=1_000_000, tokens_out=1_000_000)
    assert cost == pytest.approx(0.075 + 0.30)


def test_compute_cost_usd_current_large_model_gpt_oss_120b() -> None:
    """The model actually live in production today (config.py's groq_model_large
    default, post-2026-08-16 deprecation) — $0.15/$0.60 per 1M tokens."""
    cost = compute_cost_usd("openai/gpt-oss-120b", tokens_in=1_000_000, tokens_out=1_000_000)
    assert cost == pytest.approx(0.15 + 0.60)


def test_deprecated_and_current_models_both_present() -> None:
    # Deprecated models are kept (not deleted) so historical rows/cassettes
    # referencing them by name don't hit UnknownModelError; current models must
    # also be present since they're what actually serves live traffic today.
    for model in (
        "llama-3.1-8b-instant",
        "llama-3.3-70b-versatile",
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
    ):
        assert model in PRICING_TABLE


# ---------------------------------------------------------------------------
# compute_cost_inr — conversion correctness
# ---------------------------------------------------------------------------


def test_compute_cost_inr_applies_pinned_rate() -> None:
    assert compute_cost_inr(1.0) == pytest.approx(USD_TO_INR_RATE)
    assert compute_cost_inr(2.5) == pytest.approx(2.5 * USD_TO_INR_RATE)


def test_compute_cost_inr_zero_usd_is_zero_inr() -> None:
    assert compute_cost_inr(0.0) == 0.0


# ---------------------------------------------------------------------------
# Unrecognized model — must raise, never silently default to zero cost.
# ---------------------------------------------------------------------------


def test_compute_cost_usd_unknown_model_raises() -> None:
    with pytest.raises(UnknownModelError, match="No pricing entry"):
        compute_cost_usd("some-model-nobody-priced", tokens_in=100, tokens_out=100)


def test_price_extraction_unknown_model_raises() -> None:
    with pytest.raises(UnknownModelError):
        price_extraction("gpt-99-mystery", tokens_in=1, tokens_out=1)


def test_unknown_model_error_is_a_value_error() -> None:
    """Callers that only catch ValueError (existing convention elsewhere in this repo)
    still catch this — UnknownModelError is a narrower subclass, not an unrelated type."""
    with pytest.raises(ValueError):
        compute_cost_usd("totally-unpriced-model", tokens_in=1, tokens_out=1)


# ---------------------------------------------------------------------------
# price_extraction — bundles provider/tier/cost_usd/cost_inr correctly
# ---------------------------------------------------------------------------


def test_price_extraction_small_model_bundles_correct_fields() -> None:
    result = price_extraction("llama-3.1-8b-instant", tokens_in=1000, tokens_out=500)

    assert result.provider == "groq"
    assert result.tier == "small"
    assert result.model == "llama-3.1-8b-instant"
    assert result.tokens_in == 1000
    assert result.tokens_out == 500
    assert result.cost_usd == pytest.approx(compute_cost_usd("llama-3.1-8b-instant", 1000, 500))
    assert result.cost_inr == pytest.approx(compute_cost_inr(result.cost_usd))


def test_price_extraction_large_model_tier_is_large() -> None:
    result = price_extraction("llama-3.3-70b-versatile", tokens_in=1000, tokens_out=1000)
    assert result.tier == "large"
    assert result.provider == "groq"


def test_price_extraction_gemini_fallback_tier() -> None:
    result = price_extraction("gemini-2.0-flash", tokens_in=1000, tokens_out=1000)
    assert result.provider == "gemini"
    assert result.tier == "fallback"


# ---------------------------------------------------------------------------
# Pricing table sanity — every entry has plausible, non-negative numbers.
# ---------------------------------------------------------------------------


def test_pricing_table_all_entries_have_nonnegative_prices() -> None:
    for model, pricing in PRICING_TABLE.items():
        assert pricing.usd_per_million_input >= 0, model
        assert pricing.usd_per_million_output >= 0, model
        assert pricing.provider, model
        assert pricing.tier, model
        assert pricing.as_of, model
        assert pricing.source, model


def test_pricing_table_covers_production_router_models() -> None:
    """The two Groq tiers actually wired into app/core/config.py's default
    groq_model_small/groq_model_large must always have a pricing entry."""
    assert "llama-3.1-8b-instant" in PRICING_TABLE
    assert "llama-3.3-70b-versatile" in PRICING_TABLE
