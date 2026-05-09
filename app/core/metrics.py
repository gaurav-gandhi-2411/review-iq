"""Prometheus metric definitions and Starlette middleware for HTTP instrumentation."""

from __future__ import annotations

import re
import time

from prometheus_client import Counter, Histogram
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

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _normalize_path(path: str) -> str:
    """Replace UUIDs and numeric IDs in paths to avoid label cardinality explosion."""
    path = _UUID_RE.sub("{id}", path)
    path = re.sub(r"/\d+", "/{id}", path)
    return path


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
