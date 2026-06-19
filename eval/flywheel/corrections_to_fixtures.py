from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Fields that map directly from extraction corrections (source_type == "extraction")
_EXTRACTION_SCALAR_FIELDS = frozenset(
    {
        "product",
        "sentiment",
        "urgency",
        "language",
        "buy_again",
        "stars",
        "stars_inferred",
        "confidence",
    }
)
_EXTRACTION_LIST_FIELDS = frozenset(
    {
        "pros",
        "cons",
        "topics",
        "competitor_mentions",
        "feature_requests",
    }
)

# Default scoring_notes for extraction-type candidate fixtures.
# Mirrors the schema used in eval/fixtures/ so candidates are drop-in promotable.
_DEFAULT_SCORING_NOTES: dict[str, Any] = {
    "exact_match_fields": ["product", "stars", "buy_again", "sentiment", "language"],
    "set_overlap_fields": ["topics", "competitor_mentions"],
    "fuzzy_fields": ["pros", "cons"],
    "tolerance_fields": {"stars_inferred": 1},
}


# ---------------------------------------------------------------------------
# Pure-function core
# ---------------------------------------------------------------------------


def apply_corrections_to_extraction(
    extraction: dict[str, Any],
    corrections: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return a copy of *extraction* with each correction in *corrections* applied.

    Rules:
    - Scalar fields (_EXTRACTION_SCALAR_FIELDS): set the field directly.
    - List fields (_EXTRACTION_LIST_FIELDS): parse corrected_value as a JSON list
      when it arrives as a string; accept a bare list as-is. Silently skip if
      the string cannot be parsed.
    - Unknown field_paths: silently skipped — this function has no I/O side effects.

    Neither *extraction* nor any element of *corrections* is mutated.
    """
    result: dict[str, Any] = dict(extraction)
    for correction in corrections:
        field_path: str = correction.get("field_path", "")
        corrected_value: Any = correction.get("corrected_value")

        if field_path in _EXTRACTION_SCALAR_FIELDS:
            result[field_path] = corrected_value
        elif field_path in _EXTRACTION_LIST_FIELDS:
            if isinstance(corrected_value, str):
                try:
                    parsed = json.loads(corrected_value)
                except (json.JSONDecodeError, ValueError):
                    # Silently skip unparseable list corrections — pure function
                    continue
                if isinstance(parsed, list):
                    result[field_path] = parsed
            elif isinstance(corrected_value, list):
                result[field_path] = list(corrected_value)
        # Unknown fields: skip silently

    return result


def corrections_to_candidates(
    grouped: list[dict[str, Any]],
    generated_at: str,
) -> list[dict[str, Any]]:
    """Transform grouped correction records into candidate fixture dicts.

    Each element of *grouped* must have the shape::

        {
            "review_id": str,
            "review_text": str | None,
            "original_extraction": dict,
            "corrections": list[dict],  # correction rows
        }

    Only ``source_type == "extraction"`` corrections are handled. Groups whose
    corrections are entirely of other types are skipped — they require a
    different fixture schema not yet defined.

    Returns a list of candidate dicts. No I/O is performed.
    """
    candidates: list[dict[str, Any]] = []

    for group in grouped:
        review_id: str = group["review_id"]
        all_corrections: list[dict[str, Any]] = group.get("corrections", [])

        extraction_corrections = [
            c for c in all_corrections if c.get("source_type") == "extraction"
        ]
        if not extraction_corrections:
            # Non-extraction corrections need a different schema — skip
            continue

        corrected_extraction = apply_corrections_to_extraction(
            group["original_extraction"],
            extraction_corrections,
        )

        candidate: dict[str, Any] = {
            "candidate_id": f"flywheel-{review_id[:12]}-{generated_at[:10]}",
            "status": "pending_review",
            "review_required": True,
            "review_instructions": (
                "Human review required before promoting to eval/fixtures/. "
                "Verify each field in proposed_ground_truth against review_text."
            ),
            "source": {
                "type": "flywheel_correction",
                "review_id": review_id,
                "correction_ids": [c["id"] for c in extraction_corrections if c.get("id")],
            },
            "review_text": group.get("review_text"),
            "proposed_ground_truth": corrected_extraction,
            "original_extraction": group["original_extraction"],
            "corrections_applied": [
                {
                    "field_path": c["field_path"],
                    "original_value": c.get("original_value"),
                    "corrected_value": c["corrected_value"],
                    "correction_note": c.get("correction_note"),
                    "source_type": c["source_type"],
                }
                for c in extraction_corrections
            ],
            "scoring_notes": _DEFAULT_SCORING_NOTES.copy(),
            "generated_at": generated_at,
        }
        candidates.append(candidate)

    return candidates


def _resolve_gold_dir(script_path: Path) -> Path:
    """Return the absolute path to ``eval/fixtures/`` relative to the repo root.

    Computed as three parents up from the script location (which lives at
    ``eval/flywheel/corrections_to_fixtures.py``) plus ``eval/fixtures``.
    """
    return script_path.parent.parent.parent / "eval" / "fixtures"


# ---------------------------------------------------------------------------
# I/O shell
# ---------------------------------------------------------------------------


def write_candidates(
    candidates: list[dict[str, Any]],
    output_dir: Path,
    gold_dir: Path,
) -> int:
    """Write *candidates* to ``output_dir/candidates.jsonl``.

    Raises
    ------
    ValueError
        If ``output_dir`` resolves to the same path as ``gold_dir``
        (i.e., ``eval/fixtures/``). This is the hard gate that prevents
        accidental promotion — candidates must be reviewed by a human first.

    Returns
    -------
    int
        Number of candidates written.
    """
    if output_dir.resolve() == gold_dir.resolve():
        raise ValueError(
            "output_dir must not be eval/fixtures/ — candidates require human review before promotion."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / "candidates.jsonl"

    with out_file.open("w", encoding="utf-8") as fh:
        for candidate in candidates:
            fh.write(json.dumps(candidate, ensure_ascii=False) + "\n")

    return len(candidates)


def load_corrections_from_file(path: Path) -> list[dict[str, Any]]:
    """Load correction records from a JSON file.

    Accepts two shapes:
    - A top-level list: ``[{...}, ...]``
    - A dict with a ``"corrections"`` key: ``{"corrections": [{...}, ...]}``

    Raises
    ------
    ValueError
        If the file content matches neither expected shape.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return raw  # type: ignore[return-value]
    if isinstance(raw, dict) and "corrections" in raw:
        return raw["corrections"]  # type: ignore[return-value]
    raise ValueError(
        f"Corrections file {path} must be a JSON list or a dict with a 'corrections' key."
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Transform accepted corrections into CANDIDATE eval fixtures."
    )
    parser.add_argument(
        "--corrections-file",
        required=True,
        help="JSON file with list of correction+context records.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory to write candidates.jsonl (must not be eval/fixtures/).",
    )
    args = parser.parse_args()

    corrections_file = Path(args.corrections_file)
    output_dir = Path(args.output_dir)
    gold_dir = _resolve_gold_dir(Path(__file__))
    generated_at = datetime.now(UTC).isoformat()

    grouped = load_corrections_from_file(corrections_file)
    candidates = corrections_to_candidates(grouped, generated_at)
    count = write_candidates(candidates, output_dir, gold_dir)
    print(f"Wrote {count} candidate fixture(s) to {output_dir / 'candidates.jsonl'}")
    sys.exit(0)
