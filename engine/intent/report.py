"""Render reports/intent/model_report.md from an `engine.intent.evaluate` JSON. No number is typed by hand.

    python -m engine.intent.report reports/intent/eval_run1.json > reports/intent/model_report.md

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


def render(rep: dict, src: str) -> str:
    t = rep["tasks"]
    parts = [HEAD.format(src=src), "## Provenance\n", provenance(rep), "## Headline per task\n", summary(t),
             "## Per-class F1\n", per_class(t), "## Baselines\n", baselines(t), "## Language and category slices\n", slices(t)]  # fmt: skip
    return "\n".join(parts)


def main() -> None:
    src = Path(sys.argv[1])
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stdout.write(render(json.loads(src.read_text(encoding="utf-8")), src.as_posix()))


if __name__ == "__main__":
    main()
