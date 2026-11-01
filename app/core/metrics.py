"""Prometheus metric definitions and Starlette middleware for HTTP instrumentation."""

from __future__ import annotations

import re
import time

from prometheus_client import Counter, Gauge, Histogram
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

EXTRACTIONS_TOTAL = Counter(
    "review_iq_extractions_total",
    "Total extraction requests processed",
    ["model", "cached"],
)

EXTRACTION_LATENCY = Histogram(
    "review_iq_extraction_latency_ms",
    "LLM extraction latency in milliseconds",
    ["model"],
    buckets=[100, 250, 500, 1000, 2000, 5000, 10000, 30000],
)

HTTP_REQUESTS_TOTAL = Counter(
    "review_iq_http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status_code"],
)

HTTP_REQUEST_DURATION = Histogram(
    "review_iq_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

ROUTER_TIER_TOTAL = Counter(
    "review_iq_router_tier_total",
    "Routing decisions by final tier (escalated requests counted as large)",
    ["tier"],
)

ROUTER_ESCALATIONS_TOTAL = Counter(
    "review_iq_router_escalations_total",
    "Small-to-large escalation events",
)

ROUTER_TIER_TOKENS_IN = Counter(
    "review_iq_router_tier_tokens_in_total",
    "Input tokens consumed per routing tier",
    ["tier"],
)

FAILOVER_TOTAL = Counter(
    "review_iq_failover_total",
    "Provider failover events (Groq exhausted, falling to secondary)",
    ["from_provider"],
)

RATE_LIMITED_TOTAL = Counter(
    "review_iq_llm_rate_limited_total",
    "Provider 429 responses (see llm.rate_limited log event)",
    ["provider", "model", "tier", "limit_type"],
)

COST_ALERT_TOTAL = Counter(
    "review_iq_cost_alert_total",
    "Customers whose month-to-date inference cost exceeded the alert fraction of their plan price (flag-gated sweep)",
    ["plan"],
)

ORG_COST_RATIO = Gauge(
    "review_iq_org_inference_cost_ratio",
    "Highest month-to-date inference cost / plan price among the paying orgs of a plan (set by the cost-alert sweep)",
    ["plan"],
)

AUTHENTICITY_LABEL_TOTAL = Counter(
    "review_iq_authenticity_label_total",
    "Authenticity scoring results by label",
    ["label"],
)

AUTHENTICITY_FLAG_TOTAL = Counter(
    "review_iq_authenticity_flag_total",
    "Authenticity flag occurrences by flag type",
    ["flag"],
)

AUTHENTICITY_DUPLICATE_CLUSTER_TOTAL = Counter(
    "review_iq_authenticity_duplicate_clusters_total",
    "Near-duplicate clusters detected in batch scoring",
)

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _normalize_path(path: str) -> str:
    """Replace UUIDs and numeric IDs in paths to avoid label cardinality explosion."""
    path = _UUID_RE.sub("{id}", path)
    path = re.sub(r"/\d+", "/{id}", path)
    return path


REPLY_DRAFT_TOTAL = Counter(
    "review_iq_reply_drafts_total",
    "Total reply drafts produced",
    ["language", "tone"],
)

REPLY_DEGRADED_TOTAL = Counter(
    "review_iq_reply_degraded_total",
    "Reply drafts that fell back to small model due to large model quota cap",
)

REPLY_CACHE_HIT_TOTAL = Counter(
    "review_iq_reply_cache_hits_total",
    "Reply draft cache hits (same review/tone/brand/signature re-requested)",
)

CLASSIFY_REQUESTS_TOTAL = Counter(
    "review_iq_classify_requests_total",
    "POST /v2/classify requests by outcome (ok / unavailable / error)",
    ["outcome"],
)

CLASSIFY_LATENCY_MS = Histogram(
    "review_iq_classify_latency_ms",
    "POST /v2/classify model latency in milliseconds (batch of up to 32 reviews)",
    buckets=[25, 50, 100, 250, 500, 1000, 2500, 5000, 10000],
)

ENCODER_FIRST_TOTAL = Counter(
    "review_iq_encoder_first_total",
    "Encoder-first routing outcomes (routed to LLM / skipped = classified-only / fail_open = classifier problem, routed to LLM)",
    ["outcome"],
)

CORRECTIONS_SUBMITTED = Counter(
    "review_iq_corrections_submitted_total",
    "Correction submissions by source_type",
    ["source_type"],
)


LEADS_TOTAL = Counter(
    "review_iq_leads_total",
    "POST /leads outcomes (accepted, honeypot, invalid, persist_failed, email_degraded)",
    ["outcome"],
)


class PrometheusMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = _normalize_path(request.url.path)
        method = request.method
        t0 = time.perf_counter()

        response = await call_next(request)

        duration = time.perf_counter() - t0
        status = str(response.status_code)
        HTTP_REQUESTS_TOTAL.labels(method=method, path=path, status_code=status).inc()
        HTTP_REQUEST_DURATION.labels(method=method, path=path).observe(duration)

        return response
