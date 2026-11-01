"""Local load-test server: the real review-iq FastAPI app with LLM + DB + auth faked.

Binds 127.0.0.1 only, single uvicorn worker (same as the Dockerfile CMD). Nothing here can
reach a real provider or database: every secret/URL is replaced with a dummy BEFORE the app
is imported, the provider class is swapped for a fake, and the storage functions the
exercised routes call are replaced with in-process fakes. See docs/ops/load-test-s20.md.

Usage: python scripts/load/mock_server.py --port 18080 --llm-ms 800
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Dummy config set before app import (get_settings() is lru_cached at import time).
# 1_000_000/min disables the per-IP limiter's rejections without bypassing its code path
# (the SlowAPI middleware still runs and counts); prod's real value is 30/min.
os.environ.update(
    {
        "DEPLOY_TARGET": "cloud-run",  # same router set as prod; also skips startup migrate()
        "GROQ_API_KEY": "mock-not-a-real-key",
        "ENABLE_TIERED_ROUTING": "false",  # single-provider path; one fake call per extraction
        "RATE_LIMIT_PER_MINUTE": "1000000",
        "SUPABASE_DATABASE_URL": "postgresql://mock:mock@127.0.0.1:1/mock",  # never connected
        "SECONDARY_PROVIDER_API_KEY": "",
        "GEMINI_API_KEY": "",
    }
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Canned output of a valid ReviewExtractionLLMOutput (ASCII, no injection markers).
_CANNED = json.dumps(
    {
        "product": "wireless headphones",
        "stars": None,
        "stars_inferred": 4,
        "pros": ["great sound quality"],
        "cons": ["battery dies after 3 hours"],
        "buy_again": True,
        "sentiment": "mixed",
        "topics": ["sound_quality", "battery_life"],
        "competitor_mentions": [],
        "urgency": "low",
        "feature_requests": [],
        "language": "en",
        "confidence": 0.91,
    }
)


def build_app(llm_ms: int, db_ms: int) -> Any:
    """Import the real app, then patch the provider, auth and storage seams."""
    import app.api.bff.router as bff_router
    from app.api import ops
    from app.api.v2 import extract as v2_extract
    from app.api.v2 import reviews as v2_reviews
    from app.auth.api_key import ApiKeyContext, require_api_key
    from app.auth.session import require_session_read
    from app.core import llm
    from app.main import create_app

    class FakeProvider:
        """Structural match for app.core.providers.base.Provider."""

        trains_on_input = False

        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        async def complete(
            self,
            user_prompt: str,
            *,
            system_prompt: str,
            retry: bool = False,
            timeout: int = 30,
        ) -> tuple[str, int, int]:
            await asyncio.sleep(llm_ms / 1000)
            return _CANNED, 220, 90

    setattr(llm, "GroqProvider", FakeProvider)  # noqa: B010 -- keeps mypy quiet on monkeypatch

    async def fake_guard(text: str, *, api_key: str = "") -> bool:
        return False  # the real classifier is a second live Groq call: excluded

    async def noop_alert(**_: Any) -> None:
        return None

    def db_sleep() -> None:
        if db_ms:
            time.sleep(db_ms / 1000)

    rows = [
        {
            "id": f"row-{i}",
            "product": "wireless headphones",
            "sentiment": "mixed",
            "urgency": "low",
            "topics": ["sound_quality"],
            "review_text": "Great sound quality but the battery dies after 3 hours.",
        }
        for i in range(50)
    ]

    def fake_list(org_id: str, **_: Any) -> list[dict[str, Any]]:
        db_sleep()
        return [dict(r) for r in rows]  # callers mutate rows in place

    def fake_cost(*_: Any, **__: Any) -> str:
        db_sleep()
        return "cost-id"

    async def fake_ping(_dsn: str) -> None:
        await asyncio.sleep(db_ms / 1000)

    setattr(v2_extract, "classify_injection_risk", fake_guard)  # noqa: B010
    setattr(v2_extract, "alert_on_review_event", noop_alert)  # noqa: B010
    setattr(v2_extract, "record_extraction_cost_pg", fake_cost)  # noqa: B010
    setattr(v2_reviews, "list_extractions_pg", fake_list)  # noqa: B010
    setattr(bff_router, "list_extractions_pg", fake_list)  # noqa: B010
    setattr(ops, "_ping_postgres", fake_ping)  # noqa: B010

    ctx = ApiKeyContext(
        org_id="00000000-0000-0000-0000-000000000042",
        api_key_id="00000000-0000-0000-0000-000000000043",
        key_name="load",
        usage_record_id="",  # skips update_usage_tokens, as for system-triggered calls
        retention_mode="stateless",  # the default org mode: no cache lookup, no review persisted
    )

    async def fake_auth() -> ApiKeyContext:
        return ctx

    app = create_app()
    app.dependency_overrides[require_api_key] = fake_auth
    app.dependency_overrides[require_session_read] = fake_auth
    return app


async def bare_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """Minimal ASGI app (control run): same uvicorn + client, none of review-iq's stack."""
    if scope["type"] != "http":
        return
    await send(
        {"type": "http.response.start", "status": 200, "headers": [(b"content-length", b"2")]}
    )
    await send({"type": "http.response.body", "body": b"{}"})


def main() -> None:
    parser = argparse.ArgumentParser()
    # Non-loopback bind only for a throwaway IAM-protected Cloud Run service: auth is faked.
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--llm-ms", type=int, default=0)
    parser.add_argument("--db-ms", type=int, default=0)
    parser.add_argument(
        "--bare", action="store_true", help="serve a no-op ASGI app: measures the harness ceiling"
    )
    args = parser.parse_args()

    import uvicorn

    app = bare_app if args.bare else build_app(args.llm_ms, args.db_ms)
    uvicorn.run(app, host=args.host, port=args.port, workers=1, log_level="warning")


if __name__ == "__main__":
    main()
