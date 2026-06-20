"""Benchmark runner — feeds all candidates to each system, scores, writes results.

Usage:
    uv run python benchmark/runner.py           # record: live calls + cassette recording
    uv run python benchmark/runner.py --replay  # replay: deterministic, zero network calls

Pipeline:
    gold.jsonl → [review-iq | llm-judge | majority-baseline] → score per task/slice → results.json + REPORT.md
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GOLD_PATH = ROOT / "benchmark" / "dataset" / "gold.jsonl"
RESULTS_PATH = ROOT / "benchmark" / "results" / "results.json"
REPORT_PATH = ROOT / "benchmark" / "results" / "REPORT.md"

TASKS = ["SENT", "URG", "LANG"]
_SENT_LABELS = ["positive", "neutral", "negative"]
_URG_LABELS = ["low", "medium", "high"]
_LANG_LABELS = ["en", "hi-en", "hi"]
TASK_LABELS = {"SENT": _SENT_LABELS, "URG": _URG_LABELS, "LANG": _LANG_LABELS}
TASK_PRIMARY_METRIC = {"SENT": "macro_f1", "URG": "macro_f1", "LANG": "accuracy"}


def _load_gold() -> list[dict]:
    records = [
        json.loads(line)
        for line in GOLD_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not records:
        raise RuntimeError(f"No gold records found at {GOLD_PATH}")
    return records


# ---------------------------------------------------------------------------
# Scoring helpers (mirrors benchmark/tasks/ but inline for direct use)
# ---------------------------------------------------------------------------


def _accuracy(gold: list[str], pred: list[str]) -> float:
    return sum(g == p for g, p in zip(gold, pred, strict=True)) / len(gold)


def _per_class_f1(gold: list[str], pred: list[str], labels: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for lbl in labels:
        tp = sum(g == lbl and p == lbl for g, p in zip(gold, pred, strict=True))
        fp = sum(g != lbl and p == lbl for g, p in zip(gold, pred, strict=True))
        fn = sum(g == lbl and p != lbl for g, p in zip(gold, pred, strict=True))
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        out[lbl] = 2.0 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
    return out


def _macro_f1(gold: list[str], pred: list[str], labels: list[str]) -> float:
    return sum(_per_class_f1(gold, pred, labels).values()) / len(labels)


def _confusion(gold: list[str], pred: list[str], labels: list[str]) -> list[list[int]]:
    idx = {l: i for i, l in enumerate(labels)}
    n = len(labels)
    mat = [[0] * n for _ in range(n)]
    for g, p in zip(gold, pred, strict=True):
        gi, pi = idx.get(g), idx.get(p)
        if gi is not None and pi is not None:
            mat[gi][pi] += 1
    return mat


def score_slice(gold: list[str], pred: list[str], task: str) -> dict[str, Any]:
    labels = TASK_LABELS[task]
    valid_mask = [g is not None and p is not None for g, p in zip(gold, pred, strict=True)]
    g_valid = [g for g, ok in zip(gold, valid_mask, strict=True) if ok]
    p_valid = [p for p, ok in zip(pred, valid_mask, strict=True) if ok]
    n_error = sum(1 for ok in valid_mask if not ok)
    if not g_valid:
        return {"n": 0, "n_error": n_error, "accuracy": None, "macro_f1": None}
    acc = _accuracy(g_valid, p_valid)
    mf1 = _macro_f1(g_valid, p_valid, labels)
    per_class = _per_class_f1(g_valid, p_valid, labels)
    confusion = _confusion(g_valid, p_valid, labels)
    primary = mf1 if TASK_PRIMARY_METRIC[task] == "macro_f1" else acc
    return {
        "n": len(g_valid),
        "n_error": n_error,
        "accuracy": round(acc, 4),
        "macro_f1": round(mf1, 4),
        "primary_metric": round(primary, 4),
        "primary_metric_name": TASK_PRIMARY_METRIC[task],
        "per_class_f1": {k: round(v, 4) for k, v in per_class.items()},
        "confusion": confusion,
        "labels": labels,
    }


# ---------------------------------------------------------------------------
# System runners
# ---------------------------------------------------------------------------


async def run_review_iq(gold_records: list[dict], replay_mode: bool) -> list[dict[str, Any]]:
    from benchmark.systems.review_iq import predict  # noqa: PLC0415

    results = []
    for rec in gold_records:
        t0 = time.monotonic()
        pred = await predict(rec["text"], replay_mode)
        latency_ms = int((time.monotonic() - t0) * 1000)
        results.append({"id": rec["id"], "slice": rec["slice"], "pred": pred, "latency_ms": latency_ms})
        err = pred.get("_error", "")
        status = f"SENT={pred.get('SENT')} URG={pred.get('URG')} LANG={pred.get('LANG')}"
        if err:
            status = f"ERROR: {err[:60]}"
        print(f"    [{rec['id']}] {status}  {latency_ms}ms")
    return results


async def run_llm_judge(gold_records: list[dict], replay_mode: bool) -> list[dict[str, Any]]:
    from groq import AsyncGroq  # noqa: PLC0415

    from app.core.config import get_settings  # noqa: PLC0415
    from benchmark._cassette import BenchCassette  # noqa: PLC0415
    from benchmark.systems.llm_judge import JUDGE_CASSETTE_PATH, predict  # noqa: PLC0415

    settings = get_settings()
    cassette = BenchCassette(JUDGE_CASSETTE_PATH)
    groq_client = AsyncGroq(api_key=settings.groq_api_key)

    results = []
    for rec in gold_records:
        t0 = time.monotonic()
        try:
            pred = await predict(rec["text"], groq_client, cassette, replay_mode)
            if pred is None:
                pred = {"SENT": None, "URG": None, "LANG": None, "_error": "parse_failure"}
        except Exception as exc:
            pred = {"SENT": None, "URG": None, "LANG": None, "_error": str(exc)}
        latency_ms = int((time.monotonic() - t0) * 1000)
        results.append({"id": rec["id"], "slice": rec["slice"], "pred": pred, "latency_ms": latency_ms})
        err = pred.get("_error", "")
        status = f"SENT={pred.get('SENT')} URG={pred.get('URG')} LANG={pred.get('LANG')}"
        if err:
            status = f"ERROR: {err[:60]}"
        print(f"    [{rec['id']}] {status}  {latency_ms}ms")
    return results


def run_majority_baseline(gold_records: list[dict]) -> list[dict[str, Any]]:
    from benchmark.systems.majority_baseline import MajorityBaseline  # noqa: PLC0415

    baseline = MajorityBaseline(gold_records)
    results = []
    for rec in gold_records:
        pred = baseline.predict(rec["text"])
        results.append({"id": rec["id"], "slice": rec["slice"], "pred": pred, "latency_ms": 0})
    return results


# ---------------------------------------------------------------------------
# Results aggregation
# ---------------------------------------------------------------------------


def aggregate(
    gold_records: list[dict],
    system_results: list[dict[str, Any]],
    system_id: str,
) -> dict[str, Any]:
    by_id = {r["id"]: r for r in system_results}
    slices = sorted({r["slice"] for r in gold_records})
    all_slices = ["_all"] + slices

    per_task_per_slice: dict[str, dict[str, Any]] = defaultdict(dict)

    for sl in all_slices:
        if sl == "_all":
            subset_gold = gold_records
            subset_pred = [by_id.get(r["id"], {}) for r in gold_records]
        else:
            subset_gold = [r for r in gold_records if r["slice"] == sl]
            subset_pred = [by_id.get(r["id"], {}) for r in subset_gold]

        for task in TASKS:
            gold_labels = [r["gold"].get(task) for r in subset_gold]
            pred_labels = [p.get("pred", {}).get(task) for p in subset_pred]
            per_task_per_slice[task][sl] = score_slice(gold_labels, pred_labels, task)

    # Per-sample detail for divergence analysis
    sample_detail = []
    for rec in gold_records:
        pred_rec = by_id.get(rec["id"], {})
        pred = pred_rec.get("pred", {})
        sample_detail.append({
            "id": rec["id"],
            "slice": rec["slice"],
            "text_preview": rec["text"][:80],
            "gold": rec["gold"],
            "pred": {k: pred.get(k) for k in TASKS},
            "errors": pred.get("_error"),
            "latency_ms": pred_rec.get("latency_ms", 0),
            "diverges": {
                task: rec["gold"].get(task) != pred.get(task) for task in TASKS
            },
        })

    return {
        "system_id": system_id,
        "per_task_per_slice": {t: dict(sv) for t, sv in per_task_per_slice.items()},
        "sample_detail": sample_detail,
    }


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def _fmt_pct(val: float | None) -> str:
    if val is None:
        return "—"
    return f"{val:.1%}"


def write_report(all_results: list[dict], gold_records: list[dict], generated_at: str) -> None:
    from benchmark.systems.llm_judge import (  # noqa: PLC0415
        JUDGE_MODEL,
        JUDGE_SYSTEM_PROMPT,
        JUDGE_USER_TEMPLATE,
        SYSTEM_DESCRIPTION as JUDGE_DESC,
    )
    from benchmark.systems.review_iq import SYSTEM_DESCRIPTION as RIQD  # noqa: PLC0415

    slices = sorted({r["slice"] for r in gold_records})
    n_by_slice = {sl: sum(1 for r in gold_records if r["slice"] == sl) for sl in slices}
    gold_dist: dict[str, dict[str, int]] = {t: defaultdict(int) for t in TASKS}
    for rec in gold_records:
        for task in TASKS:
            lbl = rec["gold"].get(task)
            if lbl:
                gold_dist[task][lbl] += 1

    sys_map = {r["system_id"]: r for r in all_results}

    lines = [
        "# review-iq Vernacular Benchmark v0.1",
        "",
        "**Flipkart e-commerce reviews — audio/headphone category, English + Hinglish**",
        f"Generated: {generated_at}  |  Status: **INTERNAL ONLY — not for publication**",
        "",
        "---",
        "",
        "## ⚠ Core limitation — read before interpreting any score",
        "",
        "**Labels in this benchmark are LLM-generated, not human-validated.**",
        "Specifically: SENT, URG, and LANG labels were generated by",
        "`llama-3.3-70b-versatile` (Groq), not confirmed by a human reading each review.",
        "",
        "This means scores in this benchmark measure **agreement with the labeling LLM**,",
        "not accuracy against ground truth. A high score indicates a system behaves",
        "similarly to the labeling model. It does NOT mean the system is correct.",
        "",
        "Implications:",
        "- The `llm-judge` system uses a model from the same family as the labeling LLM.",
        "  Its high agreement is partly expected from shared pretraining — it is NOT evidence",
        "  of superior accuracy.",
        "- review-iq's scores measure divergence from the labeling LLM's classifications,",
        "  which may reflect genuine differences in extraction strategy, not errors.",
        "- Do NOT quote numbers from this benchmark as accuracy figures in external materials.",
        "",
        "**Path to a publishable v1:** Human-labeled gold set (≈20 min pass by a reviewer).",
        "The candidate list is at `benchmark/dataset/candidates_for_review.jsonl`.",
        "",
        "---",
        "",
        "## Scope — stated first-class",
        "",
        "| Limitation | Detail |",
        "|---|---|",
        "| Single source family | Flipkart Kaggle (niraliivaghani/kabirnagpal/naushads) — same pool as CI fixtures; DIFFERENT specific texts; SHA256 leakage-checked (0 en leaked, all 15 CI hi-en fixtures correctly excluded) |",
        "| Single product category | Audio/headphones only (both en and hi-en) |",
        "| Hindi (hi) deferred | No accessible authentic Hindi review corpus found. IndicSentiment (MIT) rejected: machine-translated from English. Abhishek4896/hindi-english-code-mixed: private (401). |",
        "| Small n | " + "  ".join(f"{sl}: {n_by_slice[sl]}" for sl in slices) + f"  total: {len(gold_records)} |",
        "| hi-en source overlap with CI | hi-en Hinglish text from the same Flipkart Kaggle pool used in CI fixture development — different specific texts, leakage-verified |",
        "| LLM-generated labels | Labels: llama-3.3-70b-versatile. Scores = agreement, not accuracy. |",
        "",
        "---",
        "",
        "## Results",
        "",
        "> Metric per task: SENT → macro F1; URG → macro F1; LANG → accuracy.",
        "> Slices reported separately. Combined (_all) shown for reference only.",
        "> Agreement = how often a system's output matches the LLM reference labels.",
        "",
    ]

    # Results tables per task
    for task in TASKS:
        lines += [f"### Task: {task}", ""]
        header = "| System | " + " | ".join(sl for sl in (["_all"] + slices)) + " |"
        sep = "|---|" + "|".join("---" for _ in (["_all"] + slices)) + "|"
        lines += [header, sep]
        for sys_result in all_results:
            sid = sys_result["system_id"]
            cells = []
            for sl in ["_all"] + slices:
                s = sys_result["per_task_per_slice"].get(task, {}).get(sl, {})
                primary = s.get("primary_metric")
                n = s.get("n", 0)
                cells.append(f"{_fmt_pct(primary)} (n={n})")
            lines.append(f"| {sid} | " + " | ".join(cells) + " |")
        lines.append("")

    # Divergence section: where review-iq diverges from reference labels
    lines += [
        "---",
        "",
        "## Where review-iq diverges from reference labels",
        "",
        "> These are cases where review-iq's extraction differs from the labeling LLM's labels.",
        "> This may reflect extraction strategy differences, not errors. Human judgment needed.",
        "",
    ]
    riq_result = sys_map.get("review-iq", {})
    riq_details = riq_result.get("sample_detail", [])
    for task in TASKS:
        divs = [s for s in riq_details if s["diverges"].get(task) and not s.get("errors")]
        lines += [f"### {task} divergences ({len(divs)} of {len(riq_details)})"]
        if not divs:
            lines += ["None.", ""]
            continue
        lines += ["| ID | Slice | Text (preview) | Gold | Predicted |", "|---|---|---|---|---|"]
        for d in divs[:15]:
            txt = d["text_preview"].replace("|", "/")
            lines.append(
                f"| {d['id']} | {d['slice']} | {txt} | {d['gold'].get(task)} | {d['pred'].get(task)} |"
            )
        lines.append("")

    # System prompts
    lines += [
        "---",
        "",
        "## System documentation",
        "",
        "### review-iq",
        f"_{RIQD}_",
        "",
        "Prompt: see `app/core/prompts/` (en.py, hi_en.py). Version controlled in source.",
        "SENT mapping: `mixed → neutral` (review-iq has 4-class sentiment; benchmark is 3-class).",
        "",
        "### llm-judge",
        f"_{JUDGE_DESC}_",
        "",
        f"**System prompt:** `{JUDGE_SYSTEM_PROMPT}`",
        "",
        "**User template:**",
        "```",
        JUDGE_USER_TEMPLATE,
        "```",
        "",
        "### majority-baseline",
        "Predicts the mode label from the gold distribution for each task, ignoring review text.",
        "This is the floor.",
        "",
        "**Majority labels in this run:**",
    ]
    from benchmark.systems.majority_baseline import MajorityBaseline  # noqa: PLC0415, E402

    baseline = MajorityBaseline(gold_records)
    for task, lbl in baseline.majority_labels().items():
        dist = dict(gold_dist[task])
        lines.append(f"- {task}: `{lbl}` (distribution: {dist})")

    lines += [
        "",
        "---",
        "",
        "## Label methodology",
        "",
        f"Labeling model: `llama-3.3-70b-versatile` (Groq, free tier)",
        f"Labeling prompt SHA256: see `benchmark/dataset/labeling_prompt.txt`",
        "All 43 candidates labeled in a single pass. Labels stored in `benchmark/dataset/gold.jsonl`",
        "with `labels_source: LLM-generated (llama-3.3-70b-versatile, internal benchmark)`.",
        "",
        "Leakage check: 101 CI fixture texts indexed. en: 0 leaked. hi-en: 15 leaked (all CI",
        "hi-en fixtures correctly excluded). Benchmark candidates are disjoint from CI fixtures.",
        "",
        "---",
        "",
        "## Reproducibility",
        "",
        "All LLM calls are cassette-recorded in `benchmark/cassettes/`. Replay with:",
        "```",
        "uv run python benchmark/runner.py --replay",
        "```",
        "This makes zero network calls and produces identical results.json.",
        "",
    ]

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report: {REPORT_PATH}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    replay_mode = "--replay" in sys.argv

    print("=" * 68)
    print("review-iq Vernacular Benchmark v0.1")
    print(f"Mode: {'REPLAY (cassettes)' if replay_mode else 'RECORD (live calls)'}")
    print("=" * 68)

    gold_records = _load_gold()
    slices = sorted({r["slice"] for r in gold_records})
    print(f"\nGold: {len(gold_records)} records across slices: {slices}")

    all_results = []

    # --- majority baseline (no LLM, instant) ---
    print("\n[1/3] majority-baseline")
    mb_raw = run_majority_baseline(gold_records)
    mb_agg = aggregate(gold_records, mb_raw, "majority-baseline")
    all_results.append(mb_agg)

    # --- LLM judge ---
    print("\n[2/3] llm-judge (llama-3.1-8b-instant)")
    judge_raw = await run_llm_judge(gold_records, replay_mode)
    judge_agg = aggregate(gold_records, judge_raw, "llm-judge-llama-3.1-8b")
    all_results.append(judge_agg)

    # --- review-iq ---
    print("\n[3/3] review-iq (production path)")
    riq_raw = await run_review_iq(gold_records, replay_mode)
    riq_agg = aggregate(gold_records, riq_raw, "review-iq")
    all_results.append(riq_agg)

    # --- write results ---
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": generated_at,
        "replay_mode": replay_mode,
        "n_candidates": len(gold_records),
        "slices": slices,
        "systems": all_results,
    }
    RESULTS_PATH.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nResults: {RESULTS_PATH}")

    write_report(all_results, gold_records, generated_at)

    # --- summary table ---
    print("\n" + "=" * 68)
    print("SUMMARY (primary metric per task per slice)")
    print("NOTE: scores = agreement with LLM labels, not accuracy")
    print("=" * 68)
    header = f"{'System':<30}" + "".join(
        f"{'  ' + t + '/' + sl:<18}" for t in TASKS for sl in (["_all"] + slices)
    )
    print(header)
    for res in all_results:
        row = f"{res['system_id']:<30}"
        for t in TASKS:
            for sl in ["_all"] + slices:
                s = res["per_task_per_slice"].get(t, {}).get(sl, {})
                row += f"  {_fmt_pct(s.get('primary_metric')):<16}"
        print(row)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
