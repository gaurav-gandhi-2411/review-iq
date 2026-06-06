"""Unit tests for app.api.v2.ingest endpoints."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from app.auth.api_key import ApiKeyContext, require_api_key
from app.core.csv_ingest import CsvColumnError, FileTooLargeError, RowLimitExceededError
from app.core.schemas import ExtractionMetaV2, ReviewExtractionV2, Sentiment, Urgency
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

_ORG_ID = str(uuid.uuid4())
_KEY_ID = str(uuid.uuid4())
_USAGE_ID = str(uuid.uuid4())
_JOB_ID = str(uuid.uuid4())

_CTX = ApiKeyContext(
    org_id=_ORG_ID,
    api_key_id=_KEY_ID,
    key_name="test-key",
    usage_record_id=_USAGE_ID,
)


def _fake_extraction() -> ReviewExtractionV2:
    """Minimal ReviewExtractionV2 for use as a get_by_hash_pg mock return value."""
    return ReviewExtractionV2(
        product="Test Widget",
        sentiment=Sentiment.positive,
        urgency=Urgency.low,
        extraction_meta=ExtractionMetaV2(
            model="mock",
            prompt_version="v2.0",
            schema_version="1.0.0",
            extracted_at=datetime.now(tz=UTC),
            input_hash="sha256:abc",
            org_id=_ORG_ID,
        ),
    )


@pytest.fixture
def client() -> TestClient:
    """TestClient with require_api_key dependency overridden."""
    from app.main import app

    app.dependency_overrides[require_api_key] = lambda: _CTX
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /v2/ingest/csv
# ---------------------------------------------------------------------------


def test_ingest_csv_returns_202_with_job_id(client: TestClient) -> None:
    """Happy path: returns 202 with job_id, total, and status=pending."""
    with (
        patch(
            "app.api.v2.ingest.read_and_validate_csv",
            new=AsyncMock(return_value=([{"text": "Great product!"}], "review_text", None)),
        ),
        patch(
            "app.api.v2.ingest.create_batch_job_pg",
            return_value=None,
        ),
        patch("app.api.v2.ingest._process_ingest_job", new=AsyncMock()),
    ):
        resp = client.post(
            "/v2/ingest/csv",
            files={"file": ("reviews.csv", b"review_text\ngreat product\n", "text/csv")},
        )

    assert resp.status_code == 202
    body = resp.json()
    assert "job_id" in body
    assert body["total"] == 1
    assert body["status"] == "pending"


def test_ingest_csv_413_file_too_large(client: TestClient) -> None:
    """FileTooLargeError from csv parser maps to 413."""
    with patch(
        "app.api.v2.ingest.read_and_validate_csv",
        new=AsyncMock(side_effect=FileTooLargeError("too big")),
    ):
        resp = client.post(
            "/v2/ingest/csv",
            files={"file": ("reviews.csv", b"review_text\ngreat product\n", "text/csv")},
        )

    assert resp.status_code == 413


def test_ingest_csv_413_row_limit(client: TestClient) -> None:
    """RowLimitExceededError from csv parser maps to 413."""
    with patch(
        "app.api.v2.ingest.read_and_validate_csv",
        new=AsyncMock(side_effect=RowLimitExceededError("too many")),
    ):
        resp = client.post(
            "/v2/ingest/csv",
            files={"file": ("reviews.csv", b"review_text\ngreat product\n", "text/csv")},
        )

    assert resp.status_code == 413


def test_ingest_csv_422_bad_column(client: TestClient) -> None:
    """CsvColumnError from csv parser maps to 422."""
    with patch(
        "app.api.v2.ingest.read_and_validate_csv",
        new=AsyncMock(side_effect=CsvColumnError("not found")),
    ):
        resp = client.post(
            "/v2/ingest/csv",
            files={"file": ("reviews.csv", b"review_text\ngreat product\n", "text/csv")},
        )

    assert resp.status_code == 422


def test_ingest_csv_422_empty_rows(client: TestClient) -> None:
    """Empty rows list (all rows were blank) maps to 422."""
    with (
        patch(
            "app.api.v2.ingest.read_and_validate_csv",
            new=AsyncMock(return_value=([], "review_text", None)),
        ),
        patch("app.api.v2.ingest._process_ingest_job", new=AsyncMock()),
    ):
        resp = client.post(
            "/v2/ingest/csv",
            files={"file": ("reviews.csv", b"review_text\n\n", "text/csv")},
        )

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /v2/ingest/{job_id}
# ---------------------------------------------------------------------------


def test_get_ingest_status_found(client: TestClient) -> None:
    """Returns 200 with correct status fields when job exists."""
    job_record = {
        "job_id": _JOB_ID,
        "status": "processing",
        "total": 5,
        "processed": 2,
        "failed": 0,
        "created_at": datetime.now(tz=UTC),
        "completed_at": None,
    }
    with patch("app.api.v2.ingest.get_batch_job_pg", return_value=job_record):
        resp = client.get(f"/v2/ingest/{_JOB_ID}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == _JOB_ID
    assert body["status"] == "processing"
    assert body["total"] == 5
    assert body["processed"] == 2
    assert body["failed"] == 0
    assert body["completed_at"] is None


def test_get_ingest_status_not_found(client: TestClient) -> None:
    """Returns 404 when job does not exist."""
    with patch("app.api.v2.ingest.get_batch_job_pg", return_value=None):
        resp = client.get(f"/v2/ingest/{_JOB_ID}")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /v2/ingest/{job_id}/result
# ---------------------------------------------------------------------------


def test_get_ingest_result_json(client: TestClient) -> None:
    """Returns 200 JSON with job_id, total, results list for a completed job."""
    job_record = {
        "job_id": _JOB_ID,
        "status": "done",
        "total": 1,
        "processed": 1,
        "failed": 0,
        "created_at": datetime.now(tz=UTC),
        "completed_at": datetime.now(tz=UTC),
        "source_columns": json.dumps({"input_hashes": ["sha256:abc"]}),
    }
    extraction = _fake_extraction()

    with (
        patch("app.api.v2.ingest.get_batch_job_pg", return_value=job_record),
        patch("app.api.v2.ingest.get_by_hash_pg", return_value=extraction),
    ):
        resp = client.get(f"/v2/ingest/{_JOB_ID}/result")

    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == _JOB_ID
    assert body["total"] == 1
    assert isinstance(body["results"], list)
    assert len(body["results"]) == 1


def test_get_ingest_result_pending_409(client: TestClient) -> None:
    """Returns 409 when job is not yet complete."""
    job_record = {
        "job_id": _JOB_ID,
        "status": "pending",
        "total": 3,
        "processed": 0,
        "failed": 0,
        "created_at": datetime.now(tz=UTC),
        "completed_at": None,
        "source_columns": None,
    }
    with patch("app.api.v2.ingest.get_batch_job_pg", return_value=job_record):
        resp = client.get(f"/v2/ingest/{_JOB_ID}/result")

    assert resp.status_code == 409


def test_get_ingest_result_not_found(client: TestClient) -> None:
    """Returns 404 when job does not exist."""
    with patch("app.api.v2.ingest.get_batch_job_pg", return_value=None):
        resp = client.get(f"/v2/ingest/{_JOB_ID}/result")

    assert resp.status_code == 404


def test_get_ingest_result_csv_format(client: TestClient) -> None:
    """Returns 200 with text/csv Content-Type when format=csv is requested."""
    job_record = {
        "job_id": _JOB_ID,
        "status": "done",
        "total": 1,
        "processed": 1,
        "failed": 0,
        "created_at": datetime.now(tz=UTC),
        "completed_at": datetime.now(tz=UTC),
        "source_columns": json.dumps({"input_hashes": ["sha256:abc"]}),
    }
    extraction = _fake_extraction()

    with (
        patch("app.api.v2.ingest.get_batch_job_pg", return_value=job_record),
        patch("app.api.v2.ingest.get_by_hash_pg", return_value=extraction),
    ):
        resp = client.get(f"/v2/ingest/{_JOB_ID}/result?format=csv")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
