"""
Interactive CLI for human SENT + URG + LANG labeling of benchmark candidates.

Usage:
    uv run python benchmark/data/label_helper.py

Flow per candidate:
  1. Show review text, detected language, star rating (as a hint, not the answer)
  2. SENT  — human assigns: positive / neutral / negative
             star rating shown as a HINT only; override freely
  3. URG   — human assigns: low / medium / high
             rubric printed on each entry
  4. LANG  — pre-populated from detection; human confirms or corrects

Resumable: already-labeled IDs are skipped. Labels saved after every entry.

Input file:  benchmark/dataset/candidates.jsonl
Output file: benchmark/dataset/gold_labels.jsonl
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants and rubrics
# ---------------------------------------------------------------------------

SENT_LABELS = ("positive", "neutral", "negative")
SENT_SHORTCUTS = {"p": "positive", "n": "neutral", "neg": "negative", "1": "positive",
                  "2": "neutral", "3": "negative"}

URG_LABELS = ("low", "medium", "high")
URG_SHORTCUTS = {"l": "low", "m": "medium", "h": "high", "1": "low", "2": "medium",
                 "3": "high"}

LANG_LABELS = ("en", "hi", "hi-en")
LANG_SHORTCUTS = {"e": "en", "h": "hi", "he": "hi-en", "1": "en", "2": "hi", "3": "hi-en"}

SENT_HINT_FROM_STARS: dict[int, str] = {1: "negative", 2: "negative", 3: "neutral",
                                         4: "positive", 5: "positive"}

URG_RUBRIC = """\
  URG rubric:
    HIGH   — explicit refund/return/replacement demand OR health/safety risk
    MEDIUM — quality defect or significant complaint, no explicit escalation
    LOW    — positive review, neutral feedback, praise, or minor quibble"""

SENT_RUBRIC = """\
  SENT rubric:
    POSITIVE — overall tone is appreciative / satisfied (may have minor complaints)
    NEUTRAL  — mixed or purely factual, no clear positive or negative lean
    NEGATIVE — overall tone is dissatisfied / critical"""


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def _prompt(prompt_text: str, valid: tuple[str, ...], shortcuts: dict[str, str]) -> str:
    """Prompt until a valid label is entered. Empty input → skip (return '')."""
    shortcut_hint = "/".join(k for k in shortcuts if not k.isdigit())
    while True:
        raw = input(f"  {prompt_text} [{shortcut_hint}] or ENTER to skip: ").strip().lower()
        if raw == "":
            return ""
        if raw in valid:
            return raw
        if raw in shortcuts:
            return shortcuts[raw]
        print(f"    Invalid. Choose one of: {', '.join(valid)}")


def _separator() -> None:
    print("\n" + "─" * 70)


def _display_candidate(idx: int, total: int, cand: dict[str, object]) -> None:
    _separator()
    print(f"[{idx}/{total}] ID: {cand['id']}")
    print(f"Source:   {cand.get('source', 'unknown')}")
    rating = cand.get("rating")
    rating_str = f"{'★' * int(rating)}{'☆' * (5 - int(rating))} ({rating}/5)" if rating else "N/A"
    print(f"Rating:   {rating_str}")
    lang = cand.get("language_detected", "?")
    frac = cand.get("language_script_fraction", "?")
    print(f"Language: {lang}  (Devanagari fraction: {frac})")
    print()
    print(f"REVIEW TEXT:\n{cand.get('text', '')}")
    print()


# ---------------------------------------------------------------------------
# Main labeling loop
# ---------------------------------------------------------------------------


def load_candidates(path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def load_existing_labels(path: Path) -> dict[str, dict[str, object]]:
    """Return {id: gold_record} for already-labeled entries."""
    if not path.exists():
        return {}
    labeled: dict[str, dict[str, object]] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rec = json.loads(line)
                labeled[str(rec["id"])] = rec
    return labeled


def append_label(path: Path, record: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def run(candidates_path: Path, gold_path: Path) -> None:
    candidates = load_candidates(candidates_path)
    existing = load_existing_labels(gold_path)
    remaining = [c for c in candidates if str(c["id"]) not in existing]

    print(f"\n{'='*70}")
    print("review-iq Benchmark — Human Labeling Session")
    print(f"{'='*70}")
    print(f"Total candidates: {len(candidates)}")
    print(f"Already labeled:  {len(existing)}")
    print(f"Remaining:        {len(remaining)}")
    if not remaining:
        print("\nAll candidates labeled. Nothing to do.")
        return
    print("\nPress ENTER to skip a review (it will reappear next session).")
    print("Type 'q' at any prompt to quit and save progress.")
    input("Press ENTER to begin labeling...")

    labeled_this_session = 0
    skipped_this_session = 0

    for i, cand in enumerate(remaining, start=1):
        _display_candidate(i, len(remaining), cand)

        # --- LANG ---
        lang_detected = str(cand.get("language_detected", "en"))
        print(f"  Detected language: {lang_detected}")
        lang_raw = input(
            f"  LANG — confirm ({'/'.join(LANG_LABELS)}) or ENTER to accept detected: "
        ).strip().lower()
        if lang_raw == "q":
            print(f"\nQuitting. Labeled {labeled_this_session} this session.")
            break
        if lang_raw in LANG_LABELS:
            lang = lang_raw
        elif lang_raw in LANG_SHORTCUTS:
            lang = LANG_SHORTCUTS[lang_raw]
        elif lang_raw == "":
            lang = lang_detected
        else:
            print(f"    Unrecognised — keeping detected: {lang_detected}")
            lang = lang_detected

        # --- SENT ---
        print()
        print(SENT_RUBRIC)
        rating = cand.get("rating")
        if isinstance(rating, (int, float)) and rating:
            hint = SENT_HINT_FROM_STARS.get(int(rating), "unknown")
            print(f"  [Star-rating hint → {hint}] Override freely based on TEXT.")
        sent = _prompt("SENT", SENT_LABELS, SENT_SHORTCUTS)
        if sent == "q":
            print(f"\nQuitting. Labeled {labeled_this_session} this session.")
            break
        if sent == "":
            print("  Skipped.")
            skipped_this_session += 1
            continue

        # --- URG ---
        print()
        print(URG_RUBRIC)
        urg = _prompt("URG", URG_LABELS, URG_SHORTCUTS)
        if urg == "q":
            print(f"\nQuitting. Labeled {labeled_this_session} this session.")
            break
        if urg == "":
            print("  Skipped.")
            skipped_this_session += 1
            continue

        # Confirm before saving
        print(f"\n  → LANG={lang}  SENT={sent}  URG={urg}")
        confirm = input("  Save? [ENTER=yes / r=redo]: ").strip().lower()
        if confirm == "r":
            print("  Redo — re-labeling this candidate.")
            remaining.insert(i, cand)
            continue

        record: dict[str, object] = {
            "id": cand["id"],
            "source": cand.get("source"),
            "text": cand.get("text"),
            "rating": cand.get("rating"),
            "char_len": cand.get("char_len"),
            "gold": {"SENT": sent, "LANG": lang, "URG": urg},
            "labeling_notes": {
                "lang_detected": lang_detected,
                "lang_overridden": lang != lang_detected,
                "sent_star_hint": SENT_HINT_FROM_STARS.get(int(rating)) if isinstance(rating, (int, float)) and rating else None,  # noqa: E501
                "sent_overridden": sent != SENT_HINT_FROM_STARS.get(int(rating)) if isinstance(rating, (int, float)) and rating else None,  # noqa: E501
                "labeled_by": "gg",
            },
        }
        gold_path.parent.mkdir(parents=True, exist_ok=True)
        append_label(gold_path, record)
        labeled_this_session += 1
        print(f"  Saved. ({labeled_this_session} labeled this session)")

    _separator()
    print("\nSession complete.")
    print(f"  Labeled this session: {labeled_this_session}")
    print(f"  Skipped this session: {skipped_this_session}")
    print(f"  Total labeled so far: {len(existing) + labeled_this_session}")
    print(f"  Gold labels at:       {gold_path}")


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    candidates_path = project_root / "benchmark" / "dataset" / "candidates.jsonl"
    gold_path = project_root / "benchmark" / "dataset" / "gold_labels.jsonl"

    if not candidates_path.exists():
        print(f"ERROR: candidates.jsonl not found at {candidates_path}")
        print("Run: uv run python benchmark/data/sample_candidates.py --all")
        sys.exit(1)

    run(candidates_path, gold_path)


if __name__ == "__main__":
    main()
