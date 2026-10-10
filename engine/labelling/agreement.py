"""Inter-judge agreement for the review-intent pilot (gate in docs/specs/review-intent.md section 5).

Krippendorff's alpha is computed from the coincidence matrix and handles missing ratings (a judge that
failed to produce a valid answer for an item). Fleiss' kappa needs the same number of raters on every item, so
it is computed on the items every judge answered. Consensus = at least 3 judges (and at least 75 percent of
the valid ones) give the same answer; unanimity is reported separately.
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np

from engine.labelling import schema as S

MISSING = None


def krippendorff_alpha(ratings: list[list], levels: list, ordinal: bool = False) -> float:
    """ratings[i] = the values the raters gave unit i (None = missing). levels = ordered category list."""
    k = len(levels)
    idx = {v: i for i, v in enumerate(levels)}
    o = np.zeros((k, k))
    for unit in ratings:
        vals = [idx[v] for v in unit if v is not MISSING]
        m = len(vals)
        if m < 2:
            continue
        for a in range(m):
            for b in range(m):
                if a != b:
                    o[vals[a], vals[b]] += 1.0 / (m - 1)
    n_c = o.sum(axis=1)
    n = n_c.sum()
    if n <= 1:
        return float("nan")
    delta = np.ones((k, k)) - np.eye(k)
    if ordinal:
        cum = np.cumsum(n_c)
        for c in range(k):
            for d in range(k):
                if c != d:
                    lo, hi = min(c, d), max(c, d)
                    delta[c, d] = (
                        cum[hi] - (cum[lo - 1] if lo else 0) - (n_c[c] + n_c[d]) / 2
                    ) ** 2
    d_o = (o * delta).sum()
    d_e = (np.outer(n_c, n_c) * delta).sum() / (n - 1)
    if d_e == 0:
        return 1.0  # nobody used more than one category: no disagreement is possible
    return float(1 - d_o / d_e)


def fleiss_kappa(complete: list[list]) -> float:
    """complete[i] = the answers of the SAME number of raters on unit i, no missing values."""
    if not complete:
        return float("nan")
    cats = sorted({v for u in complete for v in u}, key=str)
    n_raters = len(complete[0])
    counts = np.array([[u.count(c) for c in cats] for u in complete], dtype=float)
    p_i = ((counts**2).sum(axis=1) - n_raters) / (n_raters * (n_raters - 1))
    p_bar = p_i.mean()
    p_j = counts.sum(axis=0) / (len(complete) * n_raters)
    p_e = (p_j**2).sum()
    return float((p_bar - p_e) / (1 - p_e)) if p_e < 1 else 1.0


def pairwise_agreement(ratings: list[list]) -> float:
    agree = total = 0
    for unit in ratings:
        vals = [v for v in unit if v is not MISSING]
        for a in range(len(vals)):
            for b in range(a + 1, len(vals)):
                total += 1
                agree += vals[a] == vals[b]
    return agree / total if total else float("nan")


def consensus(ratings: list[list]) -> dict[str, float]:
    clear = unanimous = valid_units = 0
    for unit in ratings:
        vals = [v for v in unit if v is not MISSING]
        if len(vals) < 3:
            continue
        valid_units += 1
        top = Counter(vals).most_common(1)[0][1]
        clear += top >= max(3, math.ceil(0.75 * len(vals)))
        unanimous += top == len(vals)
    if not valid_units:
        return {"clear_consensus": float("nan"), "unanimous": float("nan"), "n_units": 0}
    return {
        "clear_consensus": clear / valid_units,
        "unanimous": unanimous / valid_units,
        "n_units": valid_units,
    }


def task_report(ratings: list[list], levels: list, ordinal: bool = False) -> dict:
    complete = [u for u in ratings if all(v is not MISSING for v in u)]
    return {
        "n_units": len(ratings),
        "alpha": round(krippendorff_alpha(ratings, levels, ordinal), 4),
        "fleiss_kappa": round(fleiss_kappa(complete), 4),
        "fleiss_n_complete": len(complete),
        "pairwise_agreement": round(pairwise_agreement(ratings), 4),
        **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in consensus(ratings).items()},
    }


def verdict(r: dict) -> str:
    """The pre-registered gate (spec section 5)."""
    a, c = r["alpha"], r["clear_consensus"]
    if a >= 0.80 and c >= 0.80:
        return "PASS"
    if a >= 0.667 and c >= 0.65:
        return "USABLE-WITH-CAVEAT"
    return "FAIL"


def load_judges(directory: Path) -> tuple[list[str], dict[str, dict[str, dict]]]:
    """-> (judge names, {item_id: {judge: record}}) from <directory>/<judge>.jsonl."""
    judges, items = [], {}
    for f in sorted(directory.glob("*.jsonl")):
        judges.append(f.stem)
        for line in f.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            items.setdefault(r["id"], {})[f.stem] = r
    return judges, items


def build_tasks(
    judges: list[str], items: dict[str, dict[str, dict]], version: str = "v1"
) -> dict[str, dict]:
    def col(fn, ids=None):
        out = []
        for i, recs in items.items():
            if ids is not None and i not in ids:
                continue
            out.append([fn(recs.get(j)) for j in judges])
        return out

    def text(r, key):
        return r["text"][key] if r and r.get("text") else MISSING

    if version == "v2":
        return _build_tasks_v2(judges, items, col, text)
    res: dict[str, dict] = {}
    res["T1_primary_intent"] = task_report(
        col(lambda r: text(r, "primary_intent")), list(S.INTENTS)
    )
    res["T2_sentiment"] = task_report(col(lambda r: text(r, "sentiment")), list(S.SENTIMENTS))
    res["T3_urgency"] = task_report(
        col(lambda r: text(r, "urgency")), list(S.URGENCY), ordinal=True
    )
    res["T4_buy_again"] = task_report(col(lambda r: text(r, "buy_again")), list(S.BUY_AGAIN))
    res["T5_rating_text_mismatch"] = task_report(
        col(lambda r: r.get("mismatch") if r else MISSING), list(S.YES_NO)
    )
    # secondary-intent presence: one binary alpha per class, then the macro mean
    per_class = {}
    for c in S.INTENTS:
        flags = col(
            lambda r, c=c: (
                MISSING
                if not (r and r.get("text"))
                else (c == r["text"]["primary_intent"] or c in r["text"]["secondary_intents"])
            )
        )
        per_class[c] = task_report(flags, [False, True])
    res["T1b_intent_presence_per_class"] = per_class
    mean_alpha = float(np.nanmean([v["alpha"] for v in per_class.values()]))
    res["T1b_intent_presence_macro_alpha"] = round(mean_alpha, 4)
    # aspects: presence pooled per category, sentiment where mentioned
    for cat, aspects in S.ASPECTS.items():
        if not aspects:
            continue
        ids = {
            i
            for i, recs in items.items()
            if any(r and r.get("category") == cat for r in recs.values())
        }
        cells, sent = [], []
        for i in ids:
            for a in aspects:
                cells.append([
                    MISSING if not (items[i].get(j) and items[i][j].get("text")) else a in items[i][j]["text"]["aspects"]
                    for j in judges
                ])  # fmt: skip
                sent.append([
                    (items[i][j]["text"]["aspects"].get(a, MISSING) if items[i].get(j) and items[i][j].get("text") else MISSING)
                    for j in judges
                ])  # fmt: skip
        res[f"T6_aspect_presence_{cat}"] = task_report(cells, [False, True])
        res[f"T6_aspect_sentiment_{cat}"] = task_report(sent, list(S.ASPECT_SENTIMENTS))
    return res


def _build_tasks_v2(judges, items, col, text) -> dict[str, dict]:
    """v2 (spec Amendment 3): no judged mismatch; buy_again is the explicit-no binary; five quoted aspects."""
    from engine.labelling import prompts_v2 as P2

    res: dict[str, dict] = {}
    res["T1_primary_intent"] = task_report(
        col(lambda r: text(r, "primary_intent")), list(S.INTENTS)
    )
    res["T2_sentiment"] = task_report(col(lambda r: text(r, "sentiment")), list(S.SENTIMENTS))
    res["T3_urgency"] = task_report(
        col(lambda r: text(r, "urgency")), list(S.URGENCY), ordinal=True
    )
    res["T4_explicit_no_repurchase"] = task_report(
        col(lambda r: text(r, "explicit_no_repurchase")), list(S.YES_NO)
    )
    per_class = {}
    for c in S.INTENTS:
        flags = col(
            lambda r, c=c: (
                MISSING
                if not (r and r.get("text"))
                else (c == r["text"]["primary_intent"] or c in r["text"]["secondary_intents"])
            )
        )
        per_class[c] = task_report(flags, [False, True])
    res["T1b_intent_presence_per_class"] = per_class
    res["T1b_intent_presence_macro_alpha"] = round(
        float(np.nanmean([v["alpha"] for v in per_class.values()])), 4
    )
    for cat, aspects in P2.ASPECTS_V2.items():
        if not aspects:
            continue
        ids = {
            i
            for i, recs in items.items()
            if any(r and r.get("category") == cat for r in recs.values())
        }
        cells, sent = [], []
        for i in ids:
            for a in aspects:
                cells.append([
                    MISSING if not (items[i].get(j) and items[i][j].get("text")) else a in items[i][j]["text"]["aspects"]
                    for j in judges
                ])  # fmt: skip
                sent.append([
                    (items[i][j]["text"]["aspects"].get(a, MISSING) if items[i].get(j) and items[i][j].get("text") else MISSING)
                    for j in judges
                ])  # fmt: skip
        res[f"T6_aspect_presence_{cat}"] = task_report(cells, [False, True])
        res[f"T6_aspect_sentiment_{cat}"] = task_report(sent, list(S.ASPECT_SENTIMENTS))
    return res


def main() -> None:
    judges, items = load_judges(Path(sys.argv[1]))
    version = sys.argv[3] if len(sys.argv) > 3 else "v1"
    out = {
        "judges": judges,
        "n_items": len(items),
        "version": version,
        "tasks": build_tasks(judges, items, version),
    }
    for v in out["tasks"].values():
        if isinstance(v, dict) and "alpha" in v:
            v["gate"] = verdict(v)
    # strata views for T1/T2/T3 (language and category differ in difficulty)
    strata = sorted({r["stratum"] for recs in items.values() for r in recs.values()})
    out["by_stratum"] = {}
    for s in strata:
        ids = {i for i, recs in items.items() if any(r["stratum"] == s for r in recs.values())}
        sub = {i: items[i] for i in ids}
        t = build_tasks(judges, sub, version)
        out["by_stratum"][s] = {k: {"alpha": t[k]["alpha"], "clear_consensus": t[k]["clear_consensus"], "n_units": t[k]["n_units"]}
                                for k in ("T1_primary_intent", "T2_sentiment", "T3_urgency")}  # fmt: skip
    Path(sys.argv[2]).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"wrote {sys.argv[2]}")


if __name__ == "__main__":
    main()
