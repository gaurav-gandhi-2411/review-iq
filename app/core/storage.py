"""SQLite storage layer — async, with migrations."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite
import structlog

from app.core.config import get_settings
from app.core.schemas import (
    BatchJob,
    JobStatus,
    ReviewExtraction,
    Sentiment,
    Urgency,
)

log = structlog.get_logger(__name__)

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS extractions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    input_hash          TEXT    NOT NULL UNIQUE,
    review_text         TEXT    NOT NULL,
    product             TEXT    NOT NULL,
    stars               INTEGER,
    stars_inferred      INTEGER,
    buy_again           INTEGER,                -- 0/1/NULL
    sentiment           TEXT,
    urgency             TEXT    NOT NULL DEFAULT 'low',
    language            TEXT    NOT NULL DEFAULT 'en',
    review_length_chars INTEGER,
    confidence          REAL,
    topics              TEXT    NOT NULL DEFAULT '[]',   -- JSON array
    competitor_mentions TEXT    NOT NULL DEFAULT '[]',   -- JSON array
    pros                TEXT    NOT NULL DEFAULT '[]',   -- JSON array
    cons                TEXT    NOT NULL DEFAULT '[]',   -- JSON array
    feature_requests    TEXT    NOT NULL DEFAULT '[]',   -- JSON array
    model               TEXT    NOT NULL,
    prompt_version      TEXT    NOT NULL,
    schema_version      TEXT    NOT NULL DEFAULT '1.0.0',
    latency_ms          INTEGER,
    extracted_at        TEXT    NOT NULL,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_extractions_input_hash  ON extractions(input_hash);
CREATE INDEX IF NOT EXISTS idx_extractions_product     ON extractions(product);
CREATE INDEX IF NOT EXISTS idx_extractions_sentiment   ON extractions(sentiment);
CREATE INDEX IF NOT EXISTS idx_extractions_urgency     ON extractions(urgency);
CREATE INDEX IF NOT EXISTS idx_extractions_created_at  ON extractions(created_at);

CREATE TABLE IF NOT EXISTS batch_jobs (
    job_id       TEXT    PRIMARY KEY,
    status       TEXT    NOT NULL DEFAULT 'pending',
    total        INTEGER NOT NULL,
    processed    INTEGER NOT NULL DEFAULT 0,
    failed       INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS eval_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_id    TEXT    NOT NULL,
    input_hash    TEXT    NOT NULL,
    model         TEXT    NOT NULL,
    prompt_version TEXT   NOT NULL,
    score_json    TEXT    NOT NULL,             -- JSON: field-level scores
    passed        INTEGER NOT NULL,             -- 0/1
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

_CURRENT_SCHEMA_VERSION = 1


def _db_path() -> Path:
    url = get_settings().database_url
    # sqlite+aiosqlite:///./path → extract path
    raw = url.replace("sqlite+aiosqlite:///", "")
    p = Path(raw)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    async with aiosqlite.connect(str(_db_path())) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        db.row_factory = aiosqlite.Row
        yield db


async def migrate() -> None:
    """Apply schema migrations. Safe to call multiple times (idempotent)."""
    async with _connect() as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ) as cur:
            exists = await cur.fetchone()

        if not exists:
            await db.executescript(_SCHEMA_V1)
            await db.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (_CURRENT_SCHEMA_VERSION, datetime.utcnow().isoformat()),
            )
            await db.commit()
            log.info("storage.migrated", version=_CURRENT_SCHEMA_VERSION)
        else:
            async with db.execute("SELECT MAX(version) FROM schema_version") as cur:
                row = await cur.fetchone()
                current = row[0] if row and row[0] else 0
            log.info("storage.schema_already_at_version", version=current)


async def get_by_hash(input_hash: str) -> ReviewExtraction | None:
    """Return cached extraction if input_hash already exists."""
    async with (
        _connect() as db,
        db.execute("SELECT * FROM extractions WHERE input_hash = ?", (input_hash,)) as cur,
    ):
        row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_extraction(dict(row))


async def save_extraction(
    input_hash: str,
    review_text: str,
    extraction: ReviewExtraction,
) -> int:
    """Persist a new extraction. Returns the row id."""
    meta = extraction.extraction_meta
    async with _connect() as db:
        async with db.execute(
            """
            INSERT INTO extractions (
                input_hash, review_text, product, stars, stars_inferred,
                buy_again, sentiment, urgency, language, review_length_chars,
                confidence, topics, competitor_mentions, pros, cons,
                feature_requests, model, prompt_version, schema_version,
                latency_ms, extracted_at
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?
            )
            ON CONFLICT(input_hash) DO NOTHING
            RETURNING id
            """,
            (
                input_hash,
                review_text,
                extraction.product,
                extraction.stars,
                extraction.stars_inferred,
                None if extraction.buy_again is None else int(extraction.buy_again),
                extraction.sentiment,
                extraction.urgency,
                extraction.language,
                extraction.review_length_chars,
                extraction.confidence,
                json.dumps(extraction.topics),
                json.dumps(extraction.competitor_mentions),
                json.dumps(extraction.pros),
                json.dumps(extraction.cons),
                json.dumps(extraction.feature_requests),
                meta.model if meta else "unknown",
                meta.prompt_version if meta else "unknown",
                meta.schema_version if meta else "1.0.0",
                meta.latency_ms if meta else None,
                meta.extracted_at.isoformat() if meta else datetime.utcnow().isoformat(),
            ),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
    return row[0] if row else -1


async def query_extractions(
    *,
    product: str | None = None,
    sentiment: Sentiment | None = None,
    urgency: Urgency | None = None,
    has_competitor_mention: bool | None = None,
    topic: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Query stored extractions with optional filters."""
    where: list[str] = []
    params: list[Any] = []

    if product:
        where.append("product LIKE ?")
        params.append(f"%{product}%")
    if sentiment:
        where.append("sentiment = ?")
        params.append(str(sentiment))
    if urgency:
        where.append("urgency = ?")
        params.append(str(urgency))
    if has_competitor_mention is True:
        where.append("competitor_mentions != '[]'")
    elif has_competitor_mention is False:
        where.append("competitor_mentions = '[]'")
    if topic:
        where.append("topics LIKE ?")
        params.append(f'%"{topic}"%')
    if since:
        where.append("created_at >= ?")
        params.append(since.isoformat())
    if until:
        where.append("created_at <= ?")
        params.append(until.isoformat())

    where_clause = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT * FROM extractions
        {where_clause}
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    async with _connect() as db, db.execute(sql, params) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_insights() -> dict[str, Any]:
    """Return aggregated insights for the dashboard and /insights endpoint."""
    async with _connect() as db:
        async with db.execute("SELECT COUNT(*) as total FROM extractions") as cur:
            total_row = await cur.fetchone()
        total = total_row[0] if total_row else 0

        async with db.execute(
            "SELECT sentiment, COUNT(*) as cnt FROM extractions GROUP BY sentiment"
        ) as cur:
            sentiment_rows = await cur.fetchall()
        sentiment_counts = {r[0]: r[1] for r in sentiment_rows}

        async with db.execute(
            "SELECT urgency, COUNT(*) as cnt FROM extractions GROUP BY urgency"
        ) as cur:
            urgency_rows = await cur.fetchall()
        urgency_counts = {r[0]: r[1] for r in urgency_rows}

        # Top topics (parse JSON arrays)
        async with db.execute("SELECT topics FROM extractions WHERE topics != '[]'") as cur:
            topic_rows = await cur.fetchall()
        topic_freq: dict[str, int] = {}
        for r in topic_rows:
            for t in json.loads(r[0]):
                topic_freq[t] = topic_freq.get(t, 0) + 1
        top_topics = sorted(topic_freq.items(), key=lambda x: x[1], reverse=True)[:10]

        # Top competitor mentions
        async with db.execute(
            "SELECT competitor_mentions FROM extractions WHERE competitor_mentions != '[]'"
        ) as cur:
            comp_rows = await cur.fetchall()
        comp_freq: dict[str, int] = {}
        for r in comp_rows:
            for c in json.loads(r[0]):
                comp_freq[c] = comp_freq.get(c, 0) + 1
        top_competitors = sorted(comp_freq.items(), key=lambda x: x[1], reverse=True)[:10]

        # Sentiment over time (last 30 days, daily)
        async with db.execute(
            """
            SELECT DATE(created_at) as day, sentiment, COUNT(*) as cnt
            FROM extractions
            WHERE created_at >= datetime('now', '-30 days')
            GROUP BY day, sentiment
            ORDER BY day
            """
        ) as cur:
            time_rows = await cur.fetchall()
        sentiment_over_time = [{"date": r[0], "sentiment": r[1], "count": r[2]} for r in time_rows]

    return {
        "total_extractions": total,
        "sentiment_breakdown": sentiment_counts,
        "urgency_breakdown": urgency_counts,
        "top_topics": [{"topic": t, "count": c} for t, c in top_topics],
        "top_competitor_mentions": [{"competitor": c, "count": n} for c, n in top_competitors],
        "sentiment_over_time": sentiment_over_time,
    }


