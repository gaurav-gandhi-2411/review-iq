"""Re-pilot 1 numbers (spec Amendment 3): agreement on the RANDOM 300 and on the MINED 180 separately, class support
across both, and the derived mismatch rule on the random items.

    python -m engine.labelling.repilot_report JUDGES_DIR ITEMS_JSONL OUT.json

ITEMS_JSONL is the private pilot file (star ratings); the output carries no review text.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from engine.labelling import agreement as A
from engine.labelling import schema as S


def consensus_label(vals: list) -> str | None:
    v = [x for x in vals if x is not None]
    if len(v) < 3:
        return None
    lab, n = Counter(v).most_common(1)[0]
    return lab if n >= max(3, -(-3 * len(v) // 4)) else None


def main() -> None:
    judges, items = A.load_judges(Path(sys.argv[1]))
    stars = {}
    for line in Path(sys.argv[2]).read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        stars[r["id"]] = r["stars"]
    random_ids = {
        i
        for i, recs in items.items()
        if not next(iter(recs.values()))["stratum"].startswith("mined_")
    }
    mined_ids = set(items) - random_ids
    out: dict = {"judges": judges, "n_random": len(random_ids), "n_mined": len(mined_ids)}

    for name, ids in (("random", random_ids), ("mined", mined_ids), ("all", set(items))):
        sub = {i: items[i] for i in ids}
        tasks = A.build_tasks(judges, sub, "v2")
        for v in tasks.values():
            if isinstance(v, dict) and "alpha" in v:
                v["gate"] = A.verdict(v)
        out[f"tasks_{name}"] = tasks

    def col(key: str, ids: set[str]) -> list[list]:
        return [
            [
                items[i][j]["text"][key] if items[i].get(j) and items[i][j].get("text") else None
                for j in judges
            ]
            for i in sorted(ids)
        ]

    cons = {
        k: [consensus_label(u) for u in col(k, set(items))]
        for k in ("primary_intent", "sentiment", "urgency", "explicit_no_repurchase")
    }
    order = sorted(items)
    out["consensus_distribution"] = {
        k: dict(Counter(x for x in v if x)) | {"_no_consensus": sum(1 for x in v if x is None)}
        for k, v in cons.items()
    }
    # support of each intent class: primary-intent consensus items, in random / mined / both
    sup = {}
    for c in S.INTENTS:
        n_r = sum(
            1
            for i, lab in zip(order, cons["primary_intent"], strict=True)
            if lab == c and i in random_ids
        )
        n_m = sum(
            1
            for i, lab in zip(order, cons["primary_intent"], strict=True)
            if lab == c and i in mined_ids
        )
        sup[c] = {
            "random": n_r,
            "mined": n_m,
            "total": n_r + n_m,
            "measurable_30plus": n_r + n_m >= 30,
        }
    out["intent_support"] = sup
    # presence support: class flagged (primary or secondary) by 3+ judges
    pres = {}
    for c in S.INTENTS:
        k = 0
        for i in order:
            n = sum(
                1 for j in judges
                if items[i].get(j) and items[i][j].get("text")
                and (c == items[i][j]["text"]["primary_intent"] or c in items[i][j]["text"]["secondary_intents"])
            )  # fmt: skip
            k += n >= 3
        pres[c] = k
    out["intent_presence_flagged_by_3plus_judges"] = pres
    sent = dict(zip(order, cons["sentiment"], strict=True))
    derived = [
        (
            "yes"
            if (sent[i] == "negative" and stars[i] >= 4) or (sent[i] == "positive" and stars[i] <= 2)
            else "no"
        )
        for i in sorted(random_ids) if sent[i]
    ]  # fmt: skip
    out["derived_mismatch_random"] = {
        "items_with_consensus_sentiment": len(derived),
        "mismatch_yes": derived.count("yes"),
    }
    out["parse"] = {
        j: {
            "failed": sum(1 for recs in items.values() if recs.get(j) and recs[j]["text"] is None),
            "retried": sum(1 for recs in items.values() if recs.get(j) and recs[j].get("retried")),
            "aspects_dropped_unverifiable": sum(recs[j].get("aspects_dropped_unverifiable", 0) for recs in items.values() if recs.get(j)),
        }
        for j in judges
    }  # fmt: skip
    Path(sys.argv[3]).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {sys.argv[3]}")


if __name__ == "__main__":
    main()
