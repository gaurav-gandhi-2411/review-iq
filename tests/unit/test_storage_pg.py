"""Unit tests for app.core.storage_pg — Postgres extractions repository."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from app.core.schemas import ExtractionMetaV2, ReviewExtractionV2, Sentiment, Urgency
from app.core.storage_pg import (
    aggregate_extractions_pg,
    count_job_row_statuses_pg,
    count_pending_rows_pg,
    create_batch_job_pg,
    enqueue_batch_job_rows_pg,
    get_batch_job_pg,
    get_by_hash_pg,
    list_dated_extractions_pg,
    list_extractions_pg,
    list_job_row_hashes_pg,
    list_orgs_with_dated_extractions_pg,
    record_quota_request_pg,
    save_extraction_pg,
    update_batch_job_pg,
    update_usage_tokens,
)

_ORG_ID = str(uuid.uuid4())
_KEY_ID = str(uuid.uuid4())
_USAGE_ID = str(uuid.uuid4())
_NOW = datetime.now(tz=UTC)
_HASH = "sha256:" + "a" * 64


def _make_conn() -> tuple[MagicMock, MagicMock]:
    cur = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def _make_extraction() -> ReviewExtractionV2:
    meta = ExtractionMetaV2(
        model="test-model",
        prompt_version="v1",
        schema_version="1.0.0",
        extracted_at=_NOW,
        latency_ms=100,
        input_hash=_HASH,
        org_id=_ORG_ID,
    )
    return ReviewExtractionV2(
        product="Widget",
        stars=4,
        sentiment=Sentiment.positive,
        urgency=Urgency.low,
        topics=["quality"],
        competitor_mentions=[],
        pros=["durable"],
        cons=[],
        feature_requests=[],
        extraction_meta=meta,
    )


# ---------------------------------------------------------------------------
# get_by_hash_pg
# ---------------------------------------------------------------------------


def test_get_by_hash_pg_cache_miss_returns_none() -> None:
    conn, cur = _make_conn()
    cur.fetchone.return_value = None

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        result = get_by_hash_pg(_ORG_ID, _HASH)

    assert result is None
    conn.commit.assert_called_once()


def test_get_by_hash_pg_cache_hit_returns_extraction() -> None:
    conn, cur = _make_conn()
    # (product, stars, stars_inferred, buy_again, sentiment, urgency,
    #  language, review_length_chars, confidence, topics, competitor_mentions,
    #  pros, cons, feature_requests, model, prompt_version, schema_version,
    #  latency_ms, extracted_at, input_hash, review_date)
    cur.fetchone.return_value = (
        "Widget",
        4,
        None,
        None,
        "positive",
        "low",
        "en",
        100,
        0.9,
        json.dumps(["quality"]),
        json.dumps([]),
        json.dumps(["durable"]),
        json.dumps([]),
        json.dumps([]),
        "test-model",
        "v1",
        "1.0.0",
        100,
        _NOW,
        _HASH,
        None,
    )

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        result = get_by_hash_pg(_ORG_ID, _HASH)

    assert result is not None
    assert result.product == "Widget"
    assert result.review_date is None
    assert result.extraction_meta is not None
    assert result.extraction_meta.org_id == _ORG_ID


def test_get_by_hash_pg_sets_rls_context() -> None:
    conn, cur = _make_conn()
    cur.fetchone.return_value = None

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        get_by_hash_pg(_ORG_ID, _HASH)

    sqls = [c[0][0] for c in cur.execute.call_args_list]
    assert any("SET LOCAL ROLE" in s for s in sqls)
    assert any("app.current_org_id" in s for s in sqls)


# ---------------------------------------------------------------------------
# save_extraction_pg
# ---------------------------------------------------------------------------


def test_save_extraction_pg_returns_id() -> None:
    conn, cur = _make_conn()
    new_id = uuid.uuid4()
    cur.fetchone.return_value = (new_id,)

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        result = save_extraction_pg(
            _ORG_ID,
            _KEY_ID,
            _HASH,
            "great product",
            _make_extraction(),
            "test-model",
            "v1",
            "1.0.0",
            100,
            False,
        )

    assert result == str(new_id)
    conn.commit.assert_called_once()


def test_save_extraction_pg_conflict_returns_empty_string() -> None:
    conn, cur = _make_conn()
    cur.fetchone.return_value = None  # ON CONFLICT DO NOTHING — no row returned

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        result = save_extraction_pg(
            _ORG_ID,
            _KEY_ID,
            _HASH,
            "great product",
            _make_extraction(),
            "test-model",
            "v1",
            "1.0.0",
            100,
            False,
        )

    assert result == ""


def test_save_extraction_pg_error_triggers_rollback() -> None:
    conn, cur = _make_conn()
    cur.execute.side_effect = [None, None, Exception("DB error")]

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        with pytest.raises(Exception, match="DB error"):
            save_extraction_pg(
                _ORG_ID,
                _KEY_ID,
                _HASH,
                "great product",
                _make_extraction(),
                "test-model",
                "v1",
                "1.0.0",
                100,
                False,
            )

    conn.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# update_usage_tokens
# ---------------------------------------------------------------------------


def test_update_usage_tokens_commits() -> None:
    conn, cur = _make_conn()

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        update_usage_tokens(_ORG_ID, _USAGE_ID, 150, 80)

    conn.commit.assert_called_once()
    sqls = [c[0][0] for c in cur.execute.call_args_list]
    assert any("UPDATE" in s and "tokens_in" in s and "tokens_out" in s for s in sqls)


def test_update_usage_tokens_passes_correct_values() -> None:
    conn, cur = _make_conn()

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        update_usage_tokens(_ORG_ID, _USAGE_ID, 333, 111)

    params = cur.execute.call_args_list[-1][0][1]  # last execute's positional params
    assert params[0] == 333  # tokens_in
    assert params[1] == 111  # tokens_out
    assert str(params[2]) == _USAGE_ID
    assert str(params[3]) == _ORG_ID  # org_id in WHERE clause -- app-level scoping


def test_update_usage_tokens_sets_rls_context() -> None:
    conn, cur = _make_conn()

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        update_usage_tokens(_ORG_ID, _USAGE_ID, 100, 50)

    sqls = [c[0][0] for c in cur.execute.call_args_list]
    assert any("SET LOCAL ROLE" in s for s in sqls)
    assert any("app.current_org_id" in s for s in sqls)


def test_update_usage_tokens_scoped_query_cannot_touch_another_org() -> None:
    """A row belonging to a different org must never match this WHERE clause --
    proves the fix isn't just cosmetic (org_id is a real predicate, not decoration)."""
    conn, cur = _make_conn()
    other_org = str(uuid.uuid4())

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        update_usage_tokens(other_org, _USAGE_ID, 42, 7)

    sql, params = cur.execute.call_args_list[-1][0]
    assert "org_id = %s" in sql
    assert "id = %s" in sql
    assert str(params[3]) == other_org
    # Both id AND org_id must match for the UPDATE to touch a row -- an attacker who
    # somehow supplied a foreign usage_record_id with their own org_id gets 0 rows
    # affected, not another org's row.


