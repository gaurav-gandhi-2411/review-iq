"""Ingest the 3 ODbL/DbCL-licensed Kaggle Flipkart datasets and dedupe across them.

Sources (all license-cleared 2026-07-07, see memory/project docs for the verification trail):
  - mansithummar67/flipkart-product-review-dataset  (ODbL-1.0, 194K)
  - niraliivaghani/flipkart-dataset                 (ODbL-1.0, 363K)
  - niraliivaghani/flipkart-product-customer-reviews-dataset (DbCL-1.0, sentiment-labeled)

Dedup key: SHA256 of normalized review text (Summary column — the actual free-text review
body; the "Review" column is just a short reaction label like "Awesome"). Normalization:
strip + collapse whitespace + lowercase, matching benchmark/data/leakage_check.py's convention.

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
        "cols": {"product": "ProductName", "rate": "Rate", "review": "Review", "summary": "Summary"},
        "license": "ODbL-1.0",
        "kaggle_ref": "mansithummar67/flipkart-product-review-dataset",
    },
    {
        "id": "niraliivaghani_363k",
        "path": RAW / "niraliivaghani_363k" / "Dataset.csv",
        "cols": {"product": "Product_name", "rate": "Rate", "review": "Review", "summary": "Summary"},
        "license": "ODbL-1.0",
        "kaggle_ref": "niraliivaghani/flipkart-dataset",
    },
    {
        "id": "niraliivaghani_sentiment",
        "path": RAW / "niraliivaghani_sentiment" / "Dataset-SA.csv",
        "cols": {"product": "product_name", "rate": "Rate", "review": "Review", "summary": "Summary"},
        "license": "DbCL-1.0",
        "kaggle_ref": "niraliivaghani/flipkart-product-customer-reviews-dataset",
        "extra_cols": {"uploader_sentiment": "Sentiment"},
    },
]


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
    rows: list[dict] = []
    with src["path"].open(encoding="utf-8-sig", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        for i, row in enumerate(reader):
            summary = _clean_field(row.get(src["cols"]["summary"]))
            if not summary or len(summary) < 3:
                continue
            rec = {
                "source": src["id"],
                "source_row": i,
                "product_name": _clean_field(row.get(src["cols"]["product"])),
                "rate": _clean_field(row.get(src["cols"]["rate"])),
                "review_label": _clean_field(row.get(src["cols"]["review"])),
                "text": summary,
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