# ---------------------------------------------------------------------------
# Batch job helpers
# ---------------------------------------------------------------------------


async def create_batch_job(job_id: str, total: int) -> None:
    async with _connect() as db:
        await db.execute(
            "INSERT INTO batch_jobs (job_id, total) VALUES (?, ?)",
            (job_id, total),
        )
        await db.commit()


async def update_batch_job(
    job_id: str,
    *,
    processed: int | None = None,
    failed: int | None = None,
    status: JobStatus | None = None,
) -> None:
    parts: list[str] = []
    params: list[Any] = []
    if processed is not None:
        parts.append("processed = ?")
        params.append(processed)
    if failed is not None:
        parts.append("failed = ?")
        params.append(failed)
    if status is not None:
        parts.append("status = ?")
        params.append(str(status))
        if status in (JobStatus.done, JobStatus.failed):
            parts.append("completed_at = datetime('now')")
    if not parts:
        return
    sql = f"UPDATE batch_jobs SET {', '.join(parts)} WHERE job_id = ?"
    params.append(job_id)
    async with _connect() as db:
        await db.execute(sql, params)
        await db.commit()


async def get_batch_job(job_id: str) -> BatchJob | None:
    async with (
        _connect() as db,
        db.execute("SELECT * FROM batch_jobs WHERE job_id = ?", (job_id,)) as cur,
    ):
        row = await cur.fetchone()
    if row is None:
        return None
    d = dict(row)
    return BatchJob(
        job_id=d["job_id"],
        status=JobStatus(d["status"]),
        total=d["total"],
        processed=d["processed"],
        failed=d["failed"],
        created_at=datetime.fromisoformat(d["created_at"]),
        completed_at=datetime.fromisoformat(d["completed_at"]) if d.get("completed_at") else None,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_extraction(row: dict[str, Any]) -> ReviewExtraction:
    from app.core.schemas import ExtractionMeta

    meta = ExtractionMeta(
        model=row["model"],
        prompt_version=row["prompt_version"],
        schema_version=row["schema_version"],
        extracted_at=datetime.fromisoformat(row["extracted_at"]),
        latency_ms=row.get("latency_ms"),
        input_hash=row["input_hash"],
    )
    return ReviewExtraction(
        product=row["product"],
        stars=row.get("stars"),
        stars_inferred=row.get("stars_inferred"),
        pros=json.loads(row.get("pros") or "[]"),
        cons=json.loads(row.get("cons") or "[]"),
        buy_again=None if row.get("buy_again") is None else bool(row["buy_again"]),
        sentiment=Sentiment(row["sentiment"]) if row.get("sentiment") else None,
        topics=json.loads(row.get("topics") or "[]"),
        competitor_mentions=json.loads(row.get("competitor_mentions") or "[]"),
        urgency=Urgency(row.get("urgency", "low")),
        feature_requests=json.loads(row.get("feature_requests") or "[]"),
        language=row.get("language", "en"),
        review_length_chars=row.get("review_length_chars"),
        confidence=row.get("confidence"),
        extraction_meta=meta,
    )
