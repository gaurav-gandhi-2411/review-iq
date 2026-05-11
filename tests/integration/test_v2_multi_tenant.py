"""End-to-end multi-tenancy integration test for v2 endpoints.

Creates two orgs (A and B), issues keys for each, verifies that:
  - Org A's extraction is visible to Org A's GET /v2/reviews
  - Org B's GET /v2/reviews returns empty (RLS + app-level isolation)
  - GET /v2/insights for Org A reflects the extraction
  - GET /v2/insights for Org B shows zero total_extractions

Marked 'integration' — requires live Supabase DB and valid admin/keys in .env.
Run: uv run pytest tests/integration/test_v2_multi_tenant.py -v -m integration
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import psycopg2
import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

load_dotenv(Path(__file__).parents[2] / ".env")

from app.main import app  # noqa: E402

_USERNAME = os.environ["ADMIN_USERNAME"]
_PASSWORD = os.environ["TEST_ADMIN_PASSWORD"]
_AUTH = (_USERNAME, _PASSWORD)

client = TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_org(suffix: str) -> dict:
    slug = f"v2mt-{suffix}-{uuid.uuid4().hex[:6]}"
    r = client.post("/admin/organizations", json={"name": f"MT {suffix}", "slug": slug}, auth=_AUTH)
    assert r.status_code == 201, r.text
    return r.json()


def _create_key(org_id: str) -> dict:
    r = client.post(
        f"/admin/organizations/{org_id}/keys",
        json={"name": "mt-test", "quota": 100},
        auth=_AUTH,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _teardown_org(org_id: str) -> None:
    conn = psycopg2.connect(os.environ["SUPABASE_DIRECT_URL"])
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM public.organizations WHERE id = %s", (org_id,))
        conn.commit()
    finally:
        conn.close()


def _api_headers(raw_key: str) -> dict[str, str]:
    return {"X-API-Key": raw_key}


# ---------------------------------------------------------------------------
# Mock LLM so tests don't hit real providers
# ---------------------------------------------------------------------------


def _mock_llm_output():
    from app.core.schemas import ReviewExtractionLLMOutput, Sentiment, Urgency

    return (
        ReviewExtractionLLMOutput(
            product="Test Widget",
            stars=5,
            sentiment=Sentiment.positive,
            urgency=Urgency.low,
            topics=["quality", "value"],
            competitor_mentions=[],
            pros=["great build"],
            cons=[],
            language="en",
            confidence=0.95,
        ),
        "mock-model",
        42,
        150,   # tokens_in
        80,    # tokens_out
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_v2_tenant_isolation() -> None:
    """Org A's extractions are invisible to Org B and vice versa."""
    org_a = _create_org("a")
    org_b = _create_org("b")
    key_a = _create_key(org_a["id"])
    key_b = _create_key(org_b["id"])

    try:
        with patch("app.api.v2.extract.extract_with_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = _mock_llm_output()

            # Org A submits a review
            r = client.post(
                "/v2/extract",
                json={"text": "This widget is absolutely fantastic! Best purchase ever."},
                headers=_api_headers(key_a["raw_key"]),
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["product"] == "Test Widget"
            assert data["extraction_meta"]["org_id"] == org_a["id"]

        # Org A can see its own review
        r = client.get("/v2/reviews", headers=_api_headers(key_a["raw_key"]))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["org_id"] == org_a["id"]
        assert body["count"] == 1

        # Org B sees nothing
        r = client.get("/v2/reviews", headers=_api_headers(key_b["raw_key"]))
        assert r.status_code == 200, r.text
        body_b = r.json()
        assert body_b["org_id"] == org_b["id"]
        assert body_b["count"] == 0

        # Org A insights show 1 extraction
        r = client.get("/v2/insights", headers=_api_headers(key_a["raw_key"]))
        assert r.status_code == 200, r.text
        ins = r.json()
        assert ins["org_id"] == org_a["id"]
        assert ins["total_extractions"] == 1

        # Org B insights show 0
        r = client.get("/v2/insights", headers=_api_headers(key_b["raw_key"]))
        assert r.status_code == 200, r.text
        ins_b = r.json()
        assert ins_b["total_extractions"] == 0

    finally:
        _teardown_org(org_a["id"])
        _teardown_org(org_b["id"])


@pytest.mark.integration
def test_v2_cache_hit_same_org() -> None:
    """Submitting the same review twice returns cached result (no second LLM call)."""
    org = _create_org("cache")
    key = _create_key(org["id"])

    try:
        with patch("app.api.v2.extract.extract_with_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = _mock_llm_output()

            text = "Excellent quality, very happy with this product!"
            r1 = client.post("/v2/extract", json={"text": text}, headers=_api_headers(key["raw_key"]))
            r2 = client.post("/v2/extract", json={"text": text}, headers=_api_headers(key["raw_key"]))

        assert r1.status_code == 200
        assert r2.status_code == 200
        # LLM called only once (second hit served from cache)
        assert mock_llm.call_count == 1

        # Only one row in DB
        r = client.get("/v2/reviews", headers=_api_headers(key["raw_key"]))
        assert r.json()["count"] == 1

    finally:
        _teardown_org(org["id"])


@pytest.mark.integration
def test_v2_extract_invalid_key_returns_401() -> None:
    r = client.post(
        "/v2/extract",
        json={"text": "Some review text here for testing."},
        headers={"X-API-Key": "riq_live_" + "0" * 32},
    )
    assert r.status_code == 401


@pytest.mark.integration
def test_v2_reviews_missing_key_returns_401() -> None:
    r = client.get("/v2/reviews")
    assert r.status_code == 401


@pytest.mark.integration
def test_v2_extract_token_counts_recorded_in_db() -> None:
    """After a real extraction, tokens_in and tokens_out are > 0 in usage_records."""
    org = _create_org("tokens")
    key = _create_key(org["id"])

    try:
        r = client.post(
            "/v2/extract",
            json={"text": "This is a genuinely great product, highly recommend it to everyone!"},
            headers=_api_headers(key["raw_key"]),
        )
        assert r.status_code == 200, r.text

        # Check the usage_record written during auth
        conn = psycopg2.connect(os.environ["SUPABASE_DIRECT_URL"])
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT tokens_in, tokens_out, tokens_used "
                "FROM public.usage_records "
                "WHERE org_id = %s "
                "ORDER BY created_at DESC LIMIT 1",
                (org["id"],),
            )
            row = cur.fetchone()
        finally:
            conn.close()

        assert row is not None, "No usage_record found"
        tokens_in, tokens_out, tokens_used = row
        assert tokens_in > 0, f"tokens_in should be > 0, got {tokens_in}"
        assert tokens_out > 0, f"tokens_out should be > 0, got {tokens_out}"
        assert tokens_used == tokens_in + tokens_out, "tokens_used should equal tokens_in + tokens_out"

    finally:
        _teardown_org(org["id"])
