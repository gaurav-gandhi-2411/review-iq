"""`pros` soft recall, reported beside (never inside) the headline (S18 D2).

Pre-registered in docs/specs/s18-pros-soft-recall.md BEFORE any number here was computed. The
definition, the fixed 0.5 threshold, the validation criteria V-a/V-b/V-c and the decision rules
are in that file; this module only implements them over committed artifacts ($0, no model call).
Nothing here changes a published number: the output is the additive `pros_soft_recall` block of
`eval/results/held_out_scoring_v2.json`. Kept out of `eval/free_text_scoring.py` so the ADR 0030
scorers (and their SCORER_VERSION) are untouched.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from statistics import mean
from typing import Any

from eval.bootstrap import bootstrap_ci

SPEC = "docs/specs/s18-pros-soft-recall.md"
PROS_SOFT_THRESHOLD = 0.5  # fixed by GG before any computation; the 0.3-0.7 sweep cannot change it
SWEEP = (0.3, 0.4, 0.5, 0.6, 0.7)
V_A_MAX_SWING_POINTS = 5.0  # pre-registered
V_B_MIN_CONCORDANCE = 0.85  # pre-registered, per judge, on BOTH sets
DECISION_MATCH_MIN_RECALL = 0.5  # D_X = 1 iff soft recall >= this (pre-registered)
CONDITION = "as_deployed"


def phrase_tokens(phrase: Any) -> frozenset[str]:
    """NFKC, lowercase, punctuation/symbols/underscore -> space, whitespace split into a set.

    No stemming, no stopword removal (pre-registered).
    """
    text = unicodedata.normalize("NFKC", str(phrase)).lower()
    chars = [" " if (unicodedata.category(c)[0] in ("P", "S") or c == "_") else c for c in text]
    return frozenset("".join(chars).split())


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    # An empty token set never matches anything (including another empty one).
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def pros_soft_recall(
    predicted: list[str], gold: list[str], threshold: float = PROS_SOFT_THRESHOLD
) -> float | None:
    """Matched gold phrases / |gold|; None (undefined, excluded) when gold is empty.

    A gold phrase is matched if ANY predicted phrase has token-set Jaccard >= threshold with it
    (not one-to-one). Empty prediction against non-empty gold is 0.0.
    """
    if not gold:
        return None
    pred = [phrase_tokens(p) for p in predicted]
    matched = sum(1 for g in gold if any(jaccard(phrase_tokens(g), p) >= threshold for p in pred))
    return matched / len(gold)


def pros_soft_precision(
    predicted: list[str], gold: list[str], threshold: float = PROS_SOFT_THRESHOLD
) -> float | None:
    """Predicted phrases matching some gold phrase / |predicted|; None when nothing predicted."""
    if not predicted:
        return None
    gld = [phrase_tokens(g) for g in gold]
    matched = sum(
        1 for p in predicted if any(jaccard(phrase_tokens(p), g) >= threshold for g in gld)
    )
    return matched / len(predicted)


def _cell(values: list[float]) -> dict[str, Any]:
    lo, hi = bootstrap_ci(values) if values else (0.0, 0.0)
    return {
        "score": mean(values) if values else None,
        "ci_95": {"lower": lo, "upper": hi, "n": len(values)},
    }


def _recalls(pairs: list[tuple[list[str], list[str]]], threshold: float) -> tuple[list[float], int]:
    """(defined recalls, number excluded because gold was empty)."""
    out = [pros_soft_recall(p, g, threshold) for p, g in pairs]
    return [r for r in out if r is not None], sum(1 for r in out if r is None)


def decision(predicted: list[str], gold: list[str]) -> int | None:
    r = pros_soft_recall(predicted, gold, PROS_SOFT_THRESHOLD)
    return None if r is None else int(r >= DECISION_MATCH_MIN_RECALL)


def _kappa(a: list[int], b: list[int]) -> float | None:
    n = len(a)
    if n == 0:
        return None
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return None if pe == 1 else (po - pe) / (1 - pe)


def _panel2_pros(votes_path: Path) -> dict[str, dict[str, dict[str, list[str] | None]]]:
    """{phase: {review_id: {judge: pros list or None}}} from repetition-0 votes."""
    out: dict[str, dict[str, dict[str, list[str] | None]]] = {}
    for line in votes_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        v = json.loads(line)
        if v["phase"] not in ("main_T", "main_V") or v["rep"] != 0:
            continue
        parsed = v["parsed"]
        pros = parsed.get("pros") if isinstance(parsed, dict) else None
        out.setdefault(v["phase"], {}).setdefault(v["unit_id"], {})[v["model"]] = (
            pros if isinstance(pros, list) else None
        )
    return out


def pros_soft_recall_block(
    records: list[dict[str, Any]],
    fixtures: dict[str, dict[str, Any]],
    panel1_votes: dict[str, dict[str, dict[str, Any]]],
    silver: dict[str, Any],
    votes_path: Path,
) -> dict[str, Any]:
    by_id = {r["id"]: r for r in records}
    unseen = [r for r in records if not r.get("exposure")]
    resolved = [r for r in unseen if "pros" not in r["unresolved_fields"]]
    unresolved = [r for r in unseen if "pros" in r["unresolved_fields"]]

    def pair(r: dict[str, Any]) -> tuple[list[str], list[str]]:
        return r[CONDITION]["predicted"]["pros"], fixtures[r["id"]]["ground_truth"]["pros"]

    res_pairs = [pair(r) for r in resolved]
    sweep: dict[str, Any] = {}
    for t in SWEEP:
        rec, n_empty = _recalls(res_pairs, t)
        sweep[str(t)] = {**_cell(rec), "n_gold_empty_excluded": n_empty}
    rec5, n_empty5 = _recalls(res_pairs, PROS_SOFT_THRESHOLD)
    prec = [pros_soft_precision(p, g) for p, g in res_pairs]
    prec_def = [x for x in prec if x is not None]
    scores = [v["score"] for v in sweep.values()]
    swing_04_06 = abs(sweep["0.4"]["score"] - sweep["0.6"]["score"]) * 100
    swing_all = (max(scores) - min(scores)) * 100

    # --- V-b: concordance of the match decision, model vs each panel-2 judge -------------------
    p2 = _panel2_pros(votes_path)
    active = list(silver["active_panel"])
    sets = {"validation_arm": list(silver["V_review_ids"]), "target_set": sorted(p2["main_T"])}
    phase_of = {"validation_arm": "main_V", "target_set": "main_T"}
    vb: dict[str, Any] = {}
    all_pass = True
    for name, ids in sets.items():
        per_judge: dict[str, Any] = {}
        pooled_m: list[int] = []
        pooled_j: list[int] = []
        for j in active:
            dm: list[int] = []
            dj: list[int] = []
            for rid in ids:
                r = by_id[rid]
                if "pros" in r["unresolved_fields"]:
                    continue  # no panel-1 gold to compare to
                gold = fixtures[rid]["ground_truth"]["pros"]
                jp = p2[phase_of[name]].get(rid, {}).get(j)
                a = decision(r[CONDITION]["predicted"]["pros"], gold)
                b = decision(jp, gold) if jp is not None else None
                if a is None or b is None:
                    continue
                dm.append(a)
                dj.append(b)
            n = len(dm)
            conc = sum(x == y for x, y in zip(dm, dj, strict=True)) / n if n else None
            per_judge[j] = {
                "n_reviews_both_defined": n,
                "concordance": conc,
                "passes_85": conc is not None and conc >= V_B_MIN_CONCORDANCE,
                "base_rate_D1_model": mean(dm) if dm else None,
                "base_rate_D1_judge": mean(dj) if dj else None,
                "cohen_kappa": _kappa(dm, dj),
            }
            pooled_m += dm
            pooled_j += dj
            all_pass = all_pass and per_judge[j]["passes_85"]
        vb[name] = {
            "n_reviews_in_set": len(ids),
            "per_judge": per_judge,
            "pooled_concordance": (
                sum(x == y for x, y in zip(pooled_m, pooled_j, strict=True)) / len(pooled_m)
                if pooled_m
                else None
            ),
        }
    v_a_pass = swing_04_06 <= V_A_MAX_SWING_POINTS

    # --- V-c: resolved vs unresolved gold -------------------------------------------------------
    silver_pairs = silver["silver_pairs"]
    unres_p2: list[float] = []
    n_p2_resolved = 0
    for r in unresolved:
        s = silver_pairs.get(f"{r['id']}.pros")
        if s is not None and s.get("level") in ("unanimous", "majority"):
            n_p2_resolved += 1
            rr = pros_soft_recall(r[CONDITION]["predicted"]["pros"], s["silver"])
            if rr is not None:  # a resolved-but-empty silver list is undefined, excluded
                unres_p2.append(rr)
    unres_p1_by_judge: dict[str, list[float]] = {}
    for r in unresolved:
        for j, vote in panel1_votes[r["id"]]["pros"].items():
            if isinstance(vote, list):
                rr = pros_soft_recall(r[CONDITION]["predicted"]["pros"], vote)
                if rr is not None:
                    unres_p1_by_judge.setdefault(j, []).append(rr)
    pooled_p1 = [x for xs in unres_p1_by_judge.values() for x in xs]

    return {
        "spec": SPEC,
        "definition": (
            "Per review: gold phrases matched / |gold|, a gold phrase matching if some predicted "
            "phrase has token-set Jaccard >= 0.5 (NFKC, lowercase, punctuation stripped, "
            "whitespace split; no stemming/stopwords; not one-to-one). Reviews with empty gold "
            "are undefined and excluded."
        ),
        "threshold": PROS_SOFT_THRESHOLD,
        "condition": CONDITION,
        "population": (
            "unseen reviews (exposure empty) whose pros pair is resolved (panel-1 gold is a label)"
        ),
        "n_unseen_reviews": len(unseen),
        "n_resolved_pros_pairs": len(resolved),
        "n_resolved_gold_empty_excluded": n_empty5,
        "n_scored": len(rec5),
        "soft_recall": _cell(rec5),
        "soft_precision_secondary": {
            **_cell(prec_def),
            "n_predicted_empty_excluded": len(prec) - len(prec_def),
        },
        "threshold_sweep_soft_recall": sweep,
        "swing_points_between_0.4_and_0.6": swing_04_06,
        "swing_points_max_minus_min_0.3_to_0.7": swing_all,
        "validation": {
            "V_a": {
                "criterion": f"|recall(0.4) - recall(0.6)| <= {V_A_MAX_SWING_POINTS} points",
                "swing_points": swing_04_06,
                "pass": v_a_pass,
            },
            "V_b": {
                "criterion": (
                    "every panel-2 judge concordance with the model match decision >= "
                    f"{V_B_MIN_CONCORDANCE:.0%} on BOTH the validation arm and the target set"
                ),
                "sets": vb,
                "pass": all_pass,
            },
            "V_c_reported_no_pass_fail": {
                "resolved_gold_soft_recall": _cell(rec5),
                "unresolved_vs_panel2_silver": {
                    **_cell(unres_p2),
                    "n_unresolved_pros_pairs": len(unresolved),
                    "n_resolved_by_panel2": n_p2_resolved,
                    "n_scored_silver_nonempty": len(unres_p2),
                },
                "unresolved_vs_each_panel1_judge_sensitivity": {
                    "per_judge": {j: _cell(v) for j, v in sorted(unres_p1_by_judge.items())},
                    "pooled": _cell(pooled_p1),
                },
                "gap_resolved_minus_unresolved_panel2_silver_points": (
                    (mean(rec5) - mean(unres_p2)) * 100 if rec5 and unres_p2 else None
                ),
            },
            "validated": v_a_pass and all_pass,
        },
        "headline_scorer_for_pros": (
            "token-level F1 over the pooled token sets (eval.runner._fuzzy_list_score), unchanged"
        ),
    }
