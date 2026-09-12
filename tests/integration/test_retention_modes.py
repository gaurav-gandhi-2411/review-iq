"""Integration tests for the two retention modes (Session 12 D1/P2).

This is the product claim test (P2d): a stateless-mode extraction must leave the sentinel
string in NO table and NO log line. A mock-based unit test cannot prove this negative against
real Postgres RLS/persistence -- this runs against the ephemeral, fully-migrated Postgres
container the pre-cutover-verification.yml / bypassrls-container-check.yml workflows already
provision (every migration in supabase/migrations/, including this session's
20260912000001_organizations_retention_mode.sql, applied in order first).

Marked 'integration' — requires live Supabase DB (SUPABASE_DIRECT_URL) in .env.
Run: uv run pytest tests/integration/test_retention_modes.py -v -m integration
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import psycopg2
import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

load_dotenv(Path(__file__).parents[2] / ".env")

from app.auth.keygen import generate_api_key  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app, raise_server_exceptions=True)

_SENTINEL = f"SENTINEL_RETENTION_TEST_{uuid.uuid4().hex[:12].upper()}"
_SENTINEL_TEXT = (
    f"{_SENTINEL} the battery life is disappointing but the build quality is solid {_SENTINEL}"
)


def _create_org(
    suffix: str, retention_mode: str = "stateless", retention_days: int | None = None
) -> dict:
    org_id = str(uuid.uuid4())
    slug = f"retmode-{suffix}-{uuid.uuid4().hex[:6]}"
    conn = psycopg2.connect(os.environ["SUPABASE_DIRECT_URL"])
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO public.organizations (id, name, slug, retention_mode, retention_days) "
            "VALUES (%s, %s, %s, %s, %s)",
            (org_id, f"RetMode {suffix}", slug, retention_mode, retention_days),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": org_id, "slug": slug}


def _create_key(org_id: str) -> dict:
    raw_key, key_prefix, key_hash = generate_api_key()
    key_id = str(uuid.uuid4())
    conn = psycopg2.connect(os.environ["SUPABASE_DIRECT_URL"])
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO public.api_keys (id, org_id, key_prefix, key_hash, name, quota) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (key_id, org_id, key_prefix, key_hash, "retmode-test", 100),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": key_id, "raw_key": raw_key}


def _teardown_org(org_id: str) -> None:
    conn = psycopg2.connect(os.environ["SUPABASE_DIRECT_URL"])
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM public.organizations WHERE id = %s", (org_id,))
        conn.commit()
    finally:
        conn.close()


def _every_text_column_contains(sentinel: str) -> list[str]:
    """Scan every text/jsonb column in every public table for `sentinel`.

    Same unscoped-scan methodology as Session 12's live P1 audit (ADR 0024) -- a scan
    limited to the columns this test *expects* to be clean cannot prove the negative;
    only an unscoped sweep can. Returns the list of "table.column" hits (empty = clean).
    """
    conn = psycopg2.connect(os.environ["SUPABASE_DIRECT_URL"])
    hits: list[str] = []
    try:
        cur = conn.cursor()
        cur.execute(
            "select table_name, column_name from information_schema.columns "
            "where table_schema='public' and data_type in "
            "('text','character varying','jsonb','json')"
        )
        cols = cur.fetchall()
        for table, col in cols:
            cur.execute(
                f'select count(*) from public."{table}" where "{col}"::text ilike %s',
                (f"%{sentinel}%",),
            )
            if cur.fetchone()[0] > 0:
                hits.append(f"{table}.{col}")
    finally:
        conn.close()
    return hits


def _mock_llm_output_with_sentinel_as_product():
    """Mirror the live finding from ADR 0024: the LLM can echo input verbatim into `product`."""
    from app.core.schemas import ReviewExtractionLLMOutput, Sentiment, Urgency

    return (
        ReviewExtractionLLMOutput(
            product=_SENTINEL,  # worst case: LLM output itself carries the sentinel
            stars=None,
            sentiment=Sentiment.mixed,
            urgency=Urgency.low,
            topics=["battery", "build_quality"],
            competitor_mentions=[],
            pros=["solid build quality"],
            cons=["disappointing battery life"],
            language="en",
            confidence=0.9,
        ),
        "mock-model",
        42,
        150,
        80,
        False,
    )


@pytest.mark.integration
def test_stateless_extraction_leaves_no_trace_in_any_table_or_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """THE product claim test. Stateless (default) mode: the sentinel must appear in
    NO table (unscoped scan across every text/jsonb column) and NO captured log record.
    """
    org = _create_org("stateless")
    key = _create_key(org["id"])

    try:
        with (
            patch("app.api.v2.extract.extract_with_llm", new_callable=AsyncMock) as mock_llm,
            caplog.at_level(logging.DEBUG),
        ):
            mock_llm.return_value = _mock_llm_output_with_sentinel_as_product()
            r = client.post(
                "/v2/extract",
                json={"text": _SENTINEL_TEXT},
                headers={"X-API-Key": key["raw_key"]},
            )
        assert r.status_code == 200, r.text

        table_hits = _every_text_column_contains(_SENTINEL)
        assert not table_hits, f"Sentinel found in stateless mode: {table_hits}"

        log_hits = [rec.getMessage() for rec in caplog.records if _SENTINEL in rec.getMessage()]
        assert not log_hits, f"Sentinel found in captured logs: {log_hits}"
    finally:
        _teardown_org(org["id"])


@pytest.mark.integration
def test_retained_mode_extraction_does_persist() -> None:
    """Control for the test above: retained mode DOES persist -- proves the gate actually
    distinguishes modes, not that persistence is broken for everyone."""
    org = _create_org("retained", retention_mode="retained", retention_days=30)
    key = _create_key(org["id"])
    retained_sentinel = f"{_SENTINEL}_RETAINED"

    try:
        with patch("app.api.v2.extract.extract_with_llm", new_callable=AsyncMock) as mock_llm:
            from app.core.schemas import ReviewExtractionLLMOutput, Sentiment, Urgency

            mock_llm.return_value = (
                ReviewExtractionLLMOutput(
                    product="Widget",
                    stars=None,
                    sentiment=Sentiment.positive,
                    urgency=Urgency.low,
                    topics=[],
                    competitor_mentions=[],
                    pros=[],
                    cons=[],
                    language="en",
                    confidence=0.9,
                ),
                "mock-model",
                42,
                150,
                80,
                False,
            )
            r = client.post(
                "/v2/extract",
                json={"text": f"{retained_sentinel} great product overall {retained_sentinel}"},
                headers={"X-API-Key": key["raw_key"]},
            )
        assert r.status_code == 200, r.text

        table_hits = _every_text_column_contains(retained_sentinel)
        assert "extractions.review_text" in table_hits, (
            f"Expected retained mode to persist review_text, found: {table_hits}"
        )
    finally:
        _teardown_org(org["id"])


@pytest.mark.integration
def test_csv_ingest_rejects_stateless_org() -> None:
    """P2e: CSV ingest requires retained mode -- a stateless org gets a clear 409, not a
    silent, partially-honored request."""
    org = _create_org("csv-stateless")
    key = _create_key(org["id"])

    try:
        csv_bytes = b"review_text\nGreat product overall\n"
        r = client.post(
            "/v2/ingest/csv",
            files={"file": ("reviews.csv", csv_bytes, "text/csv")},
            data={"text_column": "review_text"},
            headers={"X-API-Key": key["raw_key"]},
        )
        assert r.status_code == 409, r.text
        assert "retained mode" in r.json()["detail"]
    finally:
        _teardown_org(org["id"])


@pytest.mark.integration
def test_on_demand_purge_deletes_retained_rows() -> None:
    """P2c: POST /v2/purge deletes every stored extraction for the calling org, on demand."""
    org = _create_org("purge", retention_mode="retained", retention_days=90)
    key = _create_key(org["id"])

    try:
        with patch("app.api.v2.extract.extract_with_llm", new_callable=AsyncMock) as mock_llm:
            from app.core.schemas import ReviewExtractionLLMOutput, Sentiment, Urgency

            mock_llm.return_value = (
                ReviewExtractionLLMOutput(
                    product="Widget",
                    stars=None,
                    sentiment=Sentiment.positive,
                    urgency=Urgency.low,
                    topics=[],
                    competitor_mentions=[],
                    pros=[],
                    cons=[],
                    language="en",
                    confidence=0.9,
                ),
                "mock-model",
                42,
                150,
                80,
                False,
            )
            r = client.post(
                "/v2/extract",
                json={"text": "A review to be purged shortly."},
                headers={"X-API-Key": key["raw_key"]},
            )
        assert r.status_code == 200, r.text

        r = client.get("/v2/insights", headers={"X-API-Key": key["raw_key"]})
        assert r.json()["total_extractions"] == 1

        r = client.post("/v2/purge", headers={"X-API-Key": key["raw_key"]})
        assert r.status_code == 200, r.text
        assert r.json()["rows_deleted"] == 1

        r = client.get("/v2/insights", headers={"X-API-Key": key["raw_key"]})
        assert r.json()["total_extractions"] == 0
    finally:
        _teardown_org(org["id"])
