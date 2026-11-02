"""EXPLORATORY (not pre-registered) checks on the re-pilot labels; reported as such.

    python -m engine.labelling.repilot_explore JUDGES_DIR OUT.json

(1) intent collapsed to the reporting groups ACTION / INSIGHT / SIGNAL / NOISE, and to a binary needs-action;
(2) how prevalence deflates alpha for the rare explicit-no flag: positive-class agreement and alpha on the subset of
    items at least one judge flagged; (3) T1 alpha with each judge dropped.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from engine.labelling import agreement as A
from engine.labelling import schema as S


def main() -> None:
    judges, items = A.load_judges(Path(sys.argv[1]))
    random_ids = [
        i
        for i, recs in items.items()
        if not next(iter(recs.values()))["stratum"].startswith("mined_")
    ]
    g_of = {c: g for g, cs in S.GROUPS.items() for c in cs}

    def col(fn, ids, js=judges):
        return [
            [
                fn(items[i][j]["text"]) if items[i].get(j) and items[i][j].get("text") else None
                for j in js
            ]
            for i in ids
        ]

    out: dict = {"label": "EXPLORATORY, not pre-registered", "n_items": len(random_ids)}
    groups = col(lambda t: g_of[t["primary_intent"]], random_ids)
    out["T1_group_level"] = A.task_report(groups, list(S.GROUPS))
    out["T1_needs_action_binary"] = A.task_report(
        col(lambda t: g_of[t["primary_intent"]] == "ACTION", random_ids), [False, True]
    )
    out["T1_praise_vs_not"] = A.task_report(
        col(lambda t: t["primary_intent"] == "praise", random_ids), [False, True]
    )
    flags = col(lambda t: t["explicit_no_repurchase"] == "yes", random_ids)
    out["T4_prevalence"] = {
        "flagged_by_1plus": sum(1 for u in flags if any(x for x in u if x)),
        "flagged_by_2plus": sum(1 for u in flags if sum(1 for x in u if x) >= 2),
        "flagged_by_3plus": sum(1 for u in flags if sum(1 for x in u if x) >= 3),
        "flagged_by_all_valid": sum(
            1 for u in flags if all(x for x in u if x is not None) and any(x for x in u if x)
        ),
    }
    out["T4_alpha_on_items_any_judge_flagged"] = A.task_report(
        [u for u in flags if any(x for x in u if x)], [False, True]
    )
    out["T1_drop_one_judge"] = {
        str(d): A.task_report(col(lambda t: t["primary_intent"], random_ids, [j for j in judges if j != d]), list(S.INTENTS))["alpha"]
        for d in [None, *judges]
    }  # fmt: skip
    Path(sys.argv[2]).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                k: (
                    v
                    if not isinstance(v, dict) or "alpha" not in v
                    else {"alpha": v["alpha"], "consensus": v["clear_consensus"]}
                )
                for k, v in out.items()
            },
            indent=1,
        )[:2600]
    )


if __name__ == "__main__":
    main()
