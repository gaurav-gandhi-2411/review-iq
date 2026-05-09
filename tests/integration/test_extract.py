"""Integration tests for /extract and /extract/batch endpoints (LLM mocked)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.core.schemas import ReviewExtractionLLMOutput, Sentiment, Urgency
from app.core.storage import migrate
from httpx import ASGITransport, AsyncClient

_VALID_LLM_OUTPUT = ReviewExtractionLLMOutput(
    product="Turbo-Vac 5000",
    stars=None,
    stars_inferred=3,
    pros=["incredible suction"],
    cons=["poor battery life"],
    buy_again=False,
    sentiment=Sentiment.mixed,
    topics=["suction", "battery"],
    competitor_mentions=["Dyson"],
    urgency=Urgency.low,
    feature_requests=[],
    language="en",
    confidence=0.9,
)

_TURBO_VAC_TEXT = (
    "So I bought the 'Turbo-Vac 5000' last week. The suction is incredible "
    "but the battery dies after 15 minutes. For $300 I expected better. "
    "I'd buy a Dyson next time."
)

_MOCK_LLM = patch(
    "app.api.extract.extract_with_llm",
    new=AsyncMock(return_value=(_VALID_LLM_OUTPUT, "llama-3.3-70b-versatile", 250)),
)


def _storage_settings(db_path: Path) -> MagicMock:
    cfg = MagicMock()
    cfg.database_url = f"sqlite+aiosqlite:///{db_path}"
    return cfg


def _auth_settings() -> MagicMock:
    cfg = MagicMock()
    cfg.api_key = "test-key"
    return cfg


@pytest.fixture
async def client(tmp_path: Path) -> AsyncClient:
    """HTTP client against the full FastAPI app.

    Patches:
    - storage._db_path via get_settings → temp SQLite
    - auth.get_settings → test API key
    Runs migrate() directly so tables exist before any request.
    """
    db_path = tmp_path / "test_api.db"
    storage_cfg = _storage_settings(db_path)
    auth_cfg = _auth_settings()

    with (
        patch("app.core.storage.get_settings", return_value=storage_cfg),
        patch("app.core.auth.get_settings", return_value=auth_cfg),
    ):
        # Migrate the temp DB directly (bypasses lifespan which uses cached settings)
        await migrate()

        from app.main import app

        # Also patch main.migrate so lifespan doesn't try to re-migrate wrong DB
        with patch("app.main.migrate", new=AsyncMock()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                yield ac


class TestExtractSingle:
    @pytest.mark.asyncio
    async def test_extract_success(self, client: AsyncClient) -> None:
        with _MOCK_LLM:
            resp = await client.post(
                "/extract",
                json={"text": _TURBO_VAC_TEXT},
                headers={"X-API-Key": "test-key"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["product"] == "Turbo-Vac 5000"
        assert data["stars"] is None
        assert data["stars_inferred"] == 3
        assert data["buy_again"] is False
        assert "Dyson" in data["competitor_mentions"]
        assert data["extraction_meta"]["prompt_version"] == "v1.0"

    @pytest.mark.asyncio
    async def test_extract_missing_api_key_returns_401(self, client: AsyncClient) -> None:
        resp = await client.post("/extract", json={"text": "Great product!"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_extract_wrong_api_key_returns_401(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/extract",
            json={"text": "Great product!"},
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_extract_empty_text_returns_422(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/extract",
            json={"text": ""},
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_extract_too_long_returns_422(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/extract",
            json={"text": "x" * 5001},
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_extract_cached_on_duplicate(self, client: AsyncClient) -> None:
        call_count = 0

        async def _mock_llm(*args, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal call_count
            call_count += 1
            return _VALID_LLM_OUTPUT, "llama-3.3-70b-versatile", 250

        with patch("app.api.extract.extract_with_llm", new=_mock_llm):
            await client.post(
                "/extract",
                json={"text": _TURBO_VAC_TEXT},
                headers={"X-API-Key": "test-key"},
            )
            await client.post(
                "/extract",
                json={"text": _TURBO_VAC_TEXT},
                headers={"X-API-Key": "test-key"},
            )

        assert call_count == 1  # LLM called once; second was cache hit

    @pytest.mark.asyncio
    async def test_extract_llm_failure_returns_503(self, client: AsyncClient) -> None:
        with patch(
            "app.api.extract.extract_with_llm",
            new=AsyncMock(side_effect=RuntimeError("Both LLM providers failed")),
        ):
            resp = await client.post(
                "/extract",
                json={"text": "great product"},
                headers={"X-API-Key": "test-key"},
            )
        assert resp.status_code == 503


class TestHealth:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestBatch:
    @pytest.mark.asyncio
    async def test_batch_returns_job_id(self, client: AsyncClient) -> None:
        with _MOCK_LLM:
            resp = await client.post(
                "/extract/batch",
                json={"reviews": [{"text": "Good product"}, {"text": "Bad product"}]},
                headers={"X-API-Key": "test-key"},
            )
        assert resp.status_code == 202
        data = resp.json()
        assert "job_id" in data
        assert data["total"] == 2
        assert data["status"] == "pending"

    @pytest.mark.asyncio
    async def test_batch_status_poll(self, client: AsyncClient) -> None:
        with _MOCK_LLM:
            resp = await client.post(
                "/extract/batch",
                json={"reviews": [{"text": "Good product"}]},
                headers={"X-API-Key": "test-key"},
            )
        job_id = resp.json()["job_id"]

        status_resp = await client.get(f"/extract/batch/{job_id}")
        assert status_resp.status_code == 200
        assert status_resp.json()["job_id"] == job_id

    @pytest.mark.asyncio
    async def test_batch_unknown_job_returns_404(self, client: AsyncClient) -> None:
        resp = await client.get("/extract/batch/no-such-job")
        assert resp.status_code == 404
