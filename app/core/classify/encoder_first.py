"""Encoder-first routing: the classifier decides which reviews need full LLM extraction (spec Amendment 14, S27). Flag OFF.

The encoder classifies every review with no LLM tokens. Router R1 (fixed in Amendment 14 before any result) sends a review to
LLM extraction iff the encoder says `needs_action` is yes or abstained, OR the urgency decision is `high` (the per-class threshold
decision, never argmax) or abstained. Every other review is classified-only and is extracted when the user opens it.
Failure policy: ANY classifier problem routes the review to the LLM path (today's behaviour), never drops it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import structlog

log = structlog.get_logger(__name__)

ABSTAINED = "abstained"
_METADATA_ID = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity"
)
_TOKEN_TTL_S = 3000.0  # identity tokens live 3600 s; refresh early
# The router runs in the background ingest worker, where nobody waits, so its timeout must cover a cold start of the private
# service (scale to zero; measured 15.2 s on Cloud Run, S29) with margin: about four times that. A future USER-FACING caller must
# build its own RemoteClassifier with the short default (10 s) instead of reusing this one.
INGEST_TIMEOUT_S = 60.0


@dataclass(frozen=True)
class RouteDecision:
    route: bool  # True = run full LLM extraction
    reasons: tuple[str, ...]  # why; ("classifier_unavailable",) on the fail-open path


def route_r1(tasks: dict[str, dict[str, Any]]) -> RouteDecision:
    """Router R1 over one review's served decisions: {task: {label, candidate_label, ...}} (runtime.decisions / API shape)."""
    reasons = []
    na = tasks["needs_action"]["label"]
    if na == "yes":
        reasons.append("needs_action_yes")
    elif na == ABSTAINED:
        reasons.append("needs_action_abstained")
    urg = tasks["urgency"]
    if urg["candidate_label"] == "high":
        reasons.append("urgency_high_candidate")
    if urg["label"] == ABSTAINED:
        reasons.append("urgency_abstained")
    return RouteDecision(bool(reasons), tuple(reasons))


def route_r2(tasks: dict[str, dict[str, Any]]) -> RouteDecision:
    """R1 without the urgency-abstained clause: a sensitivity only, never selectable on results (Amendment 14)."""
    d = route_r1(tasks)
    kept = tuple(r for r in d.reasons if r != "urgency_abstained")
    return RouteDecision(bool(kept), kept)


def capacity_multiplier(routed_share: float, open_rate: float) -> float:
    """M = 1 / (f + (1 - f) * o): extractions saved when only routed reviews, plus a share `open_rate` of the rest, are extracted."""
    return 1.0 / (routed_share + (1.0 - routed_share) * open_rate)


class Classifier(Protocol):
    async def classify(self, texts: list[str]) -> list[dict[str, dict[str, Any]]]: ...


class RemoteClassifier:
    """Calls the PRIVATE classify service (app/classify_service.py) with a Google identity token for the service URL."""

    def __init__(self, base_url: str, timeout_s: float = 10.0) -> None:
        self._url = base_url.rstrip("/")
        self._timeout = timeout_s
        self._token = ""
        self._token_at = 0.0

    async def _id_token(self, client: httpx.AsyncClient) -> str:
        if not self._token or time.monotonic() - self._token_at > _TOKEN_TTL_S:
            r = await client.get(
                _METADATA_ID, params={"audience": self._url}, headers={"Metadata-Flavor": "Google"}
            )
            r.raise_for_status()
            self._token, self._token_at = r.text.strip(), time.monotonic()
        return self._token

    async def classify_full(self, texts: list[str]) -> tuple[list[dict[str, dict[str, Any]]], str]:
        """Per-review task decisions and the sha256 of the thresholds file the service decided with."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            token = await self._id_token(client)
            r = await client.post(
                f"{self._url}/v2/classify",
                json={"reviews": [{"text": t} for t in texts]},
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            body = r.json()
            return [item["tasks"] for item in body["results"]], str(
                body["decision"]["thresholds_sha256"]
            )

    async def classify(self, texts: list[str]) -> list[dict[str, dict[str, Any]]]:
        return (await self.classify_full(texts))[0]


async def plan_routes(
    client: Classifier, texts: list[str], router: Any = route_r1
) -> list[RouteDecision]:
    """One decision per text. Fail-open: any error, or a malformed answer, routes every text to the LLM path."""
    try:
        per_item = await client.classify(texts)
        if len(per_item) != len(texts):
            raise ValueError("classifier returned a different number of results")
        return [router(t) for t in per_item]
    except Exception as e:  # noqa: BLE001 (fail-open by design: an encoder problem must never drop a review)
        log.warning("encoder_first.fail_open", error=type(e).__name__, n=len(texts))
        return [RouteDecision(True, ("classifier_unavailable",)) for _ in texts]


_remote: RemoteClassifier | None = None


@dataclass(frozen=True)
class IngestPlan:
    route: bool  # True = run full LLM extraction now
    tasks: dict[str, dict[str, Any]] | None = (
        None  # the encoder's decisions; None when the classifier was not consulted or failed
    )
    thresholds_sha256: str | None = None


async def plan_ingest(text: str) -> IngestPlan:
    """The worker's question for one review: extract now, or keep it classified-only (with the decisions to persist).
    Routes to the LLM (today's behaviour) unless the flag is on, the service is configured and the encoder answered. Never raises."""
    from app.core.config import get_settings
    from app.core.metrics import ENCODER_FIRST_TOTAL

    global _remote
    s = get_settings()
    if not s.enable_encoder_first or not s.classify_service_url:
        return IngestPlan(True)
    if _remote is None or _remote._url != s.classify_service_url.rstrip("/"):
        _remote = RemoteClassifier(s.classify_service_url, timeout_s=INGEST_TIMEOUT_S)
    try:
        per_item, sha = await _remote.classify_full([text])
        if len(per_item) != 1:
            raise ValueError("classifier returned a different number of results")
        d = route_r1(per_item[0])
    except Exception as e:  # noqa: BLE001 (fail-open by design: an encoder problem must never drop a review)
        log.warning("encoder_first.fail_open", error=type(e).__name__, n=1)
        ENCODER_FIRST_TOTAL.labels(outcome="fail_open").inc()
        return IngestPlan(True)
    ENCODER_FIRST_TOTAL.labels(outcome="routed" if d.route else "skipped").inc()
    return IngestPlan(d.route, per_item[0], sha)


async def should_extract(text: str) -> bool:
    """plan_ingest's route decision alone (kept for callers that do not persist skipped reviews). Never raises."""
    return (await plan_ingest(text)).route