# ---------------------------------------------------------------------------
# Batch-job / batch_job_rows helpers now get the same RLS-context backstop as
# get_by_hash_pg/save_extraction_pg above (previously only the WHERE clause
# scoped them -- no _set_tenant call, so no RLS backstop if a WHERE clause
# were ever dropped in a future edit).
# ---------------------------------------------------------------------------

_JOB_ID = "job-" + uuid.uuid4().hex[:8]


def _assert_sets_rls_context(cur: MagicMock) -> None:
    sqls = [c[0][0] for c in cur.execute.call_args_list]
    assert any("SET LOCAL ROLE" in s for s in sqls), "expected a SET LOCAL ROLE call"
    assert any("app.current_org_id" in s for s in sqls), "expected app.current_org_id to be set"


def test_create_batch_job_pg_sets_rls_context() -> None:
    conn, cur = _make_conn()
    with patch("app.core.storage_pg._db_connect", return_value=conn):
        create_batch_job_pg(_ORG_ID, _JOB_ID, 10)
    _assert_sets_rls_context(cur)


def test_get_batch_job_pg_sets_rls_context() -> None:
    conn, cur = _make_conn()
    cur.fetchone.return_value = None
    with patch("app.core.storage_pg._db_connect", return_value=conn):
        get_batch_job_pg(_ORG_ID, _JOB_ID)
    _assert_sets_rls_context(cur)


