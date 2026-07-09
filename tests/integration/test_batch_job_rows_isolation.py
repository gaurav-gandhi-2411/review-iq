"""Integration tests: durable batch_job_rows queue (GG-mandated 4-part proof, 2026-07-09).

Covers, against the LIVE Supabase DB:

  1. RLS isolation (authenticated path) — org A cannot SELECT/UPDATE org B's
     batch_job_rows, sees only its own.
  2. Write-scope — the REAL worker (drain_rows -> _claim_one_row ->
     _run_extraction_v2) attributes every extraction to the claimed row's own
     org_id, never a shared/default org; each org's stored extraction is
     invisible under the other org.
  3. FOR UPDATE SKIP LOCKED — no row is ever claimed twice: (a) at the raw SQL
     level with two overlapping uncommitted transactions, (b) under two REAL
     concurrent drain_rows() callers racing on the same pending rows.
  4. Resume after a partial drain (simulated killed instance) + zero-LLM-cost
     input_hash dedup on reprocessing identical text.

Only the LLM boundary (app.api.v2.extract.extract_with_llm) and the alert
side-effect (app.api.v2.extract.alert_on_review_event) are patched — no Groq
calls, no live alert/email sends. The claim SQL, row/job storage writes, and
RLS enforcement all run for real against the live DB, per GG's explicit
authorization for this file (no DDL; table already exists per
supabase/migrations/20260709000001_batch_job_rows.sql).

Requires direct DB credentials (SUPABASE_DB_PASSWORD) in .env — same
connection + two-org fixture + authenticated-role simulation mechanics as
tests/integration/test_rls_isolation.py, copied verbatim below.

Marked 'integration' — skipped by default; run explicitly:
    uv run pytest tests/integration/test_batch_job_rows_isolation.py -v -m integration

Design reality (see fixture docstrings for detail): app.core.ingest_worker's
claim query (`SELECT ... WHERE status = 'pending' ... FOR UPDATE SKIP LOCKED`)
is global across ALL orgs/jobs, not scoped to this test's own job. Every
drain_rows()-touching test below therefore first asserts the queue is globally
quiescent (quiescent_queue fixture) and fails loudly rather than silently
trusting exact-count assertions while unrelated pending rows exist.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import psycopg2
import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env")

from app.core.config import get_settings  # noqa: E402
from app.core.ingest_worker import drain_rows  # noqa: E402
from app.core.ratelimit import llm_call_slot, reset_bulk_limiter_for_tests  # noqa: E402
from app.core.schemas import (  # noqa: E402
    ReviewExtractionLLMOutput,
    ReviewRequest,
    Sentiment,
    Urgency,
)
from app.core.storage_pg import (  # noqa: E402
    count_job_row_statuses_pg,
    count_pending_rows_pg,
    create_batch_job_pg,
    enqueue_batch_job_rows_pg,
    get_batch_job_pg,
    get_by_hash_pg,
)

_DB_PARAMS = {
    "host": "db.enqpluazgxewepchdeut.supabase.co",
    "port": 5432,
    "dbname": "postgres",
    "user": "postgres",
    "password": os.environ["SUPABASE_DB_PASSWORD"],
    "sslmode": "require",
    "connect_timeout": 15,
}


def _conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(**_DB_PARAMS)


def _as_authenticated(org_id: str) -> psycopg2.extensions.connection:
    """Return an open connection mid-transaction scoped to authenticated + org.

    Copied verbatim from tests/integration/test_rls_isolation.py.
    """
    conn = _conn()
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute("SET LOCAL ROLE authenticated")
    cur.execute('SET LOCAL "app.current_org_id" = %s', (org_id,))
    return conn


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def two_orgs() -> Iterator[tuple[str, str]]:
    """Create org A and org B for one test; cascade-delete both on teardown.

    organizations -> batch_jobs -> batch_job_rows and -> extractions are all
    ON DELETE CASCADE (see supabase/migrations/20260709000001_batch_job_rows.sql
    and 20260511000006_batch_jobs.sql), so deleting the two orgs is sufficient
    cleanup — teardown additionally verifies the cascade actually happened.
    """
    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())

    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO public.organizations (id, name, slug) VALUES "
            "(%s, 'Batch Row Org A', %s), (%s, 'Batch Row Org B', %s)",
            (org_a, f"bjr-a-{org_a[:8]}", org_b, f"bjr-b-{org_b[:8]}"),
        )
        conn.commit()
    finally:
        conn.close()

    yield org_a, org_b

    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM public.organizations WHERE id IN (%s, %s)", (org_a, org_b))
        conn.commit()

        # Verify the cascade actually removed every row scoped to these orgs —
        # a silent cascade failure would leak stray pending rows into a later
        # test's quiescent_queue guard below.
        for table in ("batch_job_rows", "batch_jobs", "extractions"):
            cur.execute(
                f"SELECT COUNT(*) FROM public.{table} WHERE org_id IN (%s, %s)",  # noqa: S608
                (org_a, org_b),
            )
            (remaining,) = cur.fetchone()
            assert remaining == 0, f"{table} still has rows for a deleted org — cascade failed"
        conn.commit()
    finally:
        conn.close()


def _count_pending_rows_globally() -> int:
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM public.batch_job_rows WHERE status = 'pending'")
        (count,) = cur.fetchone()
        return int(count)
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def quiescent_queue() -> None:
    """Guard for every drain_rows()-touching test below.

    drain_rows() claims the oldest pending row across ALL orgs and jobs, not
    scoped to this test's own job (see app/core/ingest_worker.py's claim
    query: `WHERE status = 'pending' ORDER BY updated_at ... FOR UPDATE SKIP
    LOCKED`, no job_id/org_id filter). A real stuck job or a stray row left
    behind by a failed prior run would silently participate in this test's
    drain_rows() calls — inflating the exact-count assertions below, or worse,
    writing this test's canned LLM output onto someone else's live row. Fail
    loudly here instead of masking that.
    """
    pending = _count_pending_rows_globally()
    if pending:
        pytest.fail(
            f"batch_job_rows has {pending} pending row(s) system-wide before this "
            "test started. drain_rows() claims globally across all orgs, so this "
            "test's exact-count assertions cannot be trusted while other pending "
            "rows exist — investigate/clear stray pending rows before re-running."
        )


@pytest.fixture
def fast_bulk_limiter() -> Iterator[None]:
    """Boost Option A's bulk rate limiter (app/core/ratelimit.py) to effectively
    unthrottled for this test, WITHOUT bypassing it.

    drain_rows() always classifies its call tree as bulk via
    set_bulk_call_class(); every stub below still does `async with
    llm_call_slot():` for real, so the gate is genuinely exercised on every
    call. At the production default (BULK_LLM_CALLS_PER_MINUTE=2,
    BULK_LLM_MAX_CONCURRENCY=1) a 4-5 call test would take tens of seconds to
    minutes waiting on the token bucket; this fixture raises both knobs for
    the duration of the test only, and restores them + resets the
    process-global limiter afterwards so no other test observes the change.
    """
    settings = get_settings()
    original_rate = settings.bulk_llm_calls_per_minute
    original_concurrency = settings.bulk_llm_max_concurrency
    settings.bulk_llm_calls_per_minute = 100_000.0
    settings.bulk_llm_max_concurrency = 8
    reset_bulk_limiter_for_tests()
    try:
        yield
    finally:
        settings.bulk_llm_calls_per_minute = original_rate
        settings.bulk_llm_max_concurrency = original_concurrency
        reset_bulk_limiter_for_tests()


def _seed_job(org_id: str, texts: list[str]) -> str:
    """Create a batch job + its pending rows via the REAL service-path storage
    functions (create_batch_job_pg + enqueue_batch_job_rows_pg) — the same
    path POST /v2/extract/batch uses. Returns the new job_id.
    """
    job_id = str(uuid.uuid4())
    create_batch_job_pg(org_id, job_id, len(texts))
    enqueue_batch_job_rows_pg(org_id, job_id, texts)
    return job_id


def _canned_llm_tuple() -> tuple[ReviewExtractionLLMOutput, str, int, int, int, bool]:
    """A fresh, valid canned LLM-boundary result — matches the 6-tuple contract of
    app.core.llm.extract_with_llm: (llm_output, model, latency_ms, tokens_in,
    tokens_out, degraded).

    Returns a NEW ReviewExtractionLLMOutput instance every call — never a
    shared mutable object — because app.api.v2.extract._run_extraction_v2
    mutates `llm_output.language` in place after receiving it.
    """
    return (
        ReviewExtractionLLMOutput(
            product="Test Widget",
            stars=5,
            sentiment=Sentiment.positive,
            urgency=Urgency.low,
            topics=["quality"],
            competitor_mentions=[],
            pros=["solid build"],
            cons=[],
            language="en",
            confidence=0.9,
        ),
        "mock-model",
        10,
        5,
        5,
        False,
    )


# ---------------------------------------------------------------------------
# Proof 1 — RLS isolation (authenticated path)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBatchJobRowsRLSIsolation:
    """Proof 1 of 4: org A cannot SELECT or UPDATE org B's batch_job_rows."""

    def test_org_a_sees_only_own_row(self, two_orgs: tuple[str, str]) -> None:
        org_a, org_b = two_orgs
        job_a = _seed_job(org_a, [f"rls-proof-a-{uuid.uuid4().hex[:8]}"])
        job_b = _seed_job(org_b, [f"rls-proof-b-{uuid.uuid4().hex[:8]}"])

        conn = _as_authenticated(org_a)
        try:
            cur = conn.cursor()
            cur.execute("SELECT job_id FROM public.batch_job_rows")
            visible = {r[0] for r in cur.fetchall()}
        finally:
            conn.rollback()
            conn.close()

        assert job_a in visible, "Org A must see its own batch_job_rows"
        assert job_b not in visible, "Org A must NOT see org B's batch_job_rows"

    def test_org_b_sees_only_own_row(self, two_orgs: tuple[str, str]) -> None:
        org_a, org_b = two_orgs
        job_a = _seed_job(org_a, [f"rls-proof-a-{uuid.uuid4().hex[:8]}"])
        job_b = _seed_job(org_b, [f"rls-proof-b-{uuid.uuid4().hex[:8]}"])

        conn = _as_authenticated(org_b)
        try:
            cur = conn.cursor()
            cur.execute("SELECT job_id FROM public.batch_job_rows")
            visible = {r[0] for r in cur.fetchall()}
        finally:
            conn.rollback()
            conn.close()

        assert job_b in visible, "Org B must see its own batch_job_rows"
        assert job_a not in visible, "Org B must NOT see org A's batch_job_rows"

    def test_org_a_cannot_update_org_b_row(self, two_orgs: tuple[str, str]) -> None:
        org_a, org_b = two_orgs
        _seed_job(org_a, [f"rls-proof-a-{uuid.uuid4().hex[:8]}"])
        job_b = _seed_job(org_b, [f"rls-proof-b-{uuid.uuid4().hex[:8]}"])

        conn = _as_authenticated(org_a)
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE public.batch_job_rows SET status = 'failed' WHERE job_id = %s",
                (job_b,),
            )
            assert cur.rowcount == 0, "UPDATE of cross-tenant batch_job_rows must affect 0 rows"
        finally:
            conn.rollback()
            conn.close()


