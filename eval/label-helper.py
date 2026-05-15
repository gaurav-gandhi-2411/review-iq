"""
Interactive CLI for labeling Hinglish review fixtures.

Usage:
    uv run python eval/label-helper.py

Prereqs:
    - eval/data/flipkart_candidates.jsonl must exist
    - Run:  uv run python eval/data/sample_flipkart.py  first

State is saved after each accepted fixture. Run again to resume where you left off.
Target: 15 fixtures in eval/fixtures/hi-en/
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

CANDIDATES_FILE = Path(__file__).parent / "data" / "flipkart_candidates.jsonl"
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "hi-en"
PROGRESS_FILE = Path(__file__).parent / "data" / ".label_progress.json"

TARGET = 15
CANDIDATE_POOL = 60  # show at most this many candidates to the user

_SENTIMENTS = {"positive", "negative", "mixed", "neutral"}
_URGENCIES = {"low", "medium", "high"}


# ---------------------------------------------------------------------------
# Terminal helpers
# ---------------------------------------------------------------------------

def _clear() -> None:
    print("\n" + "=" * 70 + "\n")


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"  {prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n\nSession interrupted. Progress saved.")
        sys.exit(0)
    return val if val else default


def _ask_list(prompt: str) -> list[str]:
    raw = _ask(prompt + " (comma-separated, or 'none')")
    if raw.lower() in ("none", "n/a", "-", ""):
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


# ---------------------------------------------------------------------------
# Progress tracking
# ---------------------------------------------------------------------------

def _load_progress() -> dict:
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    return {"seen": [], "accepted_count": 0}


def _save_progress(progress: dict) -> None:
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Candidate ranking
# ---------------------------------------------------------------------------

def _load_candidates() -> list[dict]:
    if not CANDIDATES_FILE.exists():
        print(f"ERROR: {CANDIDATES_FILE} not found.")
        print("  Run first: uv run python eval/data/sample_flipkart.py")
        sys.exit(1)

    candidates = []
    with CANDIDATES_FILE.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    candidates.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return candidates


def _rank_candidates(candidates: list[dict]) -> list[dict]:
    """Return top Hinglish candidates ranked by quality for labeling."""
    # Filter to Hinglish only, reasonable length
    hinglish = [
        c for c in candidates
        if c.get("language") == "hi-en"
        and 30 <= c.get("char_len", 0) <= 600
    ]

    # Sort: prefer medium-length reviews (100-400 chars) that are more informative
    def score(c: dict) -> float:
        n = c["char_len"]
        # Penalize very short and very long
        length_score = 1.0 - abs(n - 200) / 400
        # Prefer if review has a real product name
        product_bonus = 0.1 if c.get("product", "unknown") not in ("unknown", "nan") else 0.0
        return length_score + product_bonus

    ranked = sorted(hinglish, key=score, reverse=True)

    # Diversity: de-duplicate similar reviews by first 50 chars
    seen_prefix: set[str] = set()
    diverse = []
    for c in ranked:
        prefix = re.sub(r"\s+", " ", c["text"][:50].lower())
        if prefix not in seen_prefix:
            seen_prefix.add(prefix)
            diverse.append(c)

    return diverse[:CANDIDATE_POOL]


# ---------------------------------------------------------------------------
# Fixture writing
# ---------------------------------------------------------------------------

def _fixture_path(n: int) -> Path:
    return FIXTURES_DIR / f"{n:03d}.json"


def _next_fixture_number() -> int:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(FIXTURES_DIR.glob("*.json"))
    if not existing:
        return 1
    last = int(existing[-1].stem)
    return last + 1


def _write_fixture(n: int, candidate: dict, ground_truth: dict) -> Path:
    fixture = {
        "id": f"hi-en-{n:03d}",
        "review_text": candidate["text"],
        "ground_truth": ground_truth,
        "scoring_notes": {
            "exact_match_fields": ["product", "stars", "buy_again", "sentiment", "language"],
            "set_overlap_fields": ["topics", "competitor_mentions"],
            "fuzzy_fields": ["pros", "cons"],
            "tolerance_fields": {"stars_inferred": 1},
        },
    }
    path = _fixture_path(n)
    path.write_text(json.dumps(fixture, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Input collection
# ---------------------------------------------------------------------------

def _collect_ground_truth(candidate: dict) -> dict | None:
    """Prompt user for all ground truth fields. Returns None if user skips."""
    suggested_product = candidate.get("product", "unknown")
    suggested_rating = candidate.get("rating")

    print(f"\n  Source: {candidate.get('source', 'unknown')}")
    print(f"  Auto-detected product: {suggested_product}")
    print(f"  Auto-detected rating:  {suggested_rating}")
    print()
    print("  Commands:  [Enter] = accept default  |  's' = skip this review  |  'q' = quit & save")
    print()

    # --- Product ---
    product = _ask("Product name", default=suggested_product)
    if product.lower() == "s":
        return None
    if product.lower() == "q":
        print("Quitting. Progress saved.")
        sys.exit(0)

    # --- Stars (explicit) ---
    stars_raw = _ask("Stars (1-5 or 'none')", default=str(suggested_rating) if suggested_rating else "none")
    if stars_raw.lower() in ("s",):
        return None
    if stars_raw.lower() == "q":
        sys.exit(0)
    try:
        stars: int | None = int(stars_raw) if stars_raw.lower() not in ("none", "") else None
        if stars is not None and not (1 <= stars <= 5):
            stars = None
    except ValueError:
        stars = None

    # --- Stars inferred ---
    stars_inf_raw = _ask("Stars inferred from tone (1-5 or 'none')", default="none")
    try:
        stars_inferred: int | None = int(stars_inf_raw) if stars_inf_raw.lower() not in ("none", "") else None
        if stars_inferred is not None and not (1 <= stars_inferred <= 5):
            stars_inferred = None
    except ValueError:
        stars_inferred = None

    # --- Pros ---
    pros = _ask_list("Pros")

    # --- Cons ---
    cons = _ask_list("Cons")

    # --- Buy again ---
    buy_raw = _ask("Buy again? (y/n/unclear)", default="unclear").lower()
    buy_again: bool | None = True if buy_raw == "y" else (False if buy_raw == "n" else None)

    # --- Sentiment ---
    while True:
        sentiment = _ask("Sentiment (positive/negative/mixed/neutral)", default="mixed").lower()
        if sentiment in _SENTIMENTS:
            break
        print(f"  Invalid. Choose from: {', '.join(sorted(_SENTIMENTS))}")

    # --- Urgency ---
    while True:
        urgency = _ask("Urgency (low/medium/high)", default="low").lower()
        if urgency in _URGENCIES:
            break
        print(f"  Invalid. Choose from: {', '.join(sorted(_URGENCIES))}")

    # --- Topics ---
    topics = _ask_list("Topics (e.g. battery, price, design)")

    # --- Competitor mentions ---
    competitor_mentions = _ask_list("Competitor mentions")

    # --- Feature requests ---
    feature_requests = _ask_list("Feature requests")

    return {
        "product": product,
        "stars": stars,
        "stars_inferred": stars_inferred,
        "pros": pros,
        "cons": cons,
        "buy_again": buy_again,
        "sentiment": sentiment,
        "topics": topics,
        "competitor_mentions": competitor_mentions,
        "urgency": urgency,
        "feature_requests": feature_requests,
        "language": "hi-en",
    }


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    print("\n" + "=" * 70)
    print("  Review-IQ Hinglish Label Helper")
    print("=" * 70)

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    progress = _load_progress()
    seen_texts: set[str] = set(progress.get("seen", []))
    accepted_count = progress.get("accepted_count", 0)

    # Count existing fixtures
    existing_fixtures = sorted(FIXTURES_DIR.glob("*.json"))
    if existing_fixtures:
        print(f"\n  Found {len(existing_fixtures)} existing fixtures in {FIXTURES_DIR}")
        accepted_count = max(accepted_count, len(existing_fixtures))

    if accepted_count >= TARGET:
        print(f"\n  Already have {accepted_count} fixtures (target: {TARGET}). Done!")
        print(f"  Fixtures are in: {FIXTURES_DIR}")
        return

    print(f"\n  Progress: {accepted_count}/{TARGET} fixtures labeled")
    print(f"  Fixtures directory: {FIXTURES_DIR}")
    print()

    candidates = _load_candidates()
    ranked = _rank_candidates(candidates)

    # Filter out already-seen candidates
    unseen = [c for c in ranked if c["text"] not in seen_texts]

    if not unseen:
        print("  All ranked candidates have been seen.")
        if accepted_count < TARGET:
            print(f"  Need {TARGET - accepted_count} more fixtures but no more candidates.")
            print("  Try running eval/data/sample_flipkart.py again or add more datasets.")
        return

    print(f"  Showing up to {len(unseen)} Hinglish candidates (target: {TARGET} accepted)")
    print()
    print("  For each review:")
    print("    - Read the text carefully")
    print("    - Fill in the ground truth fields")
    print("    - 's' at any field prompt to skip this review")
    print("    - 'q' to save and quit")
    print()
    input("  Press Enter to start...")

    for i, candidate in enumerate(unseen):
        if accepted_count >= TARGET:
            break

        _clear()
        print(f"  Candidate {i + 1}/{len(unseen)}  |  Accepted: {accepted_count}/{TARGET}")
        print(f"  Chars: {candidate['char_len']}  |  Rating: {candidate.get('rating')}  |  Source: {candidate.get('source', '?')}")
        print()
        print("  REVIEW TEXT:")
        print("  " + "-" * 60)
        # Word-wrap at 60 chars for readability
        words = candidate["text"].split()
        line = ""
        for word in words:
            if len(line) + len(word) + 1 > 60:
                print(f"  {line}")
                line = word
            else:
                line = (line + " " + word).strip()
        if line:
            print(f"  {line}")
        print("  " + "-" * 60)

        seen_texts.add(candidate["text"])
        progress["seen"] = list(seen_texts)
        _save_progress(progress)

        ground_truth = _collect_ground_truth(candidate)

        if ground_truth is None:
            print("  Skipped.")
            continue

        # Save fixture
        fixture_n = _next_fixture_number()
        path = _write_fixture(fixture_n, candidate, ground_truth)
        accepted_count += 1
        progress["accepted_count"] = accepted_count
        _save_progress(progress)

        print(f"\n  ✓ Saved fixture {fixture_n:03d} → {path.name}")
        print(f"  Progress: {accepted_count}/{TARGET}")

        if accepted_count < TARGET:
            try:
                input("\n  Press Enter for next candidate...")
            except (EOFError, KeyboardInterrupt):
                print("\n  Progress saved. Resume by running this script again.")
                sys.exit(0)

    _clear()
    if accepted_count >= TARGET:
        print(f"  DONE! {accepted_count} Hinglish fixtures labeled.")
        print(f"\n  Fixtures are in: {FIXTURES_DIR}")
        print(f"\n  Next steps:")
        print(f"    1. Review the fixtures: ls {FIXTURES_DIR}")
        print(f"    2. git add eval/fixtures/hi-en/ && git commit -m 'feat(eval): 15 hand-labeled Hinglish fixtures'")
        print(f"    3. Tell Claude Code: 'labels done'")
    else:
        remaining = TARGET - accepted_count
        print(f"  Session complete. {accepted_count}/{TARGET} fixtures labeled.")
        print(f"  Need {remaining} more. Run this script again to continue.")


if __name__ == "__main__":
    main()
