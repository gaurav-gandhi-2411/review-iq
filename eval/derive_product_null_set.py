"""C2a (Session 15c): inventory of every distinct `product` value in the data, classified by the
null rule in eval/free_text_scoring.py -- the reproducible derivation of the null vocabulary.

Reads only recorded artifacts (held-out gold fixtures, recorded held-out predictions, dev-set
gold fixtures). Zero quota, zero LLM calls. The point of the inventory is auditability: every
distinct value is listed with its count and its classification, so a reader can see exactly
which strings were treated as "no product named" and confirm none is a real product.

The dev-set gold is reported separately as an INDEPENDENT check: the null vocabulary was not
built from it, so a value classified null there that is a real product would be a false-null.

Usage: uv run python eval/derive_product_null_set.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from eval.free_text_scoring import (  # noqa: E402
    NULL_PRODUCT_TOKENS,
    SCORER_VERSION,
    canonical_product,
)
from eval.provenance import get_git_sha, now_iso  # noqa: E402

HELD_OUT_DIR = ROOT / "eval" / "fixtures" / "_held_out_hindi_hinglish"
DEV_DIRS = [ROOT / "eval" / "fixtures", ROOT / "eval" / "fixtures" / "hi-en"]
PREDICTIONS = ROOT / "eval" / "results" / "held_out_scoring_v2.json"
OUT_PATH = ROOT / "eval" / "results" / "product_null_inventory.json"


def _gold(paths: list[Path]) -> Counter[str | None]:
    counts: Counter[str | None] = Counter()
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        if "review_text" in data:
            counts[data.get("ground_truth", {}).get("product")] += 1
    return counts


def _classify(counts: Counter[str | None]) -> dict[str, list[list[object]]]:
    null_rows = [[v, n] for v, n in counts.most_common() if canonical_product(v) is None]
    named_rows = [[v, n] for v, n in counts.most_common() if canonical_product(v) is not None]
    return {"classified_null": null_rows, "classified_named": named_rows}


def main() -> int:
    held_out_gold = _gold(
        sorted(p for p in HELD_OUT_DIR.glob("*.json") if not p.name.startswith("."))
    )
    dev_paths = sorted(p for d in DEV_DIRS for p in d.glob("*.json") if not p.name.startswith("."))
    dev_gold = _gold(dev_paths)
    preds = json.loads(PREDICTIONS.read_text(encoding="utf-8"))["records"]
    pred_as_deployed = Counter(r["as_deployed"]["predicted"]["product"] for r in preds)
    pred_forced = Counter(r["language_forced"]["predicted"]["product"] for r in preds)

    sections = {
        "held_out_gold": held_out_gold,
        "held_out_pred_as_deployed": pred_as_deployed,
        "held_out_pred_language_forced": pred_forced,
        "dev_gold_independent_check": dev_gold,
    }
    out: dict[str, object] = {
        "generated_at": now_iso(),
        "git_sha": get_git_sha(),
        "scorer_version": SCORER_VERSION,
        "null_tokens": sorted(NULL_PRODUCT_TOKENS),
        "sections": {
            name: {
                "n": sum(c.values()),
                "n_distinct": len(c),
                "n_null": sum(n for v, n in c.items() if canonical_product(v) is None),
                **_classify(c),
            }
            for name, c in sections.items()
        },
    }
    OUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for name, sec in out["sections"].items():  # type: ignore[union-attr]
        print(f"{name}: n={sec['n']} distinct={sec['n_distinct']} null={sec['n_null']}")
        print("  null :", [tuple(x) for x in sec["classified_null"]])
        print("  named:", [tuple(x) for x in sec["classified_named"]])
    print(f"Written: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