# ---------------------------------------------------------------------------
# Proof 2 — write-scope: extractions land under the row's OWN org_id
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_drain_writes_each_extraction_under_its_own_org(
    two_orgs: tuple[str, str], quiescent_queue: None
) -> None:
    """Proof 2 of 4: the REAL worker (drain_rows -> _claim_one_row ->
    _run_extraction_v2) — only the LLM boundary and alert side-effect are
    patched. Org A's extraction lands under org A's org_id, org B's under org
    B's, and neither is visible under the other org's context.
    """
    org_a, org_b = two_orgs
    text_a = f"Proof2 org A widget review {uuid.uuid4().hex[:10]}"
    text_b = f"Proof2 org B widget review {uuid.uuid4().hex[:10]}"
    job_a = _seed_job(org_a, [text_a])
    job_b = _seed_job(org_b, [text_b])

    hash_a = ReviewRequest(text=text_a).input_hash()
    hash_b = ReviewRequest(text=text_b).input_hash()

    with (
        patch(
            "app.api.v2.extract.extract_with_llm",
            new=AsyncMock(side_effect=lambda *a, **k: _canned_llm_tuple()),
        ),
        patch("app.api.v2.extract.alert_on_review_event", new=AsyncMock(return_value=None)),
    ):
        result = await drain_rows(max_rows=10)

    assert result["claimed"] == 2
    assert result["processed"] == 2
    assert result["failed"] == 0

    ext_a = get_by_hash_pg(org_a, hash_a)
    ext_b = get_by_hash_pg(org_b, hash_b)
    assert ext_a is not None, "org A's extraction must be stored"
    assert ext_a.extraction_meta is not None
    assert ext_a.extraction_meta.org_id == org_a
    assert ext_b is not None, "org B's extraction must be stored"
    assert ext_b.extraction_meta is not None
    assert ext_b.extraction_meta.org_id == org_b

    # Cross-tenant visibility: org A's extraction is invisible under org B's
    # org-scoped query and vice versa.
    assert get_by_hash_pg(org_b, hash_a) is None, "org B must not see org A's extraction"
    assert get_by_hash_pg(org_a, hash_b) is None, "org A must not see org B's extraction"

    done_a, failed_a = count_job_row_statuses_pg(org_a, job_a)
    done_b, failed_b = count_job_row_statuses_pg(org_b, job_b)
    assert (done_a, failed_a) == (1, 0)
    assert (done_b, failed_b) == (1, 0)

    job_a_record = get_batch_job_pg(org_a, job_a)
    job_b_record = get_batch_job_pg(org_b, job_b)
    assert job_a_record is not None and job_a_record["status"] == "done"
    assert job_b_record is not None and job_b_record["status"] == "done"


