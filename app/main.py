"""Review IQ — FastAPI application entry point."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.account import router as account_router
from app.api.admin import router as admin_router
from app.api.bff.router import router as bff_router
from app.api.dashboard import router as dashboard_router
from app.api.demo import router as demo_router
from app.api.extract import router as extract_router
from app.api.google_auth import router as google_auth_router
from app.api.internal.digest import router as internal_digest_router
from app.api.ops import router as ops_router
from app.api.query import router as query_router
from app.api.shopify_auth import router as shopify_auth_router
from app.api.unsubscribe import router as unsubscribe_router
from app.api.v2.authenticity import router as v2_authenticity_router
from app.api.v2.corrections import router as v2_corrections_router
from app.api.v2.dataset import router as v2_dataset_router
from app.api.v2.extract import router as v2_extract_router
from app.api.v2.ingest import router as ingest_router
from app.api.v2.insights import router as v2_insights_router
from app.api.v2.reply import router as v2_reply_router
from app.api.v2.reviews import router as v2_reviews_router
from app.api.webhooks.google import router as google_webhook_router
from app.api.webhooks.shopify import router as shopify_webhook_router
from app.auth.signup import router as signup_router
from app.core.config import Settings, get_settings
from app.core.logging import setup_logging
from app.core.metrics import PrometheusMiddleware
from app.core.rate_limit import limiter
from app.core.storage import migrate

log = structlog.get_logger(__name__)

# The base URL below is a placeholder — swap in whatever host this deployment
# is actually served from (Cloud Run URL today; a custom domain later). No
# other part of these docs, or any client code following them, needs to change
# when that host changes.
_API_DESCRIPTION = """
Unstructured customer reviews → queryable structured insights.

## Quickstart

1. **Get an API key.** Sign in at the Samidha Reviews dashboard (Google sign-in via
   Supabase). Your first `riq_live_*` key is issued automatically on first
   login — see `POST /auth/provision` below. Free tier: 100 requests/month.
2. **Authenticate** every `/v2/*` request with either header (Bearer takes
   precedence if both are sent):
   - `Authorization: Bearer riq_live_<32 hex chars>`
   - `X-API-Key: riq_live_<32 hex chars>`
3. **Call the main endpoint**, `POST /v2/extract`:

```bash
curl -X POST "{base_url}/v2/extract" \\
  -H "Authorization: Bearer riq_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \\
  -H "Content-Type: application/json" \\
  -d '{"text": "Great sound quality but the battery dies after 3 hours. Would still recommend for the price."}'
```

```python
import requests

BASE_URL = "{base_url}"  # this deployment's host — see note below
API_KEY = "riq_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

resp = requests.post(
    f"{BASE_URL}/v2/extract",
    headers={"Authorization": f"Bearer {API_KEY}"},
    json={"text": "Great sound quality but the battery dies after 3 hours. "
                  "Would still recommend for the price."},
)
resp.raise_for_status()
print(resp.json())
```

> **Base URL**: `{base_url}` above is a placeholder for whatever host this API
> is deployed at — check the "Servers" section of this page for the actual URL.

## Rate limits (actual, current values)

| Scope | Limit |
|---|---|
| All endpoints, per IP | 30 requests/minute (`RATE_LIMIT_PER_MINUTE`) |
| `POST /auth/provision`, per IP | 10 requests/minute |
| `POST /demo/extract`, per IP | 5 requests/minute |
| Monthly quota, per API key | 100 requests/month on the free tier |

The per-minute limit applies regardless of authentication and returns `429`
from the rate limiter. The monthly quota is tracked per API key (not per IP)
and returns `429` with a `Monthly quota exceeded (used/quota)` message once
reached — contact support to raise it.

## Data isolation

- Every `/v2/*` request is scoped to your organization via your API key —
  `org_id` is resolved server-side from the key and is never taken from the
  request body.
- Postgres Row-Level Security policies enforce the same isolation at the
  database layer, independent of the application code.
- `/admin/*` endpoints are separate, HTTP Basic-authenticated, and are for
  Samidha Reviews operators only — not part of the tenant API surface.

