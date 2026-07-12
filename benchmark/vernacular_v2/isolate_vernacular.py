"""Build the corrected, more complete vernacular candidate pool.

Two real findings from the initial pass (app/core/language.py::detect_language applied raw):
  1. The "hi" bucket (32 records) is 100% false positives — every one is pure English text
     with a single stray Devanagari danda ("।", U+0964) at the end (mobile-keyboard/IME
     artifact), not genuine Hindi-script content. Zero genuine "hi" reviews exist in this
     corpus. This is a real prod detector edge case — flagged, NOT fixed here (no prompt/
     detector changes per the no-tuning instruction).
  2. A broader Hinglish vocabulary sweep over the "en"-classified bucket (spelling variants
     and words not in detect_language()'s regex: wasool/washul/wasul, faltu, ghatiya, etc.)
     surfaces ~500 additional likely-genuine vernacular reviews prod's router would currently
     send down the ENGLISH prompt path — i.e. prod likely undercounts real vernacular content.

This script produces the final candidate pool for human gold-labeling: the union of
(a) prod-detected hi-en (clean, spot-checked), and (b) the broader-sweep hits, each tagged
with how it was found so the gold-labeling step (which re-confirms LANG per record anyway)
can resolve any remaining ambiguity. Danda-only false "hi" records are excluded entirely.

Output: data/processed/vernacular_candidates.jsonl
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IN_PATH = ROOT / "data" / "processed" / "flipkart_classified.jsonl"
OUT_PATH = ROOT / "data" / "processed" / "vernacular_candidates.jsonl"
OUT_SUMMARY = ROOT / "data" / "processed" / "vernacular_isolation_summary.json"

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_DANDA_ONLY = {"।", "॥"}

# Broader sweep — spelling variants and words review-iq's prod detector doesn't currently
# catch. Deliberately separate from app/core/language.py: this is a data-isolation tool,
# not a change to the production language router.
_BROADER_HINGLISH = re.compile(
    r"\b(wasool|washul|wasul|faltu|bekaar|ghatiya|kharab|kharaab|badhiya|dhokha|dhoka|"
    r"chalega|sasta|mehnga|mehenga|milega|karna|karo)\b",
    re.IGNORECASE,
)


def main() -> None:
    records = [
        json.loads(line)
        for line in IN_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    prod_hien = []
    danda_false_positive = 0
    genuine_hi = []
    broader_sweep_hits = []

    for rec in records:
        lang = rec["detected_language"]
        text = rec["text"]

        if lang == "hi":
            deva_chars = _DEVANAGARI.findall(text)
            if deva_chars and all(c in _DANDA_ONLY for c in deva_chars):
                danda_false_positive += 1
                continue
            genuine_hi.append(rec)
            continue

        if lang == "hi-en":
            rec["detection_method"] = "prod_detector_hi-en"
            prod_hien.append(rec)
            continue

        if lang == "en" and _BROADER_HINGLISH.search(text):
            rec["detection_method"] = "broader_sweep_only"
            broader_sweep_hits.append(rec)

    candidates = prod_hien + genuine_hi + broader_sweep_hits
    for rec in genuine_hi:
        rec["detection_method"] = "prod_detector_hi_genuine"

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as fh:
        for rec in candidates:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    summary = {
        "total_corpus": len(records),
        "prod_detector_hi-en": len(prod_hien),
        "prod_detector_hi_raw": len(genuine_hi) + danda_false_positive,
        "prod_detector_hi_danda_false_positives": danda_false_positive,
        "prod_detector_hi_genuine": len(genuine_hi),
        "broader_sweep_additional": len(broader_sweep_hits),
        "final_candidate_pool": len(candidates),
        "final_candidate_pool_pct_of_corpus": round(100 * len(candidates) / len(records), 3),
    }
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print(f"\nWritten: {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
