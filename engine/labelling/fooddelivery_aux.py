"""Out-of-domain auxiliary training set from food-delivery app reviews (S23, GG addendum item 6).

The review-intent classifier has no data for broad_intent `delivery` / `service` (train 12 / 2, sealed 1 / 0).
This builds an AUXILIARY set from "Food Delivery Apps (DoorDash, Grubhub & UberEats) Reviews" (Mendeley Data,
doi:10.17632/m5jk7wzyg7.1, CC BY 4.0, Hossain et al. 2025). Every item carries `domain=out_of_domain_food_delivery`
and split `aux_train`: it is a training-only supplement that must never enter validation or a sealed split, and it
says nothing about merchant reviews. Evaluation of `delivery` and `service` stays 'insufficient data'.

    (repo code, run from the repo root; the downloaded CSV is untrusted DATA, read by pandas only)
    python -m engine.labelling.fooddelivery_aux yield --csv RAW.csv --out reports/labelling/fooddelivery/yield.json
    python -m engine.labelling.fooddelivery_aux draw  --csv RAW.csv --texts-out PRIVATE.jsonl \
        --manifest reports/labelling/fooddelivery/manifest.json
    python -m engine.labelling.fooddelivery_aux report --manifest ... --judges DIR [DIR ...] --out reports/...

Review text goes only to the private text file / private Kaggle dataset; the manifest holds ids, hashes, app, stars.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import zlib
from collections import Counter
from pathlib import Path

from engine.labelling import leakage_neardup as ND
from engine.labelling.pilot_sample import clean
from engine.labelling.repilot2_report import wilson
from engine.labelling.scale_sample import text_sha

DOMAIN = "out_of_domain_food_delivery"
SPLIT = "aux_train"  # not one of train/val/test: nothing that reads those splits can pick this up by accident
SEED = 42
MIN_CHARS, MAX_CHARS = 20, 600
SAMPLE_N = 2000
N_ENRICHED, N_RANDOM = 1500, 1500
CATEGORY = "food"  # judge prompt needs a known category; aspect output is ignored for this set
SOURCE = {
    "title": "Food Delivery Apps (DoorDash, Grubhub & UberEats) Reviews",
    "doi": "10.17632/m5jk7wzyg7.1",
    "version": 1,
    "licence": "CC BY 4.0",
    "file": "Final data.csv",
    "sha256": "05441910ca614d49fd0744e115c1d32fe0b9ac3c8181c41742f9f5382101e7f0",
}

# Fixed before any label is seen. Candidate surfacing only: the judges decide the label.
DELIVERY_RE = re.compile(
    r"\b(?:late|cold|missing|never (?:arrived|came|showed)|didn'?t (?:arrive|come|show)|"
    r"wrong (?:order|address|item|food)|order(?:ed)? (?:was )?(?:wrong|incorrect|incomplete)|"
    r"driver|dasher|courier|rider|delivery|delivered|arrived|spilled|melted|soggy|stolen|"
    r"marked (?:as )?delivered)\b",
    re.I,
)
SERVICE_RE = re.compile(
    r"\b(?:refund\w*|customer (?:service|support|care)|support|rude|no (?:response|reply|help)|"
    r"never (?:replied|responded|helped)|chat ?bot|agent|representative|resolv\w+|complain\w*|"
    r"credit|reimburs\w+|cancel\w*|charged|compensat\w+)\b",
    re.I,
)
# Stricter problem-phrase patterns, used for ENRICHMENT only. The broad patterns above matched 42 percent of a random
# sample (generic words: delivery, support, order), so enrichment by them would barely differ from a random draw.
# Chosen after seeing that yield, before any label was produced.
DELIVERY_STRICT_RE = re.compile(
    r"\b(?:late|cold|(?:item|food|order|drink|fries|bag)s? (?:was |were |is |are )?missing|"
    r"missing (?:item|food|order|drink|fries|bag)s?|never (?:arrived|came|showed|delivered)|"
    r"didn'?t (?:arrive|come|show|deliver)|wrong (?:order|address|item|food)|dasher|courier|"
    r"spilled|melted|soggy|stolen|marked (?:as )?delivered|hours? (?:late|to)|waited|driver|rider)\b",
    re.I,
)
SERVICE_STRICT_RE = re.compile(
    r"\b(?:refund\w*|customer (?:service|support|care)|rude|no (?:response|reply|help)|"
    r"never (?:replied|responded|helped)|chat ?bot|representative|compensat\w+|reimburs\w+|unhelpful|"
    r"resolve\w*|complain\w*)\b",
    re.I,
)
# Food-app-specific vocabulary, to split the delivery/service labels into 'food-app specific' vs 'transferable'.
FOOD_SPECIFIC_RE = re.compile(
    r"\b(?:dashers?|drivers?|restaurants?|food|meals?|menu|doordash|grubhub|uber ?eats|tips?|tipping|"
    r"dash|pickup|pick up|drive[- ]?thru|fries|burgers?|pizza|chicken|order(?:s|ed|ing)?|rider|app)\b",
    re.I,
)
STOP = frozenset(
    [
        "the",
        "and",
        "to",
        "is",
        "it",
        "of",
        "a",
        "i",
        "in",
        "for",
        "this",
        "my",
        "was",
        "not",
        "with",
        "that",
        "have",
        "are",
        "but",
    ]
)


def is_english(t: str) -> bool:
    """Heuristic (source is already 'English, US'): >=95 percent ASCII and >=3 alphabetic tokens, one a stopword."""
    if sum(c.isascii() for c in t) < 0.95 * len(t):
        return False
    toks = re.findall(r"[a-z']+", t.lower())
    return len(toks) >= 3 and any(w in STOP for w in toks)


def load_pool(csv: Path) -> list[dict]:
    """Cleaned, length- and language-filtered, exact-deduplicated (text_sha) rows in file order."""
    import pandas as pd

    df = pd.read_csv(csv, usecols=["content", "score", "Apps"])
    seen: set[str] = set()
    rows = []
    for k, (text, score, app) in enumerate(
        zip(df["content"], df["score"], df["Apps"], strict=True)
    ):
        if not isinstance(text, str):
            continue
        c = clean(text)
        if not (MIN_CHARS <= len(c) <= MAX_CHARS) or not is_english(c):
            continue
        sha = text_sha(c)
        if sha in seen:
            continue
        seen.add(sha)
        rows.append({"row": k, "text": c, "stars": float(score), "app": str(app), "sha": sha})
    return rows


def tags(text: str, strict: bool = False) -> dict[str, bool]:
    d, v = (DELIVERY_STRICT_RE, SERVICE_STRICT_RE) if strict else (DELIVERY_RE, SERVICE_RE)
    return {"delivery": bool(d.search(text)), "service": bool(v.search(text))}


def yield_report(pool: list[dict], raw_rows: int) -> dict:
    """Regex yield on a seeded random sample (seed 42), with Wilson 95 percent intervals and pool projections."""
    rng = random.Random(SEED)
    samp = rng.sample(pool, SAMPLE_N)
    n = len(samp)
    t = [tags(r["text"]) for r in samp]
    c = {
        "delivery": sum(x["delivery"] for x in t),
        "service": sum(x["service"] for x in t),
        "either": sum(x["delivery"] or x["service"] for x in t),
        "both": sum(x["delivery"] and x["service"] for x in t),
    }
    full = [
        tags(r["text"]) for r in pool
    ]  # exact regex counts on the whole pool, to check the sample estimate
    f = {
        "delivery": sum(x["delivery"] for x in full),
        "service": sum(x["service"] for x in full),
        "either": sum(x["delivery"] or x["service"] for x in full),
    }
    ts = [tags(r["text"], strict=True) for r in samp]
    cs = {
        "delivery": sum(x["delivery"] for x in ts),
        "service": sum(x["service"] for x in ts),
        "either": sum(x["delivery"] or x["service"] for x in ts),
    }
    npool = len(pool)
    proj = {k: round(v / n * npool) for k, v in c.items()}
    return {
        "source": SOURCE,
        "seed": SEED,
        "raw_rows_in_csv": raw_rows,
        "pool_after_clean_len_english_dedupe": npool,
        "sample_n": n,
        "sample_regex_matches": c,
        "sample_regex_prevalence": {k: round(v / n, 4) for k, v in c.items()},
        "sample_regex_wilson95": {k: wilson(v, n) for k, v in c.items()},
        "expected_regex_matches_in_pool_from_sample": proj,
        "exact_regex_matches_in_pool": f,
        "expected_regex_matches_in_candidate_pool_of_N": {
            str(m): {k: round(v / n * m) for k, v in c.items() if k != "both"}
            for m in (1500, 3000, 10000)
        },
        "strict_enrichment_regex_sample_matches": cs,
        "strict_enrichment_regex_prevalence": {k: round(v / n, 4) for k, v in cs.items()},
        "strict_enrichment_regex_wilson95": {k: wilson(v, n) for k, v in cs.items()},
        "expected_strict_matches_in_pool": {k: round(v / n * npool) for k, v in cs.items()},
        "expected_strict_matches_in_candidate_pool_of_N": {
            str(m): {k: round(v / n * m) for k, v in cs.items()} for m in (1500, 3000, 10000)
        },
        "caveat": "regex hits are CANDIDATES, not labels: the judge consensus rate among them is unknown until labelled",
    }


def _norm_index(fp: dict) -> tuple[dict, dict]:
    items = {h: (set(v["gj"]), set(v["g12"]), v["n"]) for h, v in fp["items"].items()}
    i12: dict[int, list[str]] = {}
    ij: dict[int, list[str]] = {}
    for h, (gj, g12, _) in items.items():
        for g in g12:
            i12.setdefault(g, []).append(h)
        for g in gj:
            ij.setdefault(g, []).append(h)
    return items, {"i12": i12, "ij": ij}


def near_sealed(text: str, items: dict, idx: dict) -> str | None:
    """Sealed hash this text near-duplicates or quotes (leakage_neardup rules), else None."""
    n = ND.norm(text)
    if len(n) < ND.MIN_CAND_CHARS:
        return None
    if not any(
        zlib.crc32(n[i : i + 12].encode("utf-8")) in idx["i12"] for i in range(0, len(n) - 11, 2)
    ):
        return None
    gj = ND.shingles(n, 4)
    near = {h for g in gj if g in idx["ij"] for h in idx["ij"][g]}
    g12 = ND.shingles(n, 12)
    for h in sorted(near):
        sj, s12, ln = items[h]
        if len(sj & gj) / max(1, len(sj | gj)) >= ND.JACCARD:
            return h
        if ln >= ND.MIN_QUOTE_CHARS and s12 and len(s12 & g12) / len(s12) >= ND.CONTAIN:
            return h
    return None


def draw(pool: list[dict], sealed: set[str], fp: dict, banned: set[str]) -> tuple[list[dict], dict]:
    """1,500 regex-enriched + 1,500 random items; exact and near sealed matches and prior-manifest hashes removed."""
    items, idx = _norm_index(fp)
    removed = Counter()
    ok = []
    for r in pool:
        if r["sha"] in sealed:
            removed["exact_sealed"] += 1
        elif r["sha"] in banned:
            removed["in_earlier_manifest_or_ledger"] += 1
        else:
            ok.append(r)
    rng = random.Random(SEED)
    enriched_pool = [r for r in ok if any(tags(r["text"], strict=True).values())]

    def take(cands: list[dict], n: int, exclude: set[str]) -> list[dict]:
        # Seeded shuffle, near-duplicate check applied lazily to the walk (checking all ~1M rows is too slow);
        # a rejected candidate is skipped and the walk continues, so the quota is still met.
        order = [r for r in cands if r["sha"] not in exclude]
        rng.shuffle(order)
        got: list[dict] = []
        for r in order:
            if len(got) == n:
                break
            if near_sealed(r["text"], items, idx):
                removed["near_duplicate_of_sealed_during_draw"] += 1
                continue
            got.append(r)
        return got

    pick_e = take(enriched_pool, N_ENRICHED, set())
    pick_r = take(ok, N_RANDOM, {r["sha"] for r in pick_e})
    out = []
    for kind, picks in (("regex_enriched", pick_e), ("random", pick_r)):
        for r in picks:
            out.append({**r, "pool": kind})
    out.sort(key=lambda r: r["row"])
    for k, r in enumerate(out):
        r["id"] = f"fd-{k:04d}"
    info = {
        "removed_before_draw": dict(removed),
        "eligible_pool": len(ok),
        "regex_enriched_pool": len(enriched_pool),
    }
    return out, info


def manifest_of(rows: list[dict], info: dict) -> dict:
    return {
        "domain": DOMAIN,
        "split": SPLIT,
        "rule": "training-only auxiliary data; never validation, never sealed; evaluation of delivery/service stays insufficient data",
        "source": SOURCE,
        "attribution": "Hossain, M.S., Li, L., Sun, P., Law, K.A., Ray, S. (2025) 'Food Delivery Apps (DoorDash, Grubhub & UberEats) Reviews', Mendeley Data, V1, doi:10.17632/m5jk7wzyg7.1, CC BY 4.0. Labels and subset selection by the review-iq project; changes: cleaned, deduplicated, subsampled, relabelled.",
        "seed": SEED,
        "draw": info,
        "n_items": len(rows),
        "items": [
            {
                "id": r["id"], "stratum": "food_delivery_app", "category": CATEGORY, "split": SPLIT,
                "domain": DOMAIN, "pool": r["pool"], "app": r["app"], "stars": r["stars"], "sha": r["sha"],
                "row": r["row"],
            }
            for r in rows
        ],
    }  # fmt: skip


def write_texts(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(
                json.dumps(
                    {"id": r["id"], "stratum": "food_delivery_app", "category": CATEGORY, "text": r["text"],
                     "stars": r["stars"], "sha": r["sha"], "split": SPLIT}, ensure_ascii=False,
                ) + "\n"
            )  # fmt: skip


def file_sha16(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("yield")
    p.add_argument("--csv", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p = sub.add_parser("draw")
    p.add_argument("--csv", required=True, type=Path)
    p.add_argument("--texts-out", required=True, type=Path)
    p.add_argument("--manifest", required=True, type=Path)
    a = ap.parse_args()
    import pandas as pd

    raw = len(pd.read_csv(a.csv, usecols=["score"]))
    pool = load_pool(a.csv)
    if a.cmd == "yield":
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(yield_report(pool, raw), indent=1) + "\n", encoding="utf-8")
        print(f"wrote {a.out}")
        return
    root = ND.ROOT
    led = json.loads((root / "reports/labelling/ledger.json").read_text(encoding="utf-8"))
    sealed = set(ND.sealed_hashes())
    fp = json.loads(ND.FP.read_text(encoding="utf-8"))
    rows, info = draw(pool, sealed, fp, set(led["entries"]))
    write_texts(rows, a.texts_out)
    a.manifest.parent.mkdir(parents=True, exist_ok=True)
    a.manifest.write_text(json.dumps(manifest_of(rows, info), indent=1) + "\n", encoding="utf-8")
    print(f"{len(rows)} items -> {a.texts_out} (private); manifest -> {a.manifest}")


if __name__ == "__main__":
    main()
