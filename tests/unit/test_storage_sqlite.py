"""Tests for uncovered SQLite storage paths: topic/since/until filters and update_batch_job no-op."""

from __future__ import annotations

from datetime import datetime, timedelta
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
    migrate,
    query_extractions,
    save_extraction,
    update_batch_job,
)


@pytest.fixture(autouse=True)
def temp_db(tmp_path: Path) -> None:
    db_path = tmp_path / "test_sqlite.db"
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


def _make(product: str = "Widget", h: str = "sha256:x", **kwargs: object) -> tuple[str, ReviewExtraction]:
    meta = ExtractionMeta(
        model="llama-3.3-70b-versatile",
        prompt_version="v1.0",
        schema_version="1.0.0",
        extracted_at=datetime.utcnow(),
        latency_ms=100,
        input_hash=h,
    )
    e = ReviewExtraction(
        product=product,
        stars=None,
        stars_inferred=4,
        pros=["good"],
        cons=["bad"],
        buy_again=True,
        sentiment=Sentiment.positive,
        topics=["battery"],
        competitor_mentions=[],
        urgency=Urgency.low,
        feature_requests=[],
        language="en",
        review_length_chars=50,
        confidence=0.9,
        extraction_meta=meta,
    )
    for k, v in kwargs.items():
        setattr(e, k, v)
    return h, e


class TestQueryFilters:
    async def _save(self, product: str, h: str, **kwargs: object) -> None:
        hk, e = _make(product, h, **kwargs)
        await save_extraction(hk, "review text", e)

    async def test_filter_by_urgency(self) -> None:
        await self._save("A", "sha256:u1", urgency=Urgency.high)
        await self._save("B", "sha256:u2", urgency=Urgency.low)
        results = await query_extractions(urgency=Urgency.high)
        assert len(results) == 1
        assert results[0]["product"] == "A"

    async def test_filter_no_competitor_mention(self) -> None:
        await self._save("A", "sha256:nc1", competitor_mentions=["Dyson"])
        await self._save("B", "sha256:nc2", competitor_mentions=[])
        results = await query_extractions(has_competitor_mention=False)
        assert len(results) == 1
        assert results[0]["product"] == "B"

    async def test_filter_by_topic(self) -> None:
        await self._save("A", "sha256:t1", topics=["battery", "price"])
        await self._save("B", "sha256:t2", topics=["design"])
        results = await query_extractions(topic="battery")
        assert len(results) == 1
        assert results[0]["product"] == "A"

    async def test_filter_by_since(self) -> None:
        future = datetime.utcnow() + timedelta(days=1)
        await self._save("A", "sha256:s1")
        results = await query_extractions(since=future)
        assert len(results) == 0

    async def test_filter_by_until(self) -> None:
        past = datetime.utcnow() - timedelta(days=1)
        await self._save("A", "sha256:un1")
        results = await query_extractions(until=past)
        assert len(results) == 0

    async def test_filter_by_since_and_until_returns_matching(self) -> None:
        past = datetime.utcnow() - timedelta(days=1)
        future = datetime.utcnow() + timedelta(days=1)
        await self._save("A", "sha256:su1")
        results = await query_extractions(since=past, until=future)
        assert len(results) == 1


class TestUpdateBatchJobNoOp:
    async def test_no_op_when_no_params(self) -> None:
        await create_batch_job("noop-job", total=3)
        await update_batch_job("noop-job")  # all params None — hits early return (line 347)
        from app.core.storage import get_batch_job
        job = await get_batch_job("noop-job")
        assert job is not None
        assert job.status == JobStatus.pending
        assert job.processed == 0

    async def test_update_with_failed_count(self) -> None:
        await create_batch_job("fail-job", total=5)
        await update_batch_job("fail-job", failed=2)  # covers lines 339-340
        from app.core.storage import get_batch_job
        job = await get_batch_job("fail-job")
        assert job is not None
        assert job.failed == 2
