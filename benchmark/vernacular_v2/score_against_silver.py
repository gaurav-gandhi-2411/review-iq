"""Score v2.3's real predictions against the SILVER multi-LLM consensus benchmark.

╔══════════════════════════════════════════════════════════════════════════╗
║ SILVER BENCHMARK — silver_labels.jsonl's labels are multi-LLM consensus,  ║
║ NOT human-verified ground truth. Every number below is v2.3's AGREEMENT   ║
║ WITH CONSENSUS, NOT accuracy. DO NOT quote as accuracy externally.        ║
╚══════════════════════════════════════════════════════════════════════════╝

Only fields with an actual consensus (agreement level unanimous or majority —
i.e. silver[field] is not null) are scored. "split" fields (no majority among
the models that voted) are EXCLUDED from the agreement score — there's no
consensus label to agree or disagree with — but v2.3's prediction on those
exact cases is separately surfaced below as the "disagreement cross-cut":
the population most worth a human gold pass if GG ever wants the provable
version, per the task's original framing. Not resolved, just surfaced.

Usage:
    uv run python benchmark/vernacular_v2/score_against_silver.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SILVER_PATH = ROOT / "benchmark" / "vernacular_v2" / "silver_labels.jsonl"
# Optional CLI overrides: argv[1] = predictions path, argv[2] = report output path.
# Defaults are unchanged when no args are given.
PRED_PATH = (
    Path(sys.argv[1]).resolve()
    if len(sys.argv) > 1
    else ROOT / "benchmark" / "vernacular_v2" / "predictions.jsonl"
)
REPORT_PATH = (
    Path(sys.argv[2]).resolve()
    if len(sys.argv) > 2
    else ROOT / "benchmark" / "vernacular_v2" / "SILVER_REPORT.md"
)

FIELDS = ("SENT", "URG", "LANG")

SILVER_WARNING = (
    "SILVER BENCHMARK — labels are multi-LLM consensus, NOT human-verified "
    "ground truth. Numbers below measure AGREEMENT WITH CONSENSUS, NOT accuracy. "
    "DO NOT quote as accuracy externally."
)


def load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def main() -> None:
    silver_raw = load_jsonl(SILVER_PATH)
    metadata = next(
        (r for r in silver_raw if r.get("_marker") == "SILVER_BENCHMARK_METADATA"), None
    )
    silver = {r["id"]: r for r in silver_raw if r.get("_marker") != "SILVER_BENCHMARK_METADATA"}

    preds_raw = load_jsonl(PRED_PATH)
    preds = {r["id"]: r for r in preds_raw if not r["pred"].get("_error")}

    scoreable_ids = [i for i in silver if i in preds]
    print(
        f"Silver-labeled: {len(silver)}  Predicted (no error): {len(preds)}  Scoreable: {len(scoreable_ids)}"
    )

    slices = sorted({silver[i]["slice"] for i in scoreable_ids})

    # --- Agreement per field per slice (only where silver has a consensus label) ---
    agreement_table: dict[str, dict[str, dict]] = {}
    for field in FIELDS:
        agreement_table[field] = {}
        for sl in ["_all"] + slices:
            ids = (
                scoreable_ids
                if sl == "_all"
                else [i for i in scoreable_ids if silver[i]["slice"] == sl]
            )
            with_consensus = [i for i in ids if silver[i]["silver"].get(field) is not None]
            n_split_excluded = len(ids) - len(with_consensus)
            if not with_consensus:
                agreement_table[field][sl] = {
                    "n": 0,
                    "n_split_excluded": n_split_excluded,
                    "agree_pct": None,
                }
                continue
            agree = sum(
                1
                for i in with_consensus
                if preds[i]["pred"].get(field) == silver[i]["silver"][field]
            )
            agreement_table[field][sl] = {
                "n": len(with_consensus),
                "n_split_excluded": n_split_excluded,
                "agree_pct": round(100 * agree / len(with_consensus), 1),
            }

    # --- Disagreement cross-cut: on SPLIT cases, what does v2.3 say? ---
    disagreement_crosscut: dict[str, list[dict]] = defaultdict(list)
    for field in FIELDS:
        split_ids = [i for i in scoreable_ids if silver[i]["agreement"][field] == "split"]
        for i in split_ids:
            votes = silver[i]["votes"][field]
            disagreement_crosscut[field].append(
                {
                    "id": i,
                    "slice": silver[i]["slice"],
                    "text_preview": silver[i]["text"][:100],
                    "model_votes": votes,
                    "v2.3_predicted": preds[i]["pred"].get(field),
                }
            )

    # --- Report ---
    lines = [
        "# review-iq Vernacular Benchmark — SILVER (multi-LLM consensus)",
        "",
        f"**{SILVER_WARNING}**",
        "",
        f"Labeler models: {[m['id'] for m in metadata['_labeler_models']]}"
        if metadata
        else "Labeler models: (metadata missing)",
        f"Consensus rule: {metadata['_consensus_rule']}" if metadata else "",
        "",
        f"Predictions: {len(preds)}/210 candidates have a successful v2.3 extraction "
        f"(the rest hit real Groq daily-quota limits on the dedicated benchmark key — "
        f"not a v2.3 correctness issue, see project memory for the incident).",
        f"Scoreable (silver label + successful prediction both present): {len(scoreable_ids)}",
        "",
        "---",
        "",
        "## Agreement with consensus (NOT accuracy)",
        "",
    ]

    for field in FIELDS:
        lines += [
            f"### {field}",
            "",
            "| Slice | n scored | n split (excluded) | Agreement w/ consensus |",
            "|---|---|---|---|",
        ]
        for sl in ["_all"] + slices:
            s = agreement_table[field][sl]
            pct = f"{s['agree_pct']}%" if s["agree_pct"] is not None else "—"
            lines.append(f"| {sl} | {s['n']} | {s['n_split_excluded']} | {pct} |")
        lines.append("")

    lines += [
        "---",
        "",
        "## Disagreement cross-cut — silver 'split' cases (no model majority)",
        "",
        "Not resolved. These are the population most worth a human gold pass if GG",
        "ever wants the provable version — surfacing where v2.3 lands when even the",
        "3-model panel couldn't agree.",
        "",
    ]
    for field in FIELDS:
        items = disagreement_crosscut[field]
        lines += [f"### {field} ({len(items)} split cases)", ""]
        if not items:
            lines += ["None.", ""]
            continue
        lines += ["| ID | Slice | Text | Model votes | v2.3 predicted |", "|---|---|---|---|---|"]
        for it in items:
            txt = it["text_preview"].replace("|", "/")
            votes_str = ", ".join(f"{m.split('/')[-1]}={v}" for m, v in it["model_votes"].items())
            pred_val = (
                it["v2.3_predicted"]
                if it["v2.3_predicted"] is not None
                else "(no prediction — error)"
            )
            lines.append(f"| {it['id']} | {it['slice']} | {txt} | {votes_str} | {pred_val} |")
        lines.append("")

    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nReport: {REPORT_PATH.relative_to(ROOT)}")

    # --- Console summary ---
    print("\n" + "=" * 60)
    print("SUMMARY (agreement with multi-LLM consensus, NOT accuracy)")
    print("=" * 60)
    for field in FIELDS:
        s = agreement_table[field]["_all"]
        print(f"  {field}: {s['agree_pct']}% (n={s['n']}, {s['n_split_excluded']} split excluded)")


if __name__ == "__main__":
    main()
