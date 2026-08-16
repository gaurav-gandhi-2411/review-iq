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

LIVE SCHEDULER RACE (found + fixed 2026-07-10): the quiescent_queue guard above
only checks the queue is empty at test START -- it cannot protect against the
LIVE `review-iq-ingest-tick` Cloud Scheduler job (fires every 2 minutes against
the real deployed API, reviewiq-prod-260813 project) claiming/processing one of a
test's own rows mid-run via the SAME global drain_rows() claim query. Confirmed
via Cloud Scheduler execution logs: firings at 15:44-15:52 UTC exactly
overlapped a local suite run's 15:44-15:52 UTC window, producing two different
non-deterministic failure shapes (an LLM-boundary-patch call_count mismatch,
and a stray extra "done" row). GG-approved fix: pause_prod_scheduler below
pauses the job for the whole test session and resumes it in a finally block,
guaranteed even on test failure -- NOT guaranteed against a hard process kill
(SIGKILL) mid-run, which would leave the job paused; if that happens, resume
manually: `gcloud scheduler jobs resume review-iq-ingest-tick
--location=asia-south1 --project=reviewiq-prod-260813`.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import psycopg2
import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env")

_SCHEDULER_JOB = "review-iq-ingest-tick"
_SCHEDULER_LOCATION = "asia-south1"
_SCHEDULER_PROJECT = "reviewiq-prod-260813"


def _scheduler_cmd(action: str) -> str:
    # shell=True + a joined string, not a list -- on Windows, `gcloud` resolves to
    # gcloud.cmd, a batch-file wrapper that subprocess cannot CreateProcess() directly
    # without shell interpretation (confirmed via FileNotFoundError [WinError 2] before
    # this fix). Every argument here is a hardcoded constant, never user input, so
    # shell=True carries no injection risk.
    return (
        f"gcloud scheduler jobs {action} {_SCHEDULER_JOB} "
        f"--location={_SCHEDULER_LOCATION} --project={_SCHEDULER_PROJECT} --quiet"
    )


