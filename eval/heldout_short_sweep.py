"""Exposure sweep for the SHORTEST held-out reviews (Session 17, W3f).

ADR 0032 (V1e) swept the whole repo for held-out text with whole-review and 40-character-window
matching, and said so: the 17 held-out reviews shorter than 30 normalized characters "were not
swept by this method". `eval/heldout_exposure.py` has the same gap in a different shape: it only
finds an EXACT whole-review match after normalization, so a short review that a development set
holds with a trailing word changed, or inside a longer review, or that a prompt quotes as a
few-shot string, is invisible to it.

This module sweeps exactly those reviews with matchers that suit short strings:

  * `exact`        normalized whole-string equality (what heldout_exposure.py already does);
  * `contains`     the normalized held-out review is a substring of a longer normalized candidate
                   segment, or the reverse (min length MIN_CONTAINMENT_LEN so a 3-letter token
                   cannot match everything);
  * `near_dup`     difflib SequenceMatcher ratio >= NEAR_DUP_RATIO between normalized strings of
                   comparable length (catches one-word edits, which windows cannot).

Candidates are SEGMENTS, not just whole documents: a prompt file or a long review is also cut into
lines, quoted strings and sentence-like fragments, so a short held-out string quoted inside a
few-shot block is compared with the string it sits in, not with the whole block.

Every hit is a CANDIDATE for human adjudication, not a verdict: short Hinglish reviews are built
from a tiny vocabulary ("mast product", "paisa wasool"), so containment of a generic phrase is
expected. The report records `generic` for a hit whose matched text is entirely
GENERIC_PHRASES-level vocabulary (judged on the side that is contained in the other); a hit that is NOT generic and is exact or near-duplicate is what
counts as exposure. The deterministic output is committed (eval/results/heldout_short_sweep.json)
and `tests/unit/test_heldout_short_sweep.py` pins the matcher on synthetic data.
"""

from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.heldout_exposure import (  # noqa: E402
    load_held_out_fixtures,
    normalize_review_text,
)

OUT_PATH = ROOT / "eval" / "results" / "heldout_short_sweep.json"
# ADR 0032 V1e's own cutoff: reviews with fewer than this many normalized characters were not
# swept by the 40-char window method. Reused so this sweep covers exactly the complement.
SHORT_CUTOFF = 30
MIN_CONTAINMENT_LEN = 8
NEAR_DUP_RATIO = 0.85
# Segment-vs-review length ratio for near-dup: a 10-char string is not a near-duplicate of a
# 60-char one even if the shorter is mostly contained in it (that is the `contains` matcher).
NEAR_DUP_LEN_RATIO = 0.6

# Collections the prompt-development process can see. Benchmark candidates are included because
# they are the pre-adjudication copy of the gold rows.
SCAN_GLOBS_JSON = (
    "eval/fixtures/*.json",
    "eval/fixtures/hi-en/*.json",
    "eval/fixtures/hi/*.json",
)
SCAN_JSONL = (
    "benchmark/dataset/gold.jsonl",
    "benchmark/dataset/candidates_for_review.jsonl",
)
SCAN_TEXT_GLOBS = (
    "app/core/prompts/**/*.py",
    "app/core/prompt.py",
    "PROMPTS.md",
)
# Tokens that, on their own, make a match uninformative for a short review.
GENERIC_PHRASES = frozenset(
    {
        "product",
        "products",
        "mast",
        "good",
        "very",
        "nice",
        "great",
        "best",
        "superb",
        "awesome",
        "excellent",
        "bad",
        "worst",
        "bakwas",
        "bakwaas",
        "bekar",
        "useless",
        "paisa",
        "vasool",
        "wasool",
        "wasul",
        "value",
        "for",
        "money",
        "hai",
        "h",
        "ye",
        "is",
        "it",
        "bass",
        "sound",
        "quality",
        "love",
        "read",
        "more",
        "yaar",
        "bhai",
        "super",
        "fabulous",
        "zabardast",
        "sahi",
        "not",
    }
)


def short_reviews(
    fixtures: dict[str, dict[str, Any]] | None = None, cutoff: int = SHORT_CUTOFF
) -> dict[str, str]:
    fixtures = load_held_out_fixtures() if fixtures is None else fixtures
    return {
        fid: fx["review_text"]
        for fid, fx in sorted(fixtures.items())
        if len(normalize_review_text(fx["review_text"])) < cutoff
    }


_SEGMENT_SPLIT = re.compile(r"[\n.;:|!?]+")
_QUOTED = re.compile(r"\"([^\"\n]{3,})\"|'([^'\n]{3,})'")


