"""Build the review-intent pilot sample (docs/specs/review-intent.md section 4).

    python -m engine.labelling.pilot_sample --corpus D:/ml-cache/review-corpus --out D:/ml-cache/review-intent/pilot1.jsonl \
        --manifest reports/labelling/pilot1/manifest.json --seed 42 --exclude ""

75 reviews per stratum (apparel, food, beauty, vernacular), seeded, after exact-text dedup and a length filter.
The text file stays OUT of the repository (licensed corpora; ids only are committed). `--exclude` takes a
manifest of earlier pilots so a re-pilot draws FRESH items.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import html
import json
import random
import re
from pathlib import Path

import pandas as pd

PER_STRATUM = 75
LIMITS = {"apparel": (40, 1000), "food": (40, 1000), "beauty": (40, 1000), "vernacular": (15, 1000)}
SOURCES = {
    "apparel": ("Women's E-Commerce Clothing Reviews (Kaggle nicapotato)", "CC0-1.0"),
    "food": ("Amazon Fine Food Reviews (Kaggle snap)", "CC0-1.0"),
    "beauty": ("Sephora Skincare Reviews (Kaggle melissamonfared)", "CC BY 4.0"),
    "vernacular": ("Flipkart reviews, ADR 0004 corpus (ODbL-1.0 / DbCL-1.0 Kaggle releases)", "ODbL-1.0/DbCL-1.0"),
}  # fmt: skip


def clean(t: str) -> str:
    t = html.unescape(re.sub(r"<br\s*/?>", " ", str(t)))
    return re.sub(r"\s+", " ", t).strip()


def load_all(corpus: Path, flipkart: Path) -> dict[str, pd.DataFrame]:
    apparel = pd.read_csv(next(corpus.glob("nicapotato_*/Womens*.csv")))
    apparel = apparel.rename(columns={"Review Text": "text", "Rating": "stars"})[["text", "stars"]]
    food = pd.read_csv(
        corpus / "snap_amazon-fine-food-reviews" / "Reviews.csv", usecols=["Text", "Score"]
    )
    food = food.rename(columns={"Text": "text", "Score": "stars"})
    beauty = pd.concat(
        pd.read_csv(f, usecols=["review_text", "rating"])
        for f in sorted(
            glob.glob(str(corpus / "melissamonfared_sephora-skincare-reviews" / "reviews_*.csv"))
        )
    ).rename(columns={"review_text": "text", "rating": "stars"})
    vern = pd.read_json(flipkart, lines=True)
    vern = vern[vern.detected_language == "hi-en"].rename(columns={"rate": "stars"})[
        ["text", "stars"]
    ]
    return {"apparel": apparel, "food": food, "beauty": beauty, "vernacular": vern}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--flipkart", default="data/processed/vernacular_subset.jsonl")
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--exclude", default="", help="manifest.json of earlier pilots to avoid")
    ap.add_argument("--tag", default="pilot1")
    a = ap.parse_args()

    banned: set[str] = set()
    if a.exclude:
        for m in a.exclude.split(","):
            banned |= {r["sha"] for r in json.loads(Path(m).read_text(encoding="utf-8"))["items"]}
    rng = random.Random(a.seed)
    rows, manifest = [], []
    for stratum, df in load_all(Path(a.corpus), Path(a.flipkart)).items():
        lo, hi = LIMITS[stratum]
        seen: set[str] = set()
        pool = []
        for text, stars in zip(df["text"], df["stars"], strict=True):
            if not isinstance(text, str):
                continue
            t = clean(text)
            sha = hashlib.sha256(t.lower().encode()).hexdigest()[:16]
            if not lo <= len(t) <= hi or sha in seen or sha in banned:
                continue
            seen.add(sha)
            pool.append((t, float(stars), sha))
        for n, (t, stars, sha) in enumerate(rng.sample(pool, PER_STRATUM)):
            rid = f"{a.tag}-{stratum}-{n:03d}"
            rows.append(
                {"id": rid, "stratum": stratum, "category": stratum, "text": t, "stars": stars}
            )
            manifest.append(
                {"id": rid, "stratum": stratum, "sha": sha, "stars": stars, "chars": len(t)}
            )
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    man = {
        "tag": a.tag,
        "seed": a.seed,
        "per_stratum": PER_STRATUM,
        "sources": SOURCES,
        "items": manifest,
    }
    Path(a.manifest).parent.mkdir(parents=True, exist_ok=True)
    Path(a.manifest).write_text(json.dumps(man, indent=1) + "\n", encoding="utf-8")
    print(f"{len(rows)} items -> {out}; ids+hashes only -> {a.manifest}")


if __name__ == "__main__":
    main()
