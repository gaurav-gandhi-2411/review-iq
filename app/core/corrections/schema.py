from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, field_validator, model_validator


class SourceType(StrEnum):
    extraction = "extraction"
    authenticity = "authenticity"
    reply = "reply"


ALLOWED_FIELD_PATHS: dict[SourceType, frozenset[str]] = {
    SourceType.extraction: frozenset(
        {
            "sentiment",
            "stars",
            "stars_inferred",
            "buy_again",
            "urgency",
            "language",
            "pros",
            "cons",
            "topics",
            "competitor_mentions",
            "feature_requests",
            "product",
        }
    ),
    SourceType.authenticity: frozenset({"score", "label", "flags"}),
    SourceType.reply: frozenset({"reply_text", "tone"}),
}


def validate_field_path(source_type: SourceType, field_path: str) -> None:
    """Raise ValueError if field_path is not correctable for the given source_type."""
    allowed = ALLOWED_FIELD_PATHS[source_type]
    if field_path not in allowed:
        raise ValueError(
            f"'{field_path}' is not a correctable field for source_type '{source_type}'. "
            f"Allowed paths: {sorted(allowed)}"
        )


class Correction(BaseModel):
    id: str | None = None
    org_id: str
    # Plain sha256 hex of the review text — no "sha256:" prefix (that's the extraction format).
    review_id: str
    source_type: SourceType
    field_path: str
    original_value: str
    corrected_value: str
    correction_note: str | None = None
    language: str = "en"
    corrected_at: datetime = None  # type: ignore[assignment]

    def model_post_init(self, __context: object) -> None:
        if self.corrected_at is None:
            object.__setattr__(self, "corrected_at", datetime.utcnow())

    @field_validator("review_id")
    @classmethod
    def reject_prefixed_review_id(cls, v: str) -> str:
        if v.startswith("sha256:"):
            raise ValueError("review_id must be plain sha256 hex without prefix")
        return v

    @field_validator("language")
    @classmethod
    def lowercase_language(cls, v: str) -> str:
        return v.lower()

    @model_validator(mode="after")
    def check_field_path(self) -> Correction:
        validate_field_path(self.source_type, self.field_path)
        return self
