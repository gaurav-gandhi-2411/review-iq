"""Call-layer throttling for bulk (batch/CSV) Groq extraction.

Incident (2026-07-07): /v2/extract/batch and /v2/ingest/csv loop rows calling
the Groq-backed extraction with zero throttling. Outer-loop pacing (limiting
the number of *rows* processed per minute) provably fails because ONE
extraction internally fires 2-4 Groq calls (parse-retries in
app/core/router.py::_call_provider, small->large escalation) — pacing rows
does not bound the actual HTTP call rate hitting the shared Groq key.

The fix (Option A) sits at the CALL layer instead: every live Groq HTTP call
made from a BULK context acquires a token-bucket + semaphore slot before the
network call happens, regardless of how many calls one logical extraction
makes. INTERACTIVE calls (the default) pass through with zero awaits — the
interactive path must be provably un-degraded by bulk traffic.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Literal

import structlog

from app.core.config import get_settings

log = structlog.get_logger(__name__)

CallClass = Literal["interactive", "bulk"]

# ContextVar propagates across `await` boundaries within one asyncio task tree,
# so setting this once at the top of a background job classifies every Groq
# call made anywhere downstream (including retries/escalation) as bulk.
_llm_call_class: ContextVar[CallClass] = ContextVar("_llm_call_class", default="interactive")


def set_bulk_call_class() -> None:
    """Mark the current async context (and everything it awaits) as bulk."""
    _llm_call_class.set("bulk")


def current_call_class() -> CallClass:
    """Return the call class ("interactive" or "bulk") for the current context."""
    return _llm_call_class.get()


class TokenBucket:
    """Simple token bucket for pacing calls to `rate_per_minute`.

    `time_func` and `sleep_func` are injectable ONLY so tests can drive the
    bucket with a fake clock and assert exact wait durations deterministically
    — production code always uses the real time.monotonic / asyncio.sleep.
    """

    def __init__(
        self,
        rate_per_minute: float,
        capacity: float = 1.0,
        *,
        time_func: Callable[[], float] = time.monotonic,
        sleep_func: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._rate_per_minute = rate_per_minute
        self._capacity = capacity
        self._time_func = time_func
        self._sleep_func = sleep_func
        self._tokens = capacity
        self._last_refill = time_func()
        # Serializes concurrent acquire() calls so refill + take is atomic —
        # without this, concurrent callers could race on `_tokens` and both
        # observe >= 1 token when only one is actually available.
        self._lock = asyncio.Lock()

    async def acquire(self) -> float:
        """Block until a token is available, then take it. Returns seconds waited."""
        async with self._lock:
            now = self._time_func()
            elapsed = now - self._last_refill
            self._tokens = min(
                self._capacity, self._tokens + elapsed * self._rate_per_minute / 60.0
            )
            self._last_refill = now

            if self._tokens >= 1:
                self._tokens -= 1
                return 0.0

            deficit = 1 - self._tokens
            wait = deficit * 60.0 / self._rate_per_minute
            await self._sleep_func(wait)

            self._tokens = 0.0
            self._last_refill = self._time_func()
            return wait


class BulkLlmLimiter:
    """Bounds concurrency and rate of bulk-context Groq calls.

    Combines a semaphore (bounds in-flight bulk calls) with a token bucket
    (bounds calls/minute). Both are needed: the semaphore caps simultaneous
    network calls, the bucket caps sustained throughput against the model's
    TPM budget.
    """

    def __init__(
        self,
        calls_per_minute: float,
        max_concurrency: int,
        *,
        time_func: Callable[[], float] = time.monotonic,
        sleep_func: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._bucket = TokenBucket(
            calls_per_minute, capacity=1.0, time_func=time_func, sleep_func=sleep_func
        )
        self._calls_per_minute = calls_per_minute

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """Acquire a throttled slot, holding the semaphore for the wrapped call's duration.

        The semaphore must stay held while the caller's LLM call runs — that
        is what bounds in-flight bulk concurrency, not just the acquire step.
        """
        async with self._semaphore:
            waited_s = await self._bucket.acquire()
            if waited_s > 0:
                log.info(
                    "ratelimit.bulk_wait",
                    waited_s=waited_s,
                    calls_per_minute=self._calls_per_minute,
                )
            yield


# Lazy process-global limiter, built from settings on first use so it always
# reflects the currently loaded configuration (mirrors the get_settings()
# lru_cache pattern used elsewhere in this codebase).
_bulk_limiter: BulkLlmLimiter | None = None


def get_bulk_limiter() -> BulkLlmLimiter:
    """Return the process-global BulkLlmLimiter, constructing it on first use."""
    global _bulk_limiter
    if _bulk_limiter is None:
        settings = get_settings()
        _bulk_limiter = BulkLlmLimiter(
            settings.bulk_llm_calls_per_minute,
            settings.bulk_llm_max_concurrency,
        )
    return _bulk_limiter


def reset_bulk_limiter_for_tests() -> None:
    """Clear the process-global limiter. Tests only — never called in production code."""
    global _bulk_limiter
    _bulk_limiter = None


@asynccontextmanager
async def llm_call_slot() -> AsyncIterator[None]:
    """Throttle gate around a single live Groq HTTP call.

    Interactive calls (the default context) take this branch: no locks, no
    awaits, no semaphore — this is the interactive-not-degraded guarantee
    that bulk traffic can never slow down interactive /v2/extract.
    """
    if current_call_class() != "bulk":
        yield
        return

    async with get_bulk_limiter().slot():
        yield
