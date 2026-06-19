from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.auth.api_key import ApiKeyContext, require_api_key

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_ORG = str(uuid.uuid4())
_CTX = ApiKeyContext(org_id=_ORG, api_key_id="k1", key_name="t", usage_record_id="u1")
_REVIEW_ID = "a" * 64  # valid plain hex, no "sha256:" prefix


def _valid_body(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "review_id": _REVIEW_ID,
        "source_type": "extraction",
        "field_path": "sentiment",
        "original_value": "positive",
        "corrected_value": "negative",
    }
    base.update(overrides)
    return base


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
# POST /v2/corrections
# ---------------------------------------------------------------------------


class TestPostCorrections:
    def test_valid_submission_returns_201(self, client: TestClient) -> None:
        """Happy path: valid body + mocked DB → 201 with id and org_id."""
        with patch(
            "app.api.v2.corrections.submit_correction_pg",
            new=MagicMock(return_value="new-uuid"),
        ):
            resp = client.post("/v2/corrections", json=_valid_body())

        assert resp.status_code == 201
        body = resp.json()
        assert "id" in body
        assert "org_id" in body
        assert body["id"] == "new-uuid"
        assert body["org_id"] == _ORG

    def test_invalid_field_path_returns_422(self, client: TestClient) -> None:
        """field_path not in allowed set for source_type → 422, no DB call."""
        resp = client.post("/v2/corrections", json=_valid_body(field_path="nonexistent"))
        assert resp.status_code == 422

    def test_cross_type_field_path_returns_422(self, client: TestClient) -> None:
        """field_path belonging to a different source_type → 422."""
        # "label" belongs to authenticity, not extraction
        resp = client.post(
            "/v2/corrections",
            json=_valid_body(source_type="extraction", field_path="label"),
        )
        assert resp.status_code == 422

    def test_prefixed_review_id_returns_422(self, client: TestClient) -> None:
        """review_id with 'sha256:' prefix is rejected with 422."""
        resp = client.post("/v2/corrections", json=_valid_body(review_id="sha256:abc"))
        assert resp.status_code == 422

    def test_unauthenticated_returns_401(self, client_no_auth: TestClient) -> None:
        """Missing API key returns 401."""
        resp = client_no_auth.post("/v2/corrections", json=_valid_body())
        assert resp.status_code == 401

    def test_db_error_returns_500(self, client: TestClient) -> None:
        """DB exception maps to HTTP 500."""
        with patch(
            "app.api.v2.corrections.submit_correction_pg",
            new=MagicMock(side_effect=Exception("db down")),
        ):
            resp = client.post("/v2/corrections", json=_valid_body())

        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /v2/corrections
# ---------------------------------------------------------------------------


class TestGetCorrections:
    def test_list_returns_200_with_results(self, client: TestClient) -> None:
        """Happy path: mocked DB → 200, count=1, results non-empty."""
        fake_rows = [{"id": "x", "source_type": "extraction"}]
        with patch(
            "app.api.v2.corrections.list_corrections_pg",
            new=MagicMock(return_value=fake_rows),
        ):
            resp = client.get("/v2/corrections")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert len(body["results"]) == 1

    def test_source_type_filter_forwarded(self, client: TestClient) -> None:
        """Query param source_type is forwarded to list_corrections_pg."""
        mock_fn = MagicMock(return_value=[])
        with patch("app.api.v2.corrections.list_corrections_pg", new=mock_fn):
            resp = client.get("/v2/corrections", params={"source_type": "extraction"})

        assert resp.status_code == 200
        _, kwargs = mock_fn.call_args
        assert kwargs["source_type"] == "extraction"

    def test_limit_offset_defaults(self, client: TestClient) -> None:
        """Default limit=50 and offset=0 are forwarded to the service."""
        mock_fn = MagicMock(return_value=[])
        with patch("app.api.v2.corrections.list_corrections_pg", new=mock_fn):
            resp = client.get("/v2/corrections")

        assert resp.status_code == 200
        _, kwargs = mock_fn.call_args
        assert kwargs["limit"] == 50
        assert kwargs["offset"] == 0
