"""Amendment 15: score the fresh sealed set. Criterion A (absolute) and B (non-inferior to the all-LLM path), computed, not written.

    PYTHONPATH=. python -m engine.intent.fresh15_eval --items D:/ml-cache/s28/fresh15.jsonl --artifacts D:/ml-cache/s25/classify_artifacts_onnx \
        --judges D:/ml-cache/s28/panel/out/judges --llm-rows D:/ml-cache/s28/llm/out/shards --out reports/intent/fresh15/result.json

Gold needs_action = a label at least 3 of the 4 panel judges agree on (the Amendment 5 rule, `pilot_diag.consensus_label`); items
without it are excluded and counted. R = router R1 (`app.core.classify.encoder_first.route_r1`) on the served static-int8 ONNX decisions
(committed thresholds). L = gpt-oss-20b zero-shot `needs_action` answer (Amendment 12 settings): caught iff it answers `yes`; an empty or
unparseable completion counts as not caught. Item-level paired bootstrap (the fresh corpora carry no product ids). Silver labels.
No review text is written.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np
from app.core.classify import encoder_first as EF
from app.core.classify import runtime

from engine.labelling import agreement as AG
from engine.labelling.pilot_diag import consensus_label
from engine.labelling.repilot2_report import wilson

SEED = 42
N_BOOT = 2000
MARGIN = 0.05
A_LOWER = 0.90
EXPECTED_MIN_POSITIVES = 250


def rate(k: int, n: int) -> dict:
    lo, hi = wilson(k, n) if n else (None, None)
    return {
        "k": k,
        "n": n,
        "rate": round(k / n, 4) if n else None,
        "wilson95": [round(lo, 4), round(hi, 4)] if n else None,
    }


def paired_delta(r: np.ndarray, lm: np.ndarray, n_boot: int = N_BOOT, seed: int = SEED) -> dict:
    """recall_R - recall_L on the same positives; percentile interval from resampling the items."""
    n = len(r)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_boot, n))
    d = r[idx].mean(axis=1) - lm[idx].mean(axis=1)
    lo, hi = np.percentile(d, [2.5, 97.5])
    return {
        "delta": round(float(r.mean() - lm.mean()), 4),
        "boot95": [round(float(lo), 4), round(float(hi), 4)],
        "n": n,
    }


def load_votes(judges_dir: Path, ids: list[str]) -> dict[str, dict[str, str | None]]:
    names, items = AG.load_judges(judges_dir)
    out: dict[str, dict[str, str | None]] = {}
    for i in ids:
        recs = items.get(i, {})
        out[i] = {
            j: (recs[j]["text"]["needs_action"] if recs.get(j) and recs[j].get("text") else None)
            for j in names
        }
    return out


def load_llm(shards: Path) -> dict[str, dict]:
    rows = {}
    for f in sorted(shards.glob("*.jsonl")):
        for ln in f.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                r = json.loads(ln)
                rows[r["id"]] = r
    return rows


def evaluate(items: list[dict], votes: dict, tasks: list[dict], llm: dict[str, dict]) -> dict:
    ids = [r["id"] for r in items]
    strat = {r["id"]: r["stratum"] for r in items}
    gold = {i: consensus_label(list(votes[i].values())) for i in ids}
    labelled = [i for i in ids if gold[i] is not None]
    pos = [i for i in labelled if gold[i] == "yes"]
    pos_idx = [ids.index(i) for i in pos]
    route = np.array([EF.route_r1(tasks[k]).route for k in range(len(ids))])
    llm_yes = np.array(
        [
            bool(
                llm.get(i) and llm[i].get("ans") and llm[i]["ans"]["needs_action"]["label"] == "yes"
            )
            for i in ids
        ]
    )
    r_pos, l_pos = route[pos_idx], llm_yes[pos_idx]
    a = rate(int(r_pos.sum()), len(pos))
    crit_a = bool(a["wilson95"] and a["wilson95"][0] >= A_LOWER)
    d = paired_delta(r_pos.astype(float), l_pos.astype(float)) if len(pos) else None
    crit_b = bool(d and d["boot95"][0] > -MARGIN)
    tally = {i: dict(Counter(v for v in votes[i].values() if v is not None)) for i in pos}
    unanimous = [k for k, i in zip(pos_idx, pos, strict=True) if tally[i].get("yes") == 4]
    split = [k for k, i in zip(pos_idx, pos, strict=True) if tally[i].get("yes") == 3]
    f = float(route.mean())
    ratings = [[votes[i][j] for j in sorted(votes[i])] for i in ids]
    levels = ["no", "yes"]
    return {
        "n_items": len(ids),
        "n_consensus": len(labelled),
        "n_no_consensus": len(ids) - len(labelled),
        "n_gold_yes": len(pos),
        "size_rule": {
            "min_positives": EXPECTED_MIN_POSITIVES,
            "met": len(pos) >= EXPECTED_MIN_POSITIVES,
        },
        "A_router_routing_recall": {**a, "criterion": f"Wilson lower >= {A_LOWER}", "met": crit_a},
        "llm": {
            "rows": len(llm),
            "empty_or_unparseable_or_missing": int(
                sum(1 for i in ids if not (llm.get(i) and llm[i].get("ans")))
            ),
            "ended_at_token_cap": int(sum(1 for r in llm.values() if r.get("fin") == "length")),
            "recall": rate(int(l_pos.sum()), len(pos)),
        },
        "B_non_inferiority": {
            **(d or {}),
            "margin": MARGIN,
            "criterion": f"lower bound of the 95% paired-bootstrap interval of recall_R - recall_L > -{MARGIN}",
            "met": crit_b,
        },
        "criteria_met": crit_a and crit_b,
        "report_only": {
            "router_recall_unanimous_4_0": rate(int(route[unanimous].sum()), len(unanimous)),
            "router_recall_3_1": rate(int(route[split].sum()), len(split)),
            "router_recall_by_stratum": {
                s: rate(
                    int(sum(route[k] for k in pos_idx if strat[ids[k]] == s)),
                    sum(strat[ids[k]] == s for k in pos_idx),
                )
                for s in sorted(set(strat.values()))
            },
            "gold_yes_share_by_stratum": {
                s: rate(
                    sum(1 for i in pos if strat[i] == s), sum(1 for i in labelled if strat[i] == s)
                )
                for s in sorted(set(strat.values()))
            },
            "routed_share_all_items": rate(int(route.sum()), len(ids)),
            "capacity_multiplier_by_open_rate": {
                str(o): round(EF.capacity_multiplier(f, o), 3) for o in (0.0, 0.1, 0.3)
            },
            "panel_needs_action_agreement": AG.task_report(ratings, levels),
            "missed_positives_vote_tallies": {
                ids[k]: tally[ids[k]] for k in pos_idx if not route[k]
            },
        },
    }


def render_md(res: dict) -> str:
    """README with every number in a METRICS block; the verdict comes from `criteria_met`, not from prose."""
    a, b, ll, ro = (
        res["A_router_routing_recall"],
        res["B_non_inferiority"],
        res["llm"],
        res["report_only"],
    )

    def blk(name: str, body: str) -> str:
        return f"<!-- METRICS:START:{name} -->\n{body}\n<!-- METRICS:END -->"

    rows = "\n".join(
        f"| {k} | {v['k']} of {v['n']} | {v['rate']} | {v['wilson95']} |"
        for k, v in {
            "all gold-yes": a,
            "4-0 yes": ro["router_recall_unanimous_4_0"],
            "3-1 yes": ro["router_recall_3_1"],
            **{f"stratum {s}": v for s, v in ro["router_recall_by_stratum"].items()},
        }.items()
    )
    return (
        "# Fresh sealed set: needs_action routing recall (spec Amendment 15, S28)\n\n"
        "Registered in `docs/specs/review-intent.md` Amendment 15 before the set was drawn. 2,000 fresh ENGLISH items (the Hinglish pool was exhausted), "
        "zero overlap with every earlier set (hash, exact text and near-duplicate), labelled by the four-judge panel (SILVER); gold = at least 3 of 4 judges agree. "
        "Router R1 on the served static-int8 ONNX decisions, thresholds unchanged. The all-LLM comparator is gpt-oss-20b zero-shot answering the same needs_action question "
        "(Ollama 0.40.6 pinned and digest-checked for this run, effort low); the production extraction has no needs_action field, so this is a stand-in. "
        "The PANEL run was on an unpinned Ollama build (the pinned kernel version was superseded by a queued earlier version), recorded as a deviation.\n\n"
        "## Sizes\n\n"
        + blk(
            "fresh15_sizes",
            f"items {res['n_items']}; with consensus {res['n_consensus']}; without {res['n_no_consensus']}; gold needs_action=yes {res['n_gold_yes']} "
            f"(size rule: at least {res['size_rule']['min_positives']}, met {res['size_rule']['met']}); panel needs_action alpha "
            f"{ro['panel_needs_action_agreement']['alpha']}, clear consensus {ro['panel_needs_action_agreement']['clear_consensus']}",
        )
        + "\n\n## Criteria\n\n"
        + blk(
            "fresh15_criteria",
            f"A (absolute): router routing recall {a['rate']} ({a['k']} of {a['n']}), Wilson 95 {a['wilson95']}; {a['criterion']}: met {a['met']}\n"
            f"B (relative, non-inferiority): LLM path recall {ll['recall']['rate']} ({ll['recall']['k']} of {ll['recall']['n']}), Wilson 95 {ll['recall']['wilson95']}; "
            f"delta recall_R minus recall_L {b['delta']}, paired bootstrap 95 {b['boot95']}; {b['criterion']}: met {b['met']}\n"
            f"LLM rows {ll['rows']}, empty or unparseable or missing {ll['empty_or_unparseable_or_missing']}, ended at the token cap {ll['ended_at_token_cap']}\n"
            f"BOTH criteria met: {res['criteria_met']}",
        )
        + "\n\n## Router recall by subgroup (report only)\n\n"
        + blk(
            "fresh15_subgroups",
            "| subgroup | routed | recall | Wilson 95 |\n|---|---|---|---|\n" + rows,
        )
        + "\n\n## Capacity on this set (report only)\n\n"
        + blk(
            "fresh15_capacity",
            f"R1 routes {ro['routed_share_all_items']['k']} of {ro['routed_share_all_items']['n']} = {ro['routed_share_all_items']['rate']}; "
            f"multiplier by open rate {ro['capacity_multiplier_by_open_rate']}; gold-yes share by stratum "
            + str({s: v["rate"] for s, v in ro["gold_yes_share_by_stratum"].items()}),
        )
        + "\n\nCriteria met does NOT enable anything: it only removes C3 as the stated reason Amendment 14 says the router must not be enabled. "
        "Persisting skipped reviews (a migration, gate 4) and the on-open extraction path do not exist.\n"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", required=True, type=Path)
    ap.add_argument("--artifacts", required=True, type=Path)
    ap.add_argument("--judges", required=True, type=Path)
    ap.add_argument("--llm-rows", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()
    items = [json.loads(x) for x in a.items.read_text(encoding="utf-8").splitlines() if x.strip()]
    th = runtime.load_thresholds(runtime.THRESHOLDS_ONNX_PATH)
    backend = runtime.OnnxBackend(a.artifacts, th)
    tasks = runtime.decisions(th, backend.logits([r["text"] for r in items]))
    res = evaluate(
        items, load_votes(a.judges, [r["id"] for r in items]), tasks, load_llm(a.llm_rows)
    )
    res = {
        "spec": "docs/specs/review-intent.md Amendment 15",
        "thresholds_sha256": th.sha256,
        **res,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1, default=str) + "\n", encoding="utf-8")
    a.out.with_name("README.md").write_text(render_md(res), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("n_items", "n_consensus", "n_gold_yes", "criteria_met")}))


if __name__ == "__main__":
    main()
