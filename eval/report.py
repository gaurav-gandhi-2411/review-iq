"""Generates a markdown eval report from eval/results.json."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

RESULTS_PATH = Path(__file__).parent / "results.json"
REPORT_PATH = Path(__file__).parent / "report.md"


def generate_report(results_path: Path = RESULTS_PATH) -> str:
    data = json.loads(results_path.read_text(encoding="utf-8"))
    overall: float = data["overall_score"]
    passed: bool = data["passed"]
    threshold: float = data["threshold"]
    fixtures: list[dict] = data["fixtures"]
    per_language: dict = data.get("per_language", {})

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    status = "PASS" if passed else "FAIL"

    lines: list[str] = [
        f"# Eval Report -- {now}",
        "",
        f"**Overall accuracy: {overall:.1%}** -- {status} (threshold: {threshold:.0%})",
        "",
    ]

    if per_language:
        lines += [
            "## Per-language",
            "",
            "| Language | Score | Gate | Status |",
            "| --- | ---: | ---: | --- |",
        ]
        for lang in sorted(per_language):
            info = per_language[lang]
            lang_status = "PASS" if info["passed"] else "FAIL"
            lines.append(
                f"| {lang} | {info['score']:.1%} | {info['threshold']:.0%} | {lang_status} |"
            )
        lines.append("")

    lines += [
        "## Per-fixture results",
        "",
        "| Fixture | Language | Score | Error |",
        "| --- | --- | ---: | --- |",
    ]
    for fx in fixtures:
        error_cell = fx["error"] or ""
        lang = fx.get("language", "en")
        lines.append(f"| {fx['id']} | {lang} | {fx['overall_score']:.0%} | {error_cell} |")

    # Aggregate per-field scores across fixtures
    field_scores: dict[str, list[float]] = {}
    for fx in fixtures:
        if fx["error"]:
            continue
        for f in fx["fields"]:
            field_scores.setdefault(f["field"], []).append(f["score"])

    field_avg = {f: sum(s) / len(s) for f, s in field_scores.items()}
    ranked = sorted(field_avg.items(), key=lambda x: x[1])

    lines += [
        "",
        "## Field accuracy (ascending)",
        "",
        "| Field | Avg Score |",
        "| --- | ---: |",
    ]
    for fname, score in ranked:
        lines.append(f"| {fname} | {score:.0%} |")

    return "\n".join(lines)


def main() -> None:
    if not RESULTS_PATH.exists():
        print(
            f"No results file found at {RESULTS_PATH}. Run eval/runner.py first.", file=sys.stderr
        )
        sys.exit(1)
    report = generate_report()
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"Report written to {REPORT_PATH}")
    print()
    print(report)


if __name__ == "__main__":
    main()
