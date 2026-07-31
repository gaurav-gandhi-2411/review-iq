"""Unit tests for scripts/probe_web_surfaces.py — the nightly web-surface probe.

scripts/ has no __init__.py (matches this repo's existing convention, see
tests/test_probe_failover.py), so the module is imported by inserting its directory
onto sys.path rather than as a package.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import probe_web_surfaces  # noqa: E402 -- must follow the sys.path insert above


def _surface(name: str = "test", marker: str = "Samidha Reviews") -> probe_web_surfaces.Surface:
    return probe_web_surfaces.Surface(name, f"https://example.invalid/{name}", marker)


def _response(status_code: int, text: str) -> httpx.Response:
    return httpx.Response(
        status_code, text=text, request=httpx.Request("GET", "https://example.invalid/")
    )


@pytest.mark.asyncio
async def test_probe_surface_ok_when_200_and_marker_present() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _response(200, "<title>Samidha Reviews</title>")
    result = await probe_web_surfaces.probe_surface(client, _surface())
    assert result.ok is True
    assert result.status_code == 200


@pytest.mark.asyncio
async def test_probe_surface_fails_on_non_200() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _response(404, "not found")
    result = await probe_web_surfaces.probe_surface(client, _surface())
    assert result.ok is False
    assert "404" in result.detail


@pytest.mark.asyncio
async def test_probe_surface_fails_on_200_with_missing_marker() -> None:
    """The exact bug this probe exists for: a parked/placeholder page can return
    200 with no trace of the real product -- must be flagged as a failure, not
    treated as healthy just because the status code is green."""
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _response(200, "<html><body>Domain parked by registrar</body></html>")
    result = await probe_web_surfaces.probe_surface(client, _surface())
    assert result.ok is False
    assert "content marker" in result.detail
    assert "Samidha Reviews" in result.detail


@pytest.mark.asyncio
async def test_probe_surface_fails_on_deployment_not_found() -> None:
    """Reproduces the exact incident that motivated this probe: Vercel's own
    DEPLOYMENT_NOT_FOUND page returns 404, which is already caught by the
    non-200 branch -- confirms that specific real-world failure mode is covered."""
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _response(
        404, "The deployment could not be found on Vercel.\n\nDEPLOYMENT_NOT_FOUND"
    )
    result = await probe_web_surfaces.probe_surface(client, _surface())
    assert result.ok is False
    assert result.status_code == 404


@pytest.mark.asyncio
async def test_probe_surface_handles_connection_failure() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.side_effect = httpx.ConnectError("DNS resolution failed")
    result = await probe_web_surfaces.probe_surface(client, _surface())
    assert result.ok is False
    assert result.status_code is None
    assert "ConnectError" in result.detail


@pytest.mark.asyncio
async def test_probe_surface_uses_per_surface_content_marker() -> None:
    """The API surface asserts on a JSON fragment, not the brand string -- confirms
    each surface's own marker is actually used, not a single hardcoded string."""
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _response(200, '{"status":"ok","db":"ok"}')
    api_surface = probe_web_surfaces.Surface(
        "api", "https://example.invalid/health", '"status":"ok"'
    )
    result = await probe_web_surfaces.probe_surface(client, api_surface)
    assert result.ok is True


def test_surfaces_cover_all_four_required() -> None:
    """Wave 2 close-out P2 named exactly these four surfaces -- lock the list so a
    future edit can't silently drop one."""
    names = {s.name for s in probe_web_surfaces._SURFACES}
    assert names == {"marketing", "dashboard", "api", "try-page"}
    urls = {s.url for s in probe_web_surfaces._SURFACES}
    assert "https://samidhareviews.xyz/" in urls
    assert "https://app.samidhareviews.xyz/" in urls
    assert "https://api.samidhareviews.xyz/health" in urls
    assert "https://app.samidhareviews.xyz/try" in urls


def test_main_exits_nonzero_on_failure(capsys: pytest.CaptureFixture[str]) -> None:
    bad_result = probe_web_surfaces.ProbeResult("marketing", "https://x", False, 404, 10, "boom")
    with patch("probe_web_surfaces.run_probe", new_callable=AsyncMock, return_value=[bad_result]):
        with patch("sys.argv", ["probe_web_surfaces.py"]):
            exit_code = probe_web_surfaces.main()
    assert exit_code == 1
    assert "FAILED" in capsys.readouterr().out


def test_main_exits_zero_on_success(capsys: pytest.CaptureFixture[str]) -> None:
    good_result = probe_web_surfaces.ProbeResult("marketing", "https://x", True, 200, 10, "ok")
    with patch("probe_web_surfaces.run_probe", new_callable=AsyncMock, return_value=[good_result]):
        with patch("sys.argv", ["probe_web_surfaces.py"]):
            exit_code = probe_web_surfaces.main()
    assert exit_code == 0
    assert "All web surfaces OK" in capsys.readouterr().out
