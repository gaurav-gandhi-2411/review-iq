"""Coordinated fake-review campaign confidence score.

This is a MODERATION-PRIORITIZATION SIGNAL, not a fraud/fake verdict -- it mirrors review-iq's
existing authenticity feature framing: a confidence score plus concrete evidence surfaced for a
human moderator to review, never an automated fake/not-fake label applied to a product or a
review. A high score means "a human should look at this product's reviews soon", nothing more.

Pipeline: canonicalize.py (merge encoding-variant product names) -> artifact_filter.py (cluster
near-duplicate review text per product, separating cross-source scrape artifacts from residual
near-duplicates) -> rating_anomaly.py (soft rating-distribution signals) -> this module combines
both into one score per canonical product.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pandas as pd
from artifact_filter import (
    ProductArtifactBreakdown,
    ReviewCluster,
    analyze_product,
    normalize_cluster_text,
)
from canonicalize import canonical_product
from rating_anomaly import MIN_REVIEWS_FOR_RATING_SIGNAL, RatingStats, compute_rating_stats

# Same >=20-review density threshold rating_anomaly.py uses -- below this, both the rating
# distribution and the near-duplicate-cluster ratio are too noisy to score meaningfully.
MIN_REVIEWS_FOR_SCORING = MIN_REVIEWS_FOR_RATING_SIGNAL

# Deliberately low reporting bar: this surfaces candidates for human review, not an auto-action
# threshold.
CONFIDENCE_REPORT_THRESHOLD = 0.15
MAX_EXCLUDED_CLUSTERS_IN_EVIDENCE = 5

TEXT_SIGNAL_WEIGHT = 0.7
RATING_SIGNAL_WEIGHT = 0.3
LOW_VARIANCE_WEIGHT = 0.6
HIGH_BIMODALITY_WEIGHT = 0.4

# Text signal is a BASE-RATE-ADJUSTED enrichment ratio, not a raw cluster size. Two flawed
# formulas were tried and rejected during validation before this one:
#   1. cluster_size / product_review_count -- buried real evidence on popular products (a
#      genuine 11x cluster on a 1,279-review product scored ~0.01) behind pure rating-variance
#      noise on low-volume products (the top 10 were ALL zero-text-evidence rating-only flags,
#      including a best-selling anatomy textbook -- naturally uniform ratings from students who
#      need the book, not campaign evidence).
#   2. raw cluster_size alone (saturating at a fixed count like 8) -- overcorrected the other
#      way: products with thousands of reviews organically accumulate generic short phrases
#      ("nice product", "good") past any fixed count purely by chance, with zero campaign signal,
#      simply because they have more reviews and therefore more chances for two customers to
#      independently write the same common short reaction.
# The fix: compare each cluster's size on THIS product against the phrase's GLOBAL rate across
# the whole corpus, scaled to this product's review volume -- "how much more often does this
# exact phrasing appear here than corpus-wide base rates predict, given how many reviews this
# product has". A common phrase like "nice product" has a high global rate, so a large cluster of
# it is often still unsurprising (low enrichment); a distinctive phrasing that's rare corpus-wide
# but concentrated on one product is genuinely anomalous (high enrichment) regardless of raw count.
TEXT_ENRICHMENT_SATURATION_RATIO = 5.0  # 5x-or-more the expected rate maxes the text signal
TEXT_ENRICHMENT_MIN_EXPECTED = 0.3  # floor on the expected count, avoids blowup on rare phrases

# Rating-distribution anomaly with ZERO corroborating near-duplicate text is the weakest, most
# ambiguous case (see rating_anomaly.py's docstring) -- validation confirmed this standalone
# shape is dominated by false positives (popular books/furniture that are just genuinely loved or
# genuinely disliked). Halve its contribution when no residual text cluster exists at all; full
# weight when it corroborates real text evidence.
NO_TEXT_EVIDENCE_RATING_DISCOUNT = 0.5


def _cluster_to_evidence(cluster: ReviewCluster, enrichment: float | None = None) -> dict[str, Any]:
    """Convert a ReviewCluster into the flat evidence dict shape used in output records.
    `enrichment` (residual clusters only) shows the human reviewer WHY this cluster scored, not
    just its raw count -- a large cluster with low enrichment is base-rate noise, not evidence."""
    evidence: dict[str, Any] = {
        "text": cluster.text,
        "count": cluster.count,
        "sources": cluster.sources,
        "ratings": cluster.ratings,
    }
    if enrichment is not None:
        evidence["enrichment_vs_corpus_baseline"] = round(enrichment, 2)
    return evidence


def _cluster_enrichment(
    cluster: ReviewCluster, n_reviews: int, global_rates: dict[str, float]
) -> float:
    """How many times more often this cluster's phrase appears on this product than the
    corpus-wide base rate predicts, given this product's review volume. >1 = enriched here;
    <=1 = fully explained by the phrase just being common everywhere."""
    global_rate = global_rates.get(normalize_cluster_text(cluster.text), 0.0)
    expected = max(global_rate * n_reviews, TEXT_ENRICHMENT_MIN_EXPECTED)
    return cluster.count / expected


def score_product(
    canonical_key: str,
    raw_variants: list[str],
    breakdown: ProductArtifactBreakdown,
    rating_stats: RatingStats,
    global_rates: dict[str, float],
) -> dict[str, Any] | None:
    """Compute a moderation-prioritization confidence score for one canonical product.

    Returns None if the product does not clear the reporting bar: confidence must be > 0.15,
    OR the product must have at least one residual (non-artifact) near-duplicate cluster, so a
    genuinely-templated small cluster isn't hidden by the density/threshold math even when the
    resulting ratio is low.
    """
    n_reviews = breakdown.n_reviews
    max_enrichment = max(
        (_cluster_enrichment(c, n_reviews, global_rates) for c in breakdown.residual_clusters),
        default=0.0,
    )
    text_signal = min(
        1.0, max(0.0, max_enrichment - 1.0) / (TEXT_ENRICHMENT_SATURATION_RATIO - 1.0)
    )
    rating_signal = (LOW_VARIANCE_WEIGHT if rating_stats.low_variance else 0.0) + (
        HIGH_BIMODALITY_WEIGHT if rating_stats.high_bimodality else 0.0
    )
    rating_contribution = rating_signal * (
        1.0 if text_signal > 0 else NO_TEXT_EVIDENCE_RATING_DISCOUNT
    )
    confidence = round(
        TEXT_SIGNAL_WEIGHT * text_signal + RATING_SIGNAL_WEIGHT * rating_contribution, 3
    )

    has_residual_cluster = len(breakdown.residual_clusters) > 0
    if confidence <= CONFIDENCE_REPORT_THRESHOLD and not has_residual_cluster:
        return None

    residual_sorted = sorted(
        breakdown.residual_clusters,
        key=lambda c: _cluster_enrichment(c, n_reviews, global_rates),
        reverse=True,
    )
    excluded_sorted = sorted(
        breakdown.excluded_artifact_clusters, key=lambda c: c.count, reverse=True
    )
    excluded_capped = excluded_sorted[:MAX_EXCLUDED_CLUSTERS_IN_EVIDENCE]

    evidence: dict[str, Any] = {
        "residual_clusters": [
            _cluster_to_evidence(c, _cluster_enrichment(c, n_reviews, global_rates))
            for c in residual_sorted
        ],
        "excluded_artifact_clusters": [_cluster_to_evidence(c) for c in excluded_capped],
        "excluded_artifact_clusters_total_count": len(excluded_sorted),
        "rating_stats": {
            "std": rating_stats.std,
            "bimodality_ratio": rating_stats.bimodality_ratio,
            "low_variance": rating_stats.low_variance,
            "high_bimodality": rating_stats.high_bimodality,
        },
    }

    return {
        "canonical_product": canonical_key,
        "raw_product_name_variants": raw_variants,
        "n_reviews": n_reviews,
        "confidence": confidence,
        "text_signal": round(text_signal, 3),
        "rating_signal": round(rating_signal, 3),
        # Unsaturated, for ranking only: text_signal caps at 1.0 past 5x enrichment, which ties
        # many products at the same confidence -- this breaks those ties by raw evidence strength
        # (26x baseline is more interesting than 6x, even though both saturate the score).
        "max_enrichment": round(max_enrichment, 2),
        "evidence": evidence,
    }


def _group_records(records: Iterable[dict]) -> dict[str, list[dict]]:
    """Group review records by canonical product key."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        groups[canonical_product(record["product_name"])].append(record)
    return groups


