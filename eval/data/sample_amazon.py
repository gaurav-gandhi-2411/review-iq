"""
Amazon Reviews 2023 sampler — downloads a category from HuggingFace (McAuley Lab),
classifies language, writes English candidates to eval/data/amazon_candidates.jsonl.

Usage:
    uv run python eval/data/sample_amazon.py [--category CATEGORY] [--n N]

Default category: All_Beauty (small, ~50k reviews)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SEED = 42
DEFAULT_N = 3000
DEFAULT_CATEGORY = "All_Beauty"

_HF_REPO = "McAuley-Lab/Amazon-Reviews-2023"
_OUT = Path(__file__).parent / "amazon_candidates.jsonl"

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
# Common Hinglish markers — if detected in Amazon reviews, classify as hi-en
_HINGLISH = re.compile(
    r"\b(nahi|nhi|bahut|bohot|mujhe|yaar|vasool|bakwaas|ekdum|bilkul|paisa|paise|"
    r"kaafi|jyada|zyada|thoda|mast\s+(product|buy|item)|achha|acha)\b",
    re.IGNORECASE,
)


def _detect_language(text: str) -> str:
    text = str(text).strip()
    if len(text) < 8:
        return "other"
    if _DEVANAGARI.search(text):
        return "hi"
    if _HINGLISH.search(text):
        return "hi-en"
    return "en"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", default=DEFAULT_CATEGORY,
                        help=f"Amazon review category slug (default: {DEFAULT_CATEGORY})")
    parser.add_argument("--n", type=int, default=DEFAULT_N,
                        help=f"Number of reviews to sample (default: {DEFAULT_N})")
    args = parser.parse_args()

    try:
        from datasets import load_dataset  # type: ignore[import]
    except ImportError:
        print("ERROR: `datasets` not installed. Run: uv add --dev datasets")
        sys.exit(1)

    print(f"Loading {_HF_REPO} ({args.category}) — streaming, no full download...")
    try:
        dataset = load_dataset(
            _HF_REPO,
            f"raw_review_{args.category}",
            split="full",
            streaming=True,
            trust_remote_code=True,
        )
    except Exception as exc:
        print(f"ERROR loading dataset: {exc}")
        print(f"  Try a different category, e.g.: python eval/data/sample_amazon.py --category Electronics")
        sys.exit(1)

    import random
    rng = random.Random(SEED)
    candidates: list[dict] = []
    seen: set[str] = set()
    count = 0

    print(f"Streaming up to {args.n * 5} rows to sample {args.n} reviews...")
    for row in dataset:
        count += 1
        if count > args.n * 5:
            break

        text = str(row.get("text", "")).strip()
        if not text or len(text) < 20 or len(text) > 1000:
            continue

        key = text[:80]
        if key in seen:
            continue
        seen.add(key)

        try:
            rating = int(float(row.get("rating", 0)))
        except (ValueError, TypeError):
            rating = None

        lang = _detect_language(text)

        candidates.append({
            "source": f"amazon/{args.category}",
            "text": text,
            "product": str(row.get("parent_asin", "unknown"))[:40],
            "rating": rating,
            "language": lang,
            "char_len": len(text),
        })

        if len(candidates) >= args.n:
            break

    # Deterministic shuffle
    rng.shuffle(candidates)

    by_lang: dict[str, int] = {}
    for c in candidates:
        by_lang[c["language"]] = by_lang.get(c["language"], 0) + 1

    print(f"\nSampled {len(candidates)} Amazon reviews ({args.category})")
    print("Language breakdown:", by_lang)

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    with _OUT.open("w", encoding="utf-8") as f:
        for c in candidates:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    print(f"Wrote {len(candidates)} candidates to {_OUT}")
    print("These are primarily English — used for expanding the English eval set breadth.")


if __name__ == "__main__":
    main()
