"""Render the E2 benchmark tables from the committed result JSONs (no hand-typed numbers).

    python -m engine.experiments.report reports/engine/e2 > docs/reports/e2-benchmark-tables.md

Input layout: <dir>/*.json as produced by engine.experiments.{run,hierarchy,small_data,llm_baseline,
serve_bench}. Means are over the seeds present; ranges are min-max; CIs are the seed-42 bootstrap.
"""

from __future__ import annotations

import json
import re
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

SCORERS = ["msp_raw", "msp_calibrated", "energy", "mahalanobis", "unknown_head"]
REF_MAHA = (
    "23-34% rejection recall at 95% retention, AUROC about 0.87 (private 12-class, 500-row task)"
)


def load(d: Path) -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))}


def f(x: float, nd: int = 3) -> str:
    return f"{x:.{nd}f}"


def rng(v: list[float]) -> str:
    return f"{st.mean(v):.3f} ({min(v):.3f}-{max(v):.3f})" if len(v) > 1 else f"{v[0]:.3f}"


def track_a(r: dict[str, dict]) -> str:
    rows: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for name, d in r.items():
        m = re.match(r"(e5|minilm|xlmr)_(clinc|banking77|massive_en)_A_s(\d+)$", name)
        if m:
            rows[(m[2], m[1])].append(d)
    out = ["| Dataset | Model | Seeds | Macro-F1 mean (range) | Seed-42 macro-F1 [95% CI] | Accuracy mean |",
           "|---|---|---|---|---|---|"]  # fmt: skip
    for (ds, tag), ds_runs in sorted(rows.items()):
        a = [x["track_a"] for x in ds_runs]
        s42 = next((x["track_a"] for x in ds_runs if x["seed"] == 42), a[0])
        out.append(
            f"| {ds} | {tag} | {len(a)} | {rng([x['macro_f1'] for x in a])} | "
            f"{f(s42['macro_f1'])} [{f(s42['macro_f1_ci95'][0])}, {f(s42['macro_f1_ci95'][1])}] | "
            f"{f(st.mean(x['accuracy'] for x in a))} |"
        )
    return "\n".join(out)


PUBLISHED = {  # dataset -> (arm, accuracy, source). Secondary sources: BELIEVED, not re-read in the papers.
    "banking77": ("RoBERTa-base, full data", 0.941, "arXiv 2012.03929"),
    "massive_en": ("XLM-R base, en-US", 0.883, "arXiv 2204.08582 / ACL 2023 long.235, per-locale table"),
    "clinc": ("best aggregator entries, model and split unstated", None, "hyper.ai / wizwand list about 0.97 to 0.98"),
}  # fmt: skip


def like_for_like(r: dict[str, dict]) -> str:
    """Every arm and every published reference in BOTH metrics; a metric a source did not report is 'not reported'."""
    out = ["| Dataset | Arm | Accuracy | Macro-F1 | Basis |", "|---|---|---|---|---|"]
    for ds in ("banking77", "clinc", "massive_en"):
        for tag in ("e5", "minilm", "xlmr"):
            runs = [d["track_a"] for n, d in r.items() if re.fullmatch(rf"{tag}_{ds}_A_s\d+", n)]
            if runs:
                out.append(
                    f"| {ds} | {tag} (ours) | {f(st.mean(x['accuracy'] for x in runs))} | "
                    f"{f(st.mean(x['macro_f1'] for x in runs))} | mean of {len(runs)} seed(s), sealed test |"
                )
        arm, acc, src = PUBLISHED[ds]
        out.append(
            f"| {ds} | published: {arm} | {f(acc) if acc is not None else 'about 0.97 to 0.98'} | not reported | {src} |"
        )
    return "\n".join(out)


def selective(r: dict[str, dict]) -> str:
    """Precision at coverage (GG's headline). Oracle = best threshold on the test curve; transfer = threshold chosen
    on a random half and read on the other (200 splits); conservative = Wilson-lower-bound rule."""
    sc = r.get("selective_curves")
    if not sc:
        return "(selective_curves.json not present)"
    out = [
        "| Dataset | Model | Seeds | Oracle coverage at precision 0.95 (seed 42 [95% CI]) | Precision at coverage 0.9 / 0.8 / 0.7 (seed-42, CI at 0.9) | "
        "Chosen on half, point rule: precision / coverage / hit rate | Chosen on half, conservative rule: precision / coverage / hit rate |",
        "|---|---|---|---|---|---|---|",
    ]  # fmt: skip
    for ds in ("banking77", "clinc", "massive_en"):
        for tag in ("e5", "minilm", "xlmr"):
            key = f"{tag}_{ds}_A_s42"
            if key not in sc:
                continue
            m = sc[key]["msp"]
            seeds = sum(1 for k in sc if re.fullmatch(rf"{tag}_{ds}_A_s\d+", k))
            p9, p8, p7 = (m[f"precision_at_coverage_{c}"] for c in (0.9, 0.8, 0.7))
            t, c = m["split_half_transfer_p95"], m["split_half_transfer_p95_conservative"]
            out.append(
                f"| {ds} | {tag} | {seeds} | {f(m['coverage_at_precision_0.95'])} "
                f"[{f(m['coverage_at_precision_0.95_ci95'][0])}, {f(m['coverage_at_precision_0.95_ci95'][1])}] | "
                f"{f(p9['precision'])} [{f(p9['ci95'][0])}, {f(p9['ci95'][1])}] / {f(p8['precision'])} / {f(p7['precision'])} | "
                f"{f(t['precision_mean'])} / {f(t['coverage_mean'])} / {f(t['hit_rate'], 2)} | "
                f"{f(c['precision_mean'])} / {f(c['coverage_mean'])} / {f(c['hit_rate'], 2)} |"
            )
    return "\n".join(out)


