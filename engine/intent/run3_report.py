"""Render the run 3 sections of reports/intent/model_report.md (spec Amendment 7). Every number sits in a METRICS block.

Called by `engine.intent.report --run3 reports/intent/run3/report_inputs.json`; the inputs file only lists paths:
  comparison (run3_compare.py), safety (safety_eval.py), meta / meta_run2_local (train metadata.json), eval (eval_run3.json),
  cpu {variant: eval json}, quant (quantize metadata.json), latency (latency json), curve (epoch_log.json).
"""

from __future__ import annotations

import json
from pathlib import Path

from engine.intent.report import block, ci, f, int8, latency

NOTES = """## Run 3: training with the safety stratum (Amendment 7, registered before any run-3 training step)

Run 3 is the run 2 recipe (seed 42, up to 30 epochs, patience 3, validation-only early stopping) with the safety stratum's TRAIN items
added. Safety VAL and TEST items never enter training. Two comparisons are reported and they are separate questions. **Evaluation A**
is the Amendment 5 sealed set, run 3 versus run 2 on the same items, as before. **Evaluation B** is the safety stratum, which is
**enriched, keyword-mined, with silver (panel) labels**: it is never pooled with A and its precision and recall are not base-rate
numbers. Intervals are written as "item bootstrap, no product ids" for A, and as group bootstrap (independent near-duplicate groups) for B.
`run2_local` is the run 2 recipe re-run on this machine with no safety data, to separate environment from data; run 2 itself
was trained on Kaggle. Single seeds throughout: "outside noise" below means the paired item-bootstrap interval excludes 0 and does
not exclude seed noise."""


