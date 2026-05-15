"""Language detection for Review IQ."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Literal

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

_STRONG_HINGLISH = re.compile(
    r"\b(nahi|nhi|nahin|bahut|bohot|bhot|mujhe|mera|meri|yaar|paisa\s+vasool|vasool|"
    r"bakwaas|bakwas|ekdum|sahi\s+hai|bilkul|kaafi|kafi|thoda|thodi|jyada|zyada|"
    r"acha\s+hai|achha\s+hai|mast\s+(product|buy|item)|hai\s+na|kya\s+baat|"
    r"bindaas|jhakkas|zabardast|iska|iski|iske)\b",
    re.IGNORECASE,
)

_WEAK_HINGLISH = re.compile(
    r"\b(hai|hain|nai|mast|sahi|toh|yeh|ye(?!\s+another)|aur|bhi|"
    r"paisa|paise|value\s+for\s+money)\b",
    re.IGNORECASE,
)

DetectedLanguage = Literal["en", "hi-en", "hi", "other"]


@lru_cache(maxsize=1)
def _get_lingua_detector():
    """Build lingua-py detector (cached — slow to initialize)."""
    try:
        from lingua import Language, LanguageDetectorBuilder  # type: ignore[import]

        return LanguageDetectorBuilder.from_languages(
            Language.ENGLISH, Language.HINDI
        ).build()
    except Exception:
        return None


def detect_language(text: str) -> DetectedLanguage:
    """Detect the primary language of a review string.

    Order of precedence:
    1. Devanagari script → "hi"
    2. Strong Hinglish markers (regex) → "hi-en"
    3. Multiple weak Hinglish markers → "hi-en"
    4. lingua-py English confidence < 0.5 → "other"
    5. Default → "en"
    """
    text = text.strip()
    if len(text) < 5:
        return "other"

    if _DEVANAGARI.search(text):
        return "hi"

    if _STRONG_HINGLISH.search(text):
        return "hi-en"

    if len(_WEAK_HINGLISH.findall(text)) >= 3:
        return "hi-en"

    detector = _get_lingua_detector()
    if detector is not None:
        try:
            from lingua import Language  # type: ignore[import]

            confidence = detector.compute_language_confidence(text, Language.ENGLISH)
            if confidence < 0.5:
                return "other"
        except Exception:
            pass

    return "en"
