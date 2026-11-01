"""Diagnostics behind the pilot report: what the judges disagree about, class support, the star-rating
cross-check, and the effect of two proposed definition changes (T4 as explicit-no, T5 derived).

    python -m engine.labelling.pilot_diag JUDGES_DIR ITEMS_JSONL OUT.json

ITEMS_JSONL is the private pilot file (it has the star ratings); the output contains no review text.
"""

from __future__ import annotations

import itertools
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

    def col(key: str, js: list[str]) -> list[list]:
        return [
            [recs[j]["text"][key] if recs.get(j) and recs[j].get("text") else None for j in js]
            for recs in items.values()
        ]

    mm = [
        [recs[j].get("mismatch") if recs.get(j) else None for j in judges]
        for recs in items.values()
    ]
    ids = list(items)
    out: dict = {"judges": judges, "n_items": len(items)}

    out["parse_failures"] = {
        j: {
            "text": sum(1 for recs in items.values() if recs.get(j) and recs[j]["text"] is None),
            "mismatch": sum(
                1 for recs in items.values() if recs.get(j) and recs[j]["mismatch"] is None
            ),
        }
        for j in judges
    }
    out["primary_intent_by_judge"] = {
        j: dict(
            Counter(r[judges.index(j)] for r in col("primary_intent", judges) if r[judges.index(j)])
        )
        for j in judges
    }
    tasks = {"T1": col("primary_intent", judges), "T2": col("sentiment", judges),
             "T3": col("urgency", judges), "T4": col("buy_again", judges), "T5": mm}  # fmt: skip
    cons = {k: [consensus_label(u) for u in v] for k, v in tasks.items()}
    out["consensus_distribution"] = {
        k: dict(Counter(x for x in v if x)) | {"_no_consensus": sum(1 for x in v if x is None)}
        for k, v in cons.items()
    }
    out["top_pairwise_confusions"] = {}
    for k, v in tasks.items():
        c: Counter = Counter()
        for u in v:
            for a, b in itertools.combinations(u, 2):
                if a is not None and b is not None and a != b:
                    c[" | ".join(sorted((str(a), str(b))))] += 1
        out["top_pairwise_confusions"][k] = c.most_common(5)

    out["drop_one_judge"] = {}
    for drop in [None, *judges]:
        js = [j for j in judges if j != drop]
        mm_js = [
            [recs[j].get("mismatch") if recs.get(j) else None for j in js]
            for recs in items.values()
        ]
        out["drop_one_judge"][str(drop)] = {
            "T1_alpha": A.task_report(col("primary_intent", js), list(S.INTENTS))["alpha"],
            "T4_alpha": A.task_report(col("buy_again", js), list(S.BUY_AGAIN))["alpha"],
            "T5_alpha": A.task_report(mm_js, list(S.YES_NO))["alpha"],
        }

    flagged = {}
    for c in S.INTENTS:
        n_by = Counter()
        for recs in items.values():
            k = sum(
                1 for j in judges
                if recs.get(j) and recs[j].get("text")
                and (c == recs[j]["text"]["primary_intent"] or c in recs[j]["text"]["secondary_intents"])
            )  # fmt: skip
            for t in (1, 2, 3):
                n_by[t] += k >= t
        flagged[c] = {"flagged_by_at_least_1": n_by[1], "by_2": n_by[2], "by_3": n_by[3]}
    out["intent_support_flagged_by_k_judges"] = flagged

    # external cross-check: consensus sentiment against the star rating (never shown to the judges)
    sent = dict(zip(ids, cons["T2"], strict=True))
    bands = {"1-2": lambda s: s <= 2, "3": lambda s: s == 3, "4-5": lambda s: s >= 4}
    out["sentiment_vs_stars"] = {
        b: dict(Counter(sent[i] for i in ids if sent[i] and f(stars[i]))) for b, f in bands.items()
    }
    # proposed T4: explicit-no only (binary), and proposed T5: derived from stars + consensus sentiment
    t4_bin = [[None if x is None else (x == "no") for x in u] for u in tasks["T4"]]
    out["proposed_T4_explicit_no_binary"] = A.task_report(t4_bin, [False, True])
    derived = [
        ("yes" if (sent[i] == "negative" and stars[i] >= 4) or (sent[i] == "positive" and stars[i] <= 2) else "no")
        for i in ids if sent[i]
    ]  # fmt: skip
    out["proposed_T5_derived"] = {
        "items_with_consensus_sentiment": len(derived),
        "mismatch_yes": derived.count("yes"),
        "judge_consensus_yes": sum(1 for x in cons["T5"] if x == "yes"),
        "judge_consensus_n": sum(1 for x in cons["T5"] if x),
    }
    Path(sys.argv[3]).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {sys.argv[3]}")


if __name__ == "__main__":
    main()
