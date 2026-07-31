"""SLO measurement report — Wave 1 Section F ("Reliability").

Computes the two numbers the section's SLO is defined against, from REAL
historical data (Cloud Monitoring + Cloud Logging), never a guessed percentage:

  1. Availability: (non-5xx requests / total requests) on the Cloud Run service,
     over a lookback window, from the built-in `run.googleapis.com/request_count`
     metric (durable — survives Cloud Run scale-to-zero, unlike the app's own
     in-process Prometheus counters, which reset on every cold start and are NOT
     a valid multi-day data source as currently deployed).
  2. Extraction latency percentiles (p50/p95/p99): parsed from the app's own
     structured `llm.extracted` log events (`app/core/llm.py`, field `latency_ms`),
     which land in Cloud Logging and ARE durable across restarts.

Caveat baked into every report, not just documentation: Cloud Run's
`request_latencies`/`request_count` metrics have no per-path breakdown (the `route`
label is permanently empty — verified live against the metric descriptor), so
availability is service-wide (all endpoints, including /health), not /v2/extract-
specific. Latency percentiles ARE /v2/extract-specific (sourced from the
extraction-only log event) but their sample size may be small — this report never
states a percentile as SLO-grade evidence below --min-n real extractions; below
that threshold it prints "INSUFFICIENT DATA" instead of a confident number.

Auth: uses Application Default Credentials (gcloud CLI local auth, or Workload
Identity Federation in CI) via `google.auth` -- both already transitive
dependencies of this project (via google-genai). No new dependency added.

Usage:
    uv run python scripts/slo_report.py
    uv run python scripts/slo_report.py --project review-iq-prod --days 14 --min-n 100
"""

from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass

import google.auth
import google.auth.transport.requests
import httpx

_MONITORING_URL = "https://monitoring.googleapis.com/v3"
_LOGGING_URL = "https://logging.googleapis.com/v2/entries:list"


@dataclass
class SloReport:
    """Result of one SLO measurement run."""

    project: str
    service: str
    window_days: int
    total_requests: int
    non_5xx_requests: int
    availability_pct: float
    extraction_count: int
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    latency_p99_ms: float | None
    min_n: int

    @property
    def latency_is_slo_grade(self) -> bool:
        """True once the extraction sample size clears the confidence floor."""
        return self.extraction_count >= self.min_n


def _percentile(sorted_values: list[float], p: float) -> float:
    """Return the p-th percentile (0.0-1.0) of an already-sorted list.

    Nearest-rank method — simple, deterministic, matches how the manual
    verification in this section's report was computed. Raises ValueError on
    an empty list (callers must check `len(values) > 0` first).
    """
    if not sorted_values:
        raise ValueError("Cannot compute a percentile of an empty list.")
    idx = min(len(sorted_values) - 1, int(len(sorted_values) * p))
    return sorted_values[idx]


def _get_access_token() -> str:
    """Return a fresh OAuth2 access token via Application Default Credentials."""
    creds, _project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    return str(creds.token)