def compute_global_text_rates(records: list[dict]) -> dict[str, float]:
    """Corpus-wide rate of each normalized review text -- the base rate the per-product
    enrichment check compares against. Computed once over the whole corpus, independent of
    product grouping (a phrase's global commonness has nothing to do with any one product)."""
    counts: dict[str, int] = defaultdict(int)
    for record in records:
        counts[normalize_cluster_text(record["text"])] += 1
    total = len(records)
    return {text: count / total for text, count in counts.items()}


def build_flagged_records(records: list[dict]) -> tuple[list[dict[str, Any]], int]:
    """Run canonicalize -> artifact_filter -> rating_anomaly -> score over the full corpus.

    Returns (flagged records sorted by confidence descending, total canonical products
    considered -- i.e. clearing MIN_REVIEWS_FOR_SCORING, before the reporting-bar filter).
    """
    global_rates = compute_global_text_rates(records)
    groups = _group_records(records)
    flagged: list[dict[str, Any]] = []
    n_considered = 0
    for key, group_records in groups.items():
        if len(group_records) < MIN_REVIEWS_FOR_SCORING:
            continue
        n_considered += 1

        raw_variants = sorted({r["product_name"] for r in group_records})
        breakdown = analyze_product(key, group_records)
        rating_stats = compute_rating_stats(key, pd.Series([r["rate"] for r in group_records]))
        record = score_product(key, raw_variants, breakdown, rating_stats, global_rates)
        if record is not None:
            flagged.append(record)

    flagged.sort(key=lambda r: (r["confidence"], r["max_enrichment"]), reverse=True)
    return flagged, n_considered