def segments(text: str) -> list[str]:
    """The whole text plus its lines, sentence-like fragments and quoted strings."""
    out = [text]
    out += [s for s in _SEGMENT_SPLIT.split(text) if s.strip()]
    for m in _QUOTED.finditer(text):
        out.append(m.group(1) or m.group(2))
    return out


def _is_generic(key: str, vocab_text: str) -> bool:
    """True when every word of the review (by raw lowercase tokens) is generic vocabulary."""
    words = re.findall(r"[a-z0-9]+", vocab_text.lower().replace("read more", " "))
    return bool(words) and all(w in GENERIC_PHRASES for w in words)


def match_segment(key: str, seg_key: str) -> str | None:
    """Return the strongest matcher name for (held-out key, candidate segment key), else None."""
    if not key or not seg_key:
        return None
    if key == seg_key:
        return "exact"
    if len(key) >= MIN_CONTAINMENT_LEN and key in seg_key:
        return "contains"
    if len(seg_key) >= MIN_CONTAINMENT_LEN and seg_key in key:
        return "contained_by"
    lo, hi = sorted((len(key), len(seg_key)))
    comparable = lo / hi >= NEAR_DUP_LEN_RATIO and len(key) >= MIN_CONTAINMENT_LEN
    if comparable and difflib.SequenceMatcher(None, key, seg_key).ratio() >= NEAR_DUP_RATIO:
        return "near_dup"
    return None


def sweep(
    held: dict[str, str], sources: dict[str, list[tuple[str, str]]]
) -> dict[str, list[dict[str, Any]]]:
    """{held_out_id: [hit, ...]} for every held-out id (empty list = no hit).

    `sources` maps a source label to [(item_id, raw_text), ...]; each text is cut into segments.
    """
    result: dict[str, list[dict[str, Any]]] = {fid: [] for fid in held}
    strength = {"exact": 0, "near_dup": 1, "contains": 2, "contained_by": 3}
    for fid, raw in held.items():
        key = normalize_review_text(raw)
        for label, items in sources.items():
            for item_id, text in items:
                best: tuple[int, str, str] | None = None
                for seg in segments(text):
                    kind = match_segment(key, normalize_review_text(seg))
                    if not kind:
                        continue
                    # Strongest matcher wins; ties go to the SHORTEST segment so the report
                    # shows the line a string sits on, not the whole file that contains it.
                    cand = (strength[kind], kind, seg.strip())
                    if best is None or (cand[0], len(cand[2])) < (best[0], len(best[2])):
                        best = cand
                if best:
                    result[fid].append(
                        {
                            "source": label,
                            "item": item_id,
                            "match": best[1],
                            "segment": best[2][:160],
                            # Judged on the text that actually matched: the candidate segment
                            # when it sits INSIDE the review, otherwise the review itself.
                            "generic": _is_generic(
                                key, best[2] if best[1] == "contained_by" else raw
                            ),
                        }
                    )
    return result


def load_sources(root: Path = ROOT) -> dict[str, list[tuple[str, str]]]:
    sources: dict[str, list[tuple[str, str]]] = {}
    dev: list[tuple[str, str]] = []
    for pattern in SCAN_GLOBS_JSON:
        for p in sorted(root.glob(pattern)):
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("review_text"):
                dev.append((p.relative_to(root).as_posix(), d["review_text"]))
    sources["dev_fixtures"] = dev
    for rel in SCAN_JSONL:
        path = root / rel
        rows: list[tuple[str, str]] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    rows.append((rec["id"], rec["text"]))
        sources[rel] = rows
    prompts: list[tuple[str, str]] = []
    for pattern in SCAN_TEXT_GLOBS:
        for p in sorted(root.glob(pattern)):
            prompts.append((p.relative_to(root).as_posix(), p.read_text(encoding="utf-8")))
    sources["prompt_text"] = prompts
    return sources


def run() -> dict[str, Any]:
    held = short_reviews()
    sources = load_sources()
    hits = sweep(held, sources)
    return {
        "cutoff_normalized_chars": SHORT_CUTOFF,
        "matchers": {
            "min_containment_len": MIN_CONTAINMENT_LEN,
            "near_dup_ratio": NEAR_DUP_RATIO,
            "near_dup_len_ratio": NEAR_DUP_LEN_RATIO,
        },
        "sources_scanned": {k: len(v) for k, v in sources.items()},
        "n_short_reviews": len(held),
        "reviews": {
            fid: {"text": held[fid], "n_hits": len(hs), "hits": hs} for fid, hs in hits.items()
        },
    }


def main() -> None:
    out = run()
    OUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Written: {OUT_PATH}")


if __name__ == "__main__":
    main()
