"""Measure PII-redaction recall on a labeled synthetic fixture set (Wave 1 Section E).

Provenance: eval/fixtures/redaction/labeled.jsonl is 100% SYNTHETIC review-sentence text
written for this measurement -- there is no ethical way to source "real" customer PII for a
test fixture. Each row carries `kind` (email/phone/card/order_id/name) and
`expected_spans`: the literal ground-truth PII substring(s) that `redact_pii()` must remove.

Methodology: for each fixture, run `app.core.sanitize.redact_pii()` on `text` and check
whether every string in `expected_spans` is ABSENT from the redacted output (a span that
survives redaction is a false negative -- a real PII leak). This is a recall measurement
only (TP / (TP + FN)); it says nothing about precision/false-positive rate (see
`tests/unit/test_sanitize.py::TestRedactOrderIds::test_bare_digits_with_no_context_not_redacted`
and the NER false-positive note in this script's summary for what's known there).

Usage:
    uv run python scripts/measure_redaction_recall.py
    uv run python scripts/measure_redaction_recall.py --out reports/redaction_recall.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_PATH = REPO_ROOT / "eval" / "fixtures" / "redaction" / "labeled.jsonl"
DEFAULT_OUT_PATH = REPO_ROOT / "eval" / "fixtures" / "redaction" / "recall_report.json"

sys.path.insert(0, str(REPO_ROOT))


@dataclass
class SpanResult:
    fixture_id: int
    kind: str
    span: str
    redacted: bool  # True = span successfully removed (TP), False = leaked (FN)


def _load_fixtures(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def score_fixtures(fixtures: list[dict[str, object]]) -> list[SpanResult]:
    """Run redact_pii on every fixture and score each ground-truth span."""
    from app.core.sanitize import redact_pii

    results: list[SpanResult] = []
    for row in fixtures:
        text = str(row["text"])
        kind = str(row["kind"])
        fixture_id = int(row["id"])  # type: ignore[arg-type]
        expected_spans = row["expected_spans"]
        assert isinstance(expected_spans, list)

        redacted_text, _rmap = redact_pii(text)
        for span in expected_spans:
            leaked = span in redacted_text
            results.append(
                SpanResult(fixture_id=fixture_id, kind=kind, span=span, redacted=not leaked)
            )
    return results


def aggregate(results: list[SpanResult]) -> dict[str, dict[str, object]]:
    """Return {kind: {tp, fn, n, recall}} plus an "overall" bucket."""
    from eval.wilson import wilson_ci

    by_kind: dict[str, list[SpanResult]] = defaultdict(list)
    for r in results:
        by_kind[r.kind].append(r)

    summary: dict[str, dict[str, object]] = {}
    for kind, rs in sorted(by_kind.items()):
        tp = sum(1 for r in rs if r.redacted)
        n = len(rs)
        recall = tp / n if n else 0.0
        lo, hi = wilson_ci(recall, n)
        summary[kind] = {
            "tp": tp,
            "fn": n - tp,
            "n": n,
            "recall": recall,
            "recall_ci95": [lo, hi],
        }

    tp_all = sum(1 for r in results if r.redacted)
    n_all = len(results)
    recall_all = tp_all / n_all if n_all else 0.0
    lo_all, hi_all = wilson_ci(recall_all, n_all)
    summary["overall"] = {
        "tp": tp_all,
        "fn": n_all - tp_all,
        "n": n_all,
        "recall": recall_all,
        "recall_ci95": [lo_all, hi_all],
    }
    return summary


def _print_summary(summary: dict[str, dict[str, object]], results: list[SpanResult]) -> None:
    print("=== PII Redaction Recall (synthetic fixture set) ===\n")
    print(f"{'kind':<12} {'recall':>8}  {'95% CI':>18}  {'tp/n':>10}")
    for kind, stats in summary.items():
        recall = stats["recall"]
        lo, hi = stats["recall_ci95"]  # type: ignore[misc]
        tp, n = stats["tp"], stats["n"]
        label = "OVERALL" if kind == "overall" else kind
        print(f"{label:<12} {recall:>7.1%}  [{lo:>6.1%}, {hi:>6.1%}]  {tp:>4}/{n:<5}")

    leaks = [r for r in results if not r.redacted]
    if leaks:
        print(f"\n{len(leaks)} leaked span(s) (false negatives):")
        for r in leaks:
            print(f"  fixture {r.fixture_id} [{r.kind}]: {r.span!r} survived redaction")
    else:
        print("\nNo leaked spans across the fixture set.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    args = parser.parse_args()

    fixtures = _load_fixtures(FIXTURES_PATH)
    results = score_fixtures(fixtures)
    summary = aggregate(results)

    _print_summary(summary, results)

    report = {
        "fixtures_path": str(FIXTURES_PATH.relative_to(REPO_ROOT)),
        "n_fixtures": len(fixtures),
        "n_spans": len(results),
        "summary": summary,
        "leaks": [
            {"fixture_id": r.fixture_id, "kind": r.kind, "span": r.span}
            for r in results
            if not r.redacted
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nReport written to {args.out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