def _format_examples(clusters: list[dict[str, Any]], limit: int = 2) -> str:
    """Render up to `limit` example clusters as markdown bullet lines for the report."""
    lines: list[str] = []
    for cluster in clusters[:limit]:
        text_preview = cluster["text"][:160]
        enrichment_note = (
            f", enrichment={cluster['enrichment_vs_corpus_baseline']}x baseline"
            if "enrichment_vs_corpus_baseline" in cluster
            else ""
        )
        lines.append(
            f"    - ({cluster['count']}x, sources={cluster['sources']}, "
            f"ratings={cluster['ratings']}{enrichment_note}) {text_preview!r}"
        )
    return "\n".join(lines) if lines else "    (none)"


def write_report(flagged: list[dict[str, Any]], path: Path, top_n: int = 25) -> None:
    """Write a human-readable markdown report of the top-N flagged products."""
    lines = [
        "# Phase 2 Campaign Detector -- Top Flagged Products",
        "",
        "Moderation-prioritization signal only -- confidence + evidence for human review, "
        "never a fraud/fake verdict.",
        "",
        f"Total flagged products (reporting bar cleared): {len(flagged)}",
        "",
    ]
    for i, record in enumerate(flagged[:top_n], start=1):
        lines.append(f"## {i}. confidence={record['confidence']} -- {record['canonical_product']}")
        lines.append("")
        lines.append(f"- n_reviews: {record['n_reviews']}")
        lines.append(
            f"- text_signal: {record['text_signal']}, rating_signal: {record['rating_signal']}"
        )
        lines.append(f"- rating_stats: {record['evidence']['rating_stats']}")
        lines.append(f"- raw product_name variants: {record['raw_product_name_variants']}")
        lines.append("- example residual clusters (campaign-scoring eligible):")
        lines.append(_format_examples(record["evidence"]["residual_clusters"]))
        lines.append("- example excluded-artifact clusters (filtered out, shown for transparency):")
        lines.append(_format_examples(record["evidence"]["excluded_artifact_clusters"]))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _load_corpus(path: Path) -> list[dict]:
    """Load the deduped corpus JSONL into a list of review record dicts."""
    records: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            records.append(json.loads(line))
    return records


def main() -> None:
    """Run the full pipeline over the deduped corpus and write flagged_products.jsonl and
    TOP_FLAGGED_REPORT.md."""
    corpus_path = (
        Path(__file__).resolve().parents[2] / "data" / "processed" / "flipkart_deduped.jsonl"
    )
    records = _load_corpus(corpus_path)
    flagged, n_considered = build_flagged_records(records)

    out_dir = Path(__file__).resolve().parent
    jsonl_path = out_dir / "flagged_products.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for record in flagged:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    report_path = out_dir / "TOP_FLAGGED_REPORT.md"
    write_report(flagged, report_path)

    confidences = [r["confidence"] for r in flagged]
    n_above_bar = sum(1 for c in confidences if c > CONFIDENCE_REPORT_THRESHOLD)
    buckets = defaultdict(int)
    for c in confidences:
        bucket = min(int(c * 10) / 10, 0.9)
        buckets[bucket] += 1

    print("score report")
    print(f"  canonical products considered (>= {MIN_REVIEWS_FOR_SCORING} reviews): {n_considered}")
    print(f"  products in output (reporting bar or residual cluster): {len(flagged)}")
    print(f"  of which confidence > {CONFIDENCE_REPORT_THRESHOLD}: {n_above_bar}")
    if confidences:
        print(
            f"  confidence min/median/max: {min(confidences)} / "
            f"{statistics.median(confidences)} / {max(confidences)}"
        )
        print("  histogram (0.1-wide buckets, lower bound inclusive):")
        for bucket in sorted(buckets):
            print(f"    [{bucket:.1f}, {bucket + 0.1:.1f}): {buckets[bucket]}")
    print(f"  wrote {jsonl_path}")
    print(f"  wrote {report_path}")


if __name__ == "__main__":
    main()
