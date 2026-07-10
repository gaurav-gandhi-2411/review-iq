"""Minimal unit test for GET /bff/insights/batch-defects.

Mirrors app/api/v2/insights.py::batch_defects exactly (see that file's docstring) -- this file
only proves the BFF wiring (session auth dependency, flag gate) is correct, not the detector
algorithm itself (already covered by test_batch_defect_detector.py and
test_v2_insights_batch_defects.py). /bff/insights/trends itself has no dedicated test file, but
this is brand-new wiring without months of production mileage, so one smoke test earns its keep.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import httpx
import pytest
from app.auth.api_key import ApiKeyContext
from app.auth.session import require_session_read

_ORG_ID = str(uuid.uuid4())
_CTX = ApiKeyContext(
    org_id=_ORG_ID, api_key_id=str(uuid.uuid4()), key_name="test-key", usage_record_id=""
)


def _settings(enabled: bool) -> MagicMock:
    settings = MagicMock()
    settings.enable_batch_defect_detector = enabled
    return settings


@pytest.fixture()
async def client() -> httpx.AsyncClient:
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[require_session_read] = lambda: _CTX
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


async def test_404_when_disabled(client: httpx.AsyncClient) -> None:
    with patch("app.api.bff.router.get_settings", return_value=_settings(False)):
        resp = await client.get("/bff/insights/batch-defects")
    assert resp.status_code == 404


async def test_200_when_enabled(client: httpx.AsyncClient) -> None:
    with (
        patch("app.api.bff.router.get_settings", return_value=_settings(True)),
        patch("app.api.bff.router.list_dated_extractions_pg", return_value=[]),
    ):
        resp = await client.get("/bff/insights/batch-defects")
    assert resp.status_code == 200
    assert resp.json()["org_id"] == _ORG_ID
    assert resp.json()["flags"] == []
