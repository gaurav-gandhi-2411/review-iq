"""Rating-distribution anomaly signals for coordinated fake-review campaign detection.

IMPORTANT: low_variance and high_bimodality are SOFT signals, not evidence of fraud on their
own. A product can legitimately have a uniformly excellent (or uniformly polarizing) rating
distribution for entirely honest reasons -- e.g. a genuinely great budget product, or a product
with a real quality-control defect that half of buyers hit. These flags only contribute weight
alongside the text-clustering signal in score.py; they are never treated as strong evidence in
isolation.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from canonicalize import canonical_product

# Density threshold established in the Step 0 recon: 1,398 of 2,466 raw product_name values
# clear >=20 reviews. Below this, rating-distribution statistics are too noisy to be meaningful.
MIN_REVIEWS_FOR_RATING_SIGNAL = 20

LOW_VARIANCE_STD_THRESHOLD = 0.4
HIGH_BIMODALITY_RATIO_THRESHOLD = 0.85


@dataclass
class RatingStats:
    """Rating-distribution statistics for one canonical product."""

    canonical_product: str
    n_reviews: int
    std: float
    bimodality_ratio: float
    low_variance: bool
    high_bimodality: bool


def compute_rating_stats(canonical_key: str, ratings: pd.Series) -> RatingStats:
    """Compute rating-distribution statistics for one canonical product's `rate` values.

    `bimodality_ratio` is the fraction of non-null ratings that are either 1 or 5 (extremes) --
    high values suggest a polarized "love it or hate it" pattern, which is one soft signal a
    coordinated campaign can produce (either all-5s inflation, or a mix of genuine 1s and
    injected 5s), but is equally consistent with honest polarized opinion.
    """
    numeric = pd.to_numeric(ratings, errors="coerce")
    non_null = numeric.dropna()
    n_non_null = len(non_null)
    std = float(non_null.std()) if n_non_null > 1 else 0.0
    extremes = int(((non_null == 1) | (non_null == 5)).sum())
    bimodality_ratio = extremes / n_non_null if n_non_null else 0.0

    return RatingStats(
        canonical_product=canonical_key,
        n_reviews=len(ratings),
        std=round(std, 4),
        bimodality_ratio=round(bimodality_ratio, 4),
        low_variance=std < LOW_VARIANCE_STD_THRESHOLD,
        high_bimodality=bimodality_ratio > HIGH_BIMODALITY_RATIO_THRESHOLD,
    )


def analyze_corpus_ratings(records: Iterable[dict]) -> list[RatingStats]:
    """Group review records by canonical product and compute rating-distribution stats for
    every product clearing MIN_REVIEWS_FOR_RATING_SIGNAL."""
    by_product: dict[str, list[str]] = {}
    for record in records:
        key = canonical_product(record["product_name"])
        by_product.setdefault(key, []).append(record["rate"])

    results: list[RatingStats] = []
    for key, ratings in by_product.items():
        if len(ratings) < MIN_REVIEWS_FOR_RATING_SIGNAL:
            continue
        results.append(compute_rating_stats(key, pd.Series(ratings)))
    return results


def _load_corpus(path: Path) -> list[dict]:
    """Load the deduped corpus JSONL into a list of review record dicts."""
    records: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            records.append(json.loads(line))
    return records


def main() -> None:
    """Run rating-anomaly analysis over the full deduped corpus and print a summary."""
    corpus_path = (
        Path(__file__).resolve().parents[2] / "data" / "processed" / "flipkart_deduped.jsonl"
    )
    records = _load_corpus(corpus_path)
    stats = analyze_corpus_ratings(records)

    n_low_variance = sum(1 for s in stats if s.low_variance)
    n_high_bimodality = sum(1 for s in stats if s.high_bimodality)

    print("rating anomaly report")
    print(f"  products with >={MIN_REVIEWS_FOR_RATING_SIGNAL} reviews: {len(stats)}")
    print(f"  low_variance flagged:    {n_low_variance}")
    print(f"  high_bimodality flagged: {n_high_bimodality}")


if __name__ == "__main__":
    main()
