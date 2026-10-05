"""Unit tests for app.api.v2.dataset endpoints."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
from app.auth.api_key import ApiKeyContext, require_api_key
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_ORG = str(uuid.uuid4())
_CTX = ApiKeyContext(org_id=_ORG, api_key_id="k1", key_name="t", usage_record_id="u1")

_SAMPLE_RECORD: dict[str, object] = {
    "review_id": "a" * 64,
    "review_text": "Great product!",
    "extracted_at": "2026-06-13T12:00:00",
    "created_at": "2026-06-13T12:00:00",
    "extraction": {"product": "Widget", "sentiment": "positive"},
    "authenticity": None,
    "corrections": [],
}


@pytest.fixture
def client() -> TestClient:
    from app.main import app

    app.dependency_overrides[require_api_key] = lambda: _CTX
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.fixture
def client_no_auth() -> TestClient:
    from app.main import app

    app.dependency_overrides.clear()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /v2/dataset
# ---------------------------------------------------------------------------


class TestGetDataset:
    def test_returns_200_with_records(self, client: TestClient) -> None:
        """Happy path: mocked DB → 200, count=1, records non-empty."""
        with patch(
            "app.api.v2.dataset.get_dataset_page",
            new=MagicMock(return_value=[_SAMPLE_RECORD]),
        ):
            resp = client.get("/v2/dataset")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert len(body["records"]) == 1

    def test_unauthenticated_returns_401(self, client_no_auth: TestClient) -> None:
        """Missing API key returns 401."""
        resp = client_no_auth.get("/v2/dataset")
        assert resp.status_code == 401

    def test_limit_offset_passed_through(self, client: TestClient) -> None:
        """Query params limit and offset are forwarded to get_dataset_page."""
        mock_fn = MagicMock(return_value=[])
        with patch("app.api.v2.dataset.get_dataset_page", new=mock_fn):
            resp = client.get("/v2/dataset", params={"limit": 10, "offset": 5})

        assert resp.status_code == 200
        mock_fn.assert_called_once_with(_ORG, 10, 5)

    def test_db_error_returns_500(self, client: TestClient) -> None:
        """DB exception maps to HTTP 500."""
        with patch(
            "app.api.v2.dataset.get_dataset_page",
            new=MagicMock(side_effect=Exception("db down")),
        ):
            resp = client.get("/v2/dataset")

        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /v2/dataset/export
# ---------------------------------------------------------------------------


class TestExportDataset:
    def test_jsonl_export_returns_200(self, client: TestClient) -> None:
        """Happy path: streamed JSONL, correct content-type, valid JSON content."""
        line = json.dumps(_SAMPLE_RECORD) + "\n"
        with patch(
            "app.api.v2.dataset.iter_dataset_jsonl",
            new=MagicMock(return_value=iter([line])),
        ):
            resp = client.get("/v2/dataset/export")

        assert resp.status_code == 200
        assert "application/x-ndjson" in resp.headers["content-type"]
        # Content should be valid JSON when parsed.
        parsed = json.loads(resp.content.decode().strip())
        assert parsed["review_id"] == "a" * 64

    def test_invalid_format_returns_422(self, client: TestClient) -> None:
        """Unsupported format=csv returns 422."""
        resp = client.get("/v2/dataset/export", params={"format": "csv"})
        assert resp.status_code == 422

    def test_unauthenticated_export_returns_401(self, client_no_auth: TestClient) -> None:
        """Missing API key on export endpoint returns 401."""
        resp = client_no_auth.get("/v2/dataset/export")
        assert resp.status_code == 401


class TestAuthenticityDeprecated:
    def test_authenticity_key_present_and_null(self, client: TestClient) -> None:
        """Old clients still find the key; it is null."""
        with patch(
            "app.api.v2.dataset.get_dataset_page",
            new=MagicMock(return_value=[_SAMPLE_RECORD]),
        ):
            body = client.get("/v2/dataset").json()
        assert "authenticity" in body["records"][0]
        assert body["records"][0]["authenticity"] is None

    def test_openapi_marks_authenticity_deprecated(self, client: TestClient) -> None:
        """OpenAPI carries the deprecation, the removal policy, and no stale summary."""
        op = client.get("/openapi.json").json()["paths"]["/v2/dataset"]["get"]
        assert "authenticity" not in op["summary"]
        field = op["responses"]["200"]["content"]["application/json"]["schema"]["properties"][
            "records"
        ]["items"]["properties"]["authenticity"]
        assert field["deprecated"] is True
        assert (
            "removed in a future versioned change; not before 2027-01-01." in (field["description"])
        )
        example = op["responses"]["200"]["content"]["application/json"]["example"]
        # FastAPI drops JSON nulls when rendering openapi.json, so the null example key is
        # omitted there; what matters is that no stale score object is advertised.
        assert not isinstance(example["records"][0].get("authenticity"), dict)

    def test_builder_never_queries_audits_and_returns_null(self) -> None:
        """Stored authenticity_audits rows are not read; every record gets authenticity=None."""
        from app.core.dataset import builder

        cur = MagicMock()
        executed: list[str] = []
        cur.execute.side_effect = lambda sql, *a: executed.append(sql)
        extraction_row = (
            "id1", _ORG, "rid1", "text", "Widget", None, 4, True, "positive", "low", "en",
            4, 0.9, [], [], [], [], [], "m", "2.3", "1", 10, "2026-01-01", "2026-01-01", False,
        )  # fmt: skip
        cur.fetchall.side_effect = [[extraction_row], []]
        conn = MagicMock()
        conn.cursor.return_value = cur
        with patch.object(builder, "_db_connect", return_value=conn):
            records = builder.get_dataset_page(_ORG, 10, 0)
        assert records[0]["authenticity"] is None
        assert not any("authenticity_audits" in q for q in executed)
