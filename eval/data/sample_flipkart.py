"""
Flipkart review sampler — downloads dataset from Kaggle, runs language classification,
writes candidates to eval/data/flipkart_candidates.jsonl.

Usage:
    uv run python eval/data/sample_flipkart.py

Prereqs:
    - Kaggle account + API token (see eval/data/README.md)
    - pip install kaggle lingua-language-detector
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

SEED = 42

# Kaggle dataset refs (primary + supplements)
_DATASETS = [
    ("niraliivaghani", "flipkart-product-customer-reviews-dataset", "Dataset-SA.csv", "Review", "product_name", "Rate"),
    ("kabirnagpal", "flipkart-customer-review-and-rating", "data.csv", "review", None, "rating"),
    ("naushads", "flipkart-reviews", "flipkart_reviews_dataset.csv", "review", "product_title", "rating"),
]

_RAW_DIR = Path(__file__).parent / "raw"
_OUT = Path(__file__).parent / "flipkart_candidates.jsonl"

# Hinglish detection patterns — Roman-script Hindi words common in code-mixed reviews
_STRONG_HINGLISH = re.compile(
    r"\b(nahi|nhi|nahin|bahut|bohot|bhot|mujhe|mera|meri|yaar|paisa\s+vasool|vasool|"
    r"bakwaas|bakwas|ekdum|sahi\s+hai|bilkul|kaafi|kafi|thoda|thodi|jyada|zyada|"
    r"acha\s+hai|achha\s+hai|mast\s+(product|buy|item)|hai\s+na|kya\s+baat|"
    r"bindaas|jhakkas|zabardast|iska|iski|iske)\b",
    re.IGNORECASE,
)

_WEAK_HINGLISH = re.compile(
    r"\b(hai|hain|mast|sahi|toh|yeh|ye\b(?!\s+another)|aur|bhi|"
    r"superb|paisa|paise|value\s+for\s+money)\b",
    re.IGNORECASE,
)

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def _detect_language(text: str) -> str:
    """Heuristic language classifier: en | hi-en | hi | other."""
    text = str(text).strip()
    if len(text) < 8:
        return "other"

    if _DEVANAGARI.search(text):
        return "hi"

    words = set(re.sub(r"[^a-zA-Z\s]", " ", text.lower()).split())

    if _STRONG_HINGLISH.search(text):
        return "hi-en"

    # Require multiple weak markers to reduce false positives
    weak_hits = len([m for m in _WEAK_HINGLISH.findall(text)])
    if weak_hits >= 3:
        return "hi-en"

    return "en"


def _download(user: str, dataset: str, dest_dir: Path) -> None:
    """Download dataset from Kaggle if not already cached."""
    if not (dest_dir / dataset).exists():
        import subprocess
        dest_dir.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {user}/{dataset}...")
        result = subprocess.run(
            ["kaggle", "datasets", "download", "-d", f"{user}/{dataset}",
             "-p", str(dest_dir / dataset), "--unzip"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"  Warning: {result.stderr.strip()[:200]}")
        else:
            print(f"  Downloaded to {dest_dir / dataset}/")
    else:
        print(f"  Using cached {dataset}/")


def main() -> None:
    try:
        import pandas as pd
    except ImportError:
        print("ERROR: pandas not installed. Run: uv add --dev pandas")
        sys.exit(1)

    candidates: list[dict] = []

    for user, dataset, filename, review_col, product_col, rating_col in _DATASETS:
        dest = _RAW_DIR / dataset
        _download(user, dataset, _RAW_DIR)

        csv_path = dest / filename
        if not csv_path.exists():
            print(f"  Warning: {csv_path} not found, skipping")
            continue

        try:
            df = pd.read_csv(csv_path, on_bad_lines="skip", encoding="utf-8", encoding_errors="replace")
        except Exception as exc:
            print(f"  Warning: could not read {csv_path}: {exc}")
            continue

        df = df.dropna(subset=[review_col])
        print(f"  {dataset}: {len(df)} rows")

        for _, row in df.iterrows():
            text = str(row[review_col]).strip()
            if len(text) < 20 or len(text) > 800:
                continue

            lang = _detect_language(text)
            product = str(row[product_col]).strip() if product_col and product_col in row else "unknown"
            try:
                rating = int(float(row[rating_col])) if rating_col and rating_col in row else None
            except (ValueError, TypeError):
                rating = None

            candidates.append({
                "source": f"flipkart/{user}/{dataset}",
                "text": text,
                "product": product[:80] if product else "unknown",
                "rating": rating,
                "language": lang,
                "char_len": len(text),
            })

    # Deterministic dedup + sort
    seen: set[str] = set()
    unique: list[dict] = []
    for c in candidates:
        key = c["text"][:100]
        if key not in seen:
            seen.add(key)
            unique.append(c)

    by_lang: dict[str, int] = {}
    for c in unique:
        by_lang[c["language"]] = by_lang.get(c["language"], 0) + 1

    print(f"\nTotal unique candidates: {len(unique)}")
    print("Language breakdown:", by_lang)

    hinglish = [c for c in unique if c["language"] == "hi-en"]
    hindi = [c for c in unique if c["language"] == "hi"]
    english = [c for c in unique if c["language"] == "en"]

    print(f"\nHinglish (hi-en): {len(hinglish)}")
    print(f"Hindi (hi):       {len(hindi)}")
    print(f"English (en):     {len(english)}")

    # Write all candidates
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    with _OUT.open("w", encoding="utf-8") as f:
        for c in unique:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    print(f"\nWrote {len(unique)} candidates to {_OUT}")
    print(f"  Run: uv run python eval/label-helper.py  to start labeling")

    if len(hinglish) < 15:
        print(f"\nWARNING: Only {len(hinglish)} Hinglish candidates found (need at least 15).")
        print("  The available Kaggle Flipkart datasets are primarily English.")
        print("  The label-helper will use all available candidates.")
    elif len(hinglish) < 50:
        print(f"\nNOTE: {len(hinglish)} Hinglish candidates (plan expected 600+).")
        print("  Available public Flipkart datasets are English-heavy. User will have")
        print("  less selectivity when labeling but can still pick 15 fixtures.")


if __name__ == "__main__":
    main()
