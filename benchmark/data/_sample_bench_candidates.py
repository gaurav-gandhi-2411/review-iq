"""
Benchmark candidate sampler — produces the candidate list for human SENT+URG+LANG review.

Slices:
  en      — Flipkart held-out, e-commerce reviews
  hi-en   — Flipkart held-out, e-commerce, code-mixed Hinglish

Outputs benchmark/dataset/candidates_for_review.jsonl
Each record: id, slice, source, text, char_len, leakage (bool), leakage_source (str|None)
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Hinglish marker regex — require at least one genuinely Hindi-origin word
# ---------------------------------------------------------------------------
_HINGLISH_MARKERS = re.compile(
    r"\b(nahi|nhi|accha|acchi|bahut|zyada|kafi|acha|mujhe|yeh|boleto|"
    r"iske|iski|iska|paisa|vasool|vasul|bakwaas|bakwas|bilkul|ekdum|sahi|mast|"
    r"mera|mere|meri|aap|main|tum|hum|woh|bhi|toh|lekin|gajab|zabardast|"
    r"bole to|jada|bekar|khrab|khraab|bss|beech|gayab|dhoka|jhakkas|"
    r"yarr|yaar|bhai|kya|tha|thi|hai|ho|kar|karo|karta|karti|se|ke|ka|ko)\b",
    re.IGNORECASE,
)

# Minimum char length for a usable benchmark item
_MIN_LEN = 45

# ---------------------------------------------------------------------------
# Load CI fixture hashes for leakage check
# ---------------------------------------------------------------------------
from benchmark.data.leakage_check import LeakageChecker  # noqa: E402

checker = LeakageChecker.from_eval_dir(ROOT / "eval")
print(f"LeakageChecker: {checker.fixture_count} CI fixture texts indexed.")

# ---------------------------------------------------------------------------
# Load flipkart_candidates.jsonl
# ---------------------------------------------------------------------------
src_path = ROOT / "eval" / "data" / "flipkart_candidates.jsonl"
all_records: list[dict] = []
with src_path.open(encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if line:
            all_records.append(json.loads(line))

print(f"Raw flipkart records: {len(all_records)}")

# ---------------------------------------------------------------------------
# Slice: en
# ---------------------------------------------------------------------------
en_raw = [
    r for r in all_records
    if r.get("language") == "en" and int(r.get("char_len", 0)) >= _MIN_LEN
]
# Sort longest-first (richer reviews), then cap at 60 to run leakage on
en_raw.sort(key=lambda r: int(r.get("char_len", 0)), reverse=True)
en_candidates: list[dict] = []
for r in en_raw[:60]:
    text = r["text"]
    leaked = checker.is_leaked(text)
    en_candidates.append({
        "id": f"bench-en-{len(en_candidates)+1:03d}",
        "slice": "en",
        "domain": "e-commerce reviews (Flipkart)",
        "source": r.get("source", "flipkart"),
        "text": text,
        "char_len": int(r.get("char_len", len(text))),
        "rating": r.get("rating"),
        "leakage": leaked is not None,
        "leakage_source": leaked,
    })

en_clean = [c for c in en_candidates if not c["leakage"]]
print(f"\nen slice: {len(en_raw)} candidates >=45ch; top-60 checked; {len(en_clean)} clean after leakage.")

# Take top 22 clean (size to honest yield; user will trim further)
en_final = en_clean[:22]
print(f"en final (top 22 by length, leakage-clean): {len(en_final)}")

# ---------------------------------------------------------------------------
# Slice: hi-en (Flipkart, e-commerce, code-mixed)
# ---------------------------------------------------------------------------
hien_raw = [
    r for r in all_records
    if r.get("language") == "hi-en"
    and int(r.get("char_len", 0)) >= _MIN_LEN
    and _HINGLISH_MARKERS.search(r["text"])
]
# Sort by length (prefer 80-400 chars)
hien_raw.sort(key=lambda r: int(r.get("char_len", 0)), reverse=True)

hien_candidates: list[dict] = []
for r in hien_raw:
    text = r["text"]
    leaked = checker.is_leaked(text)
    hien_candidates.append({
        "id": f"bench-hien-{len(hien_candidates)+1:03d}",
        "slice": "hi-en",
        "domain": "e-commerce reviews (Flipkart, held-out)",
        "source": r.get("source", "flipkart"),
        "text": text,
        "char_len": int(r.get("char_len", len(text))),
        "rating": r.get("rating"),
        "leakage": leaked is not None,
        "leakage_source": leaked,
    })

hien_clean = [c for c in hien_candidates if not c["leakage"]]
print(f"\nhi-en slice: {len(hien_raw)} genuine Hinglish >=45ch; {len(hien_clean)} clean after leakage.")
hien_final = hien_clean  # take all clean (target 15-18; we need honest yield)
print(f"hi-en final (all clean, genuine): {len(hien_final)}")

# ---------------------------------------------------------------------------
# Write output
# ---------------------------------------------------------------------------
out_path = ROOT / "benchmark" / "dataset" / "candidates_for_review.jsonl"
out_path.parent.mkdir(parents=True, exist_ok=True)

all_final = en_final + hien_final
with out_path.open("w", encoding="utf-8") as fh:
    for rec in all_final:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

print(f"\nTotal candidates written: {len(all_final)} → {out_path}")

# ---------------------------------------------------------------------------
# Leakage summary
# ---------------------------------------------------------------------------
leaked_en = [c for c in en_candidates if c["leakage"]]
leaked_hien = [c for c in hien_candidates if c["leakage"]]
print(f"\nLeakage report:")
print(f"  en: {len(leaked_en)} leaked (excluded)")
for item in leaked_en[:5]:
    print(f"    [{item['id']}] matched {item['leakage_source']!r}: {item['text'][:60]!r}")
print(f"  hi-en: {len(leaked_hien)} leaked (excluded)")
for item in leaked_hien[:5]:
    print(f"    [{item['id']}] matched {item['leakage_source']!r}: {item['text'][:60]!r}")
