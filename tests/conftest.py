"""Shared pytest fixtures."""

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
