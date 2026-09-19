"""Scorers for FREE-TEXT extraction fields (`product`, `topics`, `competitor_mentions`).

Why this module exists (Session 15c C2): exact-string matching is the wrong comparator for a
field whose value is free text written independently by two parties (the model, and the
corpus labelers). Two instances of the same bug class have now been found:

  - Session 10, `topics`: near-miss paraphrases ("battery" vs "battery_life") scored 0.
  - Session 15b Q8, `product`: when a review names no product, the model writes
    "unknown product"/"general product" (its schema default / habit) and the labelers wrote
    "unknown"/"product" -- four different, all-correct spellings of "no product named", scored
    wrong by exact match. On the n=106 held-out set `product` scored 0.236 (81 of 106 zero).

Design rules (a priori, deliberately NOT fitted to the held-out set -- fitting a scorer to the
set it then scores is the contamination pattern, so every rule below is structural and the
derivation of the null vocabulary is reproducible from `eval/derive_product_null_set.py`):

  1. Normalization is applied to BOTH prediction and gold, identically (symmetric).
  2. A `product` is "null" (no product named) iff every token is in NULL_PRODUCT_TOKENS, or
     the whole string is a null literal. Any content token (a brand, model or category noun)
     makes it non-null: "OnePlus product" and "Mast" are NOT null.
  3. Non-null products compare case-, punctuation-, plural-, word-order- and spacing-
     insensitively ("Headphones" == "headphone", "OnePlus Bluetooth" == "Bluetooth oneplus",
     "one plus" == "oneplus"). Substring / superset / synonym matches are NOT credited:
     "BassDesign" != "BassDesignSound", "headset" != "Ultimate headset Paisa",
     "earphone" != "headphones". A hallucination stays wrong.
  4. `topics` are compared after structural normalization only (see canonical_topic). This is
     a partial fix for a vocabulary problem a semantic judge would solve better; it removes
     the mechanical near-misses without inventing synonym knowledge.

`strict=True` in eval.runner.score_fixture bypasses all of this and reproduces the pre-15c
exact-string behaviour, so every published delta is attributable to exactly this module.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

SCORER_VERSION = "2026-09-20.free-text-v1"

# Tokens that carry no product identity. Derived by inventorying every distinct `product`
# value in the held-out gold, held-out predictions and the dev-set gold and keeping only the
# tokens that appear in placeholder strings ("unknown", "product", "products", "general
# product", "unknown product", "this product") plus English articles/demonstratives that can
# only ever wrap them -- see eval/derive_product_null_set.py, which regenerates the inventory.
# "unspecified" was added after the independent dev-set-gold check in that script showed a
# labeler-written "unspecified product" being misclassified as a named product; it is a plain
# synonym of "unknown" and was found OUTSIDE the held-out set, so adding it is not fitting.
NULL_PRODUCT_TOKENS: frozenset[str] = frozenset(
    {
        "unknown",
        "unspecified",
        "product",
        "products",
        "general",
        "this",
        "the",
        "a",
        "an",
        "item",
        "items",
    }
)
# Whole-string null literals (their tokens alone would not qualify: "n", "na", "nil").
NULL_PRODUCT_LITERALS: frozenset[str] = frozenset({"n/a", "na", "none", "null", "nil", "-", ""})

_NON_WORD = re.compile(r"[^\w\s]", flags=re.UNICODE)


def _tokens(value: str) -> list[str]:
    text = unicodedata.normalize("NFKC", value).lower().strip()
    return _NON_WORD.sub(" ", text).split()


def _singular(token: str) -> str:
    # Symmetric on both sides, so only consistency matters, not linguistic correctness.
    # "ss" endings (glass, boss) are left alone; short tokens (bus, gas) are left alone.
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def canonical_product(value: Any) -> str | None:
    """Return None when no product is named, else a comparison key."""
    if value is None or not isinstance(value, str):
        return None
    if value.strip().lower() in NULL_PRODUCT_LITERALS:
        return None
    tokens = _tokens(value)
    if all(t in NULL_PRODUCT_TOKENS for t in tokens):
        return None
    return "".join(sorted(_singular(t) for t in tokens))


def product_score(predicted: Any, expected: Any) -> float:
    pred, gold = canonical_product(predicted), canonical_product(expected)
    if pred is None and gold is None:
        return 1.0
    if pred is None or gold is None:
        return 0.0
    return 1.0 if pred == gold else 0.0


_TOPIC_STOPWORDS = frozenset({"for", "of", "the", "and", "a"})
_TOPIC_GENERIC_PREFIXES = frozenset({"overall", "product"})
_TOPIC_GENERIC_SUFFIXES = frozenset({"quality", "life", "speed"})
# The only two aliases: an abbreviation, and one fixed English idiom whose tokens do not
# reduce structurally. Kept to two on purpose -- every added alias is a place to overfit.
_TOPIC_ALIASES = {"mic": "microphone", "value_for_money": "value"}


def canonical_topic(value: Any) -> str:
    """Structural normalization of a topic label: case, separators, generic affixes.

    "battery_life" -> "battery", "delivery_speed" -> "delivery", "sound_quality" -> "sound",
    "overall_quality" / "product_quality" -> "quality" (a lone "quality" is kept: stripping the
    only token would erase it).
    """
    text = str(value).lower().strip().replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^\w]+", "_", text).strip("_")
    text = _TOPIC_ALIASES.get(text, text)
    parts = [p for p in text.split("_") if p and p not in _TOPIC_STOPWORDS]
    while len(parts) > 1 and parts[0] in _TOPIC_GENERIC_PREFIXES:
        parts = parts[1:]
    while len(parts) > 1 and parts[-1] in _TOPIC_GENERIC_SUFFIXES:
        parts = parts[:-1]
    return "_".join(parts)


def canonical_competitor(value: Any) -> str:
    """Brand names differ only by spacing/case/punctuation: "real me" == "realme"."""
    return re.sub(r"[^\w]+", "", unicodedata.normalize("NFKC", str(value)).lower())
