"""Reply drafting when Groq quota is exhausted: the BFF must answer 503 + Retry-After.

The existing BFF tests (test_bff_reply_graceful.py) patch `draft_reply` itself, so they never
exercise the engine -> router exception contract. These tests patch only the Groq call
(`_call_groq`) and drive the real engine, which is where two bare-error gaps lived:

1. English reply, large model quota-capped, small model ALSO quota-capped: the degrade call raised
   groq.RateLimitError out of the `except` block; the routers only catch RuntimeError, so the
   dashboard got a bare 500 instead of a 503.
2. Retry-After was a hardcoded 60/30 regardless of what Groq said. A tokens-per-day cap tells the
   caller how long to wait (observed: "try again in 5m58s"); the 503 now relays it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import groq
import httpx
import pytest
from app.auth.api_key import ApiKeyContext
from app.auth.session import require_session
from app.main import create_app

_CTX = ApiKeyContext(
    org_id="test-org",
    api_key_id="test-key-id",
    key_name="test-key",
    usage_record_id="test-usage-id",
)

# A supplied extraction (as the dashboard sends) keeps the engine from making its own grounding
# extraction call, so only `_call_groq` is on the path under test and nothing touches the network.
_EXTRACTION = {
    "product": "test product",
    "cons": ["quality"],
    "topics": ["quality"],
    "pros": [],
    "feature_requests": [],
    "competitor_mentions": [],
    "language": "en",
    "urgency": "low",
}
_ENGLISH = {
    "text": "The stitching came apart after one wash.",
    "tone": "apologetic",
    "extraction": _EXTRACTION,
}
_HINGLISH = {
    "text": "Yaar battery bahut kharab hai ekdum",
    "tone": "apologetic",
    "extraction": _EXTRACTION,
}


def _rate_limit_error(retry_after: str | None = "321") -> groq.RateLimitError:
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    response = httpx.Response(
        429,
        headers=headers,
        request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
    )
    return groq.RateLimitError(
        "Rate limit reached ... on tokens per day (TPD): Limit 200000, Used 199399",
        response=response,
        body=None,
    )


@pytest.fixture()
async def client() -> httpx.AsyncClient:
    app = create_app()
    app.dependency_overrides[require_session] = lambda: _CTX
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    from app.api.bff import router as bff_router

    bff_router._DRAFT_CACHE.clear()


@pytest.mark.asyncio
async def test_english_both_models_capped_is_503_not_500(client: httpx.AsyncClient) -> None:
    """Large AND small quota-capped (English) -> 503 + Retry-After, never an unhandled 429."""
    with patch(
        "app.core.reply.engine._call_groq",
        new=AsyncMock(side_effect=_rate_limit_error()),
    ):
        resp = await client.post("/bff/reply", json=_ENGLISH)

    assert resp.status_code == 503
    assert int(resp.headers["Retry-After"]) == 321
    assert "try again" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_retry_after_relays_groq_reset_time(client: httpx.AsyncClient) -> None:
    """The 503 for a capped Hinglish reply carries Groq's own reset time, not a constant 60."""
    with patch(
        "app.core.reply.engine._call_groq",
        new=AsyncMock(side_effect=_rate_limit_error("321")),
    ):
        resp = await client.post("/bff/reply", json=_HINGLISH)

    assert resp.status_code == 503
    assert int(resp.headers["Retry-After"]) == 321


@pytest.mark.asyncio
async def test_retry_after_defaults_when_provider_gives_none(client: httpx.AsyncClient) -> None:
    """No reset hint from the provider -> a finite default, never a missing header."""
    with patch(
        "app.core.reply.engine._call_groq",
        new=AsyncMock(side_effect=_rate_limit_error(None)),
    ):
        resp = await client.post("/bff/reply", json=_HINGLISH)

    assert resp.status_code == 503
    assert int(resp.headers["Retry-After"]) == 60


@pytest.mark.asyncio
async def test_retry_after_is_capped(client: httpx.AsyncClient) -> None:
    """A far-future reset (a day) is capped so a client never sleeps for hours blindly."""
    with patch(
        "app.core.reply.engine._call_groq",
        new=AsyncMock(side_effect=_rate_limit_error("86400")),
    ):
        resp = await client.post("/bff/reply", json=_HINGLISH)

    assert resp.status_code == 503
    assert int(resp.headers["Retry-After"]) == 3600
