"""Classify every deduped Flipkart review with the SAME production language detector
(app/core/language.py::detect_language) used by review-iq's actual extraction pipeline.

Reusing prod's detector (not a new heuristic) means the isolated vernacular subset reflects
exactly what the live system would route to the hi-en/hi prompt path — the benchmark measures
the thing that matters.

Output:
  data/processed/flipkart_classified.jsonl   (all rows + detected_language field)
  data/processed/vernacular_subset.jsonl     (hi-en + hi only)
  data/processed/language_counts.json
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.core.language import detect_language  # noqa: E402

IN_PATH = ROOT / "data" / "processed" / "flipkart_deduped.jsonl"
OUT_ALL = ROOT / "data" / "processed" / "flipkart_classified.jsonl"
OUT_VERNACULAR = ROOT / "data" / "processed" / "vernacular_subset.jsonl"
OUT_COUNTS = ROOT / "data" / "processed" / "language_counts.json"


def main() -> None:
    records = [
        json.loads(line)
        for line in IN_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    print(f"Classifying {len(records)} deduped reviews...")

    counts: Counter[str] = Counter()
    vernacular: list[dict] = []

    with OUT_ALL.open("w", encoding="utf-8") as fh:
        for i, rec in enumerate(records):
            lang = detect_language(rec["text"])
            rec["detected_language"] = lang
            counts[lang] += 1
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if lang in ("hi-en", "hi"):
                vernacular.append(rec)
            if (i + 1) % 25000 == 0:
                print(f"  ...{i + 1}/{len(records)}")

    with OUT_VERNACULAR.open("w", encoding="utf-8") as fh:
        for rec in vernacular:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    result = {
        "total_classified": len(records),
        "counts": dict(counts),
        "vernacular_total": len(vernacular),
        "vernacular_pct": round(100 * len(vernacular) / len(records), 2),
    }
    OUT_COUNTS.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print("\n" + "=" * 50)
    print("LANGUAGE DISTRIBUTION")
    print("=" * 50)
    for lang, n in counts.most_common():
        print(f"  {lang:8s}: {n:7d}  ({100 * n / len(records):.2f}%)")
    print(f"\nVernacular (hi-en + hi) total: {len(vernacular)} ({result['vernacular_pct']}%)")
    print(f"Written: {OUT_ALL.relative_to(ROOT)}")
    print(f"Written: {OUT_VERNACULAR.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
