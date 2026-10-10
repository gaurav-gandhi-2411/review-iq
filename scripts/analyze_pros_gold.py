"""Is the `pros` gold the least reliable field? ($0, no model call; S17 X4c, ADR 0034).

Everything is derived from committed artifacts: the held-out fixtures, the panel-1 batch log
(`eval/consensus/results/held_out_batch_log.jsonl`), the recorded as-deployed predictions
(`eval/results/held_out_scoring_v2.json`) and the panel-2 votes/silver. Output:
`eval/results/pros_gold_analysis.json`. EXPLORATORY: nothing here changes a published number.

Sections (matching the task):
  gold_origin            how each headline field's gold was resolved (unanimous / majority / default)
  resolution_sweep       panel-1 resolution rate vs the Jaccard threshold, pros/cons/topics
  score_sweep            the model's score on resolved pairs under item-level soft matchers
  unresolved_pros        why panel-1 pros pairs fail resolution (judge count, granularity)
  validation_discordant  the V-arm pros pairs where panel 2 and panel 1 disagree, with texts
  headline_exploratory   headline with pros excluded / reported separately
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.bootstrap import bootstrap_ci  # noqa: E402
from eval.consensus import panel2, voting  # noqa: E402
from eval.free_text_scoring import canonical_topic  # noqa: E402
from eval.heldout_unscored import load_judge_votes  # noqa: E402

OUT = ROOT / "eval" / "results" / "pros_gold_analysis.json"
ARTIFACT = ROOT / "eval" / "results" / "held_out_scoring_v2.json"
FIELDS = (
    "product",
    "buy_again",
    "sentiment",
    "topics",
    "competitor_mentions",
    "pros",
    "cons",
    "stars_inferred",
)
THRESHOLDS = (0.3, 0.4, 0.5, 0.6, 0.7)
ITEM_THRESHOLDS = (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0)


def _toks(item: str) -> frozenset[str]:
    # Split on underscores too: canonical topics are snake_case ("sound_quality"), and \w+ would
    # keep each as ONE token, making every threshold equivalent to exact match.
    return frozenset(re.findall(r"[^\W_]+", str(item).lower()))


def soft_f1(pred: list[str], gold: list[str], threshold: float, canon: Any = None) -> float:
    """Item-level soft F1: greedy one-to-one matching of items with token Jaccard >= threshold."""
    if canon is not None:
        pred, gold = [canon(x) for x in pred], [canon(x) for x in gold]
    p, g = [_toks(x) for x in pred], [_toks(x) for x in gold]
    if not p and not g:
        return 1.0
    if not p or not g:
        return 0.0
    cand = sorted(
        (
            (len(a & b) / len(a | b), i, j)
            for i, a in enumerate(p)
            for j, b in enumerate(g)
            if a | b
        ),
        reverse=True,
    )
    used_p: set[int] = set()
    used_g: set[int] = set()
    tp = 0
    for sc, i, j in cand:
        if sc >= threshold and i not in used_p and j not in used_g:
            used_p.add(i)
            used_g.add(j)
            tp += 1
    if tp == 0:
        return 0.0
    prec, rec = tp / len(p), tp / len(g)
    return 2 * prec * rec / (prec + rec)


def token_f1(pred: list[str], gold: list[str]) -> float:
    """Mirror of eval.runner._fuzzy_list_score (the scorer `pros`/`cons` use today)."""
    from eval.runner import _fuzzy_list_score

    return _fuzzy_list_score(pred, gold)


def load() -> dict[str, Any]:
    art = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    from eval.score_held_out_corpus_v2 import load_quarantined_fixtures

    fx = {f["id"]: f for f in load_quarantined_fixtures()}
    return {"art": art, "fx": fx, "votes": load_judge_votes()}


def gold_origin(fx: dict[str, Any], unexposed: set[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for label, ids in (("all_106", set(fx)), ("unexposed_70", unexposed)):
        per: dict[str, dict[str, int]] = {}
        for f in FIELDS:
            c = {"unanimous": 0, "majority": 0, "default_unresolved": 0}
            for i in ids:
                lvl = fx[i]["labeling_meta"]["agreement_per_field"][f]
                c[
                    "unanimous"
                    if lvl == "unanimous"
                    else "majority"
                    if lvl == "majority"
                    else "default_unresolved"
                ] += 1
            per[f] = c
        out[label] = per
    return out


def resolution_sweep(votes: dict[str, Any], ids: set[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in ("pros", "cons", "topics"):
        row: dict[str, Any] = {}
        for t in THRESHOLDS:
            res = un = 0
            for i in ids:
                v = votes[i][f]
                _, level = voting.vote_list_overlap(v, threshold=t)
                if level in ("unanimous", "majority"):
                    res += 1
                if level == "unanimous":
                    un += 1
            row[str(t)] = {"resolved": res, "unanimous": un, "of": len(ids), "rate": res / len(ids)}
        out[f] = row
    return out


def score_sweep(art: dict[str, Any], fx: dict[str, Any], ids: set[str]) -> dict[str, Any]:
    recs = [r for r in art["records"] if r["id"] in ids]
    out: dict[str, Any] = {}
    for f in ("pros", "cons", "topics"):
        pairs = [
            (
                r["as_deployed"]["predicted"][f],
                fx[r["id"]]["ground_truth"][f],
                r["as_deployed"]["field_scores"][f],
            )
            for r in recs
            if f not in r["unresolved_fields"]
        ]
        canon = canonical_topic if f == "topics" else None
        row: dict[str, Any] = {
            "n_resolved_pairs": len(pairs),
            "current_scorer": mean(s for _, _, s in pairs),
        }
        sweep = {
            str(t): mean(soft_f1(p, g, t, canon) for p, g, _ in pairs) for t in ITEM_THRESHOLDS
        }
        row["item_soft_f1_by_threshold"] = sweep
        row["swing_max_minus_min"] = max(sweep.values()) - min(sweep.values())
        row["swing_between_0.3_and_0.7"] = abs(sweep["0.3"] - sweep["0.7"])
        out[f] = row
    return out


def _pair_stats(judge_lists: dict[str, list[str]]) -> dict[str, Any]:
    js = list(judge_lists)
    best_j = best_f = 0.0
    for a in range(len(js)):
        for b in range(a + 1, len(js)):
            la, lb = judge_lists[js[a]] or [], judge_lists[js[b]] or []
            best_j = max(
                best_j, voting._jaccard(voting._normalize_list(la), voting._normalize_list(lb))
            )
            best_f = max(best_f, token_f1(la, lb))
    return {"max_pair_item_jaccard": best_j, "max_pair_token_f1": best_f}


def unresolved_pros(votes: dict[str, Any], fx: dict[str, Any], ids: set[str]) -> dict[str, Any]:
    rows = []
    for i in sorted(ids):
        if fx[i]["labeling_meta"]["agreement_per_field"]["pros"] in voting.__dict__.get("_X", ()):
            continue
        lvl = fx[i]["labeling_meta"]["agreement_per_field"]["pros"]
        if lvl in ("unanimous", "majority"):
            continue
        lists = {j: (v if v != voting.NO_RESPONSE else None) for j, v in votes[i]["pros"].items()}
        responded = sum(1 for v in lists.values() if v is not None)
        empties = sum(1 for v in lists.values() if v is not None and not v)
        st = _pair_stats({j: (v or []) for j, v in lists.items()})
        if responded < 2:
            cls = "fewer_than_2_judges_responded"
        elif empties and responded - empties < 2:
            cls = "omission_(a judge listed no pros)"
        elif st["max_pair_token_f1"] >= 0.5:
            cls = "granularity_(token overlap >= 0.5 but item Jaccard < 0.5)"
        else:
            cls = "different_pros_altogether"
        rows.append({"id": i, "level": lvl, "responded": responded, "class": cls, **st})
    by: dict[str, int] = {}
    for r in rows:
        by[r["class"]] = by.get(r["class"], 0) + 1
    return {"n": len(rows), "by_class": by, "rows": rows}


# Hand classification of the 11 discordant V-arm pros pairs from the actual texts and judge lists
# (printed by this script). Primary cause per pair; "mixed" notes in the ADR. Categories:
#   paraphrase = same points, different wording/granularity (the item-level exact-string matcher
#                cannot see they agree)
#   omission   = at least one judge left out a pro the others (or gold) list
#   different  = judges list different pros (an ambiguous or very short Hinglish text)
HAND_CLASS: dict[str, str] = {
    "hien-0001": "different",
    "hien-0016": "paraphrase",
    "hien-0030": "paraphrase",
    "hien-0046": "paraphrase",
    "hien-0056": "omission",
    "hien-0058": "omission",
    "hien-0066": "paraphrase",
    "hien-0071": "omission",
    "hien-0079": "paraphrase",
    "hien-0089": "omission",
    "hien-0090": "omission",
}


def validation_discordant(art: dict[str, Any], fx: dict[str, Any]) -> dict[str, Any]:
    silver = json.loads(panel2.SILVER_PATH.read_text(encoding="utf-8"))
    votes = [
        json.loads(x)
        for x in panel2.VOTES_PATH.read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]
    active = silver["active_panel"]
    by: dict[str, dict[str, Any]] = {}
    for v in votes:
        if v["phase"] == "main_V" and v["rep"] == 0:
            by.setdefault(v["unit_id"], {})[v["model"]] = v["parsed"]
    rows = []
    for rid in silver["V_review_ids"]:
        outs = {m: by[rid].get(m) for m in active}
        res = panel2.resolve_field("pros", outs)
        gold = fx[rid]["ground_truth"]["pros"]
        resolved = res["level"] in ("unanimous", "majority")
        conc = resolved and panel2.equivalent("pros", res["silver"], gold)
        if conc:
            continue
        lists = {m: (o.get("pros") if o else None) for m, o in outs.items()}
        rows.append(
            {
                "id": rid,
                "text": fx[rid]["review_text"][:160],
                "panel1_gold": gold,
                "panel1_level": fx[rid]["labeling_meta"]["agreement_per_field"]["pros"],
                "panel2_level": res["level"],
                "panel2_silver": res["silver"] if resolved else None,
                "panel2_judges": lists,
                "gold_vs_silver_item_jaccard": (
                    voting._jaccard(
                        voting._normalize_list(gold), voting._normalize_list(res["silver"] or [])
                    )
                    if resolved
                    else None
                ),
                "gold_vs_silver_token_f1": token_f1(res["silver"] or [], gold)
                if resolved
                else None,
                "gold_vs_best_judge_token_f1": max(
                    token_f1(lst or [], gold) for lst in lists.values()
                ),
            }
        )
    for r in rows:
        r["hand_class"] = HAND_CLASS.get(r["id"], "UNCLASSIFIED")
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["hand_class"]] = counts.get(r["hand_class"], 0) + 1
    return {"n_discordant": len(rows), "hand_class_counts": counts, "rows": rows}


def headline_exploratory(art: dict[str, Any]) -> dict[str, Any]:
    fields = art["headline_fields"]
    recs = [r for r in art["records"] if not r.get("exposure")]

    def cell(use: list[str]) -> dict[str, Any]:
        per = []
        for r in recs:
            skip = set(r["unresolved_fields"])
            u = [f for f in use if f not in skip]
            if u:
                per.append(mean(r["as_deployed"]["field_scores"][f] for f in u))
        lo, hi = bootstrap_ci(per)
        return {"score": mean(per), "ci_95": {"lower": lo, "upper": hi, "n": len(per)}}

    pros_only = [
        r["as_deployed"]["field_scores"]["pros"]
        for r in recs
        if "pros" not in r["unresolved_fields"]
    ]
    return {
        "label": "EXPLORATORY, as deployed, unexposed 70 reviews, split pairs excluded",
        "published_headline_all_8_fields": cell(fields),
        "headline_excluding_pros": cell([f for f in fields if f != "pros"]),
        "pros_alone": {"score": mean(pros_only), "n_resolved_pairs": len(pros_only)},
    }


def main() -> None:
    d = load()
    art, fx, votes = d["art"], d["fx"], d["votes"]
    unexposed = {r["id"] for r in art["records"] if not r.get("exposure")}
    out = {
        "label": "EXPLORATORY (S17 X4c); derived from committed artifacts; no published number changes",
        "gold_origin": gold_origin(fx, unexposed),
        "resolution_sweep_unexposed_70": resolution_sweep(votes, unexposed),
        "resolution_sweep_all_106": resolution_sweep(votes, set(fx)),
        "score_sweep_unexposed_70": score_sweep(art, fx, unexposed),
        "unresolved_pros_unexposed_70": unresolved_pros(votes, fx, unexposed),
        "unresolved_pros_all_106": unresolved_pros(votes, fx, set(fx)),
        "validation_discordant_pros": validation_discordant(art, fx),
        "headline_exploratory": headline_exploratory(art),
    }
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Written: {OUT}")


if __name__ == "__main__":
    main()
