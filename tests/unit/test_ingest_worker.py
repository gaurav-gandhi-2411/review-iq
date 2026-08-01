"""Unit tests for app.core.ingest_worker + POST /internal/ingest/tick.

All tests are offline and deterministic: psycopg2.connect is replaced with an
in-memory fake connection/cursor pair (no real DB), and _run_extraction_v2 is
patched to a fake coroutine (no real LLM call). No test sleeps or waits on a
real clock.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.auth.api_key import ApiKeyContext, require_api_key
from app.core.alerts.channels.base import ChannelError
from app.core.alerts.channels.fake import FakeChannel
from app.core.alerts.rules import AlertEventType
from app.core.authenticity.schema import AuthenticityLabel, AuthenticityResult
from app.core.ingest_worker import _claim_one_row, _sync_job_progress, drain_rows
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fake psycopg2 connection/cursor — simulates the SELECT ... FOR UPDATE SKIP
# LOCKED claim + the terminal UPDATE, without touching a real database.
# ---------------------------------------------------------------------------


# Claimed-row shape: (job_id, row_index, org_id, text, product, review_date) -- matches
# _claim_one_row's SELECT (job_id, row_index, org_id, text, product, review_date).
_ClaimedRow = tuple[str, int, str, str, str | None, object]


class _FakeCursor:
    def __init__(self, claim_queue: list[_ClaimedRow | None]) -> None:
        self._claim_queue = claim_queue
        self.execute_calls: list[tuple[str, tuple[object, ...] | None]] = []

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        self.execute_calls.append((sql, params))

    def fetchone(self) -> tuple[object, ...] | None:
        return self._claim_queue.pop(0) if self._claim_queue else None


class _FakeConn:
    def __init__(self, claim_queue: list[_ClaimedRow | None]) -> None:
        self._cur = _FakeCursor(claim_queue)
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self._cur

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def _make_fake_connect(queue: list[_ClaimedRow], created: list[_FakeConn]) -> object:
    """Build a psycopg2.connect stand-in that hands out fresh _FakeConn objects,
    all sharing the same claim queue (mirrors one shared pending-rows table).
    """

    def _connect(*_args: object, **_kwargs: object) -> _FakeConn:
        conn = _FakeConn(queue)
        created.append(conn)
        return conn

    return _connect


def _update_params(conn: _FakeConn) -> tuple[object, ...]:
    """Extract the params of the (single) settle_batch_job_row call on a fake conn.

    settle_batch_job_row(job_id, row_index, status, error, input_hash) -- reorder to
    the (status, error, input_hash, job_id, row_index) shape callers here expect,
    matching the old raw UPDATE statement's param order this replaced.
    """
    updates = [c for c in conn.cursor().execute_calls if "public.settle_batch_job_row" in c[0]]
    assert len(updates) == 1
    _, params = updates[0]
    assert params is not None
    job_id, row_index, status, error, input_hash = params
    return (status, error, input_hash, job_id, row_index)


# ---------------------------------------------------------------------------
# (a) POST /internal/ingest/tick — auth
# ---------------------------------------------------------------------------


def _make_mock_settings(trigger_token: str = "", tick_rows: int = 3) -> MagicMock:
    """Mimic the MagicMock-settings pattern used by test_internal_digest.py."""
    s = MagicMock()
    s.ingest_tick_token = trigger_token
    s.ingest_tick_rows = tick_rows
    return s


@pytest.fixture
def client() -> TestClient:
    from app.main import app

    yield TestClient(app, raise_server_exceptions=False)


def test_ingest_tick_unconfigured_server_returns_503(client: TestClient) -> None:
    """ingest_tick_token empty/unset -> 503, even with a header value provided."""
    with patch(
        "app.api.internal.ingest_tick.get_settings",
        return_value=_make_mock_settings(trigger_token=""),
    ):
        resp = client.post("/internal/ingest/tick", headers={"X-Ingest-Tick-Token": "anything"})
    assert resp.status_code == 503


def test_ingest_tick_missing_token_returns_401(client: TestClient) -> None:
    """Configured token, no header sent -> 401."""
    with patch(
        "app.api.internal.ingest_tick.get_settings",
        return_value=_make_mock_settings(trigger_token="real_secret"),
    ):
        resp = client.post("/internal/ingest/tick")
    assert resp.status_code == 401


def test_ingest_tick_wrong_token_returns_401(client: TestClient) -> None:
    """Configured token, wrong header value -> 401."""
    with patch(
        "app.api.internal.ingest_tick.get_settings",
        return_value=_make_mock_settings(trigger_token="real_secret"),
    ):
        resp = client.post("/internal/ingest/tick", headers={"X-Ingest-Tick-Token": "wrong"})
    assert resp.status_code == 401


def test_ingest_tick_correct_token_drains_and_returns_200(client: TestClient) -> None:
    """Correct token -> 200, drain_rows() result surfaced in the response body."""
    fake_result = {"claimed": 2, "processed": 2, "failed": 0, "jobs_completed": ["job-x"]}
    with (
        patch(
            "app.api.internal.ingest_tick.get_settings",
            return_value=_make_mock_settings(trigger_token="real_secret", tick_rows=5),
        ),
        patch(
            "app.api.internal.ingest_tick.drain_rows",
            new=AsyncMock(return_value=fake_result),
        ) as mock_drain,
    ):
        resp = client.post("/internal/ingest/tick", headers={"X-Ingest-Tick-Token": "real_secret"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["claimed"] == 2
    assert body["processed"] == 2
    assert body["jobs_completed"] == ["job-x"]
    mock_drain.assert_awaited_once_with(5)


# ---------------------------------------------------------------------------
# (b) Cross-tenant regression: each row is extracted under ITS OWN org_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_rows_attributes_each_row_to_its_own_org() -> None:
    """THE cross-tenant regression test.

    Two rows from two different orgs claimed in one drain_rows() call must
    each be extracted with THEIR OWN org_id — never a shared/default org.
    A regression here would leak one tenant's review text and results into
    another tenant's data (same risk class as the Shopify webhook org lookup).
    """
    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    job_a, job_b = str(uuid.uuid4()), str(uuid.uuid4())

    queue: list[_ClaimedRow] = [
        (job_a, 0, org_a, "review text for org A", None, None),
        (job_b, 0, org_b, "review text for org B", None, None),
    ]
    created_conns: list[_FakeConn] = []
    captured_ctxs: list[ApiKeyContext] = []

    async def _fake_run(
        req: object, ctx: ApiKeyContext, product_override: str | None = None
    ) -> MagicMock:
        captured_ctxs.append(ctx)
        return MagicMock()

    with (
        patch(
            "app.core.ingest_worker.psycopg2.connect",
            side_effect=_make_fake_connect(queue, created_conns),
        ),
        patch("app.core.ingest_worker.get_batch_job_pg", return_value=None),
        patch("app.core.ingest_worker.count_pending_rows_pg", return_value=1),
        patch("app.core.ingest_worker.count_job_row_statuses_pg", return_value=(1, 0)),
        patch("app.core.ingest_worker.update_batch_job_pg", return_value=None),
        patch("app.api.v2.extract._run_extraction_v2", new=_fake_run),
    ):
        result = await drain_rows(max_rows=2)

    assert [ctx.org_id for ctx in captured_ctxs] == [org_a, org_b]
    # Never a shared/default org — each row's ctx carries its OWN api key
    # context, never reuses another row's or a global system context object.
    assert captured_ctxs[0] is not captured_ctxs[1]
    assert result["claimed"] == 2
    assert result["processed"] == 2
    assert result["failed"] == 0


# ---------------------------------------------------------------------------
# (c) Row failure marks failed + truncated error, and the drain continues
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_row_failure_marks_failed_with_truncated_error_and_continues() -> None:
    """The first row's extraction raises; the second row is still claimed and
    processed — one row's failure never aborts the drain loop.
    """
    org_id, job_id = str(uuid.uuid4()), str(uuid.uuid4())
    queue: list[_ClaimedRow] = [
        (job_id, 0, org_id, "bad review", None, None),
        (job_id, 1, org_id, "good review", None, None),
    ]
    created_conns: list[_FakeConn] = []
    call_count = 0

    async def _run_side_effect(
        req: object, ctx: ApiKeyContext, product_override: str | None = None
    ) -> MagicMock:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("x" * 600)  # longer than _ERROR_TRUNCATE_LEN
        return MagicMock()

    with (
        patch(
            "app.core.ingest_worker.psycopg2.connect",
            side_effect=_make_fake_connect(queue, created_conns),
        ),
        patch("app.core.ingest_worker.get_batch_job_pg", return_value=None),
        patch("app.core.ingest_worker.count_pending_rows_pg", return_value=1),
        patch("app.core.ingest_worker.count_job_row_statuses_pg", return_value=(1, 1)),
        patch("app.core.ingest_worker.update_batch_job_pg", return_value=None),
        patch("app.api.v2.extract._run_extraction_v2", new=_run_side_effect),
    ):
        result = await drain_rows(max_rows=2)

    assert result["claimed"] == 2
    assert result["processed"] == 1
    assert result["failed"] == 1
    assert len(created_conns) == 2

    failed_status, failed_error, failed_hash, failed_job, failed_row = _update_params(
        created_conns[0]
    )
    assert failed_status == "failed"
    assert len(failed_error) == 500  # truncated from 600 chars
    assert failed_row == 0

    ok_status, ok_error, _ok_hash, _ok_job, ok_row = _update_params(created_conns[1])
    assert ok_status == "done"
    assert ok_error is None
    assert ok_row == 1


# ---------------------------------------------------------------------------
# (d) Job completion: sets done/failed + preserves the result-endpoint contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_job_progress_finalizes_and_preserves_result_contract() -> None:
    """Zero pending rows -> job finalizes; source_columns keeps existing keys
    (text_column, product_column, include_authenticity) and gains the final
    input_hashes list aligned to row order — the exact shape GET
    /v2/ingest/{job_id}/result already depends on.
    """
    org_id, job_id = str(uuid.uuid4()), str(uuid.uuid4())
    job_record = {
        "job_id": job_id,
        "status": "processing",
        "source_columns": json.dumps(
            {
                "text_column": "review_text",
                "product_column": None,
                "include_authenticity": False,
                "input_hashes": [],
            }
        ),
    }
    update_calls: list[dict[str, object]] = []

    def _fake_update(_org: str, _job: str, **kwargs: object) -> None:
        update_calls.append(kwargs)

    with (
        patch("app.core.ingest_worker.count_job_row_statuses_pg", return_value=(2, 0)),
        patch("app.core.ingest_worker.count_pending_rows_pg", return_value=0),
        patch("app.core.ingest_worker.get_batch_job_pg", return_value=job_record),
        patch(
            "app.core.ingest_worker.list_job_row_hashes_pg",
            return_value=["sha256:aaa", "sha256:bbb"],
        ),
        patch("app.core.ingest_worker.update_batch_job_pg", side_effect=_fake_update),
    ):
        completed = await _sync_job_progress(org_id, job_id)

    assert completed is True
    assert len(update_calls) == 1
    final = update_calls[0]
    assert final["status"] == "done"
    assert final["processed"] == 2
    assert final["failed"] == 0
    meta = json.loads(final["source_columns"])  # type: ignore[arg-type]
    assert meta["input_hashes"] == ["sha256:aaa", "sha256:bbb"]
    assert meta["text_column"] == "review_text"
    assert meta["include_authenticity"] is False


@pytest.mark.asyncio
async def test_sync_job_progress_skips_an_already_finalized_job() -> None:
    """A job already done/failed (finalized by a concurrent drain) is never
    double-completed — no second update, no double log.
    """
    job_record = {"job_id": "job-x", "status": "done", "source_columns": "{}"}
    with (
        patch("app.core.ingest_worker.count_pending_rows_pg", return_value=0),
        patch("app.core.ingest_worker.count_job_row_statuses_pg", return_value=(1, 0)),
        patch("app.core.ingest_worker.get_batch_job_pg", return_value=job_record),
        patch("app.core.ingest_worker.update_batch_job_pg") as mock_update,
    ):
        completed = await _sync_job_progress("org-x", "job-x")

    assert completed is False
    mock_update.assert_not_called()


# ---------------------------------------------------------------------------
# (e) Enqueue endpoints create a job + durable rows, contract-compatible response
# ---------------------------------------------------------------------------


def test_extract_batch_enqueues_durable_rows_and_returns_job_id() -> None:
    """POST /v2/extract/batch creates a batch_jobs row + batch_job_rows via the
    durable path, and its 202 response keeps {status, total} plus an additive
    job_id key.
    """
    from app.main import app

    ctx = ApiKeyContext(
        org_id=str(uuid.uuid4()),
        api_key_id=str(uuid.uuid4()),
        key_name="test-key",
        usage_record_id=str(uuid.uuid4()),
    )
    create_calls: list[tuple[object, ...]] = []
    enqueue_calls: list[tuple[object, ...]] = []

    def _fake_create(
        org_id: str, job_id: str, total: int, source_columns: str | None = None
    ) -> None:
        create_calls.append((org_id, job_id, total, source_columns))

    def _fake_enqueue(
        org_id: str,
        job_id: str,
        texts: list[str],
        products: list[str | None] | None = None,
        review_dates: list[object] | None = None,
    ) -> None:
        enqueue_calls.append((org_id, job_id, texts, products, review_dates))

    app.dependency_overrides[require_api_key] = lambda: ctx
    try:
        with (
            patch("app.api.v2.extract.create_batch_job_pg", side_effect=_fake_create),
            patch("app.api.v2.extract.enqueue_batch_job_rows_pg", side_effect=_fake_enqueue),
            patch("app.api.v2.extract.update_batch_job_pg", return_value=None),
            patch("app.api.v2.extract._drain_until_batch_complete", new=AsyncMock()),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/v2/extract/batch",
                json={"reviews": [{"text": "Good product"}, {"text": "Bad product"}]},
            )
    finally:
        app.dependency_overrides.pop(require_api_key, None)

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["total"] == "2"
    assert "job_id" in body

    assert len(create_calls) == 1
    assert create_calls[0][0] == ctx.org_id
    assert create_calls[0][2] == 2
    assert len(enqueue_calls) == 1
    assert enqueue_calls[0][0] == ctx.org_id
    assert enqueue_calls[0][2] == ["Good product", "Bad product"]


def test_ingest_csv_enqueues_durable_rows_and_returns_job_id() -> None:
    """POST /v2/ingest/csv creates a batch_jobs row + batch_job_rows via the
    durable path, and its 202 response is unchanged: {job_id, total, status}.
    """
    from app.main import app

    ctx = ApiKeyContext(
        org_id=str(uuid.uuid4()),
        api_key_id=str(uuid.uuid4()),
        key_name="test-key",
        usage_record_id=str(uuid.uuid4()),
    )
    enqueue_calls: list[tuple[object, ...]] = []

    def _fake_enqueue(
        org_id: str,
        job_id: str,
        texts: list[str],
        products: list[str | None] | None = None,
        review_dates: list[object] | None = None,
    ) -> None:
        enqueue_calls.append((org_id, job_id, texts, products, review_dates))

    app.dependency_overrides[require_api_key] = lambda: ctx
    try:
        with (
            patch(
                "app.api.v2.ingest.read_and_validate_csv",
                new=AsyncMock(
                    return_value=([{"text": "Great product!"}], "review_text", None, None, False)
                ),
            ),
            patch("app.api.v2.ingest.create_batch_job_pg", return_value=None),
            patch("app.api.v2.ingest.enqueue_batch_job_rows_pg", side_effect=_fake_enqueue),
            patch("app.api.v2.ingest.update_batch_job_pg", return_value=None),
            patch("app.api.v2.ingest._drain_until_job_complete", new=AsyncMock()),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/v2/ingest/csv",
                files={"file": ("reviews.csv", b"review_text\ngreat product\n", "text/csv")},
            )
    finally:
        app.dependency_overrides.pop(require_api_key, None)

    assert resp.status_code == 202
    body = resp.json()
    assert "job_id" in body
    assert body["total"] == 1
    assert body["status"] == "pending"

    assert len(enqueue_calls) == 1
    assert enqueue_calls[0][0] == ctx.org_id
    assert enqueue_calls[0][2] == ["Great product!"]


# ---------------------------------------------------------------------------
# (f) Per-row authenticity-alert wiring — moved here from
# tests/unit/test_alert_wiring.py (see that file's module docstring) when the
# fire-and-forget _process_ingest_job coroutine was replaced by this durable
# worker path (Option B, 2026-07-09). _claim_one_row is the call site that now
# reads include_authenticity from the job's source_columns and scores each row.
# ---------------------------------------------------------------------------


def _auth_result(
    label: AuthenticityLabel = AuthenticityLabel.GENUINE, score: float = 0.9, text: str = "x"
) -> AuthenticityResult:
    """Minimal AuthenticityResult for use as a score_single mock return value."""
    return AuthenticityResult(
        score=score,
        label=label,
        review_hash=hashlib.sha256(text.encode()).hexdigest(),
        scored_at=datetime.now(UTC),
    )


class _ErrorChannel:
    """Channel that always fails delivery — simulates Resend being down. Mirrors
    ErrorChannel in tests/unit/test_alert_wiring.py and test_alert_engine.py."""

    async def send(self, message: object) -> None:
        raise ChannelError("delivery failed")


@pytest.mark.asyncio
async def test_claim_one_row_fires_alert_for_authenticity_flagged_row() -> None:
    """_claim_one_row() fires an alert for a LIKELY_FAKE row when the job has
    include_authenticity=True in its source_columns.
    """
    org_id, job_id = str(uuid.uuid4()), str(uuid.uuid4())
    queue: list[_ClaimedRow] = [(job_id, 0, org_id, "row text", None, None)]
    created_conns: list[_FakeConn] = []
    job_record = {
        "job_id": job_id,
        "source_columns": json.dumps({"include_authenticity": True}),
    }
    fake_channel = FakeChannel()
    fake_auth_result = _auth_result(label=AuthenticityLabel.LIKELY_FAKE, score=0.1, text="row text")

    with (
        patch(
            "app.core.ingest_worker.psycopg2.connect",
            side_effect=_make_fake_connect(queue, created_conns),
        ),
        patch("app.core.ingest_worker.get_batch_job_pg", return_value=job_record),
        patch("app.api.v2.extract._run_extraction_v2", new=AsyncMock(return_value=MagicMock())),
        patch(
            "app.core.authenticity.engine.score_single",
            new=AsyncMock(return_value=fake_auth_result),
        ),
        patch("app.core.storage_pg.save_authenticity_audit_pg", return_value=None),
        patch("app.core.alerts.engine.is_already_alerted_pg", MagicMock(return_value=False)),
        patch("app.core.alerts.engine.get_preference_pg", MagicMock(return_value=None)),
        patch("app.core.alerts.engine.record_alert_sent_pg", MagicMock(return_value=None)),
        patch(
            "app.core.alerts.engine.get_org_notification_email_pg",
            MagicMock(return_value="seller@example.com"),
        ),
        patch("app.core.alerts.engine._get_default_channel", MagicMock(return_value=fake_channel)),
    ):
        result = await _claim_one_row()

    assert result == (org_id, job_id, True)
    assert len(fake_channel.sent) == 1
    assert fake_channel.sent[0].event.event_type == AlertEventType.LIKELY_FAKE


@pytest.mark.asyncio
async def test_claim_one_row_authenticity_survives_channel_send_failure() -> None:
    """A ChannelError from channel.send never affects the row's terminal status —
    the extraction already succeeded by the time authenticity scoring runs.
    """
    org_id, job_id = str(uuid.uuid4()), str(uuid.uuid4())
    queue: list[_ClaimedRow] = [(job_id, 0, org_id, "row text 2", None, None)]
    created_conns: list[_FakeConn] = []
    job_record = {
        "job_id": job_id,
        "source_columns": json.dumps({"include_authenticity": True}),
    }
    error_channel = _ErrorChannel()
    fake_auth_result = _auth_result(
        label=AuthenticityLabel.LIKELY_FAKE, score=0.1, text="row text 2"
    )

    with (
        patch(
            "app.core.ingest_worker.psycopg2.connect",
            side_effect=_make_fake_connect(queue, created_conns),
        ),
        patch("app.core.ingest_worker.get_batch_job_pg", return_value=job_record),
        patch("app.api.v2.extract._run_extraction_v2", new=AsyncMock(return_value=MagicMock())),
        patch(
            "app.core.authenticity.engine.score_single",
            new=AsyncMock(return_value=fake_auth_result),
        ),
        patch("app.core.storage_pg.save_authenticity_audit_pg", return_value=None),
        patch("app.core.alerts.engine.is_already_alerted_pg", MagicMock(return_value=False)),
        patch("app.core.alerts.engine.get_preference_pg", MagicMock(return_value=None)),
        patch(
            "app.core.alerts.engine.record_alert_sent_pg", MagicMock(return_value=None)
        ) as mock_record,
        patch(
            "app.core.alerts.engine.get_org_notification_email_pg",
            MagicMock(return_value="seller@example.com"),
        ),
        patch("app.core.alerts.engine._get_default_channel", MagicMock(return_value=error_channel)),
    ):
        # Should complete without raising, despite the channel failing on every send.
        result = await _claim_one_row()

    mock_record.assert_not_called()
    # The row's terminal status still reflects extraction success — the alert
    # channel failing never affects batch_job_rows bookkeeping.
    assert result == (org_id, job_id, True)
    row_status, row_error, *_rest = _update_params(created_conns[0])
    assert row_status == "done"
    assert row_error is None
