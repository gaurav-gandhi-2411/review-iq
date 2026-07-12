"""Unit tests for GET /v2/insights/batch-defects.

All storage and auth calls are mocked — no live DB connection. The detector algorithm itself
runs for real (pure, fast, no I/O) against small hand-built row sets.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import httpx
import pytest
from app.auth.api_key import ApiKeyContext, require_api_key

_ORG_ID = str(uuid.uuid4())
_KEY_ID = str(uuid.uuid4())
_USAGE_ID = str(uuid.uuid4())

_CTX = ApiKeyContext(
    org_id=_ORG_ID,
    api_key_id=_KEY_ID,
    key_name="test-key",
    usage_record_id=_USAGE_ID,
)

_NOW = datetime(2026, 6, 1, tzinfo=UTC)

# 6 clustered negative "battery" mentions -> one obvious flag when the detector is enabled.
_SPIKE_ROWS = [
    {
        "id": f"r{i}",
        "product": "Widget Pro",
        "topics": ["battery"],
        "sentiment": "negative",
        "review_date": _NOW,
    }
    for i in range(6)
]


def _settings(enabled: bool) -> MagicMock:
    settings = MagicMock()
    settings.enable_batch_defect_detector = enabled
    return settings


@pytest.fixture()
async def client() -> httpx.AsyncClient:
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[require_api_key] = lambda: _CTX
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


class TestFlagGating:
    async def test_404_when_disabled(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.get_settings", return_value=_settings(False)):
            resp = await client.get("/v2/insights/batch-defects")
        assert resp.status_code == 404

    async def test_200_with_empty_flags_when_enabled_and_no_data(
        self, client: httpx.AsyncClient
    ) -> None:
        with (
            patch("app.api.v2.insights.get_settings", return_value=_settings(True)),
            patch("app.api.v2.insights.list_dated_extractions_pg", return_value=[]),
        ):
            resp = await client.get("/v2/insights/batch-defects")
        assert resp.status_code == 200
        body = resp.json()
        assert body["flags"] == []
        assert body["org_id"] == _ORG_ID


class TestHappyPath:
    async def test_returns_flag_for_obvious_spike(self, client: httpx.AsyncClient) -> None:
        with (
            patch("app.api.v2.insights.get_settings", return_value=_settings(True)),
            patch("app.api.v2.insights.list_dated_extractions_pg", return_value=_SPIKE_ROWS),
        ):
            resp = await client.get("/v2/insights/batch-defects")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["flags"]) == 1
        assert body["flags"][0]["product_id"] == "Widget Pro"
        assert body["flags"][0]["topic"] == "battery"
        assert "note" in body
        assert body["window"]["spike_window_days"] == 10

    async def test_min_confidence_filters_out_flag(self, client: httpx.AsyncClient) -> None:
        # A "weak" spike (4 in-window + 3 outside, same topic) empirically scores confidence
        # 0.714 -- a moderate value, unlike _SPIKE_ROWS' all-same-instant confidence of 1.0
        # (which no min_confidence <= 1.0 could ever filter out).
        weak_rows = [
            {
                "id": f"w{i}",
                "product": "Weak",
                "topics": ["battery"],
                "sentiment": "negative",
                "review_date": _NOW,
            }
            for i in range(4)
        ] + [
            {
                "id": f"o{i}",
                "product": "Weak",
                "topics": ["battery"],
                "sentiment": "negative",
                "review_date": _NOW - timedelta(days=20 * (i + 1)),
            }
            for i in range(3)
        ]
        with (
            patch("app.api.v2.insights.get_settings", return_value=_settings(True)),
            patch("app.api.v2.insights.list_dated_extractions_pg", return_value=weak_rows),
        ):
            resp = await client.get("/v2/insights/batch-defects", params={"min_confidence": 0.9})
        assert resp.status_code == 200
        assert resp.json()["flags"] == []

    async def test_limit_truncates_flags(self, client: httpx.AsyncClient) -> None:
        two_products_rows = _SPIKE_ROWS + [
            {**row, "id": f"b{i}", "product": "Widget Lite"} for i, row in enumerate(_SPIKE_ROWS)
        ]
        with (
            patch("app.api.v2.insights.get_settings", return_value=_settings(True)),
            patch(
                "app.api.v2.insights.list_dated_extractions_pg",
                return_value=two_products_rows,
            ),
        ):
            resp = await client.get("/v2/insights/batch-defects", params={"limit": 1})
        assert resp.status_code == 200
        assert len(resp.json()["flags"]) == 1
