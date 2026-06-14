from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.core.schemas import ReviewExtraction


class ReplyTone(str, Enum):
    apologetic = "apologetic"
    appreciative = "appreciative"
    professional = "professional"
    warm = "warm"


class ReplyRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)
    tone: ReplyTone = ReplyTone.professional
    brand_name: str | None = None
    signature: str | None = None
    extraction: ReviewExtraction | None = None

    def cache_key(self) -> str:
        payload = f"{self.text}|{self.tone.value}|{self.brand_name or ''}|{self.signature or ''}"
        return hashlib.sha256(payload.encode()).hexdigest()


class ReplyBatchRequest(BaseModel):
    reviews: list[ReplyRequest] = Field(..., min_length=1, max_length=20)


class ReplyDraft(BaseModel):
    reply_text: str
    language: str
    tone: ReplyTone
    grounded_on: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    model_used: str
    drafted_at: datetime
