"""Which held-out reviews the prompt-development process has already seen, and which gold labels
are a panel split rather than a label (Session 16, V1/V2).

Two independent defects in the held-out corpus, one module because both are answered by the same
question -- "is this (review, field) pair a clean held-out measurement?":

1. EXPOSURE. `eval/consensus/build_held_out_corpus.py` excluded candidates already in the
   quarantine directory, but never candidates already in the prompt-visible development sets. So
   the held-out corpus contains reviews that are also in
   `eval/fixtures/{,hi-en/,hi/}*.json` (the CI-gate/dev set the prompt was developed against) or
   in `benchmark/dataset/gold.jsonl` (whose adjudicated labels accepted prompt v2.2/v2.3). The
   verbatim-window check in `scripts/check_no_heldout_leakage.py` could not see either: it only
   scans prompt text, and only for the first 40 characters of a review.

2. SPLIT GOLD. `build_fixture` stored a panel split as a default ("unknown", [], null), which is
   indistinguishable from a genuine empty label. `unresolved_fields` names those (fixture, field)
   pairs so scoring can exclude them instead of scoring the default as if it were a label.

Matching is on whole-review normalized text (lowercased, non-alphanumerics and the scraper's
"READ MORE" artifact removed). It is exact-after-normalization, NOT semantic: a few-shot example
that paraphrases a dev fixture is invisible to it. That case is enumerated by hand in ADR 0032 and
recorded in `eval/heldout_exposure_ack.json`; do not read a clean result here as "no paraphrase".
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
HELD_OUT_DIR = ROOT / "eval" / "fixtures" / "_held_out_hindi_hinglish"
# The prompt-visible development set: eval/runner.py walks exactly these locations.
DEV_FIXTURE_GLOBS = (
    "eval/fixtures/*.json",
    "eval/fixtures/hi-en/*.json",
    "eval/fixtures/hi/*.json",
)
BENCHMARK_GOLD = ROOT / "benchmark" / "dataset" / "gold.jsonl"
ACK_PATH = ROOT / "eval" / "heldout_exposure_ack.json"

REASON_DEV_FIXTURE = "prompt_visible_dev_fixture"
REASON_BENCHMARK_GOLD = "benchmark_gold"

# Agreement levels at which the panel's `silver` value is kept as the gold label.
RESOLVED_AGREEMENT = ("unanimous", "majority")


def normalize_review_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower().replace("read more", ""))


def unresolved_fields(fixture: dict[str, Any]) -> tuple[str, ...]:
    """Fields whose gold value is a panel split stored as a default, not a label.

    Reads the explicit `labeling_meta.unresolved_fields` written by the builder (Session 16) and
    falls back to deriving it from `agreement_per_field` for fixtures built before that key
    existed, so the 106 committed fixtures stay byte-identical. Both paths must agree; a test pins
    that.
    """
    meta = fixture.get("labeling_meta", {})
    explicit = meta.get("unresolved_fields")
    if explicit is not None:
        return tuple(sorted(explicit))
    agreement = meta.get("agreement_per_field", {})
    return tuple(sorted(f for f, level in agreement.items() if level not in RESOLVED_AGREEMENT))


def dev_fixture_texts(root: Path = ROOT) -> dict[str, str]:
    """normalized review text -> dev fixture path (posix, repo-relative)."""
    out: dict[str, str] = {}
    for pattern in DEV_FIXTURE_GLOBS:
        for path in sorted(root.glob(pattern)):
            data = json.loads(path.read_text(encoding="utf-8"))
            text = data.get("review_text")
            if text:
                out[normalize_review_text(text)] = path.relative_to(root).as_posix()
    return out


def benchmark_texts(path: Path = BENCHMARK_GOLD) -> dict[str, str]:
    """normalized review text -> benchmark record id."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            out[normalize_review_text(rec["text"])] = rec["id"]
    return out


def exposure_index(
    held_out: dict[str, str],
    *,
    dev: dict[str, str] | None = None,
    bench: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """{held_out_id: sorted reasons}, only for ids that ARE exposed.

    `held_out` maps fixture id -> raw review text. `dev`/`bench` default to the repo's sets and are
    injectable so the test can prove the matcher on synthetic data.
    """
    dev = dev_fixture_texts() if dev is None else dev
    bench = benchmark_texts() if bench is None else bench
    index: dict[str, list[str]] = {}
    for fid, text in held_out.items():
        key = normalize_review_text(text)
        if not key:
            continue
        reasons = []
        if key in dev:
            reasons.append(REASON_DEV_FIXTURE)
        if key in bench:
            reasons.append(REASON_BENCHMARK_GOLD)
        if reasons:
            index[fid] = sorted(reasons)
    return index


def load_held_out_fixtures(directory: Path = HELD_OUT_DIR) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        if path.name.startswith("."):
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if "review_text" in data:
            out[data["id"]] = data
    return out


def held_out_exposure(directory: Path = HELD_OUT_DIR) -> dict[str, list[str]]:
    fixtures = load_held_out_fixtures(directory)
    return exposure_index({fid: fx["review_text"] for fid, fx in fixtures.items()})