def test_update_batch_job_pg_sets_rls_context() -> None:
    conn, cur = _make_conn()
    with patch("app.core.storage_pg._db_connect", return_value=conn):
        update_batch_job_pg(_ORG_ID, _JOB_ID, status="done")
    _assert_sets_rls_context(cur)


def test_enqueue_batch_job_rows_pg_sets_rls_context() -> None:
    conn, cur = _make_conn()
    with patch("app.core.storage_pg._db_connect", return_value=conn):
        enqueue_batch_job_rows_pg(_ORG_ID, _JOB_ID, ["review one", "review two"])
    _assert_sets_rls_context(cur)


def test_count_pending_rows_pg_sets_rls_context() -> None:
    conn, cur = _make_conn()
    cur.fetchone.return_value = (0,)
    with patch("app.core.storage_pg._db_connect", return_value=conn):
        count_pending_rows_pg(_ORG_ID, _JOB_ID)
    _assert_sets_rls_context(cur)


def test_count_job_row_statuses_pg_sets_rls_context() -> None:
    conn, cur = _make_conn()
    cur.fetchone.return_value = (0, 0)
    with patch("app.core.storage_pg._db_connect", return_value=conn):
        count_job_row_statuses_pg(_ORG_ID, _JOB_ID)
    _assert_sets_rls_context(cur)


def test_list_job_row_hashes_pg_sets_rls_context() -> None:
    conn, cur = _make_conn()
    cur.fetchall.return_value = []
    with patch("app.core.storage_pg._db_connect", return_value=conn):
        list_job_row_hashes_pg(_ORG_ID, _JOB_ID)
    _assert_sets_rls_context(cur)


def test_record_quota_request_pg_sets_rls_context() -> None:
    """quota_requests was found live with no RLS + dangerous default anon grants
    (fixed 2026-07-11, 20260711000002_quota_requests_rls.sql) -- this proves the
    paired app-code fix, _set_tenant() here, matches the rest of the module."""
    conn, cur = _make_conn()
    # record_quota_request_pg uses psycopg2.connect() directly, not the module's
    # _db_connect() helper -- patch at the source.
    with patch("psycopg2.connect", return_value=conn):
        record_quota_request_pg(_ORG_ID, 50, 100, "interested in higher quota")
    _assert_sets_rls_context(cur)


def test_get_batch_job_pg_where_clause_scopes_by_org_and_job() -> None:
    """The app-level WHERE clause must still require BOTH org_id and job_id --
    RLS is the backstop, not a replacement for the explicit predicate."""
    conn, cur = _make_conn()
    cur.fetchone.return_value = None
    other_org = str(uuid.uuid4())

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        get_batch_job_pg(other_org, _JOB_ID)

    sql, params = cur.execute.call_args_list[-1][0]
    assert "org_id = %s" in sql
    assert "job_id = %s" in sql
    assert str(params[1]) == other_org


# ---------------------------------------------------------------------------
# list_extractions_pg
# ---------------------------------------------------------------------------


def test_list_extractions_pg_empty_result() -> None:
    conn, cur = _make_conn()
    cur.fetchall.return_value = []

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        result = list_extractions_pg(_ORG_ID)

    assert result == []
    conn.commit.assert_called_once()


def test_list_extractions_pg_sets_rls_context() -> None:
    conn, cur = _make_conn()
    cur.fetchall.return_value = []

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        list_extractions_pg(_ORG_ID)

    sqls = [c[0][0] for c in cur.execute.call_args_list]
    assert any("SET LOCAL ROLE" in s for s in sqls)


def test_list_extractions_pg_filters_appended() -> None:
    conn, cur = _make_conn()
    cur.fetchall.return_value = []

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        list_extractions_pg(_ORG_ID, sentiment=Sentiment.positive, product="Widget")

    select_call = [c for c in cur.execute.call_args_list if "SELECT" in (c[0][0] or "")]
    assert select_call, "Expected a SELECT call"
    sql = select_call[0][0][0]
    assert "ILIKE" in sql
    assert "sentiment" in sql


# ---------------------------------------------------------------------------
# list_dated_extractions_pg
# ---------------------------------------------------------------------------


