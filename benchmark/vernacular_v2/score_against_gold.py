"""Score v2.3's real predictions against GG's human gold labels.

Run this AFTER benchmark/vernacular_v2/label_gold.py has produced at least some entries
in gold_labels.jsonl, and after run_predictions.py has produced predictions.jsonl.

Only scores records that have BOTH a human gold label AND a prediction — partial gold
labeling (GG hasn't finished all 210) still produces a valid, honestly-scoped report;
the record count actually scored is always printed and written to the report, so a
partial run is never mistaken for the full benchmark.

Reuses the identical scoring math (accuracy, macro-F1, confusion matrix) as
benchmark/runner.py so numbers here are directly comparable to the existing v0.1
internal benchmark's format.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOLD_PATH = ROOT / "benchmark" / "vernacular_v2" / "gold_labels.jsonl"
PRED_PATH = ROOT / "benchmark" / "vernacular_v2" / "predictions.jsonl"
REPORT_PATH = ROOT / "benchmark" / "vernacular_v2" / "REPORT.md"

TASKS = ["SENT", "URG", "LANG"]
TASK_LABELS = {
    "SENT": ["positive", "neutral", "negative"],
    "URG": ["low", "medium", "high"],
    "LANG": ["en", "hi-en", "hi"],
}


def _accuracy(gold: list[str], pred: list[str]) -> float:
    n = len(gold)
    return sum(g == p for g, p in zip(gold, pred, strict=True)) / n if n else 0.0


def _per_class_f1(gold: list[str], pred: list[str], labels: list[str]) -> dict[str, float]:
    out = {}
    for lbl in labels:
        tp = sum(g == lbl and p == lbl for g, p in zip(gold, pred, strict=True))
        fp = sum(g != lbl and p == lbl for g, p in zip(gold, pred, strict=True))
        fn = sum(g == lbl and p != lbl for g, p in zip(gold, pred, strict=True))
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        out[lbl] = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return out


def _macro_f1(gold: list[str], pred: list[str], labels: list[str]) -> float:
    f1s = _per_class_f1(gold, pred, labels)
    return sum(f1s.values()) / len(labels)


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    gold_records = load_jsonl(GOLD_PATH)
    pred_records = load_jsonl(PRED_PATH)

    if not gold_records:
        print(f"No gold labels found at {GOLD_PATH}.")
        print("Run: uv run python benchmark/vernacular_v2/label_gold.py")
        return

    gold_by_id = {r["id"]: r for r in gold_records}
    pred_by_id = {r["id"]: r for r in pred_records}

    scored_ids = [gid for gid in gold_by_id if gid in pred_by_id]
    unscored = [gid for gid in gold_by_id if gid not in pred_by_id]

    print(f"Gold labels available: {len(gold_records)}")
    print(f"Predictions available: {len(pred_records)}")
    print(f"Scoreable (both present): {len(scored_ids)}")
    if unscored:
        print(f"WARNING: {len(unscored)} gold labels have no matching prediction yet: {unscored[:5]}...")

    slices = sorted({gold_by_id[i].get("slice", "?") for i in scored_ids})

    lines = [
        "# review-iq Vernacular Benchmark v2 — REAL DATA, HUMAN-GOLD-LABELED",
        "",
        "**Source: 3 ODbL/DbCL-licensed Kaggle Flipkart datasets (air cooler / appliance",
        "category), deduped (245,757 unique reviews from 749,084 raw rows across the 3 sources).**",
        "",
        f"Gold labels: **{len(scored_ids)} human-adjudicated** (labeled_by: gg) — NOT LLM-generated.",
        "This is the material difference from the v0.1 internal benchmark (`benchmark/results/`),",
        "whose labels were LLM-generated and explicitly flagged as non-authoritative.",
        "",
        "---",
        "",
        "## Results",
        "",
    ]

    for task in TASKS:
        labels = TASK_LABELS[task]
        lines += [f"### {task}", "", "| Slice | n | Accuracy | Macro-F1 |", "|---|---|---|---|"]
        for sl in ["_all"] + slices:
            ids = scored_ids if sl == "_all" else [i for i in scored_ids if gold_by_id[i].get("slice") == sl]
            g = [gold_by_id[i]["gold"].get(task) for i in ids]
            p = [pred_by_id[i]["pred"].get(task) for i in ids]
            valid = [(gg, pp) for gg, pp in zip(g, p, strict=True) if gg is not None and pp is not None]
            if not valid:
                lines.append(f"| {sl} | 0 | — | — |")
                continue
            gv, pv = zip(*valid, strict=True)
            acc = _accuracy(list(gv), list(pv))
            mf1 = _macro_f1(list(gv), list(pv), labels)
            lines.append(f"| {sl} | {len(valid)} | {acc:.1%} | {mf1:.1%} |")
        lines.append("")

    # Confusion detail for LANG (does the language ROUTER agree with human judgment?)
    lines += ["---", "", "## LANG confusion (router's detected language vs. human-confirmed gold)", ""]
    lang_labels = TASK_LABELS["LANG"]
    lines.append("| Gold \\ Pred | " + " | ".join(lang_labels) + " |")
    lines.append("|---|" + "|".join("---" for _ in lang_labels) + "|")
    for gl in lang_labels:
        row = [gl]
        for pl in lang_labels:
            n = sum(
                1
                for i in scored_ids
                if gold_by_id[i]["gold"].get("LANG") == gl and pred_by_id[i]["pred"].get("LANG") == pl
            )
            row.append(str(n))
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nReport: {REPORT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
