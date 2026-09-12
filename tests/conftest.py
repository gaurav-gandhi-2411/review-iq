"""Shared pytest fixtures."""

from unittest.mock import patch

import pytest
from app.main import app
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client() -> AsyncClient:
    """Async HTTP client wired to the FastAPI app."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:  # type: ignore[return]
    """Clear in-memory rate-limit counters before and after each test.

    Prevents request counts from bleeding between tests (e.g. demo cache tests
    triggering the 5/minute limit before test_demo_rate_limit can assert 429).
    """
    from app.core.rate_limit import limiter

    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture(autouse=True)
def _demo_quota_allows_by_default():  # type: ignore[return]
    """Default POST /demo/extract's global daily quota check to "allowed" and its
    cost-recording call to a no-op, for every unit test.

    Both hit real Postgres (app/core/storage_pg.py) with no mock DB configured in the
    unit test environment -- without this, every existing /demo/extract test (cache,
    rate-limit) would either error or silently get a 429 from _check_demo_quota's
    fail-closed exception handler, for reasons unrelated to what those tests actually
    check. Tests that specifically exercise quota-exhaustion behavior (tests/unit/
    test_demo_quota.py) override this default explicitly, per-test.
    """
    with (
        patch("app.api.demo.check_and_increment_demo_request_pg", return_value=True),
        patch("app.api.demo.record_demo_extraction_cost_pg", return_value="mock-cost-id"),
    ):
        yield


@pytest.fixture(autouse=True)
def _injection_guard_defaults_to_not_suspicious():  # type: ignore[return]
    """Default the Session 13 P4a model-based injection classifier to "not suspicious" for
    every unit test.

    `classify_injection_risk` (app/core/injection_guard.py) makes a real Groq API call --
    without this, every existing /v2/extract and /demo/extract test would either hit the
    real network (slow, flaky, and burns the classifier's real quota for no reason) or,
    with no reachable key/network in the unit test environment, fail CLOSED (return True)
    by design -- silently making every extraction test's `is_suspicious`/logging
    assertions exercise the fail-closed path instead of the behavior actually being
    tested. Tests that specifically exercise the injection-guard's own behavior
    (tests/unit/test_injection_guard.py) patch this per-test instead.
    """
    with (
        patch("app.api.v2.extract.classify_injection_risk", return_value=False),
        patch("app.api.demo.classify_injection_risk", return_value=False),
    ):
        yield