@pytest.fixture(scope="module", autouse=True)
def pause_prod_scheduler() -> Iterator[None]:
    """Pause the LIVE review-iq-ingest-tick Cloud Scheduler job for this whole test
    module's run, resume it in a finally block -- see module docstring's "LIVE
    SCHEDULER RACE" section for why this exists. Runs once for the whole module
    (not per-test) to minimize gcloud API calls and avoid repeatedly flapping a
    real production schedule.
    """
    result = subprocess.run(
        _scheduler_cmd("pause"), shell=True, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        pytest.fail(
            f"Could not pause {_SCHEDULER_JOB} before running -- refusing to proceed "
            f"with a suite known to race it: {result.stderr}"
        )
    print(f"\n[pause_prod_scheduler] paused {_SCHEDULER_JOB}")
    try:
        yield
    finally:
        result = subprocess.run(
            _scheduler_cmd("resume"), shell=True, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            print(
                f"\n[pause_prod_scheduler] WARNING: failed to resume {_SCHEDULER_JOB} -- "
                f"resume manually: gcloud scheduler jobs resume {_SCHEDULER_JOB} "
                f"--location={_SCHEDULER_LOCATION} --project={_SCHEDULER_PROJECT}. "
                f"Error: {result.stderr}"
            )
        else:
            print(f"\n[pause_prod_scheduler] resumed {_SCHEDULER_JOB}")


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
    "host": os.environ.get("SUPABASE_DB_HOST", "db.enqpluazgxewepchdeut.supabase.co"),
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
# Proof 2b — review_date + product round-trip through the durable queue
# (2026-07-10 Phase 2 plumbing fix: added AFTER the original 4-part proof --
# extends it to cover the two new batch_job_rows/extractions columns rather
# than adding a separate, disconnected test file.)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_drain_carries_product_and_review_date_through_the_queue(
    two_orgs: tuple[str, str], quiescent_queue: None
) -> None:
    """Proof 2b: a row enqueued with a product value and a review_date (as a real
    CSV upload with a resolved product_column/date_column would produce) survives
    enqueue -> claim -> extraction -> storage intact -- the source-provided
    product PREFERRED over the LLM-inferred one, and review_date exactly as given
    (never fabricated, never silently replaced with ingestion time). Also proves
    the new column is covered by the EXISTING extractions RLS policy: org B
    cannot read org A's review_date, at the raw SQL level (not just via the
    org_id-filtered helper function) -- same pattern as Proof 1's RLS checks.
    """
    from datetime import UTC, datetime

    org_a, org_b = two_orgs
    text = f"Proof2b review with known date and product {uuid.uuid4().hex[:10]}"
    given_date = datetime(2025, 3, 14, 9, 30, tzinfo=UTC)
    given_product = "Seller's Own Product Name"

    job_id = str(uuid.uuid4())
    create_batch_job_pg(org_a, job_id, 1)
    enqueue_batch_job_rows_pg(org_a, job_id, [text], [given_product], [given_date])

    hash_ = ReviewRequest(text=text).input_hash()

    with (
        patch(
            "app.api.v2.extract.extract_with_llm",
            new=AsyncMock(side_effect=lambda *a, **k: _canned_llm_tuple()),
        ),
        patch("app.api.v2.extract.alert_on_review_event", new=AsyncMock(return_value=None)),
    ):
        result = await drain_rows(max_rows=10)

    assert result["claimed"] == 1
    assert result["processed"] == 1

    ext = get_by_hash_pg(org_a, hash_)
    assert ext is not None
    # Source-provided product wins over the canned LLM output's "Test Widget".
    assert ext.product == given_product
    assert ext.review_date == given_date

    # RLS proof, specifically for review_date: org B's authenticated connection
    # querying the review_date column directly for org A's row must return ZERO
    # rows -- not a NULL value, no row at all -- proving the new column inherits
    # the table's existing row-level policy rather than being reachable some
    # other way. Mirrors TestBatchJobRowsRLSIsolation's pattern above exactly.
    conn = _as_authenticated(org_b)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT review_date FROM public.extractions WHERE org_id = %s AND input_hash = %s",
            (org_a, hash_),
        )
        rows = cur.fetchall()
    finally:
        conn.rollback()
        conn.close()
    assert rows == [], "org B must not be able to read org A's review_date column at all"


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
        "SELECT job_id, row_index, org_id, text, product, review_date "
        "FROM public.batch_job_rows "
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
    # NOTE: matching solely on "distinct-row-" (not a run_marker prefix) is deliberate --
    # a random uuid.hex prefix has a real chance (~50% empirically) of being partially
    # rewritten by sanitize()'s phone-number regex before this stub ever sees the prompt
    # (e.g. "proof3b-d772082bb2" -> "proof3b-d[PHONE]bb2"), which silently broke this
    # assertion's tracking without affecting the actual claim/process/store behavior being
    # tested. quiescent_queue already guarantees no other test's rows are in play, so a
    # run_marker isn't needed for uniqueness here.
    texts = [f"distinct-row-{i} padding text for extraction" for i in range(4)]
    job_id = _seed_job(org_a, texts)

    call_log: list[str] = []

    async def _stub(prompt: str, allow_gemini_fallback: bool = False) -> tuple:
        # Option A gate genuinely exercised (not bypassed) — see fast_bulk_limiter.
        async with llm_call_slot():
            match = re.search(r"distinct-row-(\d+)", prompt)
            if match:
                call_log.append(match.group(0))
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


