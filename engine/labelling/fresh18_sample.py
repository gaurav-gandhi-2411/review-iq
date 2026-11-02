"""Draw the Amendment 18 SEALED set `fresh18`: S4 only (stars >= 4), for the confirmatory router-vs-LLM test on S4.

PREPARED IN S32, NOT REGISTERED. This module exists only for the D1 branch of `docs/specs/s32-s4-llm-check.md`; it refuses to
run until "### Amendment 18" is in the history of docs/specs/review-intent.md, and the quotas below are part of what
Amendment 18 must register (change them only in the registration commit, never after a draw).

    PYTHONPATH=. python -m engine.labelling.fresh18_sample --corpus D:/ml-cache/review-corpus \
        --tokenizer D:/ml-cache/s25/classify_artifacts_onnx/hf/tokenizer.json \
        --prior-texts <every earlier private text file, including fresh16.jsonl, train16.jsonl and fresh17.jsonl> \
        --out-dir D:/ml-cache/s33/fresh18 --manifest-dir reports/labelling

Sizing: the S4 positive rate on fresh16 + fresh17 is 3.8 percent (259 of 6,840 with consensus, s32-s4-llm-check.md), so the
main block of 8,100 S4 items yields about 308 positives (binomial sd about 17). The registered top-up block B2 (1,200 more, own
seed) is drawn only if the panel finds fewer than 300 S4 positives in block S, so the >= 300 floor is met without a second
look at the outcome metric. Same freshness method as Amendments 16 and 17 (`fresh16_sample._draw`: content hash against the
ledger and every earlier manifest, near-duplicate against every earlier text and every kept item). The committed manifest holds
ids, block, category, stars, token length and content hash; text goes to the private file.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
from pathlib import Path

from engine.labelling.fresh16_sample import CATEGORIES, _draw
from engine.labelling.fresh17_sample import EXCLUDE as EXCLUDE17
from engine.labelling.fresh_sample import NearDupIndex, load_prior_texts
from engine.labelling.pilot_sample import load_all
from engine.labelling.scale_sample import build_pools, load_banned

SEED_SEALED = 51  # fresh17 used 49 / 50
SEED_TOPUP = 52
BLOCK_S = {"apparel": 2700, "beauty": 2700, "food": 2700}
TOPUP_S = {"apparel": 400, "beauty": 400, "food": 400}
EXCLUDE = [*EXCLUDE17, "reports/labelling/fresh17/manifest.json"]


def _is_s4(it: dict) -> bool:
    return it["stars"] is not None and it["stars"] >= 4


def registered_commit(repo: Path) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), "log", "-S### Amendment 18", "--format=%H", "--", "docs/specs/review-intent.md"],
        capture_output=True, text=True, check=True,
    ).stdout.split()  # fmt: skip
    if not out:
        raise SystemExit(
            "Amendment 18 is not in docs/specs/review-intent.md history: register it before drawing"
        )
    return out[-1]


def draw_sealed(
    pools: dict[str, list[dict]], index: NearDupIndex, topup: bool = False
) -> list[dict]:
    """pools[category] = [{text, stars, sha}] -> sealed S4 rows with ids. topup=True draws the registered block S2 instead."""
    taken: set[str] = set()
    rng = random.Random(SEED_TOPUP if topup else SEED_SEALED)
    block, quota = ("S2", TOPUP_S) if topup else ("S", BLOCK_S)
    rows: list[dict] = []
    for cat in CATEGORIES:
        got = _draw(
            rng,
            [it for it in pools[cat] if _is_s4(it)],
            quota[cat],
            taken,
            index,
            f"fresh18 block {block} {cat}",
        )
        rows += [{**it, "id": f"fresh18-{block}-{cat}-{k:04d}", "block": block, "category": cat, "split": "test"}
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
    ap.add_argument(
        "--topup",
        action="store_true",
        help="draw registered block S2 (only if block S has < 300 S4 positives)",
    )
    a = ap.parse_args()
    repo = Path(__file__).resolve().parents[2]
    commit = registered_commit(repo)
    from tokenizers import Tokenizer

    name = "fresh18_topup" if a.topup else "fresh18"
    exclude = [*EXCLUDE, "reports/labelling/fresh18/manifest.json"] if a.topup else EXCLUDE
    banned, info = load_banned(exclude, a.ledger)
    raw_pools, _ = build_pools(load_all(Path(a.corpus), Path(a.flipkart)), banned)
    pools = {
        cat: [{"text": t, "stars": s, "sha": h} for t, s, h in raw_pools[cat]] for cat in CATEGORIES
    }
    index = NearDupIndex()
    prior = load_prior_texts([p for p in a.prior_texts.split(",") if p])
    for t in prior:
        index.add(t)
    rows = draw_sealed(pools, index, topup=a.topup)
    tok = Tokenizer.from_file(str(a.tokenizer))
    for r, e in zip(rows, tok.encode_batch([r["text"] for r in rows]), strict=True):
        r["ntok"] = len(e.ids)
    a.out_dir.mkdir(parents=True, exist_ok=True)
    (a.out_dir / f"{name}.jsonl").write_text(
        "".join(json.dumps({k: r[k] for k in ("id", "text", "stars", "category", "block", "split", "ntok", "sha")}, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )  # fmt: skip
    mdir = a.manifest_dir / name
    mdir.mkdir(parents=True, exist_ok=True)
    man = {
        "name": name, "registered_in_commit": commit, "spec": "docs/specs/review-intent.md Amendment 18",
        "seeds": {"sealed": SEED_SEALED, "top_up": SEED_TOPUP}, "exclusions": info, "n_prior_texts_for_near_duplicate_scan": len(prior),
        "near_duplicate_rule": "exact Jaccard of 4-character shingle sets >= 0.9",
        "counts": {f"{r_b}/{c}": sum(r["block"] == r_b and r["category"] == c for r in rows) for r_b in sorted({r["block"] for r in rows}) for c in CATEGORIES},
        "items": [{k: r[k] for k in ("id", "block", "category", "split", "stars", "ntok", "sha")} for r in rows],
    }  # fmt: skip
    (mdir / "manifest.json").write_text(json.dumps(man, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"registered_in_commit": commit, name: len(rows)}))


if __name__ == "__main__":
    main()
