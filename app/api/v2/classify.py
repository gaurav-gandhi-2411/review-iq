"""POST /v2/classify -- review-intent classification (needs_action, broad_intent, sentiment, urgency).

OFF by default (ENABLE_V2_CLASSIFY): 404 like the other flagged v2 endpoints. Serves the int8 multilingual-e5 model with
per-class thresholds and explicit abstention; see ops/runbooks/v2-classify.md and app/core/classify/runtime.py.
"""

from __future__ import annotations

import asyncio
import time

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from app.auth.api_key import ApiKeyContext, require_api_key
from app.core.classify.errors import ClassifyUnavailableError
from app.core.config import get_settings
from app.core.metrics import CLASSIFY_LATENCY_MS, CLASSIFY_REQUESTS_TOTAL
from app.core.reply.errors import error_response

router = APIRouter(prefix="/v2", tags=["v2"])
log = structlog.get_logger(__name__)

MAX_REVIEWS = (
    32  # one CPU forward of 32 padded items is ~2 s locally (reports/serving/v2_classify_bench.md)
)
MAX_TEXT_CHARS = (
    5000  # the model truncates at 128 tokens anyway; this only bounds the tokenizer's work
)


class ReviewIn(BaseModel):
    id: str | None = Field(
        default=None, max_length=128, description="Echoed back; not used by the model"
    )
    text: str = Field(max_length=MAX_TEXT_CHARS)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text must not be blank")
        return v


class ClassifyRequest(BaseModel):
    reviews: list[ReviewIn] = Field(min_length=1, max_length=MAX_REVIEWS)


class TaskDecision(BaseModel):
    label: str = Field(
        description="A class, or 'abstained' when max calibrated probability < the validation-fitted threshold"
    )
    candidate_label: str = Field(
        description="The per-class-threshold decision before abstention (urgency=high is never argmax)"
    )
    abstained: bool
    confidence: float = Field(description="Max calibrated softmax over the task's classes")
    probability: float = Field(description="Calibrated probability of candidate_label")
    probabilities: dict[str, float]
    calibrated: bool


class ItemResult(BaseModel):
    index: int
    id: str | None
    tasks: dict[str, TaskDecision]


class ModelInfo(BaseModel):
    name: str
    run: str
    variant: str
    max_tokens: int


class DecisionInfo(BaseModel):
    mode: str
    abstention: str
    thresholds_file: str
    thresholds_sha256: str


class ClassifyResponse(BaseModel):
    model: ModelInfo
    decision: DecisionInfo
    results: list[ItemResult]
    latency_ms: float


def _require_enabled() -> None:
    """404 when ENABLE_V2_CLASSIFY is off (default), before auth: the endpoint does not exist for anyone."""
    if not get_settings().enable_v2_classify:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")


@router.post(
    "/classify",
    response_model=ClassifyResponse,
    dependencies=[Depends(_require_enabled)],
    summary="Classify reviews: needs_action, broad_intent, sentiment, urgency",
)
async def classify(
    body: ClassifyRequest, ctx: ApiKeyContext = Depends(require_api_key)
) -> ClassifyResponse | JSONResponse:
    """Per review and task: label or 'abstained', calibrated confidence, per-class probabilities. Text is never logged."""
    return await run_classify(body, ctx.org_id)


async def run_classify(body: ClassifyRequest, caller: str) -> ClassifyResponse | JSONResponse:
    """The endpoint's work, shared with the private service (app/classify_service.py), which authenticates at the Cloud Run edge.

    `caller` is only a log label (an org id on the public route, 'internal' on the private service)."""
    settings = get_settings()
    t0 = time.perf_counter()
    try:
        # Imported here, not at module top: the runtime needs numpy and the engine package, which the default app image
        # does not ship. With the flag off (default) this line is never reached, so the default app imports without them.
        from app.core.classify import runtime

        backend, th = await asyncio.to_thread(
            runtime.get_backend, settings.classify_artifact_dir, settings.classify_torch_threads
        )
        texts = [r.text for r in body.reviews]
        per_item = runtime.decisions(th, await asyncio.to_thread(backend.logits, texts))
    except (ClassifyUnavailableError, ImportError) as e:
        code = e.code if isinstance(e, ClassifyUnavailableError) else "classify_runtime_missing"
        CLASSIFY_REQUESTS_TOTAL.labels(outcome="unavailable").inc()
        log.warning("classify.unavailable", code=code, caller=caller)
        return error_response(
            503, code, "Classification is temporarily unavailable.", retry_after=60
        )
    except Exception:
        CLASSIFY_REQUESTS_TOTAL.labels(outcome="error").inc()
        log.exception("classify.failed", caller=caller, n_reviews=len(body.reviews))
        return error_response(500, "classify_internal_error", "Classification failed.")
    latency_ms = round((time.perf_counter() - t0) * 1000, 2)
    CLASSIFY_REQUESTS_TOTAL.labels(outcome="ok").inc()
    CLASSIFY_LATENCY_MS.observe(latency_ms)
    log.info(
        "classify.ok", caller=caller, n_reviews=len(body.reviews), latency_ms=latency_ms,
        model=f"{th.run}-{th.variant}", thresholds_sha256=th.sha256[:12],
        n_abstained=sum(d["abstained"] for item in per_item for d in item.values()),
    )  # fmt: skip
    return ClassifyResponse(
        model=ModelInfo(
            name="multilingual-e5-base (review-intent multi-task)", run=th.run, variant=th.variant, max_tokens=getattr(backend, "max_len", th.max_len)
        ),
        decision=DecisionInfo(
            mode="threshold", abstention="conservative_p0.95 (validation-fitted, Wilson lower bound)",
            thresholds_file=th.path, thresholds_sha256=th.sha256,
        ),
        results=[
            ItemResult(index=i, id=r.id, tasks={t: TaskDecision(**d) for t, d in item.items()})
            for i, (r, item) in enumerate(zip(body.reviews, per_item, strict=True))
        ],
        latency_ms=latency_ms,
    )  # fmt: skip
