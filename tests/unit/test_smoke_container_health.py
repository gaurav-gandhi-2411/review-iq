"""Unit tests for scripts/smoke_container_health.py (the `docker-build` CI job's probe).

The probe must fail on "nothing answered" and on "something other than review-iq's /health
answered"; it must pass on both 200 (healthy) and 503 (unhealthy but alive) health payloads.
Tests use a real local HTTP server, not mocks of urllib.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from scripts import smoke_container_health as smoke


def _serve(status: int, body: bytes, content_type: str) -> tuple[HTTPServer, str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:  # silence test output
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/health"


@pytest.fixture
def healthy() -> Iterator[str]:
    server, url = _serve(200, json.dumps({"status": "ok", "db": "ok"}).encode(), "application/json")
    yield url
    server.shutdown()


def test_is_health_payload_accepts_200_and_503_with_status_key() -> None:
    assert smoke.is_health_payload(200, {"status": "ok"})
    assert smoke.is_health_payload(503, {"status": "unhealthy", "db": "error"})


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (404, {"status": "ok"}),  # wrong status code even though the body looks right
        (500, {"status": "ok"}),
        (200, None),  # not JSON (e.g. an HTML page from some other process on the port)
        (200, ["status"]),  # JSON but not an object
        (200, {"detail": "Not Found"}),  # JSON object without a `status` key
    ],
)
def test_is_health_payload_rejects_non_health_responses(status: int, body: object) -> None:
    assert not smoke.is_health_payload(status, body)


def test_wait_for_health_passes_on_healthy_server(healthy: str) -> None:
    ok, detail = smoke.wait_for_health(healthy, deadline_s=5, interval_s=0.05)
    assert ok
    assert "HTTP 200" in detail


def test_wait_for_health_passes_on_503_unhealthy_payload() -> None:
    server, url = _serve(503, json.dumps({"status": "unhealthy"}).encode(), "application/json")
    try:
        ok, detail = smoke.wait_for_health(url, deadline_s=5, interval_s=0.05)
    finally:
        server.shutdown()
    assert ok
    assert "HTTP 503" in detail


def test_wait_for_health_fails_when_something_else_answers() -> None:
    server, url = _serve(200, b"<html>welcome</html>", "text/html")
    try:
        ok, detail = smoke.wait_for_health(url, deadline_s=0.5, interval_s=0.05)
    finally:
        server.shutdown()
    assert not ok
    assert "unexpected response" in detail


def test_wait_for_health_fails_when_nothing_is_listening() -> None:
    # Port 9 (discard) on loopback: nothing listens in CI, so the connection is refused --
    # the crashed-container case. Must fail after the deadline, never pass or hang.
    ok, detail = smoke.wait_for_health("http://127.0.0.1:9/health", deadline_s=0.5, interval_s=0.05)
    assert not ok
    assert "connection failed" in detail
