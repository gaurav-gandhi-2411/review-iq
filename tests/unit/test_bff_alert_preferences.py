"""Unit tests for PUT /bff/alerts/preferences/{event_type} -- specifically the daily_digest
rejection guard added alongside the Phase 2 detector sweep (see app/api/bff/alerts.py).

No dedicated test file existed for this endpoint before -- this covers the one behavior change
made to it (rejecting daily_digest for non-digestible event types), not a full re-test of every
existing behavior.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import httpx
import pytest
from app.auth.api_key import ApiKeyContext
from app.auth.session import require_session

_ORG_ID = str(uuid.uuid4())
_CTX = ApiKeyContext(
    org_id=_ORG_ID, api_key_id=str(uuid.uuid4()), key_name="test-key", usage_record_id=""
)


@pytest.fixture()
async def client() -> httpx.AsyncClient:
    from app.main import app

    app.dependency_overrides[require_session] = lambda: _CTX
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "event_type", ["batch_defect", "fake_campaign", "fake_cluster", "topic_spike"]
)
async def test_daily_digest_rejected_for_non_digestible_types(
    client: httpx.AsyncClient, event_type: str
) -> None:
    resp = await client.put(
        f"/bff/alerts/preferences/{event_type}",
        json={"enabled": True, "frequency": "daily_digest"},
    )
    assert resp.status_code == 422
    assert "daily_digest" in resp.json()["detail"]


@pytest.mark.parametrize("event_type", ["high_urgency", "likely_fake"])
async def test_daily_digest_still_accepted_for_digestible_types(
    client: httpx.AsyncClient, event_type: str
) -> None:
    with patch("app.api.bff.alerts.upsert_preference_pg", return_value=None):
        resp = await client.put(
            f"/bff/alerts/preferences/{event_type}",
            json={"enabled": True, "frequency": "daily_digest"},
        )
    assert resp.status_code == 200


async def test_immediate_still_accepted_for_new_event_types(client: httpx.AsyncClient) -> None:
    """The new event types support frequency='immediate' (the default) -- only daily_digest is
    rejected."""
    with patch("app.api.bff.alerts.upsert_preference_pg", return_value=None):
        resp = await client.put(
            "/bff/alerts/preferences/batch_defect",
            json={"enabled": True, "frequency": "immediate"},
        )
    assert resp.status_code == 200
