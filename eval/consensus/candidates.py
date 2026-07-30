"""Candidate sourcing for growing eval/fixtures/ -- loading existing fixtures + real corpus.

Two data sources, per the task's ground truth (verified, not assumed):
  - Existing committed fixtures under eval/fixtures/{,hi-en/,hi/} -- used for the
    validation pass (does multi-LLM consensus roughly agree with what's already there?).
  - eval/data/flipkart_candidates.jsonl (built by eval/data/sample_flipkart.py from
    3 public Kaggle Flipkart review datasets) -- the candidate pool for GROWING the
    eval set. Real yield is low for hi-en/hi (documented in eval/data/README.md and
    reconfirmed here): out of 14,552 unique candidates in this run, only 51 classified
    hi-en and 2 classified hi (Devanagari) -- see run_consensus.py's growth-planning
    docstring for what that means for the achievable total.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = ROOT / "eval" / "fixtures"
FLIPKART_CANDIDATES_PATH = ROOT / "eval" / "data" / "flipkart_candidates.jsonl"


def _text_key(text: str) -> str:
    """Normalize a review text for de-dup comparison (matches sample_flipkart.py's own dedup key)."""
    return re.sub(r"\s+", " ", text.strip().lower())[:100]


def load_jsonl(path: Path) -> list[dict]:
    """Load a JSONL file into a list of dicts, skipping blank lines."""
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def load_existing_fixtures(fixtures_dir: Path = FIXTURES_DIR) -> list[dict]:
    """Load every committed fixture (top-level + hi-en/ + hi/), skipping non-fixture files.

    Returns a list of `{"id", "review_text", "ground_truth", "language", "path"}` dicts.
    Non-fixture files (README.md, .labeling_run.json metadata) are skipped by filename.
    """
    out: list[dict] = []
    paths = list(fixtures_dir.glob("*.json"))
    for sub in ("hi-en", "hi"):
        subdir = fixtures_dir / sub
        if subdir.is_dir():
            paths += list(subdir.glob("*.json"))

    for p in sorted(paths):
        if p.name.startswith("."):
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        if "review_text" not in data:
            continue
        gt = data.get("ground_truth", {})
        out.append(
            {
                "id": data["id"],
                "review_text": data["review_text"],
                "ground_truth": gt,
                "language": gt.get("language", "en"),
                "path": str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p),
            }
        )
    return out


def existing_text_keys(fixtures_dir: Path = FIXTURES_DIR) -> set[str]:
    """Return the de-dup keys of every already-committed fixture's review text."""
    return {_text_key(f["review_text"]) for f in load_existing_fixtures(fixtures_dir)}


def select_growth_candidates(
    candidates: list[dict],
    already_used: set[str],
    language: str,
    char_range: tuple[int, int],
    max_count: int,
    seed: int = 42,
) -> list[dict]:
    """Deterministically filter + sample candidates for growing the eval set.

    Args:
        candidates: raw rows from flipkart_candidates.jsonl (each has "text", "language",
            "char_len", "product", "rating", "source").
        already_used: de-dup keys (see `_text_key`) of fixtures already committed --
            excluded so we never propose re-labeling something already in the set.
        language: the candidate `language` value to filter to ("en", "hi-en", "hi").
        char_range: inclusive (min, max) character-length window.
        max_count: cap on how many to return (deterministic seeded sample if the
            filtered pool is larger than this).
        seed: RNG seed for the sample -- hardcoded per repo convention (rule: seed=42
            everywhere stochastic), passed explicitly so callers can override in tests.

    Returns:
        Up to `max_count` candidate dicts, each with an added `"consensus_id"` key.
    """
    lo, hi = char_range
    pool = [
        c
        for c in candidates
        if c.get("language") == language
        and lo <= c.get("char_len", 0) <= hi
        and _text_key(c["text"]) not in already_used
    ]
    # Stable order before sampling, so the same seed always yields the same subset
    # regardless of the input file's on-disk row order.
    pool.sort(key=lambda c: _text_key(c["text"]))

    rng = random.Random(seed)
    if len(pool) > max_count:
        pool = rng.sample(pool, max_count)

    for i, c in enumerate(pool, 1):
        c["consensus_id"] = f"grow-{language}-{i:04d}"
    return pool
