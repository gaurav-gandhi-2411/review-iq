"""Safety-stratum split (spec Amendment 6): computed once, committed as ids and hashes only.

    python -m engine.labelling.safety_split --items D:/ml-cache/s23/safety_items.jsonl

Salt `review-intent-safety-split-v1`, test share 0.40, of the rest 0.10 validation. The expected sealed-positive count
is derived from the panel report ALONE (Binomial(n_high, test share)) and written next to the realised count, so the
prediction cannot be fitted to the split. Train/val items that near-duplicate a safety test item are marked `dropped`
(they stay in the ledger as used items). Review text is read from the private items file and never written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from engine.labelling.leakage_neardup import JACCARD, norm, shingles
from engine.labelling.pilot_sample import clean
from engine.labelling.safety_panel import SealedIndex, dump_manifest

SALT = "review-intent-safety-split-v1"
TEST_SHARE = 0.40
VAL_SHARE_OF_REST = 0.10
MIN_SEALED_POSITIVES = 138  # Amendment 5 minimum, kept by Amendment 6
DIR = Path("reports/labelling/safety")


def split_of(sha: str) -> str:
    h = hashlib.sha256(f"{SALT}:{sha}".encode()).hexdigest()
    u = int(h[:12], 16) / 16**12
    if u < TEST_SHARE:
        return "test"
    return "val" if u < TEST_SHARE + (1 - TEST_SHARE) * VAL_SHARE_OF_REST else "train"


def expected(n_high: int) -> dict:
    mean = n_high * TEST_SHARE
    sd = math.sqrt(n_high * TEST_SHARE * (1 - TEST_SHARE))
    z = (MIN_SEALED_POSITIVES - 0.5 - mean) / sd
    return {
        "n_high_population": n_high,
        "sealed_positives_mean": round(mean, 1),
        "sealed_positives_sd": round(sd, 1),
        "p_below_minimum_normal_approx": round(0.5 * (1 + math.erf(z / math.sqrt(2))), 3),
        "train_val_positives_mean": round(n_high * (1 - TEST_SHARE), 1),
    }


def fingerprint(text: str) -> dict:
    n = norm(clean(text))
    return {"n": len(n), "gj": sorted(shingles(n, 4)), "g12": sorted(shingles(n, 12))}


def neardup_clusters(fps: dict[str, dict]) -> dict[str, int]:
    """id -> group id; items joined when the 4-char-shingle Jaccard is at least `leakage_neardup.JACCARD`."""
    ids = sorted(fps)
    sets = {i: set(fps[i]["gj"]) for i in ids}
    inv: dict[int, list[str]] = {}
    for i in ids:
        for g in sets[i]:
            inv.setdefault(g, []).append(i)
    parent = {i: i for i in ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in ids:
        for j in {m for g in sets[i] for m in inv[g] if m > i}:
            if len(sets[i] & sets[j]) / len(sets[i] | sets[j]) >= JACCARD:
                parent[find(j)] = find(i)
    roots = {r: n for n, r in enumerate(sorted({find(i) for i in ids}))}
    return {i: roots[find(i)] for i in ids}


def build(items_path: Path, out: Path) -> dict:
    report = json.loads((DIR / "panel_report.json").read_text(encoding="utf-8"))
    pred = expected(report["overall"]["high"]["k"])  # computed before any split is looked at
    labels = {
        r["id"]: r
        for r in map(json.loads, (DIR / "labels.jsonl").read_text(encoding="utf-8").splitlines())
    }
    text = {
        r["id"]: r["text"]
        for r in map(json.loads, items_path.read_text(encoding="utf-8").splitlines())
    }
    rows = [
        {"id": i, "sha": r["sha"], "split": split_of(r["sha"])} for i, r in sorted(labels.items())
    ]
    test_fp = {r["sha"]: fingerprint(text[r["id"]]) for r in rows if r["split"] == "test"}
    index = SealedIndex({"items": test_fp})
    for r in rows:
        if r["split"] != "test" and index.leaks(text[r["id"]]):
            r["split"] = "dropped"
    pos = {s: sum(labels[r["id"]]["urgency"] == "high" for r in rows if r["split"] == s)
           for s in ("test", "train", "val", "dropped")}  # fmt: skip
    test_ids = [r["id"] for r in rows if r["split"] == "test"]
    clusters = neardup_clusters({i: fingerprint(text[i]) for i in test_ids})
    high_clusters = {c for i, c in clusters.items() if labels[i]["urgency"] == "high"}
    res = {
        "salt": SALT, "test_share": TEST_SHARE, "val_share_of_rest": VAL_SHARE_OF_REST,
        "expected_before_split": pred,
        "realised": {
            "items": {s: sum(r["split"] == s for r in rows) for s in ("test", "train", "val", "dropped")},
            "urgency_high_positives": pos,
            "sealed_positives_meets_minimum": pos["test"] >= MIN_SEALED_POSITIVES,
            "test_near_duplicate_groups": {
                "note": "positives counted once per group of mutually near-duplicate test items (Jaccard 0.9)",
                "independent_high_positives": len(high_clusters),
                "independent_meets_minimum": len(high_clusters) >= MIN_SEALED_POSITIVES,
            },
        },
        "items": rows,
    }  # fmt: skip
    out.write_text(dump_manifest(res), encoding="utf-8")
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=DIR / "split.json")
    a = ap.parse_args()
    res = build(a.items, a.out)
    print(json.dumps({k: v for k, v in res.items() if k != "items"}))


if __name__ == "__main__":
    main()
