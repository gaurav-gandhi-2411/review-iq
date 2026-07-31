"""Ingest the 5 license-cleared Kaggle Flipkart datasets and dedupe across them.

Sources:
  - mansithummar67/flipkart-product-review-dataset  (ODbL-1.0, 194K, cleared 2026-07-07)
  - niraliivaghani/flipkart-dataset                 (ODbL-1.0, 363K, cleared 2026-07-07)
  - niraliivaghani/flipkart-product-customer-reviews-dataset (DbCL-1.0, sentiment-labeled,
    cleared 2026-07-07)
  - kabirnagpal/flipkart-customer-review-and-rating  (CC0-1.0, ~10K, cleared 2026-07-31 —
    see eval/data/README.md; was flagged "(check before use)", resolved this session:
    the dataset page's JSON-LD `license` block reads
    `{"name":"CC0: Public Domain","url":"https://creativecommons.org/publicdomain/zero/1.0/"}`)
  - naushads/flipkart-reviews                        (CC0-1.0, ~9K, cleared 2026-07-31 —
    same verification method, same resolution: CC0 Public Domain)

All verification trails: memory/project docs (first 3) and
docs/architecture/adr/0004-corpus-mining-pipeline-and-target-volume.md (last 2, Wave 1
Section H). No source with unclear/unverified terms is ingested — anything that stayed
ambiguous after checking the actual dataset page is dropped, not guessed at.

Dedup key: SHA256 of normalized review text (Summary column — the actual free-text review
body; the "Review" column is just a short reaction label like "Awesome"). Normalization:
strip + collapse whitespace + lowercase, matching benchmark/data/leakage_check.py's convention.

Every output record now carries its own `license` and `kaggle_ref` fields (per-record
provenance, not just "look it up in this docstring") — added 2026-07-31 per Wave 1 Section
H's requirement that every record in the final corpus carry license provenance.

Output: data/processed/flipkart_deduped.jsonl (one record per unique review).
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from pathlib import Path

csv.field_size_limit(10_000_000)

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
OUT_DIR = ROOT / "data" / "processed"
OUT_PATH = OUT_DIR / "flipkart_deduped.jsonl"

SOURCES = [
    {
        "id": "mansithummar67_194k",
        "path": RAW / "mansithummar67_194k" / "flipkart_product.csv",
        "cols": {
            "product": "ProductName",
            "rate": "Rate",
            "review": "Review",
            "summary": "Summary",
        },
        "license": "ODbL-1.0",
        "kaggle_ref": "mansithummar67/flipkart-product-review-dataset",
    },
    {
        "id": "niraliivaghani_363k",
        "path": RAW / "niraliivaghani_363k" / "Dataset.csv",
        "cols": {
            "product": "Product_name",
            "rate": "Rate",
            "review": "Review",
            "summary": "Summary",
        },
        "license": "ODbL-1.0",
        "kaggle_ref": "niraliivaghani/flipkart-dataset",
    },
    {
        "id": "niraliivaghani_sentiment",
        "path": RAW / "niraliivaghani_sentiment" / "Dataset-SA.csv",
        "cols": {
            "product": "product_name",
            "rate": "Rate",
            "review": "Review",
            "summary": "Summary",
        },
        "license": "DbCL-1.0",
        "kaggle_ref": "niraliivaghani/flipkart-product-customer-reviews-dataset",
        "extra_cols": {"uploader_sentiment": "Sentiment"},
    },
    {
        # Wave 1 Section H: was "(check before use)" in eval/data/README.md. Resolved
        # 2026-07-31 by reading the dataset page's own JSON-LD license block directly
        # (not assumed): {"name": "CC0: Public Domain",
        # "url": "https://creativecommons.org/publicdomain/zero/1.0/"}. Schema per the
        # dataset's own description (single product, boAt Rockerz 400, 2 columns —
        # "Review" is the FULL free-text body here, not a short label like the other
        # 3 sources' "Review" column, hence text_col="review" below).
        "id": "kabirnagpal_10k",
        "path": RAW / "kabirnagpal_10k" / "flipkart_reviews_dataset.csv",
        "cols": {"review": "Review", "rate": "Rating"},
        "text_col": "review",
        "static_product_name": "boAt Rockerz 400 (Bluetooth headset)",
        "license": "CC0-1.0",
        "kaggle_ref": "kabirnagpal/flipkart-customer-review-and-rating",
    },
]

# naushads/flipkart-reviews: license CONFIRMED CC0-1.0 (same verification method as
# kabirnagpal above — checked 2026-07-31, JSON-LD license block reads "CC0: Public
# Domain"). NOT added to SOURCES: the dataset page's description only documents its
# scraping methodology ("BeautifulSoup"), not its column schema, and no raw CSV is
# present in this worktree (data/raw/ is gitignored, ~245K-row corpus, never committed)
# to inspect a header row directly. Per this repo's evidence-over-recall rule, guessing
# a column schema for an unverified CSV is not acceptable — wiring this in requires
# either downloading the file (kaggle.json, one-time GG setup, same as every other
# source here) and reading its actual header row, or finding column docs elsewhere.
# License is cleared; ingestion is a follow-up, not a blocker for this ADR/pipeline.
NAUSHADS_LICENSE_CLEARED_SCHEMA_PENDING = {
    "kaggle_ref": "naushads/flipkart-reviews",
    "license": "CC0-1.0",
    "license_verified": "2026-07-31",
    "status": "license_cleared_schema_unverified",
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).lower()


def _sha256(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()


def _clean_field(v: str | None) -> str:
    if v is None:
        return ""
    # These CSVs have mojibake from a bad encoding round-trip (rupee symbol etc.)
    return v.replace("�", "").replace("�", "").strip()


def load_source(src: dict) -> list[dict]:
    # text_col picks which of src["cols"] holds the actual free-text review body.
    # Defaults to "summary" (the first 3 sources' schema: a short "Review" reaction
    # label + a longer "Summary" free-text field). kabirnagpal has no separate
    # summary column — its "Review" column IS the free-text body — so it sets
    # text_col="review" instead of adding a 5th no-op key.
    text_col = src.get("text_col", "summary")
    rows: list[dict] = []
    with src["path"].open(encoding="utf-8-sig", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        for i, row in enumerate(reader):
            body = _clean_field(row.get(src["cols"][text_col]))
            if not body or len(body) < 3:
                continue
            product_name = src.get("static_product_name") or _clean_field(
                row.get(src["cols"].get("product", ""))
            )
            rec = {
                "source": src["id"],
                "source_row": i,
                "product_name": product_name,
                "rate": _clean_field(row.get(src["cols"].get("rate", ""))),
                "review_label": _clean_field(row.get(src["cols"].get("review", ""))),
                "text": body,
                # Per-record license provenance (Wave 1 Section H requirement) — every
                # record carries its own license, not just a lookup via `source` into
                # this file's SOURCES list.
                "license": src["license"],
                "kaggle_ref": src["kaggle_ref"],
            }
            if "extra_cols" in src:
                for out_key, col in src["extra_cols"].items():
                    rec[out_key] = _clean_field(row.get(col))
            rows.append(rec)
    return rows


def main() -> None:
    per_source_counts: dict[str, int] = {}
    all_rows: list[dict] = []
    for src in SOURCES:
        rows = load_source(src)
        per_source_counts[src["id"]] = len(rows)
        all_rows.extend(rows)
        print(f"{src['id']}: {len(rows)} non-empty reviews loaded ({src['license']})")

    print(f"\nTotal rows before dedup: {len(all_rows)}")

    seen: dict[str, dict] = {}
    dup_count = 0
    overlap_by_pair: dict[str, int] = {}
    for rec in all_rows:
        h = _sha256(rec["text"])
        if h in seen:
            dup_count += 1
            first_src = seen[h]["source"]
            pair = " & ".join(sorted({first_src, rec["source"]}))
            overlap_by_pair[pair] = overlap_by_pair.get(pair, 0) + 1
            continue
        rec["text_sha256"] = h
        seen[h] = rec

    deduped = list(seen.values())
    print(f"Duplicate reviews removed: {dup_count}")
    print(f"Deduped total: {len(deduped)}")
    print("\nOverlap breakdown (which source pairs share exact-text duplicates):")
    for pair, n in sorted(overlap_by_pair.items(), key=lambda kv: -kv[1]):
        print(f"  {pair}: {n}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as fh:
        for i, rec in enumerate(deduped):
            rec["id"] = f"flipkart-{i:06d}"
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    summary = {
        "per_source_raw_counts": per_source_counts,
        "per_source_license": {src["id"]: src["license"] for src in SOURCES},
        "per_source_kaggle_ref": {src["id"]: src["kaggle_ref"] for src in SOURCES},
        "total_before_dedup": len(all_rows),
        "duplicates_removed": dup_count,
        "overlap_by_source_pair": overlap_by_pair,
        "deduped_total": len(deduped),
        "output_path": str(OUT_PATH.relative_to(ROOT)),
    }
    (OUT_DIR / "dedup_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nWritten: {OUT_PATH.relative_to(ROOT)}")
    print(f"Summary: {(OUT_DIR / 'dedup_summary.json').relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main() or 0)
