"""Unit tests for app.core.ratelimit — bulk-path Groq call throttling.

All tests are offline and deterministic: TokenBucket/BulkLlmLimiter accept an
injectable time_func/sleep_func, so FakeClock drives them with a virtual
clock and no real wall-clock waiting ever happens in this suite.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import MagicMock, patch

import pytest
from app.auth.api_key import ApiKeyContext
from app.core import ratelimit as ratelimit_mod
from app.core.providers.groq import GroqProvider
from app.core.ratelimit import (
    BulkLlmLimiter,
    TokenBucket,
    current_call_class,
    get_bulk_limiter,
    llm_call_slot,
    reset_bulk_limiter_for_tests,
    set_bulk_call_class,
)
from app.core.schemas import ReviewRequest

_ORG_ID = str(uuid.uuid4())
_KEY_ID = str(uuid.uuid4())
_USAGE_ID = str(uuid.uuid4())

_CTX = ApiKeyContext(
    org_id=_ORG_ID,
    api_key_id=_KEY_ID,
    key_name="test-key",
    usage_record_id=_USAGE_ID,
)


class FakeClock:
    """Deterministic virtual clock for TokenBucket/BulkLlmLimiter tests.

    ``time()`` returns the current virtual time; ``sleep()`` advances it by
    the requested amount and records the call instead of actually waiting —
    keeps this suite fast (~0 real wall time) and exact regardless of
    scheduling.
    """

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def _reset_ratelimit_state():
    """Ensure the ContextVar and process-global limiter never leak across tests."""
    reset_bulk_limiter_for_tests()
    token = ratelimit_mod._llm_call_class.set("interactive")
    yield
    ratelimit_mod._llm_call_class.reset(token)
    reset_bulk_limiter_for_tests()


# ---------------------------------------------------------------------------
# TokenBucket
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_bucket_rate_limits_sequential_acquires() -> None:
    """rate=2/min, capacity=1: 5 sequential acquires wait [0, 30, 30, 30, 30]."""
    clock = FakeClock()
    bucket = TokenBucket(
        rate_per_minute=2.0, capacity=1.0, time_func=clock.time, sleep_func=clock.sleep
    )

    waits = [await bucket.acquire() for _ in range(5)]

    assert waits == [0.0, 30.0, 30.0, 30.0, 30.0]
    assert clock.sleeps == [30.0, 30.0, 30.0, 30.0]


# ---------------------------------------------------------------------------
# Default context / call-class classification
# ---------------------------------------------------------------------------


def test_default_context_is_interactive() -> None:
    """With no explicit classification, the current call class is interactive."""
    assert current_call_class() == "interactive"


# ---------------------------------------------------------------------------
# Interactive-not-degraded guarantee
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interactive_calls_never_touch_the_bulk_limiter() -> None:
    """An EXHAUSTED bulk limiter installed as the global must never be entered
    from the default interactive context.

    max_concurrency=0 means the limiter's semaphore can never be acquired —
    if the interactive branch of llm_call_slot() regressed to route through
    the bulk path, this would hang forever. asyncio.wait_for converts that
    into a fast, clear test failure instead of a CI hang.
    """
    clock = FakeClock()
    exhausted = BulkLlmLimiter(
        calls_per_minute=2.0,
        max_concurrency=0,
        time_func=clock.time,
        sleep_func=clock.sleep,
    )
    ratelimit_mod._bulk_limiter = exhausted

    assert current_call_class() == "interactive"

    async def _enter_and_exit() -> None:
        async with llm_call_slot():
            pass

    await asyncio.wait_for(_enter_and_exit(), timeout=1.0)

    assert clock.sleeps == []


# ---------------------------------------------------------------------------
# Bulk serialization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_slot_serializes_concurrent_callers() -> None:
    """max_concurrency=1, 3 concurrent slot() entries: 3 bucket acquisitions,
    spaced by the token bucket's rate — the lock makes this deterministic
    even under asyncio.gather.
    """
    clock = FakeClock()
    limiter = BulkLlmLimiter(
        calls_per_minute=2.0,
        max_concurrency=1,
        time_func=clock.time,
        sleep_func=clock.sleep,
    )

    async def _worker() -> None:
        async with limiter.slot():
            pass

    await asyncio.gather(*(_worker() for _ in range(3)))

    assert clock.sleeps == [30.0, 30.0]


# ---------------------------------------------------------------------------
# Retry/escalation-shaped burst (proves call-layer throttling where the
# outer-loop pacing that caused the 2026-07-07 incident failed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_layer_caps_retry_escalation_burst() -> None:
    """4 llm_call_slot() entries back-to-back in bulk context (simulating
    parse-retries + escalation within ONE extraction) still hit 4 bucket
    acquisitions with enforced spacing — the cap holds per-call, not per-row.
    """
    clock = FakeClock()
    limiter = BulkLlmLimiter(
        calls_per_minute=2.0,
        max_concurrency=2,
        time_func=clock.time,
        sleep_func=clock.sleep,
    )
    ratelimit_mod._bulk_limiter = limiter
    set_bulk_call_class()

    for _ in range(4):
        async with llm_call_slot():
            pass

    assert clock.sleeps == [30.0, 30.0, 30.0]


# ---------------------------------------------------------------------------
# drain_rows() classifies every row as bulk (Option B durable worker)
#
# The old fire-and-forget _process_batch_v2 coroutine this test previously
# targeted was replaced (2026-07-09) by the durable batch_job_rows queue —
# see app/core/ingest_worker.py. The bulk-classification guarantee moved with
# it: drain_rows() calls set_bulk_call_class() before processing any row.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_rows_classifies_bulk() -> None:
    from app.core import ingest_worker as ingest_worker_mod

    org_id, job_id = str(uuid.uuid4()), str(uuid.uuid4())
    queue: list[tuple[str, int, str, str] | None] = [
        (job_id, 0, org_id, "Good product"),
        (job_id, 1, org_id, "Bad product"),
    ]

    class _FakeCursor:
        def execute(self, *args: object, **kwargs: object) -> None:
            pass

        def fetchone(self) -> tuple[str, int, str, str] | None:
            return queue.pop(0) if queue else None

    class _FakeConn:
        def cursor(self) -> _FakeCursor:
            return _FakeCursor()

        def commit(self) -> None:
            pass

        def rollback(self) -> None:
            pass

        def close(self) -> None:
            pass

    captured: list[str] = []

    async def _fake_run(req: ReviewRequest, ctx: ApiKeyContext) -> None:
        captured.append(current_call_class())

    with (
        patch(
            "app.core.ingest_worker.psycopg2.connect",
            side_effect=lambda *_args, **_kwargs: _FakeConn(),
        ),
        patch("app.core.ingest_worker.get_batch_job_pg", return_value=None),
        patch("app.core.ingest_worker.count_pending_rows_pg", return_value=1),
        patch("app.core.ingest_worker.count_job_row_statuses_pg", return_value=(2, 0)),
        patch("app.core.ingest_worker.update_batch_job_pg", return_value=None),
        patch("app.api.v2.extract._run_extraction_v2", new=_fake_run),
    ):
        await ingest_worker_mod.drain_rows(max_rows=2)

    assert captured == ["bulk", "bulk"]


# ---------------------------------------------------------------------------
# _process_ingest_job classifies every row as bulk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_ingest_job_classifies_bulk() -> None:
    from app.api.v2 import ingest as ingest_mod

    captured: list[str] = []

    async def _fake_run(req: ReviewRequest, ctx: ApiKeyContext) -> None:
        captured.append(current_call_class())

    with (
        patch("app.api.v2.ingest.update_batch_job_pg", return_value=None),
        patch("app.api.v2.extract._run_extraction_v2", new=_fake_run),
    ):
        await ingest_mod._process_ingest_job(
            _CTX,
            str(uuid.uuid4()),
            [{"text": "Good product"}, {"text": "Bad product"}],
            include_authenticity=False,
        )

    assert captured == ["bulk", "bulk"]


# ---------------------------------------------------------------------------
# GroqProvider replay mode never touches the limiter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_mode_never_touches_the_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cassette replay makes zero network calls and must never throttle —
    replay stays fast for CI/evals regardless of call class.
    """
    monkeypatch.setenv("EVAL_CASSETTE_MODE", "replay")

    limiter_mock = MagicMock()
    limiter_mock.slot = MagicMock(side_effect=AssertionError("limiter touched during replay"))
    monkeypatch.setattr(ratelimit_mod, "get_bulk_limiter", lambda: limiter_mock)

    with patch("app.core.providers.groq.replay", return_value=("{}", 10, 5)):
        set_bulk_call_class()
        provider = GroqProvider(model="test-model", api_key="fake-key")
        raw, tokens_in, tokens_out = await provider.complete("prompt", system_prompt="sys")

    assert (raw, tokens_in, tokens_out) == ("{}", 10, 5)
    limiter_mock.slot.assert_not_called()


# ---------------------------------------------------------------------------
# get_bulk_limiter builds from settings
# ---------------------------------------------------------------------------


def test_get_bulk_limiter_is_lazily_constructed_and_cached() -> None:
    """get_bulk_limiter() builds once and returns the same instance thereafter."""
    first = get_bulk_limiter()
    second = get_bulk_limiter()
    assert first is second
    assert isinstance(first, BulkLlmLimiter)
