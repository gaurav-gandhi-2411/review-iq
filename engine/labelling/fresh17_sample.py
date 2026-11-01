"""Draw the Amendment 17 SEALED set `fresh17` (Block A natural, Block B stars >= 4). No training additions.

    PYTHONPATH=. python -m engine.labelling.fresh17_sample --corpus D:/ml-cache/review-corpus \
        --tokenizer D:/ml-cache/s25/classify_artifacts_onnx/hf/tokenizer.json \
        --prior-texts <every earlier private text file, including fresh16.jsonl and train16.jsonl> \
        --out-dir D:/ml-cache/s31/fresh17 --manifest-dir reports/labelling

Registered in docs/specs/review-intent.md Amendment 17 before the first draw: the module refuses to run unless that text is in the history and
records the commit in the manifest. Same freshness method as Amendment 16 (`fresh16_sample._draw`: content hash against the ledger and every
earlier manifest including fresh16 and train16, near-duplicate against every earlier text and every kept item). Token length is computed for
the drawn items only. The committed manifest holds ids, block, category, stars, token length and content hash; text goes to the private file.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
from pathlib import Path

from engine.labelling.fresh16_sample import CATEGORIES, _draw
from engine.labelling.fresh16_sample import EXCLUDE as EXCLUDE16
from engine.labelling.fresh_sample import NearDupIndex, load_prior_texts
from engine.labelling.pilot_sample import load_all
from engine.labelling.scale_sample import build_pools, load_banned

SEED_SEALED = 49
SEED_TOPUP = 50
BLOCK_A = {"apparel": 600, "beauty": 600, "food": 600}
BLOCK_B = {"apparel": 800, "beauty": 800, "food": 800}
TOPUP_B = {"apparel": 400, "beauty": 400, "food": 400}
EXCLUDE = [
    *EXCLUDE16,
    "reports/labelling/fresh16/manifest.json",
    "reports/labelling/train16/manifest.json",
]


def registered_commit(repo: Path) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), "log", "-S### Amendment 17", "--format=%H", "--", "docs/specs/review-intent.md"],
        capture_output=True, text=True, check=True,
    ).stdout.split()  # fmt: skip
    if not out:
        raise SystemExit(
            "Amendment 17 is not in docs/specs/review-intent.md history: register it before drawing"
        )
    return out[-1]


def draw_sealed(
    pools: dict[str, list[dict]], index: NearDupIndex, topup: bool = False
) -> list[dict]:
    """pools[category] = [{text, stars, sha}] -> sealed rows with ids. topup=True draws the registered Block B top-up instead."""
    taken: set[str] = set()
    rng = random.Random(SEED_TOPUP if topup else SEED_SEALED)
    plan = [("B2", TOPUP_B, lambda it: it["stars"] >= 4)] if topup else [
        ("A", BLOCK_A, lambda it: True), ("B", BLOCK_B, lambda it: it["stars"] is not None and it["stars"] >= 4)]  # fmt: skip
    rows: list[dict] = []
    for block, quota, pick in plan:
        for cat in CATEGORIES:
            got = _draw(
                rng,
                [it for it in pools[cat] if pick(it)],
                quota[cat],
                taken,
                index,
                f"fresh17 block {block} {cat}",
            )
            rows += [{**it, "id": f"fresh17-{block}-{cat}-{k:04d}", "block": block, "category": cat, "split": "test"}
                     for k, it in enumerate(got)]  # fmt: skip
    return rows


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
    pools = {
        cat: [{"text": t, "stars": s, "sha": h} for t, s, h in raw_pools[cat]] for cat in CATEGORIES
    }
    index = NearDupIndex()
    prior = load_prior_texts([p for p in a.prior_texts.split(",") if p])
    for t in prior:
        index.add(t)
    rows = draw_sealed(pools, index)
    tok = Tokenizer.from_file(str(a.tokenizer))
    for r, e in zip(rows, tok.encode_batch([r["text"] for r in rows]), strict=True):
        r["ntok"] = len(e.ids)
    a.out_dir.mkdir(parents=True, exist_ok=True)
    (a.out_dir / "fresh17.jsonl").write_text(
        "".join(json.dumps({k: r[k] for k in ("id", "text", "stars", "category", "block", "split", "ntok", "sha")}, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )  # fmt: skip
    mdir = a.manifest_dir / "fresh17"
    mdir.mkdir(parents=True, exist_ok=True)
    man = {
        "name": "fresh17", "registered_in_commit": commit, "spec": "docs/specs/review-intent.md Amendment 17",
        "seeds": {"sealed": SEED_SEALED, "top_up": SEED_TOPUP}, "exclusions": info, "n_prior_texts_for_near_duplicate_scan": len(prior),
        "near_duplicate_rule": "exact Jaccard of 4-character shingle sets >= 0.9",
        "counts": {f"{b}/{c}": sum(r["block"] == b and r["category"] == c for r in rows) for b in sorted({r["block"] for r in rows}) for c in CATEGORIES},
        "items": [{k: r[k] for k in ("id", "block", "category", "split", "stars", "ntok", "sha")} for r in rows],
    }  # fmt: skip
    (mdir / "manifest.json").write_text(json.dumps(man, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"registered_in_commit": commit, "fresh17": len(rows)}))


if __name__ == "__main__":
    main()
