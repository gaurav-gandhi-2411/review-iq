"""Poll a locally running review-iq container's /health until it answers, or fail.

Runs INSIDE the container (fed on stdin by the `docker-build` CI job, so nothing is copied into
the image and the container can run with `--network none`):

    docker exec -i <container> python - < scripts/smoke_container_health.py

Why inside, not curl from the runner: the smoke test must not be able to reach any real service
(no DB, no LLM provider, no Supabase). With `--network none` a startup path that tries to dial out
fails here instead of silently succeeding against production. Standard library only, because the
runtime image is `uv sync --no-dev` and this must not depend on anything test-only.

What it proves: the image starts, `app.main:app` imports with the runtime-only dependency set
(a dev-only import would crash here but not in `lint-and-test`, which installs dev dependencies),
uvicorn binds the port, and the ops router answers. It does NOT prove the database or any
provider is reachable (there are none): 503 from /health with a parseable body still counts as
"the app is up", because with no database configured the app reports `db` state honestly.

Exit 0: HTTP response with a JSON body containing a "status" key within the deadline.
Exit 1: no response before the deadline, or a response that is not the health payload.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any


def fetch_health(url: str, timeout: float = 5.0) -> tuple[int, Any]:
    """GET `url`; return (http_status, parsed_json_or_None).

    HTTPError responses (e.g. 503 unhealthy) are returned, not raised: an application that
    answers with an error status is still an application that started. Connection-level
    failures propagate to the caller.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - fixed http://localhost URL
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return status, None


def is_health_payload(status: int, body: Any) -> bool:
    """True only for a real /health response: a JSON object with a `status` key and a status
    code the health route can produce (200 healthy, 503 unhealthy).

    Rejects a generic 404/500 or an HTML error page -- e.g. a different process bound to the
    port -- so the check cannot pass on "something answered".
    """
    return status in (200, 503) and isinstance(body, dict) and "status" in body


def wait_for_health(url: str, deadline_s: float, interval_s: float = 1.0) -> tuple[bool, str]:
    """Poll `url` until a valid health payload arrives or `deadline_s` elapses."""
    end = time.monotonic() + deadline_s
    last = "no attempt made"
    while time.monotonic() < end:
        try:
            status, body = fetch_health(url)
        except (urllib.error.URLError, OSError) as exc:
            last = f"connection failed: {exc}"
        else:
            if is_health_payload(status, body):
                return True, f"HTTP {status} {json.dumps(body, sort_keys=True)}"
            last = f"unexpected response: HTTP {status} body={body!r}"
        time.sleep(interval_s)
    return False, last


def main() -> int:
    port = os.environ.get("PORT", "8080")
    deadline_s = float(os.environ.get("SMOKE_DEADLINE_S", "60"))
    ok, detail = wait_for_health(f"http://localhost:{port}/health", deadline_s)
    print(("OK: " if ok else "FAIL: ") + detail)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
