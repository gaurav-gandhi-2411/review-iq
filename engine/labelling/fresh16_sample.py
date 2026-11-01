"""Draw the Amendment 16 data: the fresh SEALED set `fresh16` (Blocks A, B, C) and the non-sealed TRAINING additions (T1, T2).

    PYTHONPATH=. python -m engine.labelling.fresh16_sample --corpus D:/ml-cache/review-corpus \
        --tokenizer D:/ml-cache/s25/classify_artifacts_onnx/hf/tokenizer.json \
        --prior-texts D:/ml-cache/review-intent/pilot1.jsonl,...,D:/ml-cache/s23/safety_items.jsonl,D:/ml-cache/s28/fresh15.jsonl \
        --out-dir D:/ml-cache/s30/fresh16 --manifest-dir reports/labelling

Everything is registered in docs/specs/review-intent.md Amendment 16 BEFORE the first draw: this module refuses to run unless that text exists in the
repository history, and writes the commit that introduced it into both manifests. Strata are defined on OBSERVABLE features only (category, star rating,
token length under the served tokenizer); the problem lexicon selects TRAINING items only. Freshness is enforced like Amendment 15: by content hash (the
ledger and every earlier manifest) and by near-duplicate (`fresh_sample.NearDupIndex`, exact Jaccard of 4-character shingles >= 0.9) against every earlier
item of any split and every already-kept item. The result is a pure function of the seeds and the exclusions. The committed manifests hold ids, block,
category, stars, token length and content hash only; review text goes to the private files.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
from pathlib import Path

from engine.labelling.fresh_sample import NearDupIndex, load_prior_texts
from engine.labelling.pilot_sample import clean, load_all
from engine.labelling.scale_sample import build_pools, load_banned, text_sha

SEED_SEALED = 46
SEED_TRAIN = 47
SEED_TOPUP = 48
OVERSAMPLE = 1.6
LONG_TOKENS = 128
CATEGORIES = ("apparel", "beauty", "food")
# Amendment 16: sealed blocks (items per category, in CATEGORIES order) and training additions.
BLOCK_A = {"apparel": 600, "beauty": 600, "food": 600}
BLOCK_B = {"apparel": 567, "beauty": 567, "food": 566}
BLOCK_C = {"apparel": 134, "beauty": 133, "food": 133}
T1 = {"apparel": 400, "beauty": 400, "food": 400}
T2 = {"apparel": 400, "beauty": 400, "food": 400}
VAL_TARGETED_SHARE = 5  # one item in five (by content hash) goes to T-val
LEXICON = re.compile(
    r"return|returned|exchange|refund|defect|broken|missing|fell apart|came apart|unravel|snag|torn|ripped|"
    r"too (small|big|tight|loose|short|long)|runs? (small|big)|doesn.t fit|didn.t fit|wrong size|size (up|down)|"
    r"damaged|leak|stain|rash|allerg|expired|stale|stopped working|not as described|disappoint",
    re.IGNORECASE,
)
EXCLUDE = [
    "reports/labelling/pilot1/manifest.json",
    "reports/labelling/pilot2/manifest.json",
    "reports/labelling/pilot3/manifest.json",
    "reports/labelling/scale/manifest.json",
    "reports/labelling/safety/split.json",
    "reports/labelling/fresh15/manifest.json",
]


def registered_commit(repo: Path) -> str:
    """The commit that introduced the Amendment 16 text. Refuses to run if it is not in the history (no draw before the registration)."""
    out = subprocess.run(
        ["git", "-C", str(repo), "log", "-S### Amendment 16", "--format=%H", "--", "docs/specs/review-intent.md"],
        capture_output=True, text=True, check=True,
    ).stdout.split()  # fmt: skip
    if not out:
        raise SystemExit(
            "Amendment 16 is not in docs/specs/review-intent.md history: register it before drawing"
        )
    return out[-1]


def _draw(
    rng: random.Random,
    items: list[dict],
    want: int,
    taken: set[str],
    index: NearDupIndex,
    label: str,
) -> list[dict]:
    """First `want` candidates (seeded order, oversampled) that are not taken, hash-reproducible and not near-duplicates."""
    elig = [it for it in items if it["sha"] not in taken]
    cand = rng.sample(elig, min(len(elig), int(want * OVERSAMPLE) + 10))
    kept: list[dict] = []
    for it in cand:
        if len(kept) == want:
            break
        if text_sha(clean(it["text"])) != it["sha"] or index.hit(it["text"]):
            continue
        index.add(it["text"])
        taken.add(it["sha"])
        kept.append(it)
    if len(kept) < want:
        raise SystemExit(
            f"{label}: only {len(kept)} of {want} drawable (pool too small after exclusions)"
        )
    return kept


def draw_all(pools: dict[str, list[dict]], index: NearDupIndex) -> tuple[list[dict], list[dict]]:
    """pools[category] = [{text, stars, sha, ntok}] -> (sealed rows, training rows), ids and splits assigned."""
    taken: set[str] = set()
    rng = random.Random(SEED_SEALED)
    sealed: list[dict] = []
    for block, quota, pick in (
        ("A", BLOCK_A, lambda it: True),
        ("B", BLOCK_B, lambda it: it["stars"] >= 4),
        ("C", BLOCK_C, lambda it: it["ntok"] > LONG_TOKENS),
    ):
        for cat in CATEGORIES:
            got = _draw(
                rng,
                [it for it in pools[cat] if pick(it)],
                quota[cat],
                taken,
                index,
                f"fresh16 block {block} {cat}",
            )
            for k, it in enumerate(got):
                sealed.append(
                    {
                        **it,
                        "id": f"fresh16-{block}-{cat}-{k:04d}",
                        "block": block,
                        "category": cat,
                        "split": "test",
                    }
                )
    rng = random.Random(SEED_TRAIN)
    train: list[dict] = []
    for tag, quota, pick in (
        ("T1", T1, lambda it: it["stars"] >= 4 and LEXICON.search(it["text"]) is not None),
        ("T2", T2, lambda it: it["ntok"] > LONG_TOKENS),
    ):
        for cat in CATEGORIES:
            got = _draw(
                rng,
                [it for it in pools[cat] if pick(it)],
                quota[cat],
                taken,
                index,
                f"train16 {tag} {cat}",
            )
            for k, it in enumerate(got):
                split = "val16" if int(it["sha"][:8], 16) % VAL_TARGETED_SHARE == 0 else "train16"
                train.append(
                    {
                        **it,
                        "id": f"train16-{tag}-{cat}-{k:04d}",
                        "block": tag,
                        "category": cat,
                        "split": split,
                    }
                )
    return sealed, train


def manifest(rows: list[dict], name: str, commit: str, info: dict, n_prior: int) -> dict:
    return {
        "name": name,
        "registered_in_commit": commit,
        "spec": "docs/specs/review-intent.md Amendment 16",
        "seeds": {"sealed": SEED_SEALED, "train": SEED_TRAIN, "top_up": SEED_TOPUP},
        "exclusions": info,
        "n_prior_texts_for_near_duplicate_scan": n_prior,
        "near_duplicate_rule": "exact Jaccard of 4-character shingle sets >= 0.9",
        "counts": {
            f"{b}/{c}": sum(r["block"] == b and r["category"] == c for r in rows)
            for b in sorted({r["block"] for r in rows})
            for c in CATEGORIES
        },
        "items": [
            {k: r[k] for k in ("id", "block", "category", "split", "stars", "ntok", "sha")}
            for r in rows
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--flipkart", default="data/processed/vernacular_subset.jsonl")
    ap.add_argument("--tokenizer", required=True, type=Path)
    ap.add_argument("--ledger", default="reports/labelling/ledger.json")
    ap.add_argument("--prior-texts", required=True)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--manifest-dir", default="reports/labelling", type=Path)
    a = ap.parse_args()
    repo = Path(__file__).resolve().parents[2]
    commit = registered_commit(repo)
    from tokenizers import Tokenizer

    banned, info = load_banned(EXCLUDE, a.ledger)
    raw_pools, _ = build_pools(load_all(Path(a.corpus), Path(a.flipkart)), banned)
    tok = Tokenizer.from_file(str(a.tokenizer))
    pools: dict[str, list[dict]] = {}
    for cat in CATEGORIES:
        rows = raw_pools[cat]
        ntok: list[int] = []
        for k in range(0, len(rows), 2000):
            ntok += [len(e.ids) for e in tok.encode_batch([t for t, _, _ in rows[k : k + 2000]])]
        pools[cat] = [
            {"text": t, "stars": s, "sha": h, "ntok": n}
            for (t, s, h), n in zip(rows, ntok, strict=True)
        ]
    index = NearDupIndex()
    prior = load_prior_texts([p for p in a.prior_texts.split(",") if p])
    for t in prior:
        index.add(t)
    sealed, train = draw_all(pools, index)
    a.out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in (("fresh16", sealed), ("train16", train)):
        (a.out_dir / f"{name}.jsonl").write_text(
            "".join(json.dumps({k: r[k] for k in ("id", "text", "stars", "category", "block", "split", "ntok", "sha")}, ensure_ascii=False) + "\n" for r in rows),
            encoding="utf-8",
        )  # fmt: skip
        mdir = a.manifest_dir / name
        mdir.mkdir(parents=True, exist_ok=True)
        (mdir / "manifest.json").write_text(
            json.dumps(manifest(rows, name, commit, info, len(prior)), indent=1) + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps({"registered_in_commit": commit, "fresh16": len(sealed), "train16": len(train)})
    )


if __name__ == "__main__":
    main()
