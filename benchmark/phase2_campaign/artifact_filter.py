from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from canonicalize import canonical_product

# Same aggressive normalization used in the prior signal-verification recon: strip punctuation,
# lowercase, collapse whitespace. Deliberately looser than canonical_product() -- this operates
# on review body text, not product names.
_PUNCT = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")

# The empirically-observed cross-source-overlap shape from the prior signal-verification stage:
# roughly <=2 rows contributed per overlapping source file, consistent with "the same underlying
# review re-published by each scrape" rather than independent posters.
_MIN_CLUSTER_SIZE = 3
_ARTIFACT_ROWS_PER_SOURCE = 2


def normalize_cluster_text(text: str) -> str:
    """Aggressively normalize review text for near-duplicate clustering: strip punctuation,
    lowercase, collapse whitespace."""
    stripped = _PUNCT.sub("", text)
    collapsed = _WHITESPACE.sub(" ", stripped)
    return collapsed.strip().lower()


@dataclass
class ReviewCluster:
    """One near-duplicate text cluster within a single canonical product."""

    text: str
    count: int
    sources: list[str]
    ratings: list[str]
    n_sources: int
    is_artifact_explainable: bool


@dataclass
class ProductArtifactBreakdown:
    """Per-canonical-product near-duplicate clustering result: residual clusters are the only
    ones eligible for campaign scoring; excluded artifact clusters are kept and reported for
    transparency, never silently dropped, never scored."""

    canonical_product: str
    n_reviews: int
    residual_clusters: list[ReviewCluster] = field(default_factory=list)
    excluded_artifact_clusters: list[ReviewCluster] = field(default_factory=list)
    n_residual_flagged_reviews: int = 0
    n_artifact_excluded_reviews: int = 0


def analyze_product(canonical_key: str, reviews: list[dict]) -> ProductArtifactBreakdown:
    """Cluster one canonical product's reviews by normalized text and split clusters into
    residual (campaign-scoring eligible) vs excluded-artifact (cross-source scrape overlap).

    A cluster is artifact-explainable when it spans >=2 source files AND its size is <= 2x the
    number of distinct sources it spans -- the shape of "the same review re-published by each
    overlapping scrape", not independent posters submitting near-identical text.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for review in reviews:
        groups[normalize_cluster_text(review["text"])].append(review)

    residual_clusters: list[ReviewCluster] = []
    excluded_artifact_clusters: list[ReviewCluster] = []
    for group in groups.values():
        cluster_size = len(group)
        if cluster_size < _MIN_CLUSTER_SIZE:
            continue

        sources = [review["source"] for review in group]
        n_sources = len(set(sources))
        is_artifact_explainable = (
            n_sources >= 2 and cluster_size <= n_sources * _ARTIFACT_ROWS_PER_SOURCE
        )
        cluster = ReviewCluster(
            text=group[0]["text"],
            count=cluster_size,
            sources=sorted(set(sources)),
            ratings=sorted(set(review["rate"] for review in group)),
            n_sources=n_sources,
            is_artifact_explainable=is_artifact_explainable,
        )
        if is_artifact_explainable:
            excluded_artifact_clusters.append(cluster)
        else:
            residual_clusters.append(cluster)

    return ProductArtifactBreakdown(
        canonical_product=canonical_key,
        n_reviews=len(reviews),
        residual_clusters=residual_clusters,
        excluded_artifact_clusters=excluded_artifact_clusters,
        n_residual_flagged_reviews=sum(c.count for c in residual_clusters),
        n_artifact_excluded_reviews=sum(c.count for c in excluded_artifact_clusters),
    )


def analyze_corpus(records: Iterable[dict]) -> list[ProductArtifactBreakdown]:
    """Group review records by canonical product and analyze each product's near-duplicate
    clusters."""
    by_product: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        by_product[canonical_product(record["product_name"])].append(record)
    return [analyze_product(key, reviews) for key, reviews in by_product.items()]


def _load_corpus(path: Path) -> list[dict]:
    """Load the deduped corpus JSONL into a list of review record dicts."""
    records: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            records.append(json.loads(line))
    return records


def main() -> None:
    """Run the artifact filter over the full deduped corpus and report the residual-vs-excluded
    sanity check: if excluded is near-zero, the filter is not actually catching the known
    cross-source scrape overlap and needs debugging."""
    corpus_path = (
        Path(__file__).resolve().parents[2] / "data" / "processed" / "flipkart_deduped.jsonl"
    )
    records = _load_corpus(corpus_path)
    breakdowns = analyze_corpus(records)

    total_residual = sum(b.n_residual_flagged_reviews for b in breakdowns)
    total_excluded = sum(b.n_artifact_excluded_reviews for b in breakdowns)
    n_products_with_residual = sum(1 for b in breakdowns if b.residual_clusters)
    n_products_with_excluded = sum(1 for b in breakdowns if b.excluded_artifact_clusters)

    print("artifact filter sanity report")
    print(f"  total reviews:                        {len(records)}")
    print(f"  canonical products:                    {len(breakdowns)}")
    print(f"  total residual-cluster reviews:        {total_residual}")
    print(f"  total excluded-artifact-cluster reviews: {total_excluded}")
    print(f"  products with >=1 residual cluster:    {n_products_with_residual}")
    print(f"  products with >=1 excluded cluster:    {n_products_with_excluded}")


if __name__ == "__main__":
    main()
