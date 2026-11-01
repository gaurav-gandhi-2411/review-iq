"""Draw the fresh sealed set of spec Amendment 15: ~2,000 English items, test-only, disjoint from every earlier set.

    python -m engine.labelling.fresh_sample --corpus D:/ml-cache/review-corpus --flipkart data/processed/vernacular_subset.jsonl \
        --out D:/ml-cache/s28/fresh15.jsonl --manifest reports/labelling/fresh15/manifest.json \
        --exclude reports/labelling/pilot1/manifest.json,...,reports/labelling/scale/manifest.json,reports/labelling/safety/split.json \
        --ledger reports/labelling/ledger.json \
        --prior-texts D:/ml-cache/review-intent/pilot1.jsonl,...,D:/ml-cache/s23/safety_items.jsonl

Same cleared-corpus loading, cleaning, length limits and dedup as `scale_sample` (pools via `build_pools`). Freshness is enforced
twice: by content hash (every earlier manifest and the ledger) and by NEAR-DUPLICATE (exact Jaccard of 4-character shingle sets
>= 0.9, the `leakage_neardup` rule) against the private text of every earlier item of any split and against already-kept fresh
items. Candidates are oversampled and filtered in draw order, so the result is a pure function of the seed and the exclusions.
The committed manifest holds ids, stratum and content hash only; review text goes to the private file.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from engine.labelling.leakage_neardup import norm, shingles
from engine.labelling.pilot_sample import clean, load_all
from engine.labelling.scale_sample import allocate, build_pools, load_banned, text_sha

TAG = "fresh15"
SEED = 45
TOTAL = 2000
OVERSAMPLE = 1.5
K = 4
JACCARD = 0.9


class NearDupIndex:
    """Inverted index of 4-shingles: `hit(text)` is True iff some indexed item has Jaccard >= 0.9 with it."""

    def __init__(self) -> None:
        self.inv: dict[int, list[int]] = {}
        self.sizes: list[int] = []

    def add(self, text: str) -> None:
        sh = shingles(norm(text), K)
        i = len(self.sizes)
        self.sizes.append(len(sh))
        for s in sh:
            self.inv.setdefault(s, []).append(i)

    def hit(self, text: str) -> bool:
        sh = shingles(norm(text), K)
        if not sh:
            return False
        common: dict[int, int] = {}
        for s in sh:
            for i in self.inv.get(s, ()):
                common[i] = common.get(i, 0) + 1
        return any(c / (len(sh) + self.sizes[i] - c) >= JACCARD for i, c in common.items())


def load_prior_texts(paths: list[str]) -> list[str]:
    out = []
    for p in paths:
        for ln in Path(p).read_text(encoding="utf-8").splitlines():
            if ln.strip():
                out.append(json.loads(ln)["text"])
    return out


def draw_fresh(
    pools: dict[str, list[tuple[str, float, str]]],
    total: int,
    index: NearDupIndex,
    seed: int = SEED,
    tag: str = TAG,
) -> tuple[list[dict], dict]:
    """Per stratum: oversample in seeded order, drop near-duplicates (of prior items and of kept fresh items), keep the first n."""
    rng = random.Random(seed)
    alloc = allocate({s: len(p) for s, p in pools.items()}, total)
    rows: list[dict] = []
    dropped = {}
    for stratum in sorted(pools):
        want = alloc[stratum]
        cand = rng.sample(pools[stratum], min(len(pools[stratum]), int(want * OVERSAMPLE) + 10))
        kept = n_drop = 0
        for text, stars, sha in cand:
            if kept == want:
                break
            if (
                text_sha(clean(text)) != sha
            ):  # cleaning not idempotent: the leakage tools could not reproduce this hash
                n_drop += 1
                continue
            if index.hit(text):
                n_drop += 1
                continue
            index.add(text)
            rows.append({
                "id": f"{tag}-{stratum}-{kept:04d}", "stratum": stratum, "category": stratum,
                "text": text, "stars": stars, "sha": sha, "split": "test",
            })  # fmt: skip
            kept += 1
        dropped[stratum] = {
            "allocated": want,
            "kept": kept,
            "dropped_near_duplicate_or_unreproducible_hash": n_drop,
        }
    return rows, dropped


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--flipkart", default="data/processed/vernacular_subset.jsonl")
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--exclude", default="")
    ap.add_argument("--ledger", default="")
    ap.add_argument("--prior-texts", required=True)
    ap.add_argument("--total", type=int, default=TOTAL)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--tag", default=TAG)
    a = ap.parse_args()
    banned, info = load_banned([m for m in a.exclude.split(",") if m], a.ledger or None)
    pools, stats = build_pools(load_all(Path(a.corpus), Path(a.flipkart)), banned)
    pools = {
        s: p for s, p in pools.items() if p and s != "vernacular"
    }  # vernacular pool is exhausted (Amendment 15)
    index = NearDupIndex()
    prior = load_prior_texts([p for p in a.prior_texts.split(",") if p])
    for t in prior:
        index.add(t)
    rows, dropped = draw_fresh(pools, a.total, index, a.seed, a.tag)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    man = {
        "tag": a.tag, "seed": a.seed, "target": a.total, "spec": "docs/specs/review-intent.md Amendment 15",
        "exclusions": info, "n_prior_texts_for_near_duplicate_scan": len(prior),
        "near_duplicate_rule": f"exact Jaccard of {K}-character shingle sets >= {JACCARD}",
        "pools": stats, "draw": dropped, "counts": {s: sum(r["stratum"] == s for r in rows) for s in sorted(pools)},
        "split": "all test (sealed, never trained on)",
        "items": [{k: r[k] for k in ("id", "stratum", "split", "sha")} for r in rows],
    }  # fmt: skip
    Path(a.manifest).parent.mkdir(parents=True, exist_ok=True)
    Path(a.manifest).write_text(json.dumps(man, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"n": len(rows), "counts": man["counts"], "draw": dropped}, indent=1))


if __name__ == "__main__":
    main()
