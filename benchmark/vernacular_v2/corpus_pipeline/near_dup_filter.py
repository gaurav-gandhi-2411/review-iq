"""Near-duplicate removal — second dedup pass, reusing the Hinglish SBERT embedding.

`ingest_and_dedupe.py` already removes EXACT-text duplicates (SHA256 of normalized
text). This module adds a NEAR-duplicate pass on top of that output: reviews that
differ by punctuation, casing, minor spelling variants, or a few inserted/dropped
words ("Bahut accha product" vs "bahut acha product!!") but are the same underlying
review. Left uncaught, near-dupes inflate apparent corpus diversity and risk leaking
the same content across future train/eval splits — the exact failure mode
`benchmark/data/leakage_check.py` already guards against for eval fixtures.

Model: `gauravgandhi2411/hinglish-relatedness-sbert` — the LoRA r=8 fine-tune of
`l3cube-pune/indic-sentence-bert-nli` already shipped by this project (held-out
Spearman 0.435 -> 0.704, CC-BY-4.0, see docs/specs/wave1-commercialization.md). This
is the SAME model the Wave 1 spec names for near-dup removal, not a substitute.

Transport: Hugging Face's hosted Inference API (`router.huggingface.co/hf-inference`)
via `httpx` (already a repo dependency), NOT a local `sentence-transformers`/`torch`
install. This repo has zero torch dependency today; adding
`sentence-transformers` would pull in a multi-hundred-MB torch wheel — a genuine new
production/benchmark dependency decision, out of scope to add unilaterally per the
standing "don't install new dependencies without asking" rule. The HF model card
tags the repo `endpoints_compatible` / `text-embeddings-inference`, i.e. it is
designed to be called this way. `get_embeddings()` is the one function that talks to
the network — everything else here (clustering/threshold logic) is pure Python and
independently unit-testable against a fake/injected embedding function.

Known limitation, disclosed not hidden: at full corpus scale (~245K rows), the O(n^2)
pairwise-similarity clustering below does not scale — it is deliberately built for
the "small documented sample" this task calls for, not a full-corpus run. A real
full-scale near-dup pass would batch embeddings and use an ANN index (e.g. FAISS) for
candidate generation before pairwise cosine confirmation; that is future work, noted
here so the O(n^2) choice isn't silently carried forward at scale.

Usage:
    uv run python -m benchmark.vernacular_v2.corpus_pipeline.near_dup_filter \\
        --input data/processed/flipkart_classified.jsonl \\
        --output data/processed/flipkart_near_dup_filtered.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Protocol

import httpx

ROOT = Path(__file__).resolve().parents[3]

HF_MODEL_ID = "gauravgandhi2411/hinglish-relatedness-sbert"
HF_INFERENCE_URL = f"https://router.huggingface.co/hf-inference/models/{HF_MODEL_ID}"

DEFAULT_SIMILARITY_THRESHOLD = 0.92  # cosine similarity above which two reviews are
# treated as near-duplicates. Conservative (high) on purpose: false-merging two
# genuinely distinct reviews destroys real corpus signal, whereas a missed near-dup
# is caught by chance elsewhere or simply costs a little redundancy — asymmetric risk.


class EmbeddingFn(Protocol):
    """Structural interface for `text -> embedding vector`, injectable for tests."""

    def __call__(self, texts: list[str]) -> list[list[float]]: ...


def get_embeddings_via_hf_api(
    texts: list[str],
    *,
    hf_token: str,
    timeout: float = 60.0,
) -> list[list[float]]:
    """Call the HF-hosted `hinglish-relatedness-sbert` Inference API for embeddings.

    Args:
        texts: Review texts to embed (sentence-similarity feature-extraction task).
        hf_token: HF access token (`HF_TOKEN`/`HUGGINGFACEHUB_API_TOKEN`). Public model,
            but HF's router still requires an authenticated request.
        timeout: HTTP timeout in seconds.

    Returns:
        One embedding vector per input text, same order.

    Raises:
        httpx.HTTPStatusError: on a non-2xx response (e.g. invalid/expired token,
            model cold-start timeout).
        ValueError: if the response shape doesn't match `len(texts)` vectors.
    """
    resp = httpx.post(
        HF_INFERENCE_URL,
        headers={"Authorization": f"Bearer {hf_token}"},
        json={"inputs": texts},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list) or len(data) != len(texts):
        raise ValueError(
            f"Unexpected HF Inference API response shape: expected {len(texts)} vectors, "
            f"got {type(data).__name__} of length {len(data) if isinstance(data, list) else '?'}"
        )
    return data


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Standard cosine similarity between two equal-length vectors.

    Returns 0.0 for a zero-magnitude vector (rather than raising) — a degenerate
    embedding should never be treated as maximally similar to everything.
    """
    if len(a) != len(b):
        raise ValueError(f"Vector length mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class _UnionFind:
    """Minimal union-find (disjoint-set) for grouping near-duplicate indices."""

    def __init__(self, n: int) -> None:
        self._parent = list(range(n))

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra


def cluster_near_duplicates(
    embeddings: list[list[float]],
    *,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> list[list[int]]:
    """Group embedding indices into near-duplicate clusters (pairwise cosine >= threshold).

    O(n^2) pairwise comparison — see module docstring's disclosed scaling limitation.

    Returns:
        List of clusters, each a sorted list of indices into `embeddings`. Every
        index appears in exactly one cluster (singletons included).
    """
    n = len(embeddings)
    uf = _UnionFind(n)
    for i in range(n):
        for j in range(i + 1, n):
            if cosine_similarity(embeddings[i], embeddings[j]) >= threshold:
                uf.union(i, j)
    clusters: dict[int, list[int]] = {}
    for i in range(n):
        root = uf.find(i)
        clusters.setdefault(root, []).append(i)
    return [sorted(members) for members in clusters.values()]


def dedupe_near_duplicates(
    records: list[dict],
    embed_fn: EmbeddingFn,
    *,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> tuple[list[dict], list[dict]]:
    """Remove near-duplicates from `records` (each needs a "text" key), keeping the
    first-seen record per cluster — same "keep first occurrence" convention as
    `ingest_and_dedupe.py`'s exact-hash pass, for consistency.

    Args:
        records: Records to dedupe, each with at least a "text" field.
        embed_fn: `list[str] -> list[list[float]]`. Production callers pass
            `get_embeddings_via_hf_api` (partially applied with a token); tests pass
            a deterministic stub — this function has zero network dependency itself.
        threshold: Cosine similarity cutoff (see `DEFAULT_SIMILARITY_THRESHOLD`).

    Returns:
        (kept, removed) — `removed` records carry a `near_dup_of` field pointing at
        the kept record's `id` (or list index if no `id` field), for provenance.
    """
    if not records:
        return [], []
    embeddings = embed_fn([r["text"] for r in records])
    clusters = cluster_near_duplicates(embeddings, threshold=threshold)

    kept: list[dict] = []
    removed: list[dict] = []
    for cluster in clusters:
        keep_idx = cluster[0]
        kept.append(records[keep_idx])
        keep_ref = records[keep_idx].get("id", keep_idx)
        for idx in cluster[1:]:
            rec = dict(records[idx])
            rec["near_dup_of"] = keep_ref
            removed.append(rec)
    return kept, removed


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=DEFAULT_SIMILARITY_THRESHOLD)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=64,
        help="Records embedded per HF Inference API call.",
    )
    args = parser.parse_args()

    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACEHUB_API_TOKEN")
    if not hf_token:
        print(
            "ERROR: no HF token found (HF_TOKEN or HUGGINGFACEHUB_API_TOKEN).\n"
            "Get one at https://huggingface.co/settings/tokens (public model, but the "
            "hosted Inference API still requires an authenticated request).",
            file=sys.stderr,
        )
        sys.exit(1)

    records = _load_jsonl(args.input)
    print(f"Loaded {len(records)} records from {args.input}")

    def _embed(texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), args.batch_size):
            batch = texts[i : i + args.batch_size]
            out.extend(get_embeddings_via_hf_api(batch, hf_token=hf_token))
            print(f"  embedded {min(i + args.batch_size, len(texts))}/{len(texts)}")
        return out

    kept, removed = dedupe_near_duplicates(records, _embed, threshold=args.threshold)
    print(f"Kept: {len(kept)}  Removed as near-duplicates: {len(removed)}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as fh:
        for rec in kept:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"Written: {args.output}")


if __name__ == "__main__":
    main()