def test_list_dated_extractions_pg_empty_result() -> None:
    conn, cur = _make_conn()
    cur.fetchall.return_value = []

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        result = list_dated_extractions_pg(_ORG_ID)

    assert result == []
    conn.commit.assert_called_once()


def test_list_dated_extractions_pg_sets_rls_context() -> None:
    conn, cur = _make_conn()
    cur.fetchall.return_value = []

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        list_dated_extractions_pg(_ORG_ID)

    sqls = [c[0][0] for c in cur.execute.call_args_list]
    assert any("SET LOCAL ROLE" in s for s in sqls)


def test_list_dated_extractions_pg_filters_null_review_date() -> None:
    conn, cur = _make_conn()
    cur.fetchall.return_value = []

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        list_dated_extractions_pg(_ORG_ID)

    select_call = [c for c in cur.execute.call_args_list if "SELECT" in (c[0][0] or "")]
    assert select_call, "Expected a SELECT call"
    sql = select_call[0][0][0]
    assert "review_date IS NOT NULL" in sql


def test_list_dated_extractions_pg_maps_row_shape() -> None:
    conn, cur = _make_conn()
    # (id, product, topics, sentiment, review_date, review_text) -- topics as a raw JSON string,
    # exercising the isinstance guard rather than assuming psycopg2 always auto-decodes jsonb.
    cur.fetchall.return_value = [
        (uuid.uuid4(), "Widget", json.dumps(["battery"]), "negative", _NOW, "Battery died fast"),
    ]

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        result = list_dated_extractions_pg(_ORG_ID)

    assert len(result) == 1
    row = result[0]
    assert row["review_text"] == "Battery died fast"
    assert row["product"] == "Widget"
    assert row["topics"] == ["battery"]
    assert row["sentiment"] == "negative"
    assert row["review_date"] == _NOW


# ---------------------------------------------------------------------------
# list_orgs_with_dated_extractions_pg
# ---------------------------------------------------------------------------


def test_list_orgs_with_dated_extractions_pg_empty_result() -> None:
    conn, cur = _make_conn()
    cur.fetchall.return_value = []

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        result = list_orgs_with_dated_extractions_pg()

    assert result == []


def test_list_orgs_with_dated_extractions_pg_maps_ids() -> None:
    conn, cur = _make_conn()
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    cur.fetchall.return_value = [(org_a,), (org_b,)]

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        result = list_orgs_with_dated_extractions_pg()

    assert result == [str(org_a), str(org_b)]


def test_list_orgs_with_dated_extractions_pg_does_not_set_tenant() -> None:
    """Cross-org query -- deliberately does NOT call _set_tenant. Sees every org via a
    SECURITY DEFINER function (public.list_orgs_with_dated_extractions, 20260817000003),
    same pattern as list_orgs_with_daily_digest_pg. Confirms no SET LOCAL ROLE call is
    issued -- there's nothing to scope to a single org here."""
    conn, cur = _make_conn()
    cur.fetchall.return_value = []

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        list_orgs_with_dated_extractions_pg()

    sqls = [c[0][0] for c in cur.execute.call_args_list]
    assert not any("SET LOCAL ROLE" in s for s in sqls)


# ---------------------------------------------------------------------------
# aggregate_extractions_pg
# ---------------------------------------------------------------------------


def test_aggregate_extractions_pg_returns_expected_shape() -> None:
    conn, cur = _make_conn()
    # Summary: total, pos, neg, neu, mix
    # Urgency: rows
    # Topics: rows
    # Competitors: rows
    cur.fetchone.return_value = (5, 3, 1, 1, 0)
    cur.fetchall.side_effect = [
        [("low", 4), ("medium", 1)],
        [("quality", 3), ("price", 2)],
        [("CompetitorX", 2)],
    ]

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        result = aggregate_extractions_pg(_ORG_ID)

    assert result["total_extractions"] == 5
    assert result["sentiment_breakdown"]["positive"] == 3
    assert len(result["top_topics"]) == 2
    assert result["top_topics"][0]["topic"] == "quality"
    assert result["top_competitor_mentions"][0]["competitor"] == "CompetitorX"
    conn.commit.assert_called_once()
