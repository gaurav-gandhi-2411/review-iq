"""Render docs/reports/review-intent-model.md from an `engine.intent.evaluate` JSON. No number is typed by hand.

    python -m engine.intent.report reports/intent/eval_run1.json > docs/reports/review-intent-model.md
    python -m engine.intent.report --run2 reports/intent/eval_run2.json --run1 reports/intent/eval_run1.json \
        --curve reports/intent/run2/epoch_log.json --meta reports/intent/run2/metadata.json \
        --cpu fp32_cpu=... int8_linear=... int8_full=... --quant reports/intent/run2_cpu/metadata.json \
        --latency reports/intent/latency_run2.json > docs/reports/review-intent-model.md

Every number sits inside a `<!-- METRICS:START:<name> --> ... <!-- METRICS:END -->` block (the convention checked by
scripts/check_no_hardcoded_metrics.py); the prose outside the blocks states method and caveats and carries no figures.
Metrics are against four-judge panel consensus (silver), never the truth.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HEAD = """# Review-intent model: sealed-set evaluation

Method and rules: `docs/specs/review-intent.md`, Amendments 2, 4 and 5. Every figure below is rendered from
`{src}` by `engine/intent/report.py`. All metrics are **against panel consensus (silver)**; agreement with a human
audit is not measured. Every interval is a 95 percent bootstrap. Each is written as "item" (resample reviews) and,
where the sealed review could be matched back to a product, also "cluster" (resample products, which keeps reviews of
one product together); an interval marked "item-level only, no product ids" has no cluster counterpart. Sealed items
whose content hash the leakage ledger found in a tracked file are removed from every sealed evaluation.
A class with fewer than 20 sealed positives is reported as "insufficient data" and is given no metric.
The threshold for any precision claim is chosen on VALIDATION (lowest confidence whose Wilson lower bound reaches the
target) and applied once to the sealed set; rows marked ORACLE pick the best point on the sealed set and are not deployable.
The judge baselines are scored leave-one-out (each judge against the consensus of the other three) and have no
confidences, so no matched-coverage comparison with them exists.
"""


def f(x: object, nd: int = 3) -> str:
    if x is None:
        return "n/a"
    return x if isinstance(x, str) else f"{x:.{nd}f}"


def ci(v: list | None) -> str:
    return "n/a" if not v or None in v else f"[{v[0]:.3f}, {v[1]:.3f}]"


def has_ids(r: dict) -> bool:
    c = r.get("cluster")
    return bool(c) and c["n_items_with_product_id"] > 0


def both(item: list | None, clu: list | None, r: dict) -> str:
    """'<item CI> item; <cluster CI> cluster' or, with no product ids behind the task, the explicit marker."""
    if not item and not clu:
        return "n/a (no sealed item answered above the threshold)"
    if not has_ids(r) or clu is None:
        return f"{ci(item)} item-level only, no product ids"
    c = r["cluster"]
    part = (
        ""
        if c["n_items_with_product_id"] == c["n_items"]
        else " (part of the items have no product id and count as their own cluster)"
    )
    return f"{ci(item)} item; {ci(clu)} cluster{part}"


def block(name: str, lines: list[str]) -> str:
    return "\n".join([f"<!-- METRICS:START:{name} -->", *lines, "<!-- METRICS:END -->", ""])


def provenance(rep: dict) -> str:
    p = rep["provenance"]
    rows = [f"- evaluation commit `{p['evaluation_commit']}`; training commit `{p['training_metadata'].get('commit')}`",
            f"- labels: {p['label_basis']}",
            f"- bootstrap: {rep['n_boot']} resamples, seed {rep['seed']}, unit = {rep['bootstrap_unit']}"]  # fmt: skip
    if p.get("ledger_sealed_excluded"):
        x = p["ledger_sealed_excluded"]
        rows.append(f"- ledger `sealed_excluded`: {x['n_hashes']} hashes, {x['n_sealed_items_dropped']} sealed items dropped from every sealed evaluation (ledger sha256[:16] `{x['file_sha256_16']}`)")  # fmt: skip
    if p.get("clusters"):
        x = p["clusters"]
        rows.append(f"- product ids recovered for {x['stats']['n_matched']} of {x['stats']['n_wanted']} sealed items ({x['stats']['n_products']} products); {x['stats']['n_text_under_several_products']} sealed texts occur under more than one product in the source and take the first row, as the sampler's dedup did (clusters file sha256[:16] `{x['file_sha256_16']}`)")  # fmt: skip
        for st, v in x["per_stratum"].items():
            rows.append(f"  - stratum {st}: {v['n_with_product_id']} of {v['n_sealed']} sealed items with a product id, {v['n_products']} products")  # fmt: skip
    rows += [f"- `{k}` sha256[:16] `{v}`" for k, v in {**p["artifacts_sha256_16"], **p.get("baseline_inputs_sha256_16", {})}.items()]  # fmt: skip
    return block("provenance", rows)


def summary(tasks: dict) -> str:
    rows = ["| Task | Sealed labelled | Consensus ceiling | Accuracy [95% CI] | Macro-F1 [95% CI] | Conservative p0.95: coverage [95% CI] | precision [95% CI] | Reached | ORACLE coverage at p0.95 [95% CI] |",
            "|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for t, r in tasks.items():
        if r.get("missing_predictions") or r.get("insufficient_data"):
            rows.append(
                f"| {t} | {r.get('n_sealed_labelled', 'n/a')} | | insufficient data | | | | | |"
            )
            continue
        c, o = r["selective"]["conservative_p0.95"], r["selective"]["oracle"]
        mf = r["macro_f1"]
        rows.append(
            f"| {t} | {r['n']} | {f(r['consensus_ceiling']['fraction'])} | {f(r['accuracy'])} {both(r['accuracy_wilson95'], r.get('accuracy_cluster_ci95'), r)} | "
            f"{f(mf)} {both(r.get('macro_f1_ci95'), r.get('macro_f1_cluster_ci95'), r) if not isinstance(mf, str) else ''} | "
            f"{f(c['coverage'])} {both(c['coverage_ci95'], c.get('coverage_cluster_ci95'), r)} | "
            f"{f(c['precision'])} {both(c['precision_ci95'], c.get('precision_cluster_ci95'), r)} | {'yes' if c['reached_target'] else 'no'} | "
            f"{f(o['coverage_at_precision_0.95'])} {ci(o['coverage_at_precision_0.95_ci95'])} item-level only (oracle curve is not cluster-resampled) |"
        )  # fmt: skip
    return block("summary", rows)


def per_class(tasks: dict) -> str:
    rows = [
        "| Task | Class | Sealed positives | F1 [CI] | Floor 0.60 | Note |",
        "|---|---|---|---|---|---|",
    ]
    for t, r in tasks.items():
        for name, c in (r.get("classes") or {}).items():
            if c["insufficient_data"]:
                rows.append(f"| {t} | {name} | {c['n']} | insufficient data | | no metric given |")
            else:
                note = (
                    "known weak class, excluded from any product claim" if c["below_floor"] else ""
                )
                note += "; support below 30" if c["low_support_lt30"] else ""
                rows.append(f"| {t} | {name} | {c['n']} | {f(c['f1'])} {both(c['f1_ci95'], c.get('f1_cluster_ci95'), r)} | {'below' if c['below_floor'] else 'ok'} | {note.strip('; ')} |")  # fmt: skip
    return block("per_class", rows)


def baselines(tasks: dict) -> str:
    rows = ["| Task | Arm | Accuracy | Macro-F1 | Note |", "|---|---|---|---|---|"]
    for t, r in tasks.items():
        b = r.get("baselines") or {}
        if not b:
            continue
        rows.append(f"| {t} | model | {f(r['accuracy'])} | {f(r['macro_f1'])} | full coverage |")
        if "majority" in b:
            m = b["majority"]
            rows.append(
                f"| {t} | majority ({m['class']}) | {f(m['accuracy'])} | {f(m['macro_f1'])} | no confidence |"
            )
        tf = b.get("tfidf_lr")
        if tf and not isinstance(tf.get("insufficient_data"), str):
            note = "; ".join(
                f"model minus TF-IDF at matched coverage {f(m['target_coverage'])}: {f(m['difference_a_minus_b'])} {ci(m['difference_ci95'])}"
                for k, m in tf.items() if k.startswith("matched_to_model")
            )  # fmt: skip
            rows.append(
                f"| {t} | TF-IDF + LR | {f(tf['accuracy'])} | {f(tf['macro_f1'])} | {note} |"
            )
        for j, v in (b.get("llm_judges_leave_one_out") or {}).items():
            if isinstance(v, dict) and not v["insufficient_data"]:
                mo = v["model_on_same_items"]
                rows.append(
                    f"| {t} | judge {j} (leave-one-out, n={v['n_items']}) | {f(v['judge_missing_as_wrong']['accuracy'])} | "
                    f"{f(v['judge_missing_as_wrong']['macro_f1'])} | answered {f(v['answered_fraction'])}; model on the same items: "
                    f"{f(mo['accuracy'])} / {f(mo['macro_f1'])} |"
                )  # fmt: skip
    return block("baselines", rows)


def slices(tasks: dict) -> str:
    rows = [
        "| Task | Slice | Consensus items | Claimed (at least 100) | Accuracy |",
        "|---|---|---|---|---|",
    ]
    for t, r in tasks.items():
        for s, v in (r.get("slices_accuracy") or {}).items():
            rows.append(
                f"| {t} | {s} | {v['n']} | {'yes' if v['claimed'] else 'no'} | {f(v.get('accuracy'))} |"
            )
    return block("slices", rows)


def comparison(r1: dict, r2: dict) -> str:
    rows = ["| Task | Metric | Run 1 | Run 2 |", "|---|---|---|---|"]
    for t, b in r2.items():
        a = r1.get(t)
        if (
            b.get("insufficient_data")
            or b.get("missing_predictions")
            or not a
            or a.get("insufficient_data")
        ):
            continue

        def cells(r: dict) -> list[tuple[str, str]]:
            c, mf = r["selective"]["conservative_p0.95"], r["macro_f1"]
            return [
                ("accuracy", f"{f(r['accuracy'])} {both(r['accuracy_wilson95'], r.get('accuracy_cluster_ci95'), r)}"),
                ("macro-F1", "insufficient data" if isinstance(mf, str) else f"{f(mf)} {both(r.get('macro_f1_ci95'), r.get('macro_f1_cluster_ci95'), r)}"),
                ("conservative p0.95 coverage", f"{f(c['coverage'])} {both(c['coverage_ci95'], c.get('coverage_cluster_ci95'), r)}"),
                ("precision at that threshold", f"{f(c['precision'])} {both(c['precision_ci95'], c.get('precision_cluster_ci95'), r)}"),
                ("target reached", "yes" if c["reached_target"] else "no"),
            ]  # fmt: skip

        for (m, x), (_, y) in zip(cells(a), cells(b), strict=True):
            rows.append(f"| {t} (run 1 n={a['n']}, run 2 n={b['n']}) | {m} | {x} | {y} |")
    return block("comparison", rows)


def curve(log: list[dict], meta: dict) -> str:
    tasks = ["needs_action", "broad_intent", "sentiment", "urgency"]
    rows = ["| Epoch | Validation mean macro-F1 (selection metric) | " + " | ".join(tasks) + " | Train loss (last batch) |",
            "|---|---|" + "---|" * len(tasks) + "---|"]  # fmt: skip
    for h in log:
        rows.append(f"| {h['epoch']} | {f(h['val_mean_macro_f1'], 4)} | " + " | ".join(f(h['val_macro_f1_by_task'].get(t), 4) for t in tasks) + f" | {f(h['loss'], 4)} |")  # fmt: skip
    i = meta["train_info"]
    rows.append("")
    rows.append(f"Best epoch {i['best_epoch']} of at most {meta['config']['epochs']}; stopped early: {'yes' if i['stopped_early'] else 'no'}; patience {meta['config']['patience']}; best validation mean macro-F1 {f(i['best_val_mean_macro_f1'], 4)}; training commit `{meta['commit']}`; GPU training time {meta['seconds']} s.")  # fmt: skip
    return block("curve", rows)


def delta(u: object, w: object) -> str:
    return "n/a" if isinstance(u, str) or isinstance(w, str) else f(u - w, 3)


def int8(fp: dict, cpu: dict[str, dict], quant: dict) -> str:
    rows = ["| Task | Variant | Accuracy | Macro-F1 | Conservative p0.95 coverage | precision | Reached | Accuracy delta vs GPU fp16 | Macro-F1 delta | Coverage delta | Argmax agreement with GPU on sealed |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for t, b in fp.items():
        if (
            b.get("insufficient_data")
            or b.get("missing_predictions")
            or isinstance(b["macro_f1"], str)
        ):
            continue
        c0 = b["selective"]["conservative_p0.95"]
        rows.append(f"| {t} | GPU fp16 (reference) | {f(b['accuracy'])} | {f(b['macro_f1'])} | {f(c0['coverage'])} | {f(c0['precision'])} | {'yes' if c0['reached_target'] else 'no'} | | | | |")  # fmt: skip
        for v, r in cpu.items():
            x = r["tasks"][t]
            c = x["selective"]["conservative_p0.95"]
            ag = quant["variants"][v]["argmax_agreement_with_gpu_fp16"]["test"][t]
            rows.append(f"| {t} | {v} | {f(x['accuracy'])} | {f(x['macro_f1'])} | {f(c['coverage'])} | {f(c['precision'])} | {'yes' if c['reached_target'] else 'no'} | {delta(x['accuracy'], b['accuracy'])} | {delta(x['macro_f1'], b['macro_f1'])} | {delta(c['coverage'], c0['coverage'])} | {f(ag)} |")  # fmt: skip
    out = [block("int8", rows)]
    sz = [
        "| Model | File size (MB) |",
        "|---|---|",
        f"| fp32 checkpoint | {quant['fp32_checkpoint_mb']} |",
    ]
    sz += [f"| {v} state dict | {m['model_file_mb']} |" for v, m in quant["variants"].items()]
    out.append(block("int8_sizes", sz))
    return "\n".join(out)


def latency(lat: dict) -> str:
    c = lat["cpu"]
    rows = [f"- CPU: {c.get('Name', 'unknown')}; cores {c.get('NumberOfCores', 'n/a')}, logical processors {c.get('NumberOfLogicalProcessors', 'n/a')}; torch threads {lat['torch_threads']}; torch {lat['torch']}; {lat['os']}",  # fmt: skip
            f"- inputs: {lat['n_sealed_texts']} real sealed review texts, mean {lat['tokens_per_text']['mean']} tokens, p95 {lat['tokens_per_text']['p95']}, share truncated at the {lat['max_len']}-token limit {lat['tokens_per_text']['share_truncated_at_128']}; {lat['warmup_calls']} warm-up calls discarded per configuration",  # fmt: skip
            f"- system CPU use before / after the run: {lat['cpu_percent_before']} / {lat['cpu_percent_after']} percent",
            "", "| Variant | Batch | Timed calls | Total p50 ms | p95 ms | p99 ms | Tokenise p50 ms | Forward p50 ms |", "|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for v, d in lat["variants"].items():
        for b, x in d.items():
            t = x["total_ms"]
            rows.append(f"| {v} | {b.split('_')[1]} | {x['calls']} | {t['p50']} | {t['p95']} | {t['p99']} | {x['tokenise_ms']['p50']} | {x['forward_ms']['p50']} |")  # fmt: skip
    return block("latency", rows)


RUN2_NOTES = """Run 2 retrains the run 1 model with only the training length changed: up to 30 epochs instead of 6, patience 3
instead of 2, same seed, learning rate, batch size, maximum length, frozen embeddings and labels. Early stopping looks
at VALIDATION only; the sealed set is never used to choose an epoch, a threshold or a temperature.

