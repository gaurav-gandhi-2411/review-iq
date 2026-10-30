"""Behavioural test for uptime-alert.yml's "Probe /health" step (S19 Z12).

The step used to call /health UP on any HTTP 200, so a 200 maintenance/error page counted as
up -- while the Cloud Monitoring check api-health matches the body for "status":"ok"
(CONTAINS_STRING, read from the live config 2026-10-07). This test executes the step's real
`run:` script (extracted from the YAML, not re-typed here) against a local stdlib HTTP server.

SURFACE: needs bash, curl, tr on PATH (ubuntu-latest has them). It proves the probe step's
UP/DOWN decision on the four shapes below; it does not exercise the issue open/close steps
(they need the GitHub API) and it does not prove the live endpoint is currently healthy.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "uptime-alert.yml"

# Byte-for-byte the shape the live /health returned on 2026-10-07.
REAL_BODY = '{"status":"ok","db":"ok","db_backend":"postgres","provider":"configured"}'
# Same content, pretty-printed: the match must be whitespace-insensitive.
PRETTY_BODY = '{\n  "status": "ok",\n  "db": "ok"\n}\n'


def probe_script() -> str:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for job in wf["jobs"].values():
        for step in job["steps"]:
            if step.get("id") == "probe":
                return str(step["run"])
    raise AssertionError("uptime-alert.yml has no step with id 'probe'")


class _Server:
    def __init__(self, code: int, body: str) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 -- http.server API
                payload = body.encode("utf-8")
                self.send_response(outer.code)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args: object) -> None:  # silence test noise
                return

        self.code = code
        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/health"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> _Server:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def run_probe(tmp_path: Path, url: str) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    bash = shutil.which("bash")
    assert bash, "bash is required to execute the workflow step"
    script = tmp_path / "probe.sh"
    script.write_text(probe_script(), encoding="utf-8", newline="\n")
    out = tmp_path / "github_output.txt"
    out.write_text("", encoding="utf-8")
    result = subprocess.run(  # noqa: S603 -- executes this repo's own workflow step on test data
        [bash, "-e", "-o", "pipefail", script.as_posix()],
        env={
            **os.environ,
            "TEST_URL_OVERRIDE": url,
            "HEALTH_URL": "http://invalid.invalid/never-used",
            "GITHUB_OUTPUT": out.as_posix(),
            "RUNNER_TEMP": tmp_path.as_posix(),
            "CURL_TIMEOUT": "5",
            "RETRY_SLEEP": "0",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    outputs = dict(
        line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines() if "=" in line
    )
    return result, outputs


@pytest.fixture
def closed_port_url() -> Iterator[str]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    # Socket closed again: nothing listens on this port -> connection refused.
    yield f"http://127.0.0.1:{port}/health"


@pytest.mark.parametrize("body", [REAL_BODY, PRETTY_BODY])
def test_200_with_status_ok_is_up(tmp_path: Path, body: str) -> None:
    with _Server(200, body) as srv:
        result, out = run_probe(tmp_path, srv.url)
    assert result.returncode == 0, result.stdout + result.stderr
    assert out["up"] == "true", out
    assert out["reason"] == ""


def test_200_with_maintenance_page_is_down_by_content(tmp_path: Path) -> None:
    """The decorative case: HTTP 200, so the old status-only check called it UP."""
    with _Server(200, "<html>maintenance</html>") as srv:
        result, out = run_probe(tmp_path, srv.url)
    assert result.returncode == 0, result.stdout + result.stderr
    assert out["up"] == "false", out
    assert out["reason"] == "content"
    assert out["status"] == "200"
    assert "maintenance" in out["body"]


def test_200_with_unhealthy_json_is_down_by_content(tmp_path: Path) -> None:
    with _Server(200, '{"status":"unhealthy","db":"unreachable"}') as srv:
        _, out = run_probe(tmp_path, srv.url)
    assert out["up"] == "false"
    assert out["reason"] == "content"


def test_503_is_down_by_status(tmp_path: Path) -> None:
    with _Server(503, REAL_BODY) as srv:  # even a 503 carrying an ok-looking body is DOWN
        _, out = run_probe(tmp_path, srv.url)
    assert out["up"] == "false"
    assert out["reason"] == "status"
    assert out["status"] == "503"


def test_connection_refused_is_down_by_status(tmp_path: Path, closed_port_url: str) -> None:
    result, out = run_probe(tmp_path, closed_port_url)
    assert result.returncode == 0, result.stdout + result.stderr
    assert out["up"] == "false"
    assert out["reason"] == "status"
    assert out["status"] == "000"


def test_downstream_steps_key_on_up_output() -> None:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = {s["name"]: s for job in wf["jobs"].values() for s in job["steps"]}
    assert steps["Handle DOWN"]["if"] == "always() && steps.probe.outputs.up != 'true'"
    assert steps["Handle recovery"]["if"] == "always() && steps.probe.outputs.up == 'true'"