## Endpoint groups

- **v2 / v2-authenticity / v2-insights / v2-ingest** — the multi-tenant API
  (Postgres-backed), all requiring a `riq_live_*` API key.
- **demo** — keyless, heavily rate-limited, for evaluation only.
- **auth** — first-login API key issuance.
- **admin** — org and key management (HTTP Basic auth, operators only).
- **ops** — `/health` and `/metrics` (unauthenticated).
"""


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    log.info("app.startup")
    if get_settings().deploy_target != "cloud-run":
        await migrate()
    yield
    log.info("app.shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()

    _app = FastAPI(
        title="Samidha Reviews API",
        description=_API_DESCRIPTION,
        version="0.2.0",
        lifespan=lifespan,
        servers=[{"url": "https://api.samidhareviews.xyz"}],
        openapi_tags=[
            {
                "name": "v2",
                "description": "Multi-tenant endpoints (Postgres-backed). Requires riq_live_* API key.",
            },
            {
                "name": "v2-authenticity",
                "description": "Tenant-scoped fake-review authenticity scoring. Requires riq_live_* API key.",
            },
            {
                "name": "v2-insights",
                "description": "Aggregated analytics — authenticity summaries, theme trends, health score. "
                "Requires riq_live_* API key.",
            },
            {
                "name": "v2-ingest",
                "description": "Bulk CSV upload and async batch-job polling. Requires riq_live_* API key.",
            },
            {
                "name": "demo",
                "description": "Keyless public demo of extraction. Heavily rate-limited (5/minute), "
                "no results stored — for evaluation only, not production use.",
            },
            {
                "name": "auth",
                "description": "Sign-up flow — issues your first riq_live_* API key on first login "
                "via the web dashboard.",
            },
            {"name": "extraction", "description": "v1 single-tenant extraction (SQLite-backed)."},
            {"name": "query", "description": "v1 query and analytics (SQLite-backed)."},
            {
                "name": "admin",
                "description": "Admin endpoints — org and key management. Requires HTTP Basic auth.",
            },
            {"name": "ops", "description": "Health check and Prometheus metrics."},
        ],
    )

    _app.state.limiter = limiter
    _app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

    # Middleware order (last add_middleware = outermost = first to process requests):
    #   SlowAPIMiddleware → PrometheusMiddleware → CORSMiddleware → route handler
    # CORS is innermost so it handles preflight OPTIONS before rate-limit counters advance.
    # CORS — explicit allowlist only. Wildcard must never reach production.
    # Origins configured via ALLOWED_ORIGINS env var (comma-separated).
    # Default covers local dev + demo Pages site; production Cloud Run sets
    # ALLOWED_ORIGINS to the locked web-app origin before the web app deploys.
    _app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
    )

    _app.add_middleware(PrometheusMiddleware)
    _app.add_middleware(SlowAPIMiddleware)

    # Ops (health + metrics) — always mounted, unauthenticated
    _app.include_router(ops_router)

    # v2, admin, and demo are always mounted
    _app.include_router(v2_extract_router)
    _app.include_router(ingest_router)
    _app.include_router(v2_reviews_router)
    _app.include_router(v2_authenticity_router)
    _app.include_router(v2_insights_router)
    _app.include_router(v2_reply_router)
    _app.include_router(v2_corrections_router)
    _app.include_router(v2_dataset_router)
    _app.include_router(shopify_webhook_router)
    _app.include_router(shopify_auth_router)
    _app.include_router(google_webhook_router)
    _app.include_router(google_auth_router)
    _app.include_router(bff_router)
    _app.include_router(admin_router)
    _app.include_router(signup_router)
    _app.include_router(account_router)
    _app.include_router(demo_router)
    _app.include_router(internal_digest_router)
    _app.include_router(unsubscribe_router)

    if settings.deploy_target != "cloud-run":
        _app.include_router(dashboard_router)
        _app.include_router(extract_router)
        _app.include_router(query_router)

    @_app.get("/metrics", tags=["ops"])
    async def metrics() -> Response:
        """Prometheus metrics in text exposition format."""
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return _app


app = create_app()
