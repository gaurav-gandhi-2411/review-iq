"""Trailing-24h token ledger and its wiring into POST /demo/extract. Injected clock, no network."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from app.core import token_ledger
from app.core.schemas import ReviewExtractionLLMOutput, Sentiment, Urgency
from app.core.token_ledger import (
    WINDOW_SECONDS,
    TrailingTokenLedger,
    demo_ceiling_tokens,
    is_tpd_message,
    retry_hint_seconds,
)
from fastapi.testclient import TestClient

TPD_MSG = (
    "Rate limit reached for model `openai/gpt-oss-20b` in organization `org_x` service tier "
    "`on_demand` on tokens per day (TPD): Limit 200000, Used 199445, Requested 972. "
    "Please try again in 3m0.144s."
)
_OUT = ReviewExtractionLLMOutput(
    product="W",
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


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


def test_under_ceiling_admitted_over_ceiling_refused() -> None:
    led = TrailingTokenLedger(Clock())
    assert led.try_admit(3000, 10_000) is not None
    assert led.try_admit(3000, 10_000) is not None
    assert led.try_admit(3000, 10_000) is not None
    assert led.used() == 9000
    assert led.try_admit(3000, 10_000) is None  # 12,000 > 10,000
    assert led.used() == 9000  # a refusal reserves nothing


def test_ages_out_after_24h_and_readmits() -> None:
    clock = Clock()
    led = TrailingTokenLedger(clock)
    led.try_admit(6000, 10_000)
    clock.t += 3600
    led.try_admit(3000, 10_000)
    assert led.try_admit(3000, 10_000) is None
    # Not free at 23h59m: rolling, not a midnight reset.
    clock.t += WINDOW_SECONDS - 3600 - 1
    assert led.try_admit(3000, 10_000) is None
    clock.t += 2  # the 6,000 entry is now > 24h old; 3,000 remains
    assert led.used() == 3000
    assert led.try_admit(3000, 10_000) is not None


def test_retry_after_is_time_until_enough_ages_out() -> None:
    clock = Clock()
    led = TrailingTokenLedger(clock)
    led.try_admit(6000, 10_000)
    clock.t += 3600
    led.try_admit(3000, 10_000)
    # Need 3000 headroom: only dropping the first entry (6000) suffices -> 23h from now.
    assert led.retry_after_seconds(3000, 10_000) == pytest.approx(WINDOW_SECONDS - 3600)
    assert led.retry_after_seconds(1000, 10_000) == 0.0


def test_settle_replaces_estimate_with_actual() -> None:
    led = TrailingTokenLedger(Clock())
    entry = led.try_admit(3300, 10_000)
    assert entry is not None
    led.settle(entry, 1200)
    assert led.used() == 1200


def test_observed_429_blocks_until_hint_elapses() -> None:
    clock = Clock()
    led = TrailingTokenLedger(clock)
    led.observe_rate_limit(retry_hint_seconds(TPD_MSG))
    assert led.try_admit(10, 1_000_000) is None
    assert led.retry_after_seconds(10, 1_000_000) == pytest.approx(180.144)
    clock.t += 181
    assert led.try_admit(10, 1_000_000) is not None


def test_observe_groq_429_only_for_tpd(monkeypatch: pytest.MonkeyPatch) -> None:
    led = TrailingTokenLedger(Clock())
    monkeypatch.setattr(token_ledger, "demo_ledger", led)
    token_ledger.observe_groq_429("Rate limit on tokens per minute (TPM): try again in 4s")
    assert led.blocked_for() == 0
    token_ledger.observe_groq_429(TPD_MSG)
    assert led.blocked_for() == pytest.approx(180.144)
    assert is_tpd_message(TPD_MSG)
    assert retry_hint_seconds("no hint") is None


def test_ceiling_keeps_production_reserve() -> None:
    assert demo_ceiling_tokens(200_000, 0.25, 3) == 16_666
    # A misconfigured share can never take more than half the pool.
    assert demo_ceiling_tokens(200_000, 0.9, 1) == 100_000
    assert demo_ceiling_tokens(200_000, -1.0, 1) == 0


# --- endpoint wiring ---------------------------------------------------------------------


def _post(text: str) -> object:
    from app.api.demo import demo_cache_clear
    from app.main import app

    demo_cache_clear()
    with (
        patch(
            "app.api.demo.extract_with_llm",
            new=AsyncMock(return_value=(_OUT, "openai/gpt-oss-20b", 10, 1000, 500, False)),
        ) as llm,
    ):
        resp = TestClient(app, raise_server_exceptions=False).post(
            "/demo/extract", json={"text": text}
        )
    return resp, llm


def test_endpoint_refuses_over_ceiling_with_rolling_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = Clock()
    led = TrailingTokenLedger(clock)
    monkeypatch.setattr("app.api.demo.demo_ledger", led)
    ceiling = demo_ceiling_tokens(200_000, 0.25, 3)
    led.try_admit(ceiling - 100, ceiling)  # almost full; a 3,300-token estimate cannot fit

    resp, llm = _post("unique ledger-full review")
    assert resp.status_code == 429
    assert llm.call_count == 0
    detail = resp.json()["detail"]
    assert "rolling last 24 hours" in detail
    assert "midnight UTC" not in detail
    assert int(resp.headers["retry-after"]) >= 60


def test_endpoint_admits_then_ages_out(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    led = TrailingTokenLedger(clock)
    monkeypatch.setattr("app.api.demo.demo_ledger", led)
    ceiling = demo_ceiling_tokens(200_000, 0.25, 3)
    led.try_admit(ceiling - 100, ceiling)

    assert _post("unique age-out review a")[0].status_code == 429
    clock.t += WINDOW_SECONDS + 1  # the earlier usage has aged out
    resp, llm = _post("unique age-out review b")
    assert resp.status_code == 200
    assert llm.call_count == 1
    assert led.used() == 1500  # estimate settled to the real tokens_in + tokens_out


def test_endpoint_refuses_during_recent_429_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    led = TrailingTokenLedger(Clock())
    monkeypatch.setattr("app.api.demo.demo_ledger", led)
    led.observe_rate_limit(600.0)
    resp, llm = _post("unique 429-hold review")
    assert resp.status_code == 429
    assert llm.call_count == 0
    assert int(resp.headers["retry-after"]) == 600


def test_db_budget_refusal_releases_ledger_reservation(monkeypatch: pytest.MonkeyPatch) -> None:
    led = TrailingTokenLedger(Clock())
    monkeypatch.setattr("app.api.demo.demo_ledger", led)
    with patch("app.api.demo.check_and_increment_demo_request_pg", return_value=False):
        resp, _ = _post("unique db-refusal review")
    assert resp.status_code == 429
    assert led.used() == 0