def load(p: str) -> dict:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def setup(metas: dict[str, dict]) -> str:
    rows = ["| Run | Training commit | Device / torch | Train items | Best epoch / stopped early | Best val mean macro-F1 | Training wall time (s) | Safety stratum |", "|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for n, m in metas.items():
        i = m["train_info"]
        s = m.get("safety_train")
        rows.append(f"| {n} | `{m['commit']}` | {m['device']} / {m['torch']} | {m['n_items']['train']} | {i['best_epoch']} / {'yes' if i['stopped_early'] else 'no'} | {f(i['best_val_mean_macro_f1'], 4)} | {m['seconds']} | {'included: ' + str(s['n_added_train']) + ' train items, ' + str(s['n_urgency_high']) + ' urgency=high' if s else 'excluded'} |")  # fmt: skip
    return block("run3_setup", rows)


def base_table(cmp: dict) -> str:
    rows = ["| Task | n (sealed, ledger exclusions dropped) | Run | Macro-F1 | Accuracy | Difference vs run 2 [paired item bootstrap 95%, no product ids] | Outside noise |", "|---|---|---|---|---|---|---|"]  # fmt: skip
    for t, d in cmp["base_sealed_set"].items():
        for n, r in d["runs"].items():
            diff = (
                "baseline"
                if "diff_vs_baseline" not in r
                else f"{r['diff_vs_baseline']:+.3f} {ci(r['diff_paired_item_boot95'])}"
            )
            rows.append(f"| {t} | {d['n']} | {n} | {f(r['macro_f1'])} | {f(r['accuracy'])} | {diff} | {'yes' if r.get('outside_noise') else 'no' if 'outside_noise' in r else ''} |")  # fmt: skip
    return block("run3_base", rows)


def safety_table(sa: dict) -> str:
    rows = [f"Label basis: {sa['label_basis']}. Bootstrap: {sa['bootstrap']}.",
            f"Test items {sa['n_test_items']}; with a panel urgency label {sa['n_test_items_labelled_urgency']}; ledger-excluded dropped {sa['n_ledger_excluded_dropped']}; evaluated {sa['n_test_items_evaluated']}. "
            f"Independent positive groups (BINDING n): {sa['independent_positive_groups']} after exclusions, {sa['independent_positive_groups_before_ledger_exclusions']} before; "
            f"nominal positive items before exclusions: {sa['nominal_positive_items_before_ledger_exclusions']} (shown beside, not used as n). Safety validation: {sa['n_val_items_evaluated']} items, {sa['n_val_high']} urgency=high.",
            "", "| Run | Operating point | Independent positive groups | Recall [Wilson 95%] [group bootstrap 95%] | Precision [Wilson 95%] [group bootstrap 95%] | Nominal items beside: positives / flagged / true positives |", "|---|---|---|---|---|---|"]  # fmt: skip
    for n, r in sa["runs"].items():
        for op in ("P_argmax", "S_val_f1_threshold", "D_serving_default"):
            if op not in r:
                continue
            x, nb = r[op], r[op]["nominal_items_shown_beside"]
            label = (
                "P: argmax (primary)"
                if op == "P_argmax"
                else f"S: threshold {r['S_threshold_on_safety_val']} chosen on safety validation (secondary)"
                if op == "S_val_f1_threshold"
                else f"D: serving default, threshold {r['D_threshold_on_base_val']} fitted on the Amendment 5 validation set"
            )
            rows.append(f"| {n} | {label} | {x['independent_positive_groups']} | {f(x['recall'])} {ci(x['recall_wilson95'])} {ci(x['recall_group_boot95'])} | {f(x['precision'])} {ci(x['precision_wilson95'])} {ci(x['precision_group_boot95'])} | {nb['positive_items']} / {nb['flagged_items']} / {nb['true_positive_items']} |")  # fmt: skip
    return block("run3_safety", rows)


def safety_paired(sa: dict) -> str:
    pv = sa["paired_vs_baseline"]
    rows = [f"| Run | Operating point | Recall difference vs {pv['baseline']} [paired group bootstrap 95%] | Precision difference [paired group bootstrap 95%] |", "|---|---|---|---|"]  # fmt: skip
    for n, d in pv.items():
        if n == "baseline":
            continue
        for op, x in d.items():
            rows.append(f"| {n} | {op} | {x['recall_diff']:+.3f} {ci(x['recall_diff_group_boot95'])} | {f(x['precision_diff'])} {ci(x['precision_diff_group_boot95'])} |")  # fmt: skip
    return block("run3_safety_paired", rows)


def verdict(v: dict) -> str:
    a, b = v["A_nondegradation"], v["B_safety_gain"]
    rows = [
        f"- Criterion A (every core task's macro-F1 difference vs run 2 >= {a['tolerance']}): {'PASS' if a['pass'] else 'FAIL'}; differences "
        + ", ".join(
            f"{t} {d:+.3f}" for t, d in a["core_macro_f1_diff_vs_run2"].items()
        ),  # fmt: skip
        f"- Criterion B (urgency=high recall up with an interval excluding 0, precision not down by more than 0.05; operating point P): {'PASS' if b['pass'] else 'FAIL'}; recall difference {b['recall_diff']:+.3f} {ci(b['recall_diff_group_boot95'])}; precision difference {f(b['precision_diff'])}",  # fmt: skip
        f"- run2_local differs from run 2 by more than 0.02 macro-F1 on a core task (environment-confounded): {'yes' if v['environment_confounded'] else 'no' if v['environment_confounded'] is not None else 'not measured'}",  # fmt: skip
        f"- **Outcome: {v['outcome']}**",
    ]
    return block("run3_verdict", rows)


def render_run3(inputs_path: str) -> str:
    p = load(inputs_path)
    cmp, sa = load(p["comparison"]), load(p["safety"])
    metas = {"run2_local": load(p["meta_run2_local"]), "run3": load(p["meta"])}
    parts = [NOTES, "\n### Run 3 setup (provenance of the training runs)\n", setup(metas), "### Evaluation A: Amendment 5 sealed set, run 3 versus run 2\n", base_table(cmp),
             "### Evaluation B: safety stratum (enriched, keyword-mined, silver panel labels; reported separately)\n", safety_table(sa), safety_paired(sa),
             "### Verdict against the pre-registered criteria\n", verdict(cmp["verdict"])]  # fmt: skip
    if p.get("cpu"):
        parts += ["### Run 3 int8 CPU export: probabilities recomputed, temperatures and thresholds refit on validation\n",
                  int8(load(p["eval"])["tasks"], {v: load(x) for v, x in p["cpu"].items()}, load(p["quant"]))]  # fmt: skip
    if p.get("latency"):
        parts += [
            "### Run 3 CPU latency (measured exactly as run 2)\n",
            latency(load(p["latency"])),
        ]
    return "\n".join(parts)