def fetch_availability(project: str, service: str, days: int, token: str) -> tuple[int, int]:
    """Return (total_requests, non_5xx_requests) over the lookback window.

    Sourced from `run.googleapis.com/request_count`, grouped by response_code_class.
    """
    now = dt.datetime.now(dt.UTC)
    start = now - dt.timedelta(days=days)
    window_seconds = days * 86400

    resp = httpx.get(
        f"{_MONITORING_URL}/projects/{project}/timeSeries",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "filter": (
                'metric.type="run.googleapis.com/request_count" '
                f'AND resource.labels.service_name="{service}"'
            ),
            "interval.startTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "interval.endTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "aggregation.alignmentPeriod": f"{window_seconds}s",
            "aggregation.perSeriesAligner": "ALIGN_SUM",
            "aggregation.crossSeriesReducer": "REDUCE_SUM",
            "aggregation.groupByFields": "metric.labels.response_code_class",
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    series = resp.json().get("timeSeries", [])

    totals: dict[str, int] = {}
    for s in series:
        cls = s["metric"]["labels"].get("response_code_class", "unknown")
        total = sum(int(p["value"]["int64Value"]) for p in s.get("points", []))
        totals[cls] = totals.get(cls, 0) + total

    grand_total = sum(totals.values())
    non_5xx = grand_total - totals.get("5xx", 0)
    return grand_total, non_5xx


def fetch_extraction_latencies(project: str, service: str, days: int, token: str) -> list[float]:
    """Return every `llm.extracted` log event's `latency_ms` over the lookback window."""
    now = dt.datetime.now(dt.UTC)
    start = now - dt.timedelta(days=days)

    body = {
        "resourceNames": [f"projects/{project}"],
        "filter": (
            'resource.type="cloud_run_revision" '
            f'AND resource.labels.service_name="{service}" '
            'AND jsonPayload.event="llm.extracted" '
            f'AND timestamp>="{start.strftime("%Y-%m-%dT%H:%M:%SZ")}"'
        ),
        "orderBy": "timestamp desc",
        "pageSize": 1000,
    }
    resp = httpx.post(
        _LOGGING_URL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=30.0,
    )
    resp.raise_for_status()
    entries = resp.json().get("entries", [])
    return [
        float(e["jsonPayload"]["latency_ms"])
        for e in entries
        if "latency_ms" in e.get("jsonPayload", {})
    ]


def build_report(
    project: str, service: str, days: int, min_n: int, *, token: str | None = None
) -> SloReport:
    """Query Cloud Monitoring + Cloud Logging and assemble the SLO report."""
    token = token or _get_access_token()
    total, non_5xx = fetch_availability(project, service, days, token)
    latencies = sorted(fetch_extraction_latencies(project, service, days, token))

    availability_pct = (non_5xx / total * 100.0) if total else 0.0
    p50 = _percentile(latencies, 0.50) if latencies else None
    p95 = _percentile(latencies, 0.95) if latencies else None
    p99 = _percentile(latencies, 0.99) if latencies else None

    return SloReport(
        project=project,
        service=service,
        window_days=days,
        total_requests=total,
        non_5xx_requests=non_5xx,
        availability_pct=availability_pct,
        extraction_count=len(latencies),
        latency_p50_ms=p50,
        latency_p95_ms=p95,
        latency_p99_ms=p99,
        min_n=min_n,
    )


def print_report(report: SloReport) -> None:
    """Print the report, marking latency INSUFFICIENT DATA below the sample-size floor."""
    print(f"=== SLO report: {report.service} ({report.project}), last {report.window_days}d ===")
    print(
        f"Availability (non-5xx / total, ALL endpoints -- not /v2/extract-specific, "
        f"see module docstring): {report.availability_pct:.3f}% "
        f"({report.non_5xx_requests}/{report.total_requests} requests)"
    )
    if report.latency_is_slo_grade:
        print(
            f"Extraction latency (n={report.extraction_count}): "
            f"p50={report.latency_p50_ms:.0f}ms p95={report.latency_p95_ms:.0f}ms "
            f"p99={report.latency_p99_ms:.0f}ms"
        )
    else:
        print(
            f"Extraction latency: INSUFFICIENT DATA -- n={report.extraction_count} real "
            f"extractions in the window, below the --min-n={report.min_n} confidence floor. "
            "Do not publish a percentile from this sample; re-run once more traffic exists."
        )


def main() -> int:
    """CLI entry point. Always exits 0 -- this is a report, not a pass/fail gate."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="review-iq-prod")
    parser.add_argument("--service", default="review-iq")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument(
        "--min-n",
        type=int,
        default=100,
        help="Minimum real-extraction sample size before a latency percentile is SLO-grade.",
    )
    args = parser.parse_args()

    report = build_report(args.project, args.service, args.days, args.min_n)
    print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
