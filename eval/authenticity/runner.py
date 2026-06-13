"""Authenticity eval runner.

Usage: uv run python eval/authenticity/runner.py [--dry-run]
  --dry-run: skip Groq calls, use heuristics-only scoring (useful for CI smoke tests)

Prints: per-language and overall precision / recall / F1 on the flagged class.
Exits 0 if precision >= 0.80; exits 1 otherwise (precision gate).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Add project root to sys.path so 'app' is importable
sys.path.insert(0, str(Path(__file__).parents[2]))

from app.core.authenticity.engine import score_single
from app.core.authenticity.heuristics import compute_heuristic_score
from app.core.authenticity.schema import AuthenticityLabel, AuthenticityResult
from app.core.config import get_settings

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "labeled.jsonl"
PRECISION_GATE = 0.80
RECALL_TARGET = 0.60


def load_fixtures() -> list[dict]:
    """Load all fixtures from labeled.jsonl."""
    with FIXTURES_PATH.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def is_flagged_true(true_label: str) -> bool:
    """Ground truth: suspicious or likely_fake = flagged."""
    return true_label in ("suspicious", "likely_fake")


def is_flagged_pred(result: AuthenticityResult) -> bool:
    """Prediction: suspicious or likely_fake = flagged."""
    return result.label in (AuthenticityLabel.SUSPICIOUS, AuthenticityLabel.LIKELY_FAKE)


def compute_metrics(tp: int, fp: int, fn: int) -> dict[str, float]:
    """Compute precision, recall, and F1 from confusion matrix counts."""
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def score_heuristic_only(text: str, stars: int | None) -> AuthenticityResult:
    """Dry-run path: heuristics only, no LLM call."""
    heuristic_score, flags = compute_heuristic_score(text, stars)
    return AuthenticityResult.from_signals(
        heuristic_score=heuristic_score,
        llm_score=None,
        flags=flags,
        reasons="heuristic-only (dry-run)",
        review_text=text,
        model_used=None,
    )


async def run_eval(dry_run: bool) -> None:
    """Run the full authenticity eval against all fixtures."""
    fixtures = load_fixtures()
    settings = get_settings()

    results: list[tuple[dict, AuthenticityResult]] = []
    llm_error_count: int = 0
    for i, fixture in enumerate(fixtures):
        text = fixture["text"]
        stars = fixture.get("stars")
        if dry_run:
            result = score_heuristic_only(text, stars)
        else:
            result = await score_single(text, stars=stars, settings=settings)
        if not result.llm_signal_ok and not dry_run:
            llm_error_count += 1
            print(f"  [LLM ERROR on fixture {fixture['id']}]")
        results.append((fixture, result))
        status_char = (
            "OK" if is_flagged_true(fixture["true_label"]) == is_flagged_pred(result) else "XX"
        )
        print(
            f"[{i + 1:02d}] {status_char} true={fixture['true_label']:<12} pred={result.label.value:<12} score={result.score:.2f}"
        )

    # Overall metrics
    languages = sorted({f["language"] for f, _ in results})
    print("\n" + "=" * 60)
    print("RESULTS BY LANGUAGE")
    print("=" * 60)
    for lang in languages + ["all"]:
        subset = [(f, r) for f, r in results if lang == "all" or f["language"] == lang]
        tp = sum(1 for f, r in subset if is_flagged_true(f["true_label"]) and is_flagged_pred(r))
        fp = sum(
            1 for f, r in subset if not is_flagged_true(f["true_label"]) and is_flagged_pred(r)
        )
        fn = sum(
            1 for f, r in subset if is_flagged_true(f["true_label"]) and not is_flagged_pred(r)
        )
        tn = sum(
            1 for f, r in subset if not is_flagged_true(f["true_label"]) and not is_flagged_pred(r)
        )
        m = compute_metrics(tp, fp, fn)
        print(f"\nLanguage: {lang} (n={len(subset)})")
        print(f"  TP={tp} FP={fp} FN={fn} TN={tn}")
        print(f"  Precision: {m['precision']:.3f}  Recall: {m['recall']:.3f}  F1: {m['f1']:.3f}")

    if llm_error_count > 0:
        print(f"\nINVALID RUN: LLM signal failed on {llm_error_count}/{len(fixtures)} rows.")
        print("Fix the LLM integration and re-run — this run cannot be scored.")
        sys.exit(2)

    # Precision gate
    all_tp = sum(1 for f, r in results if is_flagged_true(f["true_label"]) and is_flagged_pred(r))
    all_fp = sum(
        1 for f, r in results if not is_flagged_true(f["true_label"]) and is_flagged_pred(r)
    )
    all_fn = sum(
        1 for f, r in results if is_flagged_true(f["true_label"]) and not is_flagged_pred(r)
    )
    overall = compute_metrics(all_tp, all_fp, all_fn)

    print("\n" + "=" * 60)
    print("PRECISION GATE")
    print("=" * 60)
    gate_pass = overall["precision"] >= PRECISION_GATE
    print(f"  Required: precision >= {PRECISION_GATE:.2f}")
    print(f"  Achieved: precision = {overall['precision']:.3f}")
    print(f"  Status: {'PASS' if gate_pass else 'FAIL — escalate before shipping'}")
    if dry_run:
        print("\n  [DRY-RUN: Groq calls skipped; heuristics-only scoring]")

    sys.exit(0 if gate_pass else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(run_eval(args.dry_run))
