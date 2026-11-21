"""Persist side of the encoder-first skipped reviews (S29, flag OFF): storage SQL, ingest hook, purge."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from app.auth.api_key import ApiKeyContext
from app.core import storage_pg as SP
from app.core.classify.encoder_first import IngestPlan
from app.core.schemas import ReviewRequest

from tests.unit.test_storage_pg import _make_conn

ORG = str(uuid.uuid4())
HEX = "b" * 64
STORED = "sha256:" + HEX
CTX = ApiKeyContext(
    org_id=ORG, api_key_id=None, key_name="t", usage_record_id="", retention_mode="retained"
)
NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)


def _row(status: str = "classified_only", text: str | None = "The zip broke.") -> dict:
    return {
        "input_hash": STORED, "review_text": text, "review_date": NOW, "product": "Jacket",
        "classification": {"needs_action": {"label": "no"}, "urgency": {"label": "low"}},
        "status": status, "extraction_id": None, "created_at": NOW,
    }  # fmt: skip


# ------------------------------------------------------------------------------------------------------------- storage SQL


def test_save_is_tenant_scoped_and_idempotent_on_the_org_hash_key() -> None:
    conn, cur = _make_conn()
    cur.rowcount = 0
    with patch("app.core.storage_pg._db_connect", return_value=conn):
        inserted = SP.save_classified_review_pg(
            ORG, STORED, "t", None, None, {"urgency": {"label": "low"}}, "a" * 64
        )
    sqls = [c.args[0] for c in cur.execute.call_args_list]
    assert (
        sqls[0].startswith("SET LOCAL ROLE authenticated")
        and "ON CONFLICT (org_id, input_hash) DO NOTHING" in sqls[-1]
    )
    assert inserted is False  # a replayed identical review inserts nothing


def test_purge_is_a_no_op_before_the_migration_exists_and_deletes_by_org_after() -> None:
    conn, cur = _make_conn()
    cur.fetchone.return_value = (False,)
    with patch("app.core.storage_pg._db_connect", return_value=conn):
        assert SP.purge_org_classified_reviews_pg(ORG, None) == 0
    assert not any("DELETE" in c.args[0] for c in cur.execute.call_args_list)

    conn, cur = _make_conn()
    cur.fetchone.return_value = (True,)
    cur.rowcount = 3
    with patch("app.core.storage_pg._db_connect", return_value=conn):
        assert SP.purge_org_classified_reviews_pg(ORG, NOW) == 3
    assert (
        "DELETE FROM public.classified_reviews WHERE org_id = %s AND created_at < %s"
        in cur.execute.call_args_list[-1].args[0]
    )


@pytest.mark.asyncio
async def test_retention_sweep_purges_classified_reviews_next_to_extractions() -> None:
    from app.core import retention

    with (
        patch("app.core.retention.list_orgs_with_retained_mode_pg", return_value=[(ORG, 30)]),
        patch("app.core.retention.purge_org_extractions_pg", return_value=2),
        patch("app.core.retention.purge_org_classified_reviews_pg", return_value=5) as cls,
    ):
        out = await retention.purge_expired_extractions()
    assert out["rows_deleted"] == 7 and cls.call_args.args[0] == ORG


# ------------------------------------------------------------------------------------------------------------ ingest hook


@pytest.mark.asyncio
async def test_keep_classified_only_reports_failure_so_the_caller_extracts_instead() -> None:
    from app.core import ingest_worker as W

    req = ReviewRequest(text="fine")
    plan = IngestPlan(False, {"needs_action": {"label": "no"}, "extra": {"label": "x"}}, "c" * 64)
    with patch("app.core.ingest_worker.save_classified_review_pg", return_value=True) as save:
        assert await W._keep_classified_only(ORG, req, STORED, "P", plan) is True
    assert save.call_args.args[5] == {
        "needs_action": {"label": "no"}
    }  # only the four served tasks are kept
    with patch(
        "app.core.ingest_worker.save_classified_review_pg",
        side_effect=RuntimeError("no such table"),
    ):
        assert (
            await W._keep_classified_only(ORG, req, STORED, "P", plan) is False
        )  # never drop a review
