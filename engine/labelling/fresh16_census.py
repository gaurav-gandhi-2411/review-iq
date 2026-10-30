"""Amendment 16, step 3a: how many FRESH items are left in the licensed pools, overall and in the two weak strata.

    PYTHONPATH=. python -m engine.labelling.fresh16_census --corpus D:/ml-cache/review-corpus --tokenizer D:/ml-cache/s25/classify_artifacts_onnx/hf/tokenizer.json \
        --out reports/labelling/fresh16/census.json

"Fresh" = the pool of `scale_sample.build_pools` (same cleaning, length limits and dedup as every earlier draw) after dropping every content
hash in the leakage ledger and in the manifests of all earlier sets, including the fresh15 set. The near-duplicate filter that the draw applies on top
removed 1 of 2,000 candidates last time, so the counts below are upper bounds by that rate. Strata are defined on OBSERVABLE features only (star rating,
token length under the served tokenizer), never on a label. No review text is read out or written.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from engine.labelling.pilot_sample import load_all
from engine.labelling.scale_sample import build_pools, load_banned

EXCLUDE = [
    "reports/labelling/pilot1/manifest.json",
    "reports/labelling/pilot2/manifest.json",
    "reports/labelling/pilot3/manifest.json",
    "reports/labelling/scale/manifest.json",
    "reports/labelling/safety/split.json",
    "reports/labelling/fresh15/manifest.json",
]
LEN_TOKENS = 128


def census(pools: dict[str, list[tuple[str, float, str]]], tokenizer_path: Path) -> dict:
    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(tokenizer_path))
    out: dict = {}
    for stratum, pool in sorted(pools.items()):
        n4 = n_long = n4_long = 0
        for k in range(0, len(pool), 2000):
            chunk = pool[k : k + 2000]
            enc = tok.encode_batch([t for t, _, _ in chunk])
            for (_, stars, _), e in zip(chunk, enc, strict=True):
                s4, lg = stars >= 4, len(e.ids) > LEN_TOKENS
                n4 += s4
                n_long += lg
                n4_long += s4 and lg
        out[stratum] = {
            "pool": len(pool),
            "stars_ge_4": n4,
            f"tokens_gt_{LEN_TOKENS}": n_long,
            "stars_ge_4_and_long": n4_long,
        }
    out["ALL"] = {k: sum(v[k] for v in out.values()) for k in next(iter(out.values()))}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--flipkart", default="data/processed/vernacular_subset.jsonl")
    ap.add_argument("--tokenizer", required=True, type=Path)
    ap.add_argument("--ledger", default="reports/labelling/ledger.json")
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()
    banned, info = load_banned(EXCLUDE, a.ledger)
    pools, stats = build_pools(load_all(Path(a.corpus), Path(a.flipkart)), banned)
    pools = {s: p for s, p in pools.items() if p and s != "vernacular"}
    res = {
        "spec": "docs/specs/review-intent.md Amendment 16, step 3a",
        "exclusions": info,
        "pools_after_exclusions": {s: stats[s] for s in pools},
        "census": census(pools, a.tokenizer),
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(res["census"], indent=1))


if __name__ == "__main__":
    main()
