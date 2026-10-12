"""Product ids for the sealed set, recovered by matching cleaned-text hashes back to the raw corpora (S23).

`scale.jsonl` carries no product id, but every source corpus has one: Clothing ID (apparel), ProductId (food),
product_id (Sephora beauty) and product_name (Flipkart, the vernacular stratum). The raw rows are read again with the
SAME loaders' filters as `engine.labelling.pilot_sample.load_all`, hashed with `scale_sample.text_sha(clean(text))`,
and an item whose hash is found gets the cluster id `<stratum>:<product id>`. An item that cannot be matched keeps
no id (the bootstrap then treats it as its own cluster, i.e. item-level, and the report says so).
Only hashes and ids leave this module: no review text.

A text that occurs under several products in a stratum was kept once by the sampler (exact-text dedup, first row
wins); this module follows the same first-row rule and counts how many sealed hashes had more than one product.
"""

from __future__ import annotations

import glob
from pathlib import Path

import pandas as pd

from engine.labelling.pilot_sample import clean, load_all
from engine.labelling.scale_sample import text_sha


def load_with_ids(corpus: Path, flipkart: Path) -> dict[str, pd.DataFrame]:
    """stratum -> frame with columns text, stars, pid. Same filters and renames as `load_all`."""
    apparel = pd.read_csv(next(corpus.glob("nicapotato_*/Womens*.csv")))
    apparel = apparel.rename(
        columns={"Review Text": "text", "Rating": "stars", "Clothing ID": "pid"}
    )
    food = pd.read_csv(
        corpus / "snap_amazon-fine-food-reviews" / "Reviews.csv",
        usecols=["Text", "Score", "ProductId"],
    ).rename(columns={"Text": "text", "Score": "stars", "ProductId": "pid"})
    beauty = pd.concat(
        pd.read_csv(f, usecols=["review_text", "rating", "product_id"])
        for f in sorted(
            glob.glob(str(corpus / "melissamonfared_sephora-skincare-reviews" / "reviews_*.csv"))
        )
    ).rename(columns={"review_text": "text", "rating": "stars", "product_id": "pid"})
    vern = pd.read_json(flipkart, lines=True)
    vern = vern[vern.detected_language == "hi-en"].rename(
        columns={"rate": "stars", "product_name": "pid"}
    )
    return {
        s: d[["text", "stars", "pid"]]
        for s, d in {"apparel": apparel, "food": food, "beauty": beauty, "vernacular": vern}.items()
    }


def assert_same_rows_as_load_all(corpus: Path, flipkart: Path, frames: dict) -> None:
    """The id-bearing frames must be row-for-row the frames the sampler used, else matching would be wrong."""
    for s, d in load_all(corpus, flipkart).items():
        cols = ["text", "stars"]
        a, b = d[cols].reset_index(drop=True), frames[s][cols].reset_index(drop=True)
        if not a.equals(b):
            raise RuntimeError(
                f"loader drift in stratum {s}: rows differ from pilot_sample.load_all"
            )


def cluster_map(
    frames: dict[str, pd.DataFrame], wanted: dict[str, str]
) -> tuple[dict[str, str], dict]:
    """wanted = {sha: stratum}. -> ({sha: cluster id}, stats). First matching row wins (the sampler's dedup rule)."""
    out: dict[str, str] = {}
    multi: dict[str, set] = {}
    for stratum, d in frames.items():
        for text, pid in zip(d["text"], d["pid"], strict=True):
            if not isinstance(text, str):
                continue
            sha = text_sha(clean(text))
            if wanted.get(sha) != stratum:
                continue
            cid = f"{stratum}:{pid}"
            if sha not in out:
                out[sha] = cid
            elif out[sha] != cid:
                multi.setdefault(sha, {out[sha]}).add(cid)
    stats = {
        "n_wanted": len(wanted), "n_matched": len(out), "n_text_under_several_products": len(multi),
        "n_products": len(set(out.values())),
    }  # fmt: skip
    return out, stats


def main() -> None:
    import argparse
    import hashlib
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--flipkart", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    man = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    test = [r for r in man["items"] if r["split"] == "test"]
    frames = load_with_ids(Path(a.corpus), Path(a.flipkart))
    assert_same_rows_as_load_all(Path(a.corpus), Path(a.flipkart), frames)
    by_sha, stats = cluster_map(frames, {r["sha"]: r["stratum"] for r in test})
    # product ids are hashed: the file is for grouping only and must not leak catalogue contents
    cid = lambda c: hashlib.sha256(c.encode()).hexdigest()[:12]  # noqa: E731
    clusters = {r["id"]: cid(by_sha[r["sha"]]) for r in test if r["sha"] in by_sha}
    per_stratum = {}
    for s in sorted({r["stratum"] for r in test}):
        ids = [r["id"] for r in test if r["stratum"] == s]
        got = [clusters[i] for i in ids if i in clusters]
        per_stratum[s] = {
            "n_sealed": len(ids),
            "n_with_product_id": len(got),
            "n_products": len(set(got)),
        }
    Path(a.out).write_text(
        json.dumps({"stats": stats, "per_stratum": per_stratum, "clusters": clusters}, indent=0)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"stats": stats, "per_stratum": per_stratum}))


if __name__ == "__main__":
    main()