def paired(r: dict[str, dict]) -> str:
    out = ["| Dataset | LLM | n paired | Fine-tuned accuracy | LLM accuracy | Difference [95% CI] | McNemar exact p | LLM coverage at precision 0.95 (verbalised confidence) | Fine-tuned coverage at precision 0.95 on the same items |", "|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for name, d in r.items():
        if name.startswith("paired_"):
            ci = d["accuracy_difference_ci95"]
            ls, fs = d["llm_selective_verbalised_confidence"], d["finetuned_selective"]
            out.append(
                f"| {name[7:]} | {d.get('llm', '')} | {d['n_paired']} | {f(d['finetuned_accuracy'])} | {f(d['llm_accuracy'])} | "
                f"{f(d['accuracy_difference'])} [{f(ci[0])}, {f(ci[1])}] | {d['mcnemar_exact_p']} | "
                f"{f(ls['coverage_at_precision_0.95'])} [{f(ls['coverage_at_precision_0.95_ci95'][0])}, {f(ls['coverage_at_precision_0.95_ci95'][1])}] | "
                f"{f(fs['coverage_at_precision_0.95'])} [{f(fs['coverage_at_precision_0.95_ci95'][0])}, {f(fs['coverage_at_precision_0.95_ci95'][1])}] |"
            )
    return "\n".join(out) if len(out) > 2 else "(no paired LLM run present yet)"


def track_b(r: dict[str, dict]) -> str:
    acc: dict[tuple[str, str, str], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    ci: dict[tuple[str, str, str], dict] = {}
    for name, d in r.items():
        m = re.match(r"e5_(clinc|banking77|massive_en)_B_h(\d+)_s(\d+)$", name)
        if not m:
            continue
        base = d["track_a"]["macro_f1"]
        for o in d["open_set"]:
            for u in ("held_out_classes", "clinc_oos_test"):
                if u not in o:
                    continue
                k = (m[1], u, o["scorer"])
                acc[k]["auroc"].append(o[u]["auroc"])
                acc[k]["rej"].append(o[u]["rejection_recall"])
                acc[k]["ret"].append(o["known_retention"])
                acc[k]["f1drop"].append(base - o["known_macro_f1_answered"])
                if d["seed"] == 42:
                    ci[k] = o[u]
    out = [
        "| Dataset | Unknown set | Scorer | AUROC mean (range) | Rejection recall @95% val-retention | "
        "Seed-42 recall [95% CI] | Seed-42 AUROC [95% CI] | Known retention | Answered macro-F1 drop (pts) | Guard |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]  # fmt: skip
    for k in sorted(acc, key=lambda k: (k[0], k[1], SCORERS.index(k[2]))):
        v, c = acc[k], ci[k]
        ret, drop = st.mean(v["ret"]), 100 * st.mean(v["f1drop"])
        guard = "FAIL" if ret < 0.90 or drop > 2.0 else "ok"
        out.append(
            f"| {k[0]} | {k[1]} | {k[2]} | {rng(v['auroc'])} | {f(st.mean(v['rej']))} | "
            f"[{c['rejection_recall_ci95'][0]}, {c['rejection_recall_ci95'][1]}] | "
            f"[{c['auroc_ci95'][0]}, {c['auroc_ci95'][1]}] | {f(ret)} | {drop:.1f} | {guard} |"
        )
    return "\n".join(out)


def hierarchy(r: dict[str, dict]) -> str:
    out = ["| Dataset | Variant | Macro-F1 [95% CI] | Errors | Within-parent | Cross-parent |", "|---|---|---|---|---|---|"]  # fmt: skip
    for name, d in r.items():
        if not name.endswith("_hier"):
            continue
        for k in ("flat", "joint_unconstrained", "joint_constrained"):
            x = d[k]
            out.append(
                f"| {d['dataset']} ({d['n_intents']} intents, {d['n_parents']} parents) | {k} | "
                f"{f(x['macro_f1'])} [{f(x['macro_f1_ci95'][0])}, {f(x['macro_f1_ci95'][1])}] | "
                f"{x['errors']} | {x['errors_within_parent']} | {x['errors_cross_parent']} |"
            )
    return "\n".join(out)


def small_data(r: dict[str, dict]) -> str:
    out = [
        "| Dataset | Rows (per class) | Method | Macro-F1 [95% CI] | Accuracy |",
        "|---|---|---|---|---|",
    ]
    for name, d in r.items():
        if not name.endswith("_small500"):
            continue
        for k in ("plain", "char_noise", "emb_logreg", "backtrans"):
            if k in d:
                x = d[k]
                out.append(
                    f"| {d['dataset']} | {d['n_train']} ({d['rows_per_class']}) | {k} | {f(x['macro_f1'])} "
                    f"[{f(x['macro_f1_ci95'][0])}, {f(x['macro_f1_ci95'][1])}] | {f(x['accuracy'])} |"
                )
        if "active" in d:
            last = d["active"][-1]
            out.append(
                f"| {d['dataset']} | {last['n']} | active (uncertainty, 100 random + 4x100) | {f(last['macro_f1'])} "
                f"[{f(last['macro_f1_ci95'][0])}, {f(last['macro_f1_ci95'][1])}] | {f(last['accuracy'])} |"
            )
    return "\n".join(out)


def multilingual(r: dict[str, dict]) -> str:
    out = ["| Condition | Model | Macro-F1 all [95% CI] | Non-English macro-F1 | " + " | ".join(
        ["en-US", "hi-IN", "ta-IN", "bn-BD", "de-DE", "ja-JP", "ar-SA", "sw-KE"]) + " |",
        "|---|---|---|---|" + "---|" * 8]  # fmt: skip
    cond = {"e5_massive_8loc_A": ("train 8 locales", "e5"), "minilm_massive_8loc_A": ("train 8 locales", "minilm"),
            "e5_massive_8loc_enonly": ("train en-US only (zero-shot transfer)", "e5")}  # fmt: skip
    for name, (c, tag) in cond.items():
        if name not in r:
            continue
        d = r[name]
        a, ne, loc = d["track_a"], d["track_a_non_english"], d["track_a_by_locale_macro_f1"]
        out.append(
            f"| {c} | {tag} | {f(a['macro_f1'])} [{f(a['macro_f1_ci95'][0])}, {f(a['macro_f1_ci95'][1])}] | "
            f"{f(ne['macro_f1'])} | " + " | ".join(f(loc[k]) for k in loc) + " |"
        )
    return "\n".join(out)


def llm(r: dict[str, dict]) -> str:
    out = ["| Dataset | Model | Shots | n | Unparsed | Accuracy [95% CI] | Macro-F1 on sample | p50 / p95 latency (s) | USD per 1K at published rate |", "|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for name, d in r.items():
        if name.startswith("llm_"):
            ci = d["accuracy_ci95"]
            usd = d["usd_per_1k_messages_at_published_rate"]
            out.append(
                f"| {d['dataset']} | {d['model']} | {d['shots']} | {d['n_completed']} | {d['unparsed']} | "
                f"{f(d['accuracy'])} [{f(ci[0])}, {f(ci[1])}] | {f(d['macro_f1_on_sample'])} | "
                f"{d['latency_p50_s']} / {d['latency_p95_s']} | {usd if usd is not None else 'n/a (no published rate)'} |"
            )
    return "\n".join(out)


def serving(r: dict[str, dict]) -> str:
    out = ["| Model | Threshold | Int8 macro-F1 | Known retention (test) | CLINC OOS rejection | p50 / p95 / p99 ms (1 thread) | RSS MB | Model MB | CPU |", "|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for name, d in r.items():
        if name.startswith("serve_"):
            out.append(
                f"| {name[6:]} | {'recalibrated on int8' if d['recalibrated'] else 'fp32-calibrated'} | "
                f"{f(d['int8_macro_f1'])} | {f(d['known_retention_test'])} | {f(d['oos_rejection_recall'])} | "
                f"{d['latency_ms_p50']} / {d['latency_ms_p95']} / {d['latency_ms_p99']} | {d['rss_mb']} | "
                f"{d['model_mb']} | {d['cpu']} |"
            )
    return "\n".join(out)


def main() -> None:
    r = load(Path(sys.argv[1]))
    sections = [
        ("Like-for-like: accuracy and macro-F1 for every arm and reference", like_for_like(r)),
        ("Track A (known intents)", track_a(r)),
        ("Precision at coverage (the product claim)", selective(r)),
        ("Fine-tuned vs LLM on the SAME items (paired)", paired(r)),
        ("Track B (open set)", track_b(r)),
        ("Hierarchy", hierarchy(r)),
        ("Small data (~500 rows)", small_data(r)),
        ("Multilingual (MASSIVE, 8 locales)", multilingual(r)),
        ("LLM baseline", llm(r)),
        ("Serving (int8 ONNX, CPU)", serving(r)),
    ]
    print(f"<!-- generated by engine/experiments/report.py from {sys.argv[1]}; do not edit -->")
    print(f"Reference point for Track B from a separate private project: {REF_MAHA}.\n")
    for title, body in sections:
        print(f"### {title}\n\n{body}\n")


if __name__ == "__main__":
    main()
