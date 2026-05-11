"""Unit tests for app.core.storage_pg — Postgres extractions repository."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from app.core.schemas import ReviewExtractionV2, ExtractionMetaV2, Sentiment, Urgency
from app.core.storage_pg import (
    aggregate_extractions_pg,
    get_by_hash_pg,
    list_extractions_pg,
    save_extraction_pg,
    update_usage_tokens,
)

_ORG_ID = str(uuid.uuid4())
_KEY_ID = str(uuid.uuid4())
_USAGE_ID = str(uuid.uuid4())
_NOW = datetime.now(tz=timezone.utc)
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
    #  latency_ms, extracted_at, input_hash)
    cur.fetchone.return_value = (
        "Widget", 4, None, None, "positive", "low",
        "en", 100, 0.9,
        json.dumps(["quality"]), json.dumps([]),
        json.dumps(["durable"]), json.dumps([]), json.dumps([]),
        "test-model", "v1", "1.0.0",
        100, _NOW, _HASH,
    )

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        result = get_by_hash_pg(_ORG_ID, _HASH)

    assert result is not None
    assert result.product == "Widget"
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
            _ORG_ID, _KEY_ID, _HASH, "great product", _make_extraction(),
            "test-model", "v1", "1.0.0", 100, False,
        )

    assert result == str(new_id)
    conn.commit.assert_called_once()


def test_save_extraction_pg_conflict_returns_empty_string() -> None:
    conn, cur = _make_conn()
    cur.fetchone.return_value = None  # ON CONFLICT DO NOTHING — no row returned

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        result = save_extraction_pg(
            _ORG_ID, _KEY_ID, _HASH, "great product", _make_extraction(),
            "test-model", "v1", "1.0.0", 100, False,
        )

    assert result == ""


def test_save_extraction_pg_error_triggers_rollback() -> None:
    conn, cur = _make_conn()
    cur.execute.side_effect = [None, None, Exception("DB error")]

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        with pytest.raises(Exception, match="DB error"):
            save_extraction_pg(
                _ORG_ID, _KEY_ID, _HASH, "great product", _make_extraction(),
                "test-model", "v1", "1.0.0", 100, False,
            )

    conn.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# update_usage_tokens
# ---------------------------------------------------------------------------


def test_update_usage_tokens_commits() -> None:
    conn, cur = _make_conn()

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        update_usage_tokens(_USAGE_ID, 150, 80)

    conn.commit.assert_called_once()
    sqls = [c[0][0] for c in cur.execute.call_args_list]
    assert any("UPDATE" in s and "tokens_in" in s and "tokens_out" in s for s in sqls)


def test_update_usage_tokens_passes_correct_values() -> None:
    conn, cur = _make_conn()

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        update_usage_tokens(_USAGE_ID, 333, 111)

    params = cur.execute.call_args_list[-1][0][1]  # last execute's positional params
    assert params[0] == 333  # tokens_in
    assert params[1] == 111  # tokens_out
    assert str(params[2]) == _USAGE_ID


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
