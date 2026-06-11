"""Unit tests for app.api.v2.authenticity endpoints."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.auth.api_key import ApiKeyContext, require_api_key
from app.core.authenticity.schema import AuthenticityLabel, AuthenticityResult
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_ORG_ID = str(uuid.uuid4())
_KEY_ID = str(uuid.uuid4())
_USAGE_ID = str(uuid.uuid4())

_CTX = ApiKeyContext(
    org_id=_ORG_ID,
    api_key_id=_KEY_ID,
    key_name="test-key",
    usage_record_id=_USAGE_ID,
)


def _fake_result(score: float = 0.8) -> AuthenticityResult:
    """Construct a minimal AuthenticityResult for tests."""
    return AuthenticityResult(
        score=score,
        label=AuthenticityLabel.GENUINE,
        flags=[],
        reasons="test",
        review_hash=hashlib.sha256(b"test").hexdigest(),
        scored_at=datetime.now(UTC),
    )


@pytest.fixture
def client() -> TestClient:
    """TestClient with require_api_key dependency overridden to bypass DB."""
    from app.main import app

    app.dependency_overrides[require_api_key] = lambda: _CTX
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.fixture
def client_no_auth() -> TestClient:
    """TestClient without any auth override — tests real 401 path."""
    from app.main import app

    app.dependency_overrides.clear()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /v2/authenticity — single review
# ---------------------------------------------------------------------------


def test_authenticity_single_valid_key(client: TestClient) -> None:
    """Happy path: valid key, mocked engine, returns score and label."""
    fake = _fake_result(score=0.85)

    with (
        patch(
            "app.api.v2.authenticity.engine.score_single",
            new=AsyncMock(return_value=fake),
        ),
        patch(
            "app.api.v2.authenticity.save_authenticity_audit_pg",
            new=MagicMock(return_value=None),
        ),
    ):
        resp = client.post(
            "/v2/authenticity",
            json={"text": "Great product", "stars": 5},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "score" in body
    assert "label" in body
    assert body["label"] == "genuine"
    assert abs(body["score"] - 0.85) < 1e-6


def test_authenticity_single_no_key(client_no_auth: TestClient) -> None:
    """Missing API key returns 401."""
    resp = client_no_auth.post(
        "/v2/authenticity",
        json={"text": "Great product", "stars": 5},
    )
    assert resp.status_code == 401


def test_authenticity_single_engine_error_returns_500(client: TestClient) -> None:
    """Engine raising an unexpected exception maps to HTTP 500."""
    with patch(
        "app.api.v2.authenticity.engine.score_single",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        resp = client.post(
            "/v2/authenticity",
            json={"text": "Some review"},
        )

    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /v2/authenticity/batch
# ---------------------------------------------------------------------------


def test_authenticity_batch_too_large(client: TestClient) -> None:
    """Batch of 501 reviews returns 422 with size-limit message."""
    payload = {"reviews": [{"text": "x"}] * 501}
    resp = client.post("/v2/authenticity/batch", json=payload)

    assert resp.status_code == 422
    assert "500" in resp.json()["detail"]


def test_authenticity_batch_valid(client: TestClient) -> None:
    """Two-review batch returns total=2 and results list."""
    results = [_fake_result(0.9), _fake_result(0.7)]

    with (
        patch(
            "app.api.v2.authenticity.engine.score_batch",
            new=AsyncMock(return_value=results),
        ),
        patch(
            "app.api.v2.authenticity.save_authenticity_audit_pg",
            new=MagicMock(return_value=None),
        ),
    ):
        resp = client.post(
            "/v2/authenticity/batch",
            json={"reviews": [{"text": "First review"}, {"text": "Second review"}]},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["results"]) == 2


def test_authenticity_batch_with_dates(client: TestClient) -> None:
    """Dates list is parsed and forwarded to score_batch."""
    results = [_fake_result()]
    captured: list[object] = []

    async def _mock_score_batch(
        reviews: list[tuple[str, int | None]],
        *,
        dates: list[datetime | None] | None,
        settings: object,
    ) -> list[AuthenticityResult]:
        captured.append(dates)
        return results

    with (
        patch(
            "app.api.v2.authenticity.engine.score_batch",
            new=_mock_score_batch,
        ),
        patch(
            "app.api.v2.authenticity.save_authenticity_audit_pg",
            new=MagicMock(return_value=None),
        ),
    ):
        resp = client.post(
            "/v2/authenticity/batch",
            json={
                "reviews": [{"text": "Review one"}],
                "dates": ["2024-01-15"],
            },
        )

    assert resp.status_code == 200
    assert captured[0] is not None
    parsed = captured[0]
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    assert isinstance(parsed[0], datetime)


def test_authenticity_batch_no_auth(client_no_auth: TestClient) -> None:
    """Missing API key on batch endpoint returns 401."""
    resp = client_no_auth.post(
        "/v2/authenticity/batch",
        json={"reviews": [{"text": "hello"}]},
    )
    assert resp.status_code == 401
