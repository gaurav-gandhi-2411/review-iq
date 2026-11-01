"""Draw the scale-labelling sample (spec Amendment 5): ~5,000 items, fresh, stratified by language and category.

    python -m engine.labelling.scale_sample --corpus D:/ml-cache/review-corpus --flipkart data/processed/vernacular_subset.jsonl \
        --out D:/ml-cache/review-intent/scale.jsonl --manifest reports/labelling/scale/manifest.json \
        --exclude reports/labelling/pilot1/manifest.json,reports/labelling/pilot2/manifest.json,reports/labelling/pilot3/manifest.json \
        [--ledger reports/labelling/ledger.json]

Same cleared-corpus loading, cleaning, length filter and content-hash exclusion as `pilot_sample`; the pool of each
stratum is minus every hash in the earlier manifests and the leakage ledger. Hinglish (vernacular) is capped by its
pool: it takes what is left (up to an equal share) and the three English strata split the rest equally.
The split is a pure function of the content hash (Amendment 2: sealed test 15 percent), so it needs no RNG and a
re-draw cannot move an item across the sealed boundary. The committed manifest holds ids, stratum, split and content
hash only; the review text goes to the private text file, which stays out of the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

from engine.labelling.pilot_sample import LIMITS, SOURCES, clean, load_all

TAG = "scale"
SEED = 44
TARGET = 5000
TEST_SHARE = 0.15  # sealed test, Amendment 2
VAL_SHARE_OF_REST = 0.10  # validation share of the non-test items (fixed here: 8.5 percent of all)
SPLIT_SALT = "review-intent-scale-split-v1"


def split_of(sha: str) -> str:
    """Deterministic split from the content hash: test < 0.15, val the next 8.5 percent, else train."""
    h = hashlib.sha256(f"{SPLIT_SALT}:{sha}".encode()).hexdigest()
    u = int(h[:12], 16) / 16**12
    if u < TEST_SHARE:
        return "test"
    if u < TEST_SHARE + (1 - TEST_SHARE) * VAL_SHARE_OF_REST:
        return "val"
    return "train"


def text_sha(t: str) -> str:
    return hashlib.sha256(t.lower().encode()).hexdigest()[:16]


def allocate(pool_sizes: dict[str, int], total: int) -> dict[str, int]:
    """Smallest pool first takes min(pool, equal share of what is left); the rest split the remainder."""
    out = {s: 0 for s in pool_sizes}
    left = total
    order = sorted(pool_sizes, key=lambda s: pool_sizes[s])
    for k, s in enumerate(order):
        share = left // (len(order) - k)
        out[s] = min(pool_sizes[s], share)
        left -= out[s]
    return out


def load_banned(manifests: list[str], ledger: str | None) -> tuple[set[str], dict]:
    """Content hashes to avoid: every earlier manifest and the ledger. A named file that is missing raises."""
    banned: set[str] = set()
    info: dict = {"manifests": {}, "ledger": None}
    for m in manifests:
        shas = {r["sha"] for r in json.loads(Path(m).read_text(encoding="utf-8"))["items"]}
        info["manifests"][m] = len(shas)
        banned |= shas
    if ledger:
        data = json.loads(Path(ledger).read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else data.get("items", data.get("hashes", []))
        shas = {r if isinstance(r, str) else r.get("sha", r.get("hash")) for r in rows}
        if None in shas:
            raise ValueError("ledger row without a sha/hash")
        info["ledger"] = {"path": ledger, "hashes": len(shas)}
        banned |= shas
    return banned, info


def build_pools(
    frames: dict, banned: set[str]
) -> tuple[dict[str, list[tuple[str, float, str]]], dict[str, dict]]:
    """Per stratum: clean, length filter, dedup (also across strata), drop banned. -> pools, stats."""
    pools: dict[str, list[tuple[str, float, str]]] = {}
    seen_global: set[str] = set()
    stats: dict[str, dict] = {}
    for stratum, df in frames.items():
        lo, hi = LIMITS[stratum]
        seen: set[str] = set()
        pool = []
        n_banned = 0
        for text, stars in zip(df["text"], df["stars"], strict=True):
            if not isinstance(text, str):
                continue
            t = clean(text)
            sha = text_sha(t)
            if not lo <= len(t) <= hi or sha in seen or sha in seen_global:
                continue
            seen.add(sha)
            if sha in banned:
                n_banned += 1
                continue
            pool.append((t, float(stars), sha))
        seen_global |= seen
        pools[stratum] = pool
        stats[stratum] = {"pool_after_exclusions": len(pool), "excluded_as_seen_before": n_banned}
    return pools, stats


def draw(
    pools: dict[str, list[tuple[str, float, str]]], total: int, seed: int = SEED
) -> list[dict]:
    """pools[stratum] = [(text, stars, sha)] -> item rows (with text) in a fixed order."""
    rng = random.Random(seed)
    alloc = allocate({s: len(p) for s, p in pools.items()}, total)
    rows = []
    for stratum in sorted(pools):
        for n, (t, stars, sha) in enumerate(rng.sample(pools[stratum], alloc[stratum])):
            rows.append({
                "id": f"{TAG}-{stratum}-{n:04d}", "stratum": stratum, "category": stratum,
                "text": t, "stars": stars, "sha": sha, "split": split_of(sha),
            })  # fmt: skip
    return rows


def manifest_of(rows: list[dict], banned_info: dict, stats: dict, total: int) -> dict:
    """The committed manifest: ids, stratum, split, content hash. No text, no stars."""
    counts: dict = {}
    for r in rows:
        c = counts.setdefault(r["stratum"], {"train": 0, "val": 0, "test": 0})
        c[r["split"]] += 1
    return {
        "tag": TAG, "seed": SEED, "target": total, "sources": SOURCES,
        "split_rule": {"salt": SPLIT_SALT, "test": TEST_SHARE, "val_share_of_rest": VAL_SHARE_OF_REST,
                       "note": "hash of the content hash; the corpora carry no product/reviewer ids here"},
        "exclusions": banned_info, "pools": stats, "counts": counts,
        "items": [{k: r[k] for k in ("id", "stratum", "split", "sha")} for r in rows],
    }  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--flipkart", default="data/processed/vernacular_subset.jsonl")
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--exclude", default="", help="comma list of earlier manifest.json files")
    ap.add_argument("--ledger", default="", help="leakage ledger json; omitted = recorded as null")
    ap.add_argument("--total", type=int, default=TARGET)
    a = ap.parse_args()

    banned, info = load_banned([m for m in a.exclude.split(",") if m], a.ledger or None)
    pools, stats = build_pools(load_all(Path(a.corpus), Path(a.flipkart)), banned)
    rows = draw(pools, a.total)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    man = manifest_of(rows, info, stats, a.total)
    Path(a.manifest).parent.mkdir(parents=True, exist_ok=True)
    Path(a.manifest).write_text(json.dumps(man, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"n": len(rows), "counts": man["counts"], "pools": stats}, indent=1))


if __name__ == "__main__":
    main()
