from __future__ import annotations

import enum
import hashlib
from datetime import UTC, datetime

from pydantic import BaseModel, field_validator


class AuthenticityFlag(enum.StrEnum):
    INCENTIVIZED_PHRASE = "incentivized_phrase"
    RATING_TEXT_MISMATCH = "rating_text_mismatch"
    GENERIC_LOW_INFO = "generic_low_info"
    EXCESSIVE_BREVITY = "excessive_brevity"
    PROMOTIONAL_TONE = "promotional_tone"
    NEAR_DUPLICATE = "near_duplicate"
    REVIEW_BURST = "review_burst"
    REPETITIVE_CONTENT = "repetitive_content"


class AuthenticityLabel(enum.StrEnum):
    GENUINE = "genuine"
    SUSPICIOUS = "suspicious"
    LIKELY_FAKE = "likely_fake"


class AuthenticityResult(BaseModel):
    """Result of the authenticity scoring pipeline for a single review."""

    score: float  # 0.0–1.0, higher = more likely GENUINE
    label: AuthenticityLabel
    flags: list[AuthenticityFlag] = []
    reasons: str = ""  # short human-readable explanation
    llm_signal_ok: bool = False  # True when LLM call succeeded and was parsed
    # Provenance
    review_hash: str  # sha256 hex of the raw review text
    scored_at: datetime
    model_used: str | None = None  # set when LLM signal contributed

    @field_validator("score")
    @classmethod
    def clamp_score(cls, v: float) -> float:
        return max(0.0, min(1.0, v))

    @classmethod
    def from_signals(
        cls,
        heuristic_score: float,
        llm_score: float | None,
        flags: list[AuthenticityFlag],
        reasons: str,
        review_text: str,
        model_used: str | None = None,
        llm_signal_ok: bool = False,
    ) -> AuthenticityResult:
        """Factory that combines heuristic and optional LLM scores into a final result.

        Blending: if llm_score is None, combined = heuristic_score.
        Otherwise combined = 0.4 * heuristic_score + 0.6 * llm_score.

        Label thresholds:
            combined >= 0.65 → GENUINE
            combined >= 0.40 → SUSPICIOUS
            else             → LIKELY_FAKE
        """
        combined = heuristic_score if llm_score is None else 0.4 * heuristic_score + 0.6 * llm_score

        combined = max(0.0, min(1.0, combined))

        if combined >= 0.65:
            label = AuthenticityLabel.GENUINE
        elif combined >= 0.40:
            label = AuthenticityLabel.SUSPICIOUS
        else:
            label = AuthenticityLabel.LIKELY_FAKE

        review_hash = hashlib.sha256(review_text.encode()).hexdigest()

        return cls(
            score=combined,
            label=label,
            flags=flags,
            reasons=reasons,
            llm_signal_ok=llm_signal_ok,
            review_hash=review_hash,
            scored_at=datetime.now(UTC),
            model_used=model_used,
        )
