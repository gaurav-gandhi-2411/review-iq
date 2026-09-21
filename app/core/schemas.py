"""Pydantic v2 schemas for Review IQ extraction pipeline."""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    Field,
    GetJsonSchemaHandler,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema


class Sentiment(StrEnum):
    positive = "positive"
    negative = "negative"
    neutral = "neutral"
    mixed = "mixed"


class Urgency(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class ExtractionMeta(BaseModel):
    """Provenance metadata attached to every extraction."""

    model: str
    prompt_version: str
    schema_version: str = "1.0.0"
    extracted_at: datetime = Field(default_factory=datetime.utcnow)
    latency_ms: int | None = None
    input_hash: str


class InjectionControlsReport(BaseModel):
    """What the opt-in field-targeted injection controls did to this extraction.

    Present on a response ONLY when ENABLE_FIELD_INJECTION_INPUT_CONTROL or
    ENABLE_FIELD_INJECTION_OUTPUT_CHECK is on (both default off); absent otherwise, so flag-off
    responses are byte-identical to before. Never carries review text. See
    app/core/injection_controls.py and SECURITY.md section 2.
    """

    input_stripped: bool = Field(
        default=False,
        description="True when one or more sentences were removed from the text sent to the "
        "extraction model because they looked like an instruction aimed at the schema fields.",
    )
    input_rules: list[str] = Field(
        default_factory=list, description="Names of the input rules that fired."
    )
    output_nulled: list[str] = Field(
        default_factory=list,
        description="Fields set to null because they contradicted the rest of the extraction "
        "(only ever `buy_again` and/or `stars_inferred`).",
    )
    output_soft_flags: list[str] = Field(
        default_factory=list,
        description="Consistency signals recorded without changing the output.",
    )
    needs_review: bool = Field(
        default=False,
        description="True when the input was stripped or an output field was nulled: a human "
        "should look at this review. A tripwire, not a guarantee: evasions exist.",
    )


class ReviewExtraction(BaseModel):
    """Structured output of the LLM extraction pipeline.

    All fields except `product` and `extraction_meta` may be null when the
    information is absent or uninferable from the source review.
    """

    product: str
    stars: Annotated[int | None, Field(ge=1, le=5)] = None
    stars_inferred: Annotated[int | None, Field(ge=1, le=5)] = None
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    buy_again: bool | None = None
    sentiment: Sentiment | None = None
    topics: list[str] = Field(default_factory=list)
    competitor_mentions: list[str] = Field(default_factory=list)
    urgency: Urgency = Urgency.low
    feature_requests: list[str] = Field(default_factory=list)
    language: str = "en"
    review_length_chars: int | None = None
    confidence: Annotated[float | None, Field(ge=0.0, le=1.0)] = None
    # The review's ORIGINAL post date, when the source provided one (NOT ingestion time, NOT
    # LLM-extracted -- carried through from ReviewRequest.review_date). None when unknown; never
    # fabricated from ingestion time. See ReviewRequest.review_date for the source-side contract.
    review_date: datetime | None = None
    extraction_meta: ExtractionMeta | None = None
    # Additive, opt-in (flags default off): see InjectionControlsReport. None => omitted from
    # serialised output entirely (model_serializer below), so a flag-off response has no new key.
    injection_controls: InjectionControlsReport | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_injection_controls(self, handler: SerializerFunctionWrapHandler) -> Any:
        data = handler(self)
        if isinstance(data, dict) and data.get("injection_controls") is None:
            data.pop("injection_controls", None)
        return data

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        # A wrap model_serializer with no return annotation makes pydantic emit an empty ({})
        # serialization-mode schema, which would blank this model's OpenAPI response docs.
        # Generate the schema from the plain field definitions instead.
        return handler({k: v for k, v in core_schema.items() if k != "serialization"})

    @field_validator("language")
    @classmethod
    def language_is_lowercase(cls, v: str) -> str:
        return v.lower()

    @field_validator("topics", "competitor_mentions", "feature_requests", mode="before")
    @classmethod
    def deduplicate_list(cls, v: list[str]) -> list[str]:
        seen: set[str] = set()
        result = []
        for item in v:
            lower = item.strip().lower()
            if lower not in seen:
                seen.add(lower)
                result.append(item.strip())
        return result


class ReviewExtractionLLMOutput(BaseModel):
    """Schema returned by the LLM — no extraction_meta (added by pipeline)."""

    product: str = "unknown product"
    stars: int | None = None
    stars_inferred: int | None = None

    @field_validator("product", mode="before")
    @classmethod
    def product_defaults_to_unknown(cls, v: Any) -> str:
        if v is None or not str(v).strip():
            return "unknown product"
        return str(v)

    @field_validator("stars", "stars_inferred", mode="before")
    @classmethod
    def coerce_stars(cls, v: Any) -> int | None:
        if v is None:
            return None
        try:
            n = int(v)
            return n if 1 <= n <= 5 else None
        except (TypeError, ValueError):
            return None

    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    buy_again: bool | None = None
    sentiment: Sentiment | None = None
    topics: list[str] = Field(default_factory=list)
    competitor_mentions: list[str] = Field(default_factory=list)
    urgency: Urgency = Urgency.low
    feature_requests: list[str] = Field(default_factory=list)
    language: str = "en"
    confidence: Annotated[float | None, Field(ge=0.0, le=1.0)] = None


class ReviewRequest(BaseModel):
    """Incoming request body for POST /extract."""

    text: Annotated[str, Field(min_length=1, max_length=5000)]
    # The review's ORIGINAL post date (NOT ingestion time) -- optional, since most sources don't
    # provide one today. Never fabricated: absent/unparseable means None, not a fallback to "now".
    # Deliberately excluded from input_hash() -- see that method's docstring.
    review_date: datetime | None = None

    @model_validator(mode="after")
    def strip_text(self) -> ReviewRequest:
        self.text = self.text.strip()
        return self

    def input_hash(self) -> str:
        """Content hash used for extraction caching -- TEXT ONLY, deliberately excludes
        review_date. If date were included, re-uploading identical review text with a corrected
        date would silently create a duplicate row instead of reusing the cached extraction,
        breaking get_by_hash_pg's "same text = same result" contract."""
        return "sha256:" + hashlib.sha256(self.text.encode()).hexdigest()


class BatchReviewRequest(BaseModel):
    """Incoming request body for POST /extract/batch."""

    reviews: Annotated[list[ReviewRequest], Field(min_length=1, max_length=100)]


class JobStatus(StrEnum):
    pending = "pending"
    processing = "processing"
    done = "done"
    failed = "failed"


class BatchJob(BaseModel):
    """Tracks a batch extraction job."""

    job_id: str
    status: JobStatus = JobStatus.pending
    total: int
    processed: int = 0
    failed: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None


class ExtractionMetaV2(ExtractionMeta):
    """ExtractionMeta extended with multi-tenant org_id for v2 responses.

    ``degraded`` is set True when the large model was quota-capped mid-escalation
    and the response was served from the small-model result instead.  Additive
    optional field — existing v2 clients that don't read it are unaffected.
    """

    org_id: str
    degraded: bool = False


class ReviewExtractionV2(ReviewExtraction):
    """ReviewExtraction with v2 metadata (includes org_id)."""

    extraction_meta: ExtractionMetaV2 | None = None


class StoredReview(BaseModel):
    """A review extraction as stored in the database."""

    id: int | None = None
    input_hash: str
    review_text: str
    extraction: ReviewExtraction
    created_at: datetime = Field(default_factory=datetime.utcnow)
