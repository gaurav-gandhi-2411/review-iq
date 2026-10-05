"""Language detection for Review IQ."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

# Session 10 P3 (review-iq): excludes U+0964/U+0965 (DEVANAGARI DANDA / DOUBLE DANDA) from the
# block this regex otherwise covers wholesale -- the exact fix ADR 0016 already applied to
# eval/data/sample_flipkart.py's corpus-mining detector, found to have a live sibling here.
# Found by measurement, not inspection: scoring current production against the held-out corpus
# (docs/architecture/adr/0018-*.md) showed language-field accuracy of only 52% on hi-en
# fixtures, and a live smoke test against this exact detector confirmed a pure-English review
# using a stray danda as a period ("...canbe like this । It's awesome") gets classified "hi"
# here too -- the same false positive, in the actual serving path, not just corpus tooling.
_DEVANAGARI = re.compile(r"[ऀ-ॣ०-ॿ]")

_STRONG_HINGLISH = re.compile(
    r"\b(nahi|nhi|nahin|bahut|bohot|bhot|mujhe|mera|meri|yaar|paisa\s+vasool|vasool|"
    r"bakwaas|bakwas|ekdum|sahi\s+hai|bilkul|kaafi|kafi|thoda|thodi|jyada|zyada|"
    r"acha\s+hai|achha\s+hai|mast\s+(product|buy|item)|hai\s+na|kya\s+baat|"
    r"bindaas|jhakkas|zabardast|iska|iski|iske|"
    # 2026-07-08: added from real-data misses on the review-iq vernacular silver
    # benchmark (58 hi-en reviews the detector sent down the English prompt path
    # purely because these spelling variants/words were absent — see
    # benchmark/vernacular_v2/ + project memory). "wasool" is the transliteration
    # of "वसूल" — Latin-script Hindi has no fixed spelling, w/v are used
    # interchangeably by Indian typists, hence both variants.
    r"wasool|wasul|washul|faltu|ghatiya|sasta|milega|tikau|sunder|"
    # 2026-07-10: added from a real GAP2 field-drop failure (benchmark/gap_fixes/) —
    # "Bekar product" (2 words, no other language cue) misrouted to en.py and the
    # model read "Bekar" as a literal product name, extracting sentiment=neutral,
    # 0 cons on an unambiguous 1-star complaint. "kharab"/"dhoka"/"dhokha" added
    # alongside from the same real-data sweep — previously catalogued as
    # unproven single-occurrence tokens (see corpus-isolation notes) but now have
    # concrete production-failure evidence, not just theoretical coverage.
    r"bekar|bekaar|kharab|kharaab|dhoka|dhokha)\b",
    re.IGNORECASE,
)

_WEAK_HINGLISH = re.compile(
    r"\b(hai|hain|nai|mast|sahi|toh|yeh|ye(?!\s+another)|aur|bhi|"
    r"paisa|paise|value\s+for\s+money)\b",
    re.IGNORECASE,
)

DetectedLanguage = Literal["en", "hi-en", "hi", "other"]


@lru_cache(maxsize=1)
def _get_lingua_detector() -> Any | None:
    """Build lingua-py detector (cached — slow to initialize)."""
    try:
        from lingua import Language, LanguageDetectorBuilder

        return LanguageDetectorBuilder.from_languages(Language.ENGLISH, Language.HINDI).build()
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
            from lingua import Language

            confidence = detector.compute_language_confidence(text, Language.ENGLISH)
            if confidence < 0.5:
                return "other"
        except Exception:
            pass

    return "en"


# ---------------------------------------------------------------------------
# Session 15d (D7): customer-facing signal that accompanies the discrete `language` label.
#
# WHY: `language` is the detector's output, and its agreement with the held-out corpus label is
# only 48.1% (95% CI 38.8-57.5) against a label whose inter-rater alpha is 0.380
# (eval/results/routing_cost_n106.json; ADR 0021/0023 Correction sections), so a bare discrete
# value overstates what is known. This is deliberately NOT a calibrated probability: no labelled
# set exists on which a probability could be fitted without contaminating the held-out corpus,
# and D7 rules out investing in the detector. It only reports the rule-hit evidence the detector
# already computes. detect_language()'s decisions and the prompt routing are untouched.
# ---------------------------------------------------------------------------

SignalStrength = Literal["none", "weak", "moderate", "strong"]

# Latin letters, to tell Devanagari-only text (`hi`) from Devanagari mixed with English.
_LATIN = re.compile(r"[A-Za-z]")


@dataclass(frozen=True)
class LanguageSignal:
    """Rule-hit evidence for Hindi content in a text (not a probability).

    code_mixed: the text contains Romanized-Hindi vocabulary (any strong or weak marker from
        detect_language's own lexicons) or Devanagari together with Latin letters. It is a
        recall-oriented flag: the weak lexicon includes an English collision ("value for money"),
        so it can be True for English text. On the held-out corpus the "strong or >=1 weak
        marker" rule flagged 101/101 corpus-hi-en and 5/5 corpus-en reviews, and 0/49 external
        English texts (routing_cost_n106.json, detector_baselines / detector_external_controls).
        Always True when detect_language returns "hi-en".
    strength: ordinal strength of the Hindi-marker evidence, ad hoc thresholds, uncalibrated:
        strong = Devanagari present or >=2 strong-marker hits; moderate = exactly 1 strong hit or
        >=3 weak hits; weak = 1-2 weak hits and no strong hit (below the hi-en threshold, i.e.
        near the decision boundary); none = no Hindi marker found. "none" is absence of evidence,
        NOT evidence that the text is English.
    """

    code_mixed: bool
    strength: SignalStrength


def language_signal(text: str) -> LanguageSignal:
    """Summarise the Hindi-marker evidence detect_language sees in `text`.

    Pure and deterministic; uses the same regexes as detect_language and never changes its
    decision. Pass the same text that was passed to detect_language.
    """
    text = text.strip()
    devanagari = bool(_DEVANAGARI.search(text))
    n_strong = len(_STRONG_HINGLISH.findall(text))
    n_weak = len(_WEAK_HINGLISH.findall(text))

    strength: SignalStrength
    if devanagari or n_strong >= 2:
        strength = "strong"
    elif n_strong == 1 or n_weak >= 3:
        strength = "moderate"
    elif n_weak >= 1:
        strength = "weak"
    else:
        strength = "none"

    romanized_markers = n_strong + n_weak >= 1
    devanagari_mixed_with_latin = devanagari and bool(_LATIN.search(text))
    return LanguageSignal(
        code_mixed=romanized_markers or devanagari_mixed_with_latin,
        strength=strength,
    )
