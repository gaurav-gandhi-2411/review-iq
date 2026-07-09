"""Sample a stratified gold-labeling candidate set from the isolated vernacular pool
(+ a small English control slice), in the exact schema benchmark/data/label_helper.py
already expects — so GG can label with the existing, already-built interactive CLI tool,
no new tooling needed.

Stratification rationale:
  - prod_detector_hi-en (361 available): the "prod correctly flags this as vernacular" slice.
  - broader_sweep_only (234 available): the "prod's language router would currently MISS this
    and send it down the English prompt path" slice — small pool, high value, sample more of it
    proportionally so the benchmark can actually measure this failure mode.
  - en control (from the same deduped corpus, NOT from the candidate pool): lets v2.3's
    vernacular accuracy be compared against its English accuracy on the exact same domain/
    product-category mix, not just an isolated number.

Output: benchmark/vernacular_v2/candidates.jsonl (label_helper.py-compatible schema)
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES_PATH = ROOT / "data" / "processed" / "vernacular_candidates.jsonl"
CLASSIFIED_PATH = ROOT / "data" / "processed" / "flipkart_classified.jsonl"
OUT_PATH = ROOT / "benchmark" / "vernacular_v2" / "candidates.jsonl"

SEED = 42
N_HIEN_PROD = 100
N_BROADER_SWEEP = 60
N_EN_CONTROL = 50

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def _script_fraction(text: str) -> float:
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    deva = sum(1 for c in chars if _DEVANAGARI.match(c))
    return round(deva / len(chars), 4)


def _parse_rate(rate_str: str) -> int | None:
    try:
        v = int(float(rate_str))
        return v if 1 <= v <= 5 else None
    except (ValueError, TypeError):
        return None


def _to_label_helper_schema(rec: dict, slice_name: str) -> dict:
    return {
        "id": rec["id"],
        "slice": slice_name,
        "source": f"kaggle/{rec['source']}",
        "text": rec["text"],
        "rating": _parse_rate(rec.get("rate", "")),
        "char_len": len(rec["text"]),
        "language_detected": rec["detected_language"],
        "language_script_fraction": _script_fraction(rec["text"]),
        "detection_method": rec.get("detection_method", "en_control"),
        "product_name": rec.get("product_name", ""),
    }


def main() -> None:
    random.seed(SEED)
    candidates = [json.loads(line) for line in CANDIDATES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]

    prod_hien = [r for r in candidates if r["detection_method"] == "prod_detector_hi-en"]
    broader = [r for r in candidates if r["detection_method"] == "broader_sweep_only"]

    sample_hien = random.sample(prod_hien, min(N_HIEN_PROD, len(prod_hien)))
    sample_broader = random.sample(broader, min(N_BROADER_SWEEP, len(broader)))

    all_classified = [json.loads(line) for line in CLASSIFIED_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    en_pool = [r for r in all_classified if r["detected_language"] == "en"]
    sample_en = random.sample(en_pool, min(N_EN_CONTROL, len(en_pool)))

    out_records = (
        [_to_label_helper_schema(r, "hi-en") for r in sample_hien]
        + [_to_label_helper_schema(r, "hi-en-missed") for r in sample_broader]
        + [_to_label_helper_schema(r, "en") for r in sample_en]
    )
    random.shuffle(out_records)  # avoid labeling fatigue bias (all-vernacular-then-all-english)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as fh:
        for rec in out_records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"Candidate benchmark set: {len(out_records)} records")
    print(f"  hi-en (prod-detected):       {len(sample_hien)}  (of {len(prod_hien)} available)")
    print(f"  hi-en-missed (broader sweep): {len(sample_broader)}  (of {len(broader)} available)")
    print(f"  en (control):                {len(sample_en)}")
    print(f"\nWritten: {OUT_PATH.relative_to(ROOT)}")
    print("Ready for GG to label with: uv run python benchmark/data/label_helper.py")
    print("  (point candidates_path at benchmark/vernacular_v2/candidates.jsonl,")
    print("   gold_path at benchmark/vernacular_v2/gold_labels.jsonl)")


if __name__ == "__main__":
    main()
