"""U6b (Session 15d): how much of the held-out gold is 'no consensus' rather than a label.

eval/consensus/build_held_out_corpus.py::build_fixture writes `silver_or_none(field) or <default>`:
when the 3-judge panel SPLITS on a field (no exact-value majority) the gold silently becomes the
default -- "unknown" for product, [] for list fields, null for enums -- indistinguishable from
"the review genuinely has no product / no pros / no answer". Each fixture records the truth in
`labeling_meta.agreement_per_field` (unanimous / majority / split). This script quantifies, per
field, how many gold values are split-collapsed defaults and what the held-out score would be if
those (fixture, field) pairs were EXCLUDED (not relabelled): a split is not a label, so it should
not be scored as one. It is symmetric: exclusion can raise or lower any field's score.

Zero quota, reads recorded predictions and fixtures only. It changes no gold and no scorer.

Usage: uv run python scripts/audit_split_gold.py
"""

from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from eval.provenance import get_git_sha, now_iso  # noqa: E402
from eval.runner import score_fixture  # noqa: E402

HELD_OUT = ROOT / "eval" / "fixtures" / "_held_out_hindi_hinglish"
PRED = ROOT / "eval" / "results" / "held_out_scoring_v2.json"
OUT = ROOT / "eval" / "results" / "split_gold_audit_n106.json"
CONSTANT_FIELDS = {"stars"}  # null in gold and prediction on all 106 (see ADR 0030)
SEED, N_RES = 42, 10_000


def _paired_ci(deltas: list[float]) -> list[float]:
    rng = random.Random(SEED)  # noqa: S311 -- resampling, not security
    n = len(deltas)
    ms = sorted(mean(rng.choices(deltas, k=n)) for _ in range(N_RES))
    return [ms[int(0.025 * N_RES)], ms[int(0.975 * N_RES) - 1]]


def main() -> int:
    fixtures = {}
    for p in sorted(HELD_OUT.glob("hien-*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        fixtures[d["id"]] = d
    payload = json.loads(PRED.read_text(encoding="utf-8"))
    records = {r["id"]: r for r in payload["records"]}

    agreement: Counter[tuple[str, str]] = Counter()
    split_pairs: set[tuple[str, str]] = set()
    for fid, fx in fixtures.items():
        for field, level in fx["labeling_meta"]["agreement_per_field"].items():
            agreement[(field, level)] += 1
            if level == "split":
                split_pairs.add((fid, field))

    scored_fields = list(next(iter(records.values()))["as_deployed"]["field_scores"])
    per_field: dict[str, dict] = {}
    overall_all: list[float] = []
    overall_excl: list[float] = []
    for fid, rec in records.items():
        res = {
            r.field: r.score for r in score_fixture(fixtures[fid], rec["as_deployed"]["predicted"])
        }
        overall_all.append(mean(v for f, v in res.items() if f not in CONSTANT_FIELDS))
        kept = {
            f: v for f, v in res.items() if f not in CONSTANT_FIELDS and (fid, f) not in split_pairs
        }
        overall_excl.append(mean(kept.values()))
    for f in scored_fields:
        rows = [(fid, records[fid]["as_deployed"]["field_scores"][f]) for fid in records]
        kept = [s for fid, s in rows if (fid, f) not in split_pairs]
        n_split = len(rows) - len(kept)
        per_field[f] = {
            "n": len(rows),
            "n_split_gold": n_split,
            "score_all": mean(s for _, s in rows),
            "score_excluding_split": mean(kept) if kept else None,
        }
    result = {
        "generated_at": now_iso(),
        "git_sha": get_git_sha(),
        "predictions_git_sha": payload.get("git_sha"),
        "n": len(records),
        "agreement_counts": {f"{f}:{lvl}": n for (f, lvl), n in sorted(agreement.items())},
        "overall_as_deployed_excl_constant": {
            "all_pairs": mean(overall_all),
            "excluding_split_pairs": mean(overall_excl),
            "delta": mean(overall_excl) - mean(overall_all),
            "delta_ci95_paired_bootstrap": _paired_ci(
                [b - a for a, b in zip(overall_all, overall_excl, strict=True)]
            ),
        },
        "per_field": per_field,
    }
    # U6c: product behaviour, separating genuine "no product" gold from split-collapsed defaults.
    from eval.free_text_scoring import canonical_product, product_score

    cat_words = {"headphone", "earphone", "headset", "speaker", "earbud", "bud", "audio", "device"}
    prod: Counter[tuple[str, str, str]] = Counter()
    for fid, fx in fixtures.items():
        g = fx["ground_truth"]["product"]
        lvl = fx["labeling_meta"]["agreement_per_field"]["product"]
        pred = records[fid]["as_deployed"]["predicted"]["product"]
        toks = {t.rstrip("s") for t in str(g).lower().replace("&", " ").split()}
        gk = (
            "null"
            if canonical_product(g) is None
            else ("category" if toks <= cat_words else "brand")
        )
        gk += "/split" if lvl == "split" else "/consensus"
        pk = "abstained" if canonical_product(pred) is None else "named"
        ok = "correct" if product_score(pred, g) == 1.0 else "wrong"
        prod[(gk, pk, ok)] += 1
    result["product_behaviour_by_gold_kind"] = {
        f"{a} | model {b} | {c}": n for (a, b, c), n in sorted(prod.items())
    }
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "agreement_counts"}, indent=1)[:3500])
    print(
        "agreement counts (split only):",
        {k: v for k, v in result["agreement_counts"].items() if k.endswith(":split")},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
