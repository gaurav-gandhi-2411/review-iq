"""Unit tests for POST /demo/extract's global (cross-IP) daily quota and cost recording.

See app/api/demo.py's DEMO_DAILY_REQUEST_BUDGET docstring for why this exists: the
per-IP slowapi limit has no cross-IP cap, and the demo endpoint shares the SAME Groq
API key -- and its SAME free-tier daily budget -- as every real paying customer's
/v2/extract call.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core.schemas import ReviewExtractionLLMOutput, Sentiment, Urgency
from fastapi.testclient import TestClient

_LLM_OUTPUT = ReviewExtractionLLMOutput(
    product="Quota Widget",
    stars=None,
    sentiment=Sentiment.positive,
    urgency=Urgency.low,
    topics=[],
    competitor_mentions=[],
    pros=["good"],
    cons=[],
    feature_requests=[],
    language="en",
    confidence=0.9,
)


def _client() -> TestClient:
    from app.api.demo import demo_cache_clear
    from app.main import app

    demo_cache_clear()
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Quota gate
# ---------------------------------------------------------------------------


def test_quota_available_returns_200() -> None:
    client = _client()
    with (
        patch("app.api.demo.check_and_increment_demo_request_pg", return_value=True),
        patch("app.api.demo.record_demo_extraction_cost_pg", return_value=None),
        patch(
            "app.api.demo.extract_with_llm",
            new=AsyncMock(return_value=(_LLM_OUTPUT, "openai/gpt-oss-20b", 10, 100, 20, False)),
        ),
    ):
        resp = client.post("/demo/extract", json={"text": "unique quota-available review"})
    assert resp.status_code == 200


def test_quota_exhausted_returns_429_before_any_llm_call() -> None:
    """When the daily budget is used up, no LLM call happens at all -- the whole point
    of gating BEFORE extract_with_llm is that a scripted abuser can't burn tokens past
    the cap even if they hit the endpoint directly (bypassing the per-IP window)."""
    client = _client()
    with (
        patch("app.api.demo.check_and_increment_demo_request_pg", return_value=False),
        patch("app.api.demo.extract_with_llm", new=AsyncMock()) as mock_llm,
    ):
        resp = client.post("/demo/extract", json={"text": "unique quota-exhausted review"})

    assert resp.status_code == 429
    assert mock_llm.call_count == 0, "LLM must not be called once the daily quota is exhausted"
    assert "daily capacity" in resp.json()["detail"]


def test_quota_check_db_error_fails_closed() -> None:
    """A DB error in the quota check itself must reject the request (fail closed), not
    silently let unlimited demo traffic through -- see _check_demo_quota's docstring."""
    client = _client()
    with (
        patch(
            "app.api.demo.check_and_increment_demo_request_pg",
            side_effect=RuntimeError("connection refused"),
        ),
        patch("app.api.demo.extract_with_llm", new=AsyncMock()) as mock_llm,
    ):
        resp = client.post("/demo/extract", json={"text": "unique db-error review"})

    assert resp.status_code == 429
    assert mock_llm.call_count == 0


def test_cache_hit_does_not_consume_quota() -> None:
    """A cache-served response must not call the quota check at all -- repeated
    identical text is free (see app/api/demo.py's LRU cache), not just free of a fresh
    LLM call."""
    client = _client()
    text = "unique cache-then-quota review"
    with (
        patch("app.api.demo.check_and_increment_demo_request_pg", return_value=True) as mock_quota,
        patch("app.api.demo.record_demo_extraction_cost_pg", return_value=None),
        patch(
            "app.api.demo.extract_with_llm",
            new=AsyncMock(return_value=(_LLM_OUTPUT, "openai/gpt-oss-20b", 10, 100, 20, False)),
        ),
    ):
        client.post("/demo/extract", json={"text": text})
        client.post("/demo/extract", json={"text": text})

    assert mock_quota.call_count == 1, "Quota should only be checked on the real (first) call"


# ---------------------------------------------------------------------------
# Cost recording
# ---------------------------------------------------------------------------


