"""
Candidate sampler: reads raw source data, runs language detection, deduplicates,
runs the leakage check, and writes benchmark/dataset/candidates.jsonl.

Usage:
    uv run python benchmark/data/sample_candidates.py --source flipkart --slice en --n 30
    uv run python benchmark/data/sample_candidates.py --source <new_source> --slice hi-en --n 25
    uv run python benchmark/data/sample_candidates.py --all   # run all configured sources

Each candidate in candidates.jsonl:
    {
        "id": "bench-en-001",
        "source": "flipkart/niraliivaghani/...",
        "text": "...",
        "rating": 4,              # null if not available
        "language_detected": "en",
        "language_script_fraction": 0.0,   # fraction of Devanagari chars
        "char_len": 80,
        "leakage": false
    }
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path

# ---------------------------------------------------------------------------
# Language detection helpers (no external deps)
# ---------------------------------------------------------------------------

_DEVANAGARI_RANGE = re.compile(r"[ऀ-ॿ]")
_LATIN_WORD = re.compile(r"[a-zA-Z]")


def _devanagari_fraction(text: str) -> float:
    """Fraction of non-whitespace characters that are Devanagari."""
    chars = [c for c in text if not unicodedata.category(c).startswith("Z")]
    if not chars:
        return 0.0
    deva = sum(1 for c in chars if _DEVANAGARI_RANGE.match(c))
    return deva / len(chars)


def detect_language(text: str) -> str:
    """
    Heuristic language ID — same rule used in sample_flipkart.py:
    - deva_fraction > 0.10  → 'hi'
    - deva_fraction == 0 and has Latin letters → 'en'
    - otherwise (some Devanagari + Latin)      → 'hi-en'
    """
    frac = _devanagari_fraction(text)
    if frac > 0.10:
        return "hi"
    if frac == 0.0 and _LATIN_WORD.search(text):
        return "en"
    return "hi-en"


# ---------------------------------------------------------------------------
# Source adapters
# ---------------------------------------------------------------------------


def _load_flipkart_candidates(
    candidates_jsonl: Path, language_slice: str, n: int
) -> list[dict[str, object]]:
    """Load from the existing eval/data/flipkart_candidates.jsonl."""
    records: list[dict[str, object]] = []
    with candidates_jsonl.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("language") != language_slice:
                continue
            text = str(obj.get("text", ""))
            if len(text) < 20:  # filter very short texts
                continue
            records.append(
                {
                    "source": str(obj.get("source", "flipkart")),
                    "text": text,
                    "rating": obj.get("rating"),
                    "language_detected": language_slice,
                    "language_script_fraction": round(_devanagari_fraction(text), 4),
                    "char_len": len(text),
                }
            )
    # Sort by length descending (longer reviews are richer) then take n
    records.sort(key=lambda r: int(str(r["char_len"])), reverse=True)
    return records[:n]


def _load_source(
    source: str, language_slice: str, n: int, project_root: Path
) -> list[dict[str, object]]:
    """Dispatch to the correct source adapter."""
    if source == "flipkart":
        candidates_jsonl = project_root / "eval" / "data" / "flipkart_candidates.jsonl"
        if not candidates_jsonl.exists():
            raise FileNotFoundError(
                f"flipkart_candidates.jsonl not found at {candidates_jsonl}. "
                "Run: uv run python eval/data/sample_flipkart.py"
            )
        return _load_flipkart_candidates(candidates_jsonl, language_slice, n)
    # Future adapters go here:
    # if source == "indicsentiment": return _load_indicsentiment(...)
    # if source == "semeval2020": return _load_semeval2020(...)
    raise ValueError(f"Unknown source: {source!r}. Add an adapter in sample_candidates.py.")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

_SLICES: list[str] = ["en", "hi", "hi-en"]
_BENCH_ID_PREFIX = {"en": "bench-en", "hi": "bench-hi", "hi-en": "bench-hien"}


def run_sampler(
    source: str,
    language_slice: str,
    n: int,
    project_root: Path,
    output_path: Path,
    dry_run: bool = False,
) -> None:
    from benchmark.data.leakage_check import LeakageChecker

    print(f"\n{'='*60}")
    print(f"Sampling: source={source!r}  slice={language_slice!r}  n={n}")

    checker = LeakageChecker.from_eval_dir(project_root / "eval")
    print(f"Leakage checker loaded {checker.fixture_count} CI fixture texts.")

    raw = _load_source(source, language_slice, n, project_root)
    print(f"Raw candidates loaded: {len(raw)}")

    report = checker.check(raw)  # type: ignore[arg-type]
    print(report.summary())

    # Load existing candidates to determine next ID number
    existing: list[dict[str, object]] = []
    if output_path.exists():
        with output_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    existing.append(json.loads(line))
    existing_ids = {str(e.get("id", "")) for e in existing}

    prefix = _BENCH_ID_PREFIX[language_slice]
    existing_slice = [e for e in existing if str(e.get("id", "")).startswith(prefix)]
    next_idx = len(existing_slice) + 1

    new_records: list[dict[str, object]] = []
    for raw_rec in raw:
        rid = str(raw_rec.get("id", ""))  # may be empty for raw records
        if rid and rid in existing_ids:
            continue
        if checker.is_leaked(str(raw_rec.get("text", ""))):
            continue
        bench_id = f"{prefix}-{next_idx:03d}"
        next_idx += 1
        new_records.append(
            {
                "id": bench_id,
                **raw_rec,
                "leakage": False,
            }
        )

    print(f"New clean candidates to write: {len(new_records)}")

    if dry_run:
        print("[dry-run] No file written.")
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as fh:
        for rec in new_records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"Written to {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sample benchmark candidates from a source.")
    parser.add_argument("--source", default="flipkart", help="Source adapter name")
    parser.add_argument(
        "--slice",
        choices=_SLICES,
        help="Language slice (en / hi / hi-en). Omit with --all to run all slices.",
    )
    parser.add_argument("--n", type=int, default=30, help="Max candidates per slice")
    parser.add_argument("--all", action="store_true", help="Run all language slices")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[2]
    output_path = project_root / "benchmark" / "dataset" / "candidates.jsonl"

    slices = _SLICES if args.all else ([args.slice] if args.slice else ["en"])
    for sl in slices:
        run_sampler(
            source=args.source,
            language_slice=sl,
            n=args.n,
            project_root=project_root,
            output_path=output_path,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    main()