# ---------------------------------------------------------------------------
# Proof 3 — FOR UPDATE SKIP LOCKED: no double-processing
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_for_update_skip_locked_returns_different_rows_at_sql_level(
    two_orgs: tuple[str, str], quiescent_queue: None
) -> None:
    """Proof 3a of 4 (SQL level): a second connection's claim query, issued
    while the first connection holds its FOR UPDATE lock uncommitted, must
    land on a DIFFERENT row (or none, if only one was pending) — never the
    same row twice.

    Copies the exact claim SQL from app.core.ingest_worker._claim_one_row.
    """
    org_a, _org_b = two_orgs
    _seed_job(
        org_a,
        [f"skiplocked-a-{uuid.uuid4().hex[:8]}", f"skiplocked-b-{uuid.uuid4().hex[:8]}"],
    )

    claim_sql = (
        "SELECT job_id, row_index, org_id, text FROM public.batch_job_rows "
        "WHERE status = 'pending' ORDER BY updated_at LIMIT 1 FOR UPDATE SKIP LOCKED"
    )

    conn1 = _conn()
    conn1.autocommit = False
    conn2 = _conn()
    conn2.autocommit = False
    try:
        cur1 = conn1.cursor()
        cur1.execute(claim_sql)
        row1 = cur1.fetchone()
        assert row1 is not None, "expected at least one pending row for conn1 to claim"

        cur2 = conn2.cursor()
        cur2.execute(claim_sql)
        row2 = cur2.fetchone()

        assert row2 is None or (row2[0], row2[1]) != (row1[0], row1[1]), (
            "conn2 must skip conn1's locked row (SKIP LOCKED) — got the same "
            f"(job_id, row_index) twice: {row1[:2]}"
        )
    finally:
        conn1.rollback()
        conn1.close()
        conn2.rollback()
        conn2.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_drain_rows_never_double_processes_a_row(
    two_orgs: tuple[str, str], quiescent_queue: None, fast_bulk_limiter: None
) -> None:
    """Proof 3b of 4 (worker level): two REAL drain_rows() callers racing on the
    same 4 pending rows must together process each row EXACTLY once — no
    double-processing, no lost row.
    """
    org_a, _org_b = two_orgs
    run_marker = f"proof3b-{uuid.uuid4().hex[:10]}"
    texts = [f"{run_marker} distinct-row-{i} padding text for extraction" for i in range(4)]
    job_id = _seed_job(org_a, texts)

    call_log: list[str] = []

    async def _stub(prompt: str, allow_gemini_fallback: bool = False) -> tuple:
        # Option A gate genuinely exercised (not bypassed) — see fast_bulk_limiter.
        async with llm_call_slot():
            if run_marker in prompt:
                match = re.search(r"distinct-row-(\d+)", prompt)
                call_log.append(match.group(0) if match else prompt)
            await asyncio.sleep(0.05)
        return _canned_llm_tuple()

    with (
        patch("app.api.v2.extract.extract_with_llm", new=AsyncMock(side_effect=_stub)),
        patch("app.api.v2.extract.alert_on_review_event", new=AsyncMock(return_value=None)),
    ):
        await asyncio.gather(drain_rows(max_rows=4), drain_rows(max_rows=4))

    assert len(call_log) == 4, f"expected exactly 4 LLM calls for our own rows, got {call_log}"
    assert len(set(call_log)) == 4, f"a row was processed more than once: {call_log}"

    done, failed = count_job_row_statuses_pg(org_a, job_id)
    assert (done, failed) == (4, 0)