# ---------------------------------------------------------------------------
# Proof 5 — the BFF's own POST /bff/ingest/csv rides the SAME durable queue
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBffEntryPoint:
    """Proves POST /bff/ingest/csv — the path the production web app actually
    calls — enqueues onto the SAME durable batch_job_rows queue as POST
    /v2/ingest/csv, rather than entering through the storage functions
    directly like proofs 1-4 above. Goes through the real FastAPI HTTP
    handler with require_session dependency-overridden to a fixed org A ctx,
    exactly the way the browser's Supabase-JWT session resolves to an
    ApiKeyContext in production.
    """

    def test_bff_upload_drains_and_isolates_by_org(
        self, two_orgs: tuple[str, str], quiescent_queue: None, fast_bulk_limiter: None
    ) -> None:
        from app.auth.api_key import ApiKeyContext
        from app.auth.session import require_session
        from app.main import app
        from fastapi.testclient import TestClient

        org_a, org_b = two_orgs
        text_1 = f"BffProof org A widget review one {uuid.uuid4().hex[:10]}"
        text_2 = f"BffProof org A widget review two {uuid.uuid4().hex[:10]}"
        csv_bytes = f"review_text\n{text_1}\n{text_2}\n".encode()

        ctx_a = ApiKeyContext(
            org_id=org_a,
            api_key_id=str(uuid.uuid4()),
            key_name="bff-integration-test",
            usage_record_id=str(uuid.uuid4()),
        )

        # The real bff_ingest_csv handler schedules its own BackgroundTask
        # (_drain_until_job_complete). Under Starlette's TestClient,
        # BackgroundTasks run synchronously as part of response completion —
        # i.e. BEFORE client.post() returns below — which would drain every
        # pending row (including both of this test's) before we get a chance
        # to call drain_rows(max_rows=1) ourselves and simulate a partial
        # drain under the patched LLM boundary. Patch it to a no-op AsyncMock
        # here so this test — not the endpoint's own background task —
        # controls exactly when and how many rows get drained.
        app.dependency_overrides[require_session] = lambda: ctx_a
        try:
            with patch("app.api.bff.router._drain_until_job_complete", new=AsyncMock()):
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.post(
                    "/bff/ingest/csv",
                    files={"file": ("reviews.csv", csv_bytes, "text/csv")},
                )
        finally:
            app.dependency_overrides.pop(require_session, None)

        assert resp.status_code == 202
        body = resp.json()
        assert set(body.keys()) == {
            "job_id",
            "total",
            "status",
            "date_column",
            "date_ambiguous",
        }
        assert body["total"] == 2
        assert body["status"] == "pending"
        job_id = body["job_id"]

        hash_1 = ReviewRequest(text=text_1).input_hash()
        hash_2 = ReviewRequest(text=text_2).input_hash()

        call_count = 0

        async def _counting_stub(prompt: str, allow_gemini_fallback: bool = False) -> tuple:
            nonlocal call_count
            call_count += 1
            return _canned_llm_tuple()

        with (
            patch("app.api.v2.extract.extract_with_llm", new=AsyncMock(side_effect=_counting_stub)),
            patch("app.api.v2.extract.alert_on_review_event", new=AsyncMock(return_value=None)),
        ):
            # Partial drain — simulates an instance dying mid-job, same
            # pattern as Proof 4's test_resume_after_partial_drain_and_zero_cost_dedup.
            first = asyncio.run(drain_rows(max_rows=1))
            assert first["claimed"] == 1

            done, failed = count_job_row_statuses_pg(org_a, job_id)
            assert (done, failed) == (1, 0)
            assert count_pending_rows_pg(org_a, job_id) == 1
            job_mid = get_batch_job_pg(org_a, job_id)
            assert job_mid is not None
            assert job_mid["status"] not in ("done", "failed"), (
                "job must not be finalized while one row is still pending"
            )

            second = asyncio.run(drain_rows(max_rows=10))
            assert second["claimed"] == 1

        assert call_count == 2, (
            f"expected exactly 2 LLM-boundary calls (one per row, no reprocessing), got {call_count}"
        )

        done, failed = count_job_row_statuses_pg(org_a, job_id)
        assert (done, failed) == (2, 0)
        job_final = get_batch_job_pg(org_a, job_id)
        assert job_final is not None
        assert job_final["status"] == "done"

        ext_1 = get_by_hash_pg(org_a, hash_1)
        ext_2 = get_by_hash_pg(org_a, hash_2)
        assert ext_1 is not None, "org A's first row must be stored"
        assert ext_1.extraction_meta is not None
        assert ext_1.extraction_meta.org_id == org_a
        assert ext_2 is not None, "org A's second row must be stored"
        assert ext_2.extraction_meta is not None
        assert ext_2.extraction_meta.org_id == org_a

        # Cross-tenant visibility: org B must not see either of org A's extractions.
        assert get_by_hash_pg(org_b, hash_1) is None, "org B must not see org A's first extraction"
        assert get_by_hash_pg(org_b, hash_2) is None, "org B must not see org A's second extraction"
