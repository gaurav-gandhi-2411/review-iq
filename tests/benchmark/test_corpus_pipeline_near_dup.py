from __future__ import annotations

import pytest
from benchmark.vernacular_v2.corpus_pipeline.near_dup_filter import (
    cluster_near_duplicates,
    cosine_similarity,
    dedupe_near_duplicates,
)


def test_cosine_similarity_identical_vectors() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_opposite_vectors() -> None:
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_similarity_zero_vector_returns_zero() -> None:
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_cosine_similarity_mismatched_length_raises() -> None:
    with pytest.raises(ValueError, match="length mismatch"):
        cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0])


def test_cluster_near_duplicates_groups_similar_vectors() -> None:
    embeddings = [
        [1.0, 0.0],
        [0.99, 0.05],  # near-dup of index 0
        [0.0, 1.0],  # distinct
    ]
    clusters = cluster_near_duplicates(embeddings, threshold=0.95)
    cluster_sets = {frozenset(c) for c in clusters}
    assert frozenset({0, 1}) in cluster_sets
    assert frozenset({2}) in cluster_sets


def test_cluster_near_duplicates_no_matches_all_singletons() -> None:
    embeddings = [[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]]
    clusters = cluster_near_duplicates(embeddings, threshold=0.99)
    assert sorted(len(c) for c in clusters) == [1, 1, 1]


def test_cluster_near_duplicates_transitive_grouping() -> None:
    """A~B and B~C (but A/C below threshold directly) still land in one cluster —
    union-find, not naive pairwise partitioning."""
    embeddings = [
        [1.0, 0.0, 0.0],
        [0.9, 0.436, 0.0],  # ~A (cos ~0.9), not quite ~C
        [0.7, 0.714, 0.0],  # ~B, distinct enough from A alone
    ]
    clusters = cluster_near_duplicates(embeddings, threshold=0.85)
    assert len(clusters) == 1
    assert clusters[0] == [0, 1, 2]


def test_dedupe_near_duplicates_keeps_first_occurrence() -> None:
    records = [
        {"id": "a", "text": "bahut accha product hai"},
        {"id": "b", "text": "bahut acha product hai"},  # near-dup of "a"
        {"id": "c", "text": "totally different review content"},
    ]

    def stub_embed(texts: list[str]) -> list[list[float]]:
        # Deterministic stub: "a"/"b" texts -> near-identical vectors, "c" -> orthogonal.
        vectors = []
        for t in texts:
            if "product hai" in t:
                vectors.append([1.0, 0.01])
            else:
                vectors.append([0.0, 1.0])
        return vectors

    kept, removed = dedupe_near_duplicates(records, stub_embed, threshold=0.9)
    assert [r["id"] for r in kept] == ["a", "c"]
    assert len(removed) == 1
    assert removed[0]["id"] == "b"
    assert removed[0]["near_dup_of"] == "a"


def test_dedupe_near_duplicates_empty_input() -> None:
    kept, removed = dedupe_near_duplicates([], lambda texts: [], threshold=0.9)
    assert kept == []
    assert removed == []


def test_dedupe_near_duplicates_no_near_dups_keeps_all() -> None:
    records = [{"id": "a", "text": "x"}, {"id": "b", "text": "y"}]

    def stub_embed(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0], [0.0, 1.0]]

    kept, removed = dedupe_near_duplicates(records, stub_embed, threshold=0.9)
    assert len(kept) == 2
    assert removed == []