# ---------------------------------------------------------------------------
# Proof 4 — resume after partial drain + zero-LLM-cost dedup on reprocessing
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_resume_after_partial_drain_and_zero_cost_dedup(
    two_orgs: tuple[str, str], quiescent_queue: None, fast_bulk_limiter: None
) -> None:
    """Proof 4 of 4: a killed-instance-style partial drain resumes cleanly on
    the next tick with no row processed twice or lost, and reprocessing
    identical review text (a duplicate row in a new job) costs zero new LLM
    calls (cache hit in _run_extraction_v2 via input_hash).
    """
    org_a, _org_b = two_orgs
    run_marker = f"proof4-{uuid.uuid4().hex[:10]}"
    texts = [f"{run_marker} unique review body number {i} padding text" for i in range(5)]
    job_id = _seed_job(org_a, texts)

    call_count = 0

    async def _counting_stub(prompt: str, allow_gemini_fallback: bool = False) -> tuple:
        nonlocal call_count
        if run_marker in prompt:
            call_count += 1
        return _canned_llm_tuple()

    with (
        patch("app.api.v2.extract.extract_with_llm", new=AsyncMock(side_effect=_counting_stub)),
        patch("app.api.v2.extract.alert_on_review_event", new=AsyncMock(return_value=None)),
    ):
        # First tick: simulates a Cloud Run instance killed mid-drain — only 2
        # of the 5 pending rows get claimed.
        first = await drain_rows(max_rows=2)
        assert first["claimed"] == 2

        done, failed = count_job_row_statuses_pg(org_a, job_id)
        assert (done, failed) == (2, 0)
        assert count_pending_rows_pg(org_a, job_id) == 3
        job_mid = get_batch_job_pg(org_a, job_id)
        assert job_mid is not None
        assert job_mid["status"] not in ("done", "failed"), (
            "job must not be finalized while rows are still pending"
        )

        # Next tick: resumes the remaining rows — nothing reprocessed, nothing lost.
        second = await drain_rows(max_rows=10)
        assert second["claimed"] == 3

        done, failed = count_job_row_statuses_pg(org_a, job_id)
        assert (done, failed) == (5, 0)
        assert count_pending_rows_pg(org_a, job_id) == 0
        job_final = get_batch_job_pg(org_a, job_id)
        assert job_final is not None
        assert job_final["status"] == "done"
        assert call_count == 5, "each of the 5 rows must have called the LLM boundary exactly once"

        # Dedup proof: a NEW job whose single row has IDENTICAL text to an
        # already-processed row must hit the extraction cache in
        # _run_extraction_v2 (keyed on input_hash) — zero new LLM calls.
        dup_text = texts[0]
        dup_job_id = _seed_job(org_a, [dup_text])
        third = await drain_rows(max_rows=10)
        assert third["claimed"] == 1
        assert third["processed"] == 1

        done, failed = count_job_row_statuses_pg(org_a, dup_job_id)
        assert (done, failed) == (1, 0)
        dup_job_final = get_batch_job_pg(org_a, dup_job_id)
        assert dup_job_final is not None
        assert dup_job_final["status"] == "done"
        assert call_count == 5, "duplicate text must be served from cache — zero new LLM calls"
