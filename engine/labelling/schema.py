"""Review-intent taxonomy constants. The source of truth is docs/specs/review-intent.md (pre-registered)."""

from __future__ import annotations

INTENTS = (
    "product_defect",
    "delivery",
    "customer_service",
    "pricing",
    "praise",
    "question",
    "return_refund_request",
    "suggestion",
    "competitor_comparison",
    "spam_irrelevant",
)
SENTIMENTS = ("positive", "negative", "neutral", "mixed")
URGENCY = ("low", "medium", "high")  # ordinal
BUY_AGAIN = ("yes", "no", "unclear")
YES_NO = ("yes", "no")
ASPECT_SENTIMENTS = ("positive", "negative", "neutral")

ASPECTS: dict[str, tuple[str, ...]] = {
    "beauty": (
        "texture", "fragrance", "packaging", "results_efficacy",
        "skin_reaction", "shade_match", "value", "longevity",
    ),
    "apparel": (
        "fit_size", "fabric_quality", "colour_accuracy", "stitching_durability",
        "comfort", "style", "value", "length",
    ),
    "food": (
        "taste", "freshness_expiry", "packaging_seal", "quantity",
        "ingredient_quality", "efficacy_claims", "value", "safety_allergen",
    ),
    "vernacular": (),  # marketplace products outside the three categories: T1-T5 only
}  # fmt: skip

GROUPS = {  # reporting only, never trained on
    "ACTION": (
        "product_defect",
        "delivery",
        "customer_service",
        "return_refund_request",
        "question",
    ),
    "INSIGHT": ("pricing", "suggestion", "competitor_comparison"),
    "SIGNAL": ("praise",),
    "NOISE": ("spam_irrelevant",),
}