Two caveats are part of the result. (1) The stopping metric is the mean macro-F1 over every task that has validation
labels, as in run 1; this includes aspect tasks with very few validation items, so the curve is noisy and the best
epoch is not a stable optimum. "Kept tasks" could also mean only the four core tasks, which would pick a different
epoch; the per-task columns of the curve let a reader check that. (2) The learning rate decays linearly over the epoch
budget, so a 30-epoch run follows a different schedule from a 6-epoch run (slower decay) and stopping early means the
rate had not decayed; run 2 is therefore not a pure "same run, longer" comparison. Both runs are single seeds, so
differences between them are not separated from seed noise by this report.

The Kaggle run records a different manifest hash from the evaluation provenance only because the kernel checks the
repository out with LF line endings and the Windows checkout has CRLF; the content is identical.

The CPU export uses PyTorch dynamic quantisation. Variants: fp32 on CPU (separates CPU-versus-GPU differences from
quantisation error); int8 Linear layers with one scale per tensor, with and without an int8 word-embedding table;
the same with one scale per output channel; and a mixed variant that quantises only the feed-forward layers and
the embedding table and leaves attention and the heads in fp32. For each, probabilities are recomputed, the
per-task temperature is refit on validation, and the conservative threshold is re-picked on validation before the
sealed set is scored once. Read the delta columns: a variant that reaches the precision target at a lower coverage
is not equivalent to the fp16 model.
"""


def render_full(run2: dict, src: str, run1: dict | None, log: list[dict], meta: dict, cpu: dict, quant: dict, lat: dict | None) -> str:  # fmt: skip
    t = run2["tasks"]
    parts = [
        HEAD.format(src=src),
        "## Run 2 setup and caveats\n",
        RUN2_NOTES,
        "## Provenance\n",
        provenance(run2),
    ]
    parts += ["## Training curve (run 2, validation only)\n", curve(log, meta)]
    parts += ["## Headline per task (run 2)\n", summary(t)]
    if run1:
        parts += [
            "## Run 1 and run 2 side by side (same sealed set, same intervals)\n",
            comparison(run1["tasks"], t),
        ]
    parts += ["## Per-class F1 (run 2)\n", per_class(t), "## Baselines (run 2)\n", baselines(t), "## Language and category slices (run 2)\n", slices(t)]  # fmt: skip
    if cpu:
        parts += [
            "## int8 CPU export: probabilities recomputed, temperatures and thresholds refit on validation\n",
            int8(t, cpu, quant),
        ]
    if lat:
        parts += ["## CPU latency\n", latency(lat)]
    return "\n".join(parts)


def render(rep: dict, src: str) -> str:
    t = rep["tasks"]
    parts = [HEAD.format(src=src), "## Provenance\n", provenance(rep), "## Headline per task\n", summary(t),
             "## Per-class F1\n", per_class(t), "## Baselines\n", baselines(t), "## Language and category slices\n", slices(t)]  # fmt: skip
    return "\n".join(parts)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    if len(sys.argv) == 2 and not sys.argv[1].startswith("--"):
        src = Path(sys.argv[1])
        sys.stdout.write(render(json.loads(src.read_text(encoding="utf-8")), src.as_posix()))
        return
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--run2", required=True)
    ap.add_argument("--run1")
    ap.add_argument("--curve", required=True, help="run 2 epoch_log.json")
    ap.add_argument("--meta", required=True, help="run 2 metadata.json")
    ap.add_argument("--cpu", nargs="*", default=[], help="variant=eval.json ...")
    ap.add_argument("--quant")
    ap.add_argument("--latency")
    a = ap.parse_args()
    load = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))  # noqa: E731
    cpu = {kv.split("=", 1)[0]: load(kv.split("=", 1)[1]) for kv in a.cpu}
    sys.stdout.write(render_full(
        load(a.run2), Path(a.run2).as_posix(), load(a.run1) if a.run1 else None, load(a.curve), load(a.meta),
        cpu, load(a.quant) if a.quant else {}, load(a.latency) if a.latency else None,
    ))  # fmt: skip


if __name__ == "__main__":
    main()