def test_successful_extraction_records_cost_with_correct_args() -> None:
    client = _client()
    with (
        patch("app.api.demo.check_and_increment_demo_request_pg", return_value=True),
        patch("app.api.demo.record_demo_extraction_cost_pg", return_value=None) as mock_cost,
        patch(
            "app.api.demo.extract_with_llm",
            new=AsyncMock(return_value=(_LLM_OUTPUT, "openai/gpt-oss-120b", 10, 1500, 150, False)),
        ),
    ):
        resp = client.post("/demo/extract", json={"text": "unique cost-recording review"})

    assert resp.status_code == 200
    mock_cost.assert_called_once()
    args = mock_cost.call_args.args
    assert args[0] == "groq"  # provider, derived from the pricing table entry
    assert args[1] == "openai/gpt-oss-120b"  # model
    assert args[2] == "large"  # tier, derived from the pricing table entry
    assert args[4] == 1500  # tokens_in
    assert args[5] == 150  # tokens_out


def test_unknown_model_pricing_does_not_fail_the_response() -> None:
    """A pricing gap must never 500 a request that already succeeded -- same tolerance
    as app/api/v2/extract.py's org-path recording."""
    client = _client()
    with (
        patch("app.api.demo.check_and_increment_demo_request_pg", return_value=True),
        patch("app.api.demo.record_demo_extraction_cost_pg") as mock_cost,
        patch(
            "app.api.demo.extract_with_llm",
            new=AsyncMock(
                return_value=(_LLM_OUTPUT, "some-brand-new-unpriced-model", 10, 100, 20, False)
            ),
        ),
    ):
        resp = client.post("/demo/extract", json={"text": "unique unpriced-model review"})

    assert resp.status_code == 200
    mock_cost.assert_not_called()


# ---------------------------------------------------------------------------
# Self-expiring budget override (Session 12 P7a)
# ---------------------------------------------------------------------------


def _settings(*, budget: int, expires_at: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        demo_daily_request_budget=budget,
        demo_daily_request_budget_override_expires_at=expires_at,
    )


def test_default_budget_returned_unconditionally() -> None:
    from app.api.demo import _effective_demo_daily_budget

    with patch("app.api.demo.get_settings", return_value=_settings(budget=50)):
        assert _effective_demo_daily_budget() == 50


def test_override_honored_before_expiry() -> None:
    from app.api.demo import _effective_demo_daily_budget

    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    with patch("app.api.demo.get_settings", return_value=_settings(budget=0, expires_at=future)):
        assert _effective_demo_daily_budget() == 0


def test_override_falls_back_to_default_once_expired() -> None:
    """The whole point of the TTL: an override left in place past its expiry must not
    keep suppressing real demo traffic -- it silently reverts to the safe default."""
    from app.api.demo import _effective_demo_daily_budget

    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    with patch("app.api.demo.get_settings", return_value=_settings(budget=0, expires_at=past)):
        assert _effective_demo_daily_budget() == 50


def test_override_without_ttl_falls_back_to_default() -> None:
    """A non-default budget with no expiry set at all is ignored, not honored forever --
    fails toward the safe default, not toward the override, on any ambiguity."""
    from app.api.demo import _effective_demo_daily_budget

    with patch("app.api.demo.get_settings", return_value=_settings(budget=0, expires_at="")):
        assert _effective_demo_daily_budget() == 50


def test_override_with_unparseable_ttl_falls_back_to_default() -> None:
    from app.api.demo import _effective_demo_daily_budget

    with patch(
        "app.api.demo.get_settings",
        return_value=_settings(budget=0, expires_at="not-a-timestamp"),
    ):
        assert _effective_demo_daily_budget() == 50


def test_override_accepts_z_suffix_utc_timestamp() -> None:
    """DEMO_DAILY_REQUEST_BUDGET_OVERRIDE_EXPIRES_AT is meant to be set by hand (env var,
    runbook), so a bare 'Z' suffix (not offset-qualified isoformat) must parse."""
    from app.api.demo import _effective_demo_daily_budget

    future_z = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with patch("app.api.demo.get_settings", return_value=_settings(budget=0, expires_at=future_z)):
        assert _effective_demo_daily_budget() == 0


def test_cost_recording_db_error_does_not_fail_the_response() -> None:
    client = _client()
    with (
        patch("app.api.demo.check_and_increment_demo_request_pg", return_value=True),
        patch(
            "app.api.demo.record_demo_extraction_cost_pg",
            side_effect=RuntimeError("connection refused"),
        ),
        patch(
            "app.api.demo.extract_with_llm",
            new=AsyncMock(return_value=(_LLM_OUTPUT, "openai/gpt-oss-20b", 10, 100, 20, False)),
        ),
    ):
        resp = client.post("/demo/extract", json={"text": "unique cost-db-error review"})

    assert resp.status_code == 200
