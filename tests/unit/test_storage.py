"""Unit tests for the storage module using a temporary SQLite database."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from app.core.schemas import (
    ExtractionMeta,
    JobStatus,
    ReviewExtraction,
    Sentiment,
    Urgency,
)
from app.core.storage import (
    create_batch_job,
    get_batch_job,
    get_by_hash,
    get_insights,
    migrate,
    query_extractions,
    save_extraction,
    update_batch_job,
)


@pytest.fixture(autouse=True)
def temp_db(tmp_path: Path) -> None:
    """Redirect all storage calls to a fresh temp database."""
    db_path = tmp_path / "test.db"
    with patch(
        "app.core.storage.get_settings",
        return_value=type(
            "S",
            (),
            {"database_url": f"sqlite+aiosqlite:///{db_path}"},
        )(),
    ):
        yield


@pytest.fixture(autouse=True)
async def apply_migrations(temp_db: None) -> None:
    await migrate()


def _make_extraction(product: str = "Test Vacuum") -> ReviewExtraction:
    meta = ExtractionMeta(
        model="llama-3.3-70b-versatile",
        prompt_version="v1.0",
        schema_version="1.0.0",
        extracted_at=datetime.utcnow(),
        latency_ms=300,
        input_hash="sha256:abc123",
    )
    return ReviewExtraction(
        product=product,
        stars=None,
        stars_inferred=3,
        pros=["great suction"],
        cons=["bad battery"],
        buy_again=False,
        sentiment=Sentiment.mixed,
        topics=["suction", "battery"],
        competitor_mentions=["Dyson"],
        urgency=Urgency.low,
        feature_requests=[],
        language="en",
        review_length_chars=100,
        confidence=0.85,
        extraction_meta=meta,
    )


class TestMigrate:
    async def test_migrate_creates_tables(self) -> None:
        # migrate is called in autouse fixture; verify by saving
        e = _make_extraction()
        row_id = await save_extraction("sha256:abc123", "Great product", e)
        assert row_id > 0

    async def test_migrate_is_idempotent(self) -> None:
        await migrate()
        await migrate()  # Should not raise


class TestSaveAndRetrieve:
    async def test_save_and_get_by_hash(self) -> None:
        e = _make_extraction()
        input_hash = "sha256:unique001"
        e.extraction_meta.input_hash = input_hash  # type: ignore[union-attr]
        await save_extraction(input_hash, "Great product", e)

        result = await get_by_hash(input_hash)
        assert result is not None
        assert result.product == "Test Vacuum"
        assert result.stars is None
        assert result.stars_inferred == 3

    async def test_get_by_hash_returns_none_when_missing(self) -> None:
        result = await get_by_hash("sha256:doesnotexist")
        assert result is None

    async def test_save_is_idempotent_on_conflict(self) -> None:
        e = _make_extraction()
        h = "sha256:same"
        e.extraction_meta.input_hash = h  # type: ignore[union-attr]
        id1 = await save_extraction(h, "text", e)
        id2 = await save_extraction(h, "text", e)
        # Second insert returns -1 (ON CONFLICT DO NOTHING)
        assert id1 > 0
        assert id2 == -1

    async def test_buy_again_false_roundtrips(self) -> None:
        e = _make_extraction()
        e.buy_again = False
        h = "sha256:buyagain_false"
        e.extraction_meta.input_hash = h  # type: ignore[union-attr]
        await save_extraction(h, "text", e)
        result = await get_by_hash(h)
        assert result is not None
        assert result.buy_again is False

    async def test_buy_again_none_roundtrips(self) -> None:
        e = _make_extraction()
        e.buy_again = None
        h = "sha256:buyagain_none"
        e.extraction_meta.input_hash = h  # type: ignore[union-attr]
        await save_extraction(h, "text", e)
        result = await get_by_hash(h)
        assert result is not None
        assert result.buy_again is None

    async def test_competitor_mentions_roundtrip(self) -> None:
        e = _make_extraction()
        e.competitor_mentions = ["Dyson", "Shark"]
        h = "sha256:comps"
        e.extraction_meta.input_hash = h  # type: ignore[union-attr]
        await save_extraction(h, "text", e)
        result = await get_by_hash(h)
        assert result is not None
        assert "Dyson" in result.competitor_mentions
        assert "Shark" in result.competitor_mentions


class TestQueryExtractions:
    async def _save(self, product: str, h: str, **kwargs: object) -> None:
        e = _make_extraction(product)
        for k, v in kwargs.items():
            setattr(e, k, v)
        e.extraction_meta.input_hash = h  # type: ignore[union-attr]
        await save_extraction(h, "text", e)

    async def test_query_all(self) -> None:
        await self._save("VacA", "sha256:a1")
        await self._save("VacB", "sha256:b1")
        results = await query_extractions()
        assert len(results) == 2

    async def test_filter_by_product(self) -> None:
        await self._save("VacA", "sha256:a2")
        await self._save("Blender", "sha256:b2")
        results = await query_extractions(product="VacA")
        assert len(results) == 1
        assert results[0]["product"] == "VacA"

    async def test_filter_by_sentiment(self) -> None:
        await self._save("X", "sha256:x1", sentiment=Sentiment.positive)
        await self._save("Y", "sha256:y1", sentiment=Sentiment.negative)
        results = await query_extractions(sentiment=Sentiment.positive)
        assert len(results) == 1

    async def test_filter_has_competitor_mention(self) -> None:
        await self._save("A", "sha256:ca1", competitor_mentions=["Dyson"])
        await self._save("B", "sha256:cb1", competitor_mentions=[])
        results = await query_extractions(has_competitor_mention=True)
        assert len(results) == 1
        assert results[0]["product"] == "A"

    async def test_limit_and_offset(self) -> None:
        for i in range(5):
            await self._save("P", f"sha256:lo{i}")
        r1 = await query_extractions(limit=2, offset=0)
        r2 = await query_extractions(limit=2, offset=2)
        assert len(r1) == 2
        assert len(r2) == 2
        assert r1[0]["id"] != r2[0]["id"]


class TestInsights:
    async def test_empty_db_insights(self) -> None:
        result = await get_insights()
        assert result["total_extractions"] == 0
        assert result["top_topics"] == []

    async def test_insights_counts(self) -> None:
        e1 = _make_extraction()
        e1.extraction_meta.input_hash = "sha256:ins1"  # type: ignore[union-attr]
        e1.sentiment = Sentiment.positive
        e1.topics = ["suction", "price"]
        await save_extraction("sha256:ins1", "text1", e1)

        e2 = _make_extraction()
        e2.extraction_meta.input_hash = "sha256:ins2"  # type: ignore[union-attr]
        e2.sentiment = Sentiment.negative
        e2.topics = ["suction", "battery"]
        await save_extraction("sha256:ins2", "text2", e2)

        result = await get_insights()
        assert result["total_extractions"] == 2
        assert result["sentiment_breakdown"].get("positive") == 1
        assert result["sentiment_breakdown"].get("negative") == 1
        topics = {t["topic"]: t["count"] for t in result["top_topics"]}
        assert topics.get("suction") == 2


class TestBatchJobs:
    async def test_create_and_get_job(self) -> None:
        await create_batch_job("job-001", total=10)
        job = await get_batch_job("job-001")
        assert job is not None
        assert job.job_id == "job-001"
        assert job.total == 10
        assert job.status == JobStatus.pending

    async def test_update_job_status(self) -> None:
        await create_batch_job("job-002", total=5)
        await update_batch_job("job-002", processed=5, status=JobStatus.done)
        job = await get_batch_job("job-002")
        assert job is not None
        assert job.status == JobStatus.done
        assert job.processed == 5
        assert job.completed_at is not None

    async def test_get_nonexistent_job(self) -> None:
        job = await get_batch_job("no-such-job")
        assert job is None
