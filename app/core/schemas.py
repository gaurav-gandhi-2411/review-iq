"""Pydantic v2 schemas for Review IQ extraction pipeline."""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator, model_validator


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
    extraction_meta: ExtractionMeta | None = None

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

    @model_validator(mode="after")
    def strip_text(self) -> ReviewRequest:
        self.text = self.text.strip()
        return self

    def input_hash(self) -> str:
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


class StoredReview(BaseModel):
    """A review extraction as stored in the database."""

    id: int | None = None
    input_hash: str
    review_text: str
    extraction: ReviewExtraction
    created_at: datetime = Field(default_factory=datetime.utcnow)
