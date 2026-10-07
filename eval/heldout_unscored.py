"""How much of the held-out headline is NOT scored, and what that does to it (Session 17, W3).

The published held-out headline averages `headline_fields` over the reviews the development
process had not seen, **excluding every (review, field) pair on which the three-judge panel did
not reach consensus**. Excluding those pairs is right -- the stored gold for a split pair is a
schema-valid default, not a label, and scoring it as one blames the model for the panel's
disagreement (ADR 0032). But it is also a selection effect: split pairs are where the panel could
not agree, which is where the review is hardest or most ambiguous, so the headline is accuracy
*where the panel agreed*, not overall accuracy. This module measures how big that gap in coverage
is and bounds what it could do to the number. Everything is derived from the committed artifact
records (and, for the judge sensitivity, the committed judge votes): no model call, deterministic.

Definitions (precise, because every figure below depends on them):

  field-pair   one (review, field) cell: a review in the published cell (unseen reviews) crossed
               with one headline field. Pairs = n_reviews x len(headline_fields).
  unscored     a field-pair whose field is in the review's `unresolved_fields` (panel split).
  unscored fraction = unscored pairs / pairs, overall and per field.

Bounds (Manski, 1989-style worst/best case, no assumption about WHY pairs are unscored):
  every unscored pair assigned score 0 (lower bound) or 1 (upper bound), then the same unit as the
  headline -- per-review mean over headline fields, averaged over reviews. These bound the score
  the model would get against the TRUE labels of every pair, whatever they are, because any pair
  score lies in [0, 1]. They are bounds, never a headline.

Is a point estimate identifiable? Not from this data. Whether an unscored pair is answered
correctly is exactly what is unobserved, and the reason it is unobserved (panel disagreement) is
plausibly correlated with whether it is answered correctly, so scored-only accuracy is not a
consistent estimate of overall accuracy and no assumption short of an independent adjudication
of the 60 pairs identifies one. `judge_sensitivity` narrows the interval under a stated, weaker-
than-point assumption (the true label is one of the three judges' answers) and is labelled so.
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any

from eval.bootstrap import bootstrap_ci

ROOT = Path(__file__).resolve().parent.parent
BATCH_LOG_PATH = ROOT / "eval" / "consensus" / "results" / "held_out_batch_log.jsonl"
CONDITION = "as_deployed"

DEFINITION = (
    "A field-pair is one (review, headline field) cell on the reviews in the published cell; it "
    "is unscored when the three-judge panel did not reach unanimous/majority agreement on that "
    "field for that review, so the stored gold is a default, not a label."
)


def _unscored_set(record: dict[str, Any], fields: list[str]) -> set[str]:
    return set(record.get("unresolved_fields", ())) & set(fields)


def _published_records(
    records: list[dict[str, Any]], exclude_exposed: bool
) -> list[dict[str, Any]]:
    return [r for r in records if not (exclude_exposed and r.get("exposure"))]


def unscored_block(
    records: list[dict[str, Any]], fields: list[str], *, exclude_exposed: bool = True
) -> dict[str, Any]:
    """Unscored fraction and Manski bounds for the published cell (as deployed)."""
    kept = _published_records(records, exclude_exposed)
    n = len(kept)
    pairs = n * len(fields)
    per_field: dict[str, dict[str, Any]] = {}
    for f in fields:
        k = sum(1 for r in kept if f in _unscored_set(r, fields))
        per_field[f] = {"pairs": n, "unscored": k, "unscored_fraction": k / n if n else 0.0}
    unscored = sum(v["unscored"] for v in per_field.values())

    # Per-review means over ALL headline fields with each unscored pair filled with `fill`.
    def filled(fill: float) -> list[float]:
        out = []
        for r in kept:
            skip = _unscored_set(r, fields)
            fs = r[CONDITION]["field_scores"]
            out.append(mean(fill if f in skip else fs[f] for f in fields))
        return out

    lower_v, upper_v = filled(0.0), filled(1.0)
    scored_only = []
    for r in kept:
        skip = _unscored_set(r, fields)
        used = [f for f in fields if f not in skip]
        if used:
            scored_only.append(mean(r[CONDITION]["field_scores"][f] for f in used))
    defaults_as_labels = [mean(r[CONDITION]["field_scores"][f] for f in fields) for r in kept]

    def cell(values: list[float]) -> dict[str, Any]:
        lo, hi = bootstrap_ci(values) if values else (0.0, 0.0)
        return {
            "score": mean(values) if values else 0.0,
            "ci_95": {"lower": lo, "upper": hi, "n": len(values)},
        }

    per_field_bounds: dict[str, dict[str, float]] = {}
    for f in fields:
        resolved = [
            r[CONDITION]["field_scores"][f] for r in kept if f not in _unscored_set(r, fields)
        ]
        k = per_field[f]["unscored"]
        per_field_bounds[f] = {
            "scored_only": mean(resolved) if resolved else 0.0,
            "lower_unscored_all_wrong": sum(resolved) / n if n else 0.0,
            "upper_unscored_all_correct": (sum(resolved) + k) / n if n else 0.0,
        }

    fully = sum(1 for r in kept if _unscored_set(r, fields) == set(fields))
    return {
        "definition": DEFINITION,
        "cell": "unexposed_split_excluded" if exclude_exposed else "all_reviews_split_excluded",
        "condition": CONDITION,
        "fields": list(fields),
        "n_reviews": n,
        "n_pairs": pairs,
        "n_pairs_unscored": unscored,
        "unscored_fraction": unscored / pairs if pairs else 0.0,
        "n_reviews_with_any_unscored_pair": sum(1 for r in kept if _unscored_set(r, fields)),
        "n_reviews_with_every_pair_unscored": fully,
        "per_field": per_field,
        "scored_only": cell(scored_only),
        "bounds": {
            "method": (
                "Manski worst/best case: every unscored pair scored 0 (lower) or 1 (upper); "
                "per-review mean over all headline fields, averaged over reviews (the headline's "
                "unit). Valid for any true labels, as each pair score lies in [0, 1]."
            ),
            "lower_unscored_all_wrong": cell(lower_v),
            "upper_unscored_all_correct": cell(upper_v),
            "per_field": per_field_bounds,
        },
        # NOT a bound and NOT an estimate: the stored default scored as if it were a label. It is
        # in the artifact so a reader can see how far the old "all pairs" figure sits inside the
        # interval; the default is not a label, so it measures nothing.
        "defaults_scored_as_labels_reference": cell(defaults_as_labels),
        "point_estimate_identifiable": False,
        "identifiability_note": (
            "Not identifiable from this data: whether an unscored pair is answered correctly is "
            "the unobserved quantity, and unscored pairs are by construction the ones the panel "
            "could not agree on, so scored-only accuracy is accuracy where the panel agreed. "
            "Only an independent adjudication of the unscored pairs, or an assumption stated "
            "and defended outside this data, yields a point estimate."
        ),
    }


def load_judge_votes(path: Path = BATCH_LOG_PATH) -> dict[str, dict[str, dict[str, Any]]]:
    """{fixture_id: {field: {judge_id: vote}}} from the LAST batch-log entry per fixture.

    The log is append-only and 40 fixtures were re-labelled; the last entry is the one the
    committed fixtures were built from (`validate_votes_match_gold` pins that).
    """
    last: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            last[row["fixture_id"]] = row
    return {
        fid: {field: dict(c.get("votes", {})) for field, c in row["consensus"].items()}
        for fid, row in last.items()
    }


def judge_sensitivity(
    records: list[dict[str, Any]],
    fixtures: dict[str, dict[str, Any]],
    votes: dict[str, dict[str, dict[str, Any]]],
    fields: list[str],
    *,
    exclude_exposed: bool = True,
) -> dict[str, Any]:
    """Score each unscored pair against each judge's own answer, instead of the default.

    ASSUMPTION (stated, not defended): the true label of an unscored pair is one of the three
    judges' answers. Under it, the overall score lies between the per-pair min and per-pair max
    over judges -- a tighter interval than the Manski one, and wrong if all three judges are
    wrong. It is a sensitivity analysis, not an estimate: no judge is the truth.
    """
    from eval.runner import score_fixture

    kept = _published_records(records, exclude_exposed)
    judges = sorted({j for r in kept for f in fields for j in votes[r["id"]].get(f, {})})
    per_review_by_judge: dict[str, list[float]] = {j: [] for j in judges}
    lows: list[float] = []
    highs: list[float] = []
    n_pairs = 0
    for r in kept:
        fx = fixtures[r["id"]]
        pred = r[CONDITION]["predicted"]
        skip = _unscored_set(r, fields)
        base = r[CONDITION]["field_scores"]
        pair_scores: dict[str, dict[str, float]] = {}
        for f in sorted(skip):
            n_pairs += 1
            pair_scores[f] = {}
            for j, vote in votes[r["id"]][f].items():
                alt = {**fx, "ground_truth": {**fx["ground_truth"], f: vote}}
                pair_scores[f][j] = {fr.field: fr.score for fr in score_fixture(alt, pred)}[f]
        for j in judges:
            per_review_by_judge[j].append(
                mean(pair_scores[f][j] if f in skip else base[f] for f in fields)
            )
        lows.append(mean(min(pair_scores[f].values()) if f in skip else base[f] for f in fields))
        highs.append(mean(max(pair_scores[f].values()) if f in skip else base[f] for f in fields))

    def cell(values: list[float]) -> dict[str, Any]:
        lo, hi = bootstrap_ci(values) if values else (0.0, 0.0)
        return {
            "score": mean(values) if values else 0.0,
            "ci_95": {"lower": lo, "upper": hi, "n": len(values)},
        }

    return {
        "assumption": (
            "The true label of each unscored pair is one of the three judges' answers. NOT "
            "verified (all three can be wrong); a sensitivity analysis, not an estimate."
        ),
        "n_unscored_pairs_scored": n_pairs,
        "scored_against_each_judge": {j: cell(v) for j, v in per_review_by_judge.items()},
        "envelope_min_over_judges": cell(lows),
        "envelope_max_over_judges": cell(highs),
    }


def validate_votes_match_gold(
    fixtures: dict[str, dict[str, Any]],
    votes: dict[str, dict[str, dict[str, Any]]],
    unresolved: dict[str, set[str]],
) -> list[str]:
    """Problems if the judge votes are not the ones the committed gold was built from.

    On every RESOLVED field the panel's modal vote must equal the stored gold; otherwise the votes
    for the unresolved fields are from a different labelling run and must not be used.
    """
    problems: list[str] = []
    for fid, fx in fixtures.items():
        if fid not in votes:
            problems.append(f"{fid}: no judge votes in the batch log")
            continue
        for field, vs in votes[fid].items():
            if field in unresolved.get(fid, set()) or field not in fx["ground_truth"]:
                continue
            gold = fx["ground_truth"][field]
            if not any(v == gold for v in vs.values()):
                problems.append(f"{fid}.{field}: no judge vote equals the stored gold {gold!r}")
    return problems
