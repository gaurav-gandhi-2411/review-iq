"""Pure aggregation logic for the consensus-labeling run -- no network calls here.

Kept separate from run_consensus.py (which does the live LLM calls) so the actual
business logic -- validation agreement, reliability-matrix construction, growth
gating, new-fixture assembly -- is unit-testable against fixed fake consensus data,
per this repo's existing convention (multi_llm_labeler.py/llm_labeler.py, the live-call
scripts this was modeled on, have no unit tests either; the logic worth testing here is
split out instead of living inline in the live-call loop).
"""

from __future__ import annotations

from typing import Any

from eval.consensus.voting import NO_RESPONSE

# Fields that gate whether a growth candidate becomes a new committed fixture -- these
# 4 are the fields Krippendorff's alpha/Fleiss' kappa are computed over (see
# eval/agreement.py's docstring for why: closed category sets, not open text). A new
# fixture needs a non-null (agreement != split/insufficient) consensus on ALL of them;
# `product`/`stars`/list fields are allowed to be null or lower-confidence.
GROWTH_GATE_FIELDS: tuple[str, ...] = ("sentiment", "urgency", "buy_again", "language")

VALIDATION_FIELDS: tuple[str, ...] = (
    "sentiment",
    "urgency",
    "buy_again",
    "language",
    "stars",
)


def passes_growth_gate(consensus: dict[str, dict[str, Any]]) -> bool:
    """True if every GROWTH_GATE_FIELDS field reached a non-split/insufficient consensus."""
    return all(
        consensus.get(field, {}).get("agreement") in ("unanimous", "majority")
        for field in GROWTH_GATE_FIELDS
    )


def raw_votes_matrix(
    records: list[dict[str, Any]], field: str, judge_ids: list[str]
) -> list[list[Any]]:
    """Build a raters-by-units matrix (see eval/agreement.py) for one field across items.

    `records` is a list of `{"consensus": {field: {"votes": {judge_id: value}}}}`-shaped
    dicts (as produced by voting.consensus_for_item, stored under "consensus"). Returns
    `matrix[r][u]` = judge `judge_ids[r]`'s vote for item `u`, or `None` if that judge
    didn't respond (`NO_RESPONSE` is translated to `None`, eval.agreement's own missing
    marker) for that item/field.

    Uses `==` against `NO_RESPONSE`, not `is` -- `records` here is typically read back
    from a JSONL file (run_consensus.py's consensus_labels.jsonl), so the sentinel has
    been through a json.dumps/json.loads round-trip and is a distinct string object
    with the same value, not the same object voting.py constructed in-process. `is`
    would silently fail to match after that round-trip and leak the raw sentinel string
    into the reliability matrix (confirmed live: caused a `KeyError` inside
    eval.agreement's ordinal-rank lookup on a real run).
    """
    matrix: list[list[Any]] = [[] for _ in judge_ids]
    for rec in records:
        votes = rec.get("consensus", {}).get(field, {}).get("votes", {})
        for r, jid in enumerate(judge_ids):
            v = votes.get(jid, NO_RESPONSE)
            matrix[r].append(None if v == NO_RESPONSE else v)
    return matrix


def fleiss_table(
    records: list[dict[str, Any]], field: str, judge_ids: list[str]
) -> list[list[int]]:
    """Build a Fleiss' kappa category-count table, restricted to fully-covered items.

    Only items where EVERY judge in `judge_ids` responded (no NO_RESPONSE) are
    included -- Fleiss' kappa requires a fixed rater count per item (see
    eval/agreement.py's docstring for why Krippendorff's alpha is primary and this is
    the secondary cross-check).
    """
    categories: list[Any] = []
    seen: set[Any] = set()
    rows: list[dict[Any, int]] = []

    for rec in records:
        votes = rec.get("consensus", {}).get(field, {}).get("votes", {})
        values = [votes.get(jid, NO_RESPONSE) for jid in judge_ids]
        if any(v == NO_RESPONSE for v in values):
            continue
        row: dict[Any, int] = {}
        for v in values:
            row[v] = row.get(v, 0) + 1
            if v not in seen:
                seen.add(v)
                categories.append(v)
        rows.append(row)

    return [[row.get(cat, 0) for cat in categories] for row in rows]


def validation_agreement(
    existing_fixtures: list[dict[str, Any]],
    consensus_by_id: dict[str, dict[str, Any]],
    fields: tuple[str, ...] = VALIDATION_FIELDS,
) -> dict[str, Any]:
    """Compare consensus silver labels to the ALREADY-COMMITTED ground truth, per field.

    This is the "does the new consensus mechanism roughly agree with what's already
    checked in" validation pass -- large disagreement is itself a finding to report,
    not to hide (per the task spec).
    """
    per_field: dict[str, dict[str, Any]] = {
        f: {"n_compared": 0, "n_agree": 0, "n_no_consensus": 0} for f in fields
    }
    scored_ids: list[str] = []

    for fx in existing_fixtures:
        rec = consensus_by_id.get(fx["id"])
        if rec is None:
            continue
        scored_ids.append(fx["id"])
        gt = fx["ground_truth"]
        for field in fields:
            field_result = rec.get("consensus", {}).get(field, {})
            agreement = field_result.get("agreement")
            if agreement not in ("unanimous", "majority"):
                per_field[field]["n_no_consensus"] += 1
                continue
            silver = field_result.get("silver")
            expected = gt.get(field)
            match = (
                silver.strip().lower() == expected.strip().lower()
                if isinstance(silver, str) and isinstance(expected, str)
                else silver == expected
            )
            per_field[field]["n_compared"] += 1
            if match:
                per_field[field]["n_agree"] += 1

    for field in fields:
        n = per_field[field]["n_compared"]
        per_field[field]["agreement_rate"] = (per_field[field]["n_agree"] / n) if n else None

    return {"n_fixtures_scored": len(scored_ids), "per_field": per_field}


def build_new_fixture(
    fixture_id: str, review_text: str, consensus: dict[str, dict[str, Any]], source: str
) -> dict[str, Any]:
    """Assemble a new fixture JSON (matching the existing eval/fixtures/ schema) from consensus.

    Only called for candidates that already passed `passes_growth_gate` -- silver
    values for the gated fields are guaranteed non-null at that point.
    """
    silver = {field: consensus[field]["silver"] for field in consensus}
    ground_truth = {
        "product": silver.get("product") or "unknown",
        "stars": silver.get("stars"),
        "stars_inferred": silver.get("stars_inferred"),
        "pros": silver.get("pros") or [],
        "cons": silver.get("cons") or [],
        "buy_again": silver.get("buy_again"),
        "sentiment": silver.get("sentiment"),
        "topics": silver.get("topics") or [],
        "competitor_mentions": silver.get("competitor_mentions") or [],
        "urgency": silver.get("urgency"),
        "feature_requests": silver.get("feature_requests") or [],
        "language": silver.get("language"),
    }
    agreement_levels = {field: consensus[field]["agreement"] for field in consensus}
    return {
        "id": fixture_id,
        "review_text": review_text,
        "ground_truth": ground_truth,
        "scoring_notes": {
            "exact_match_fields": ["product", "stars", "buy_again", "sentiment", "language"],
            "set_overlap_fields": ["topics", "competitor_mentions"],
            "fuzzy_fields": ["pros", "cons"],
            "tolerance_fields": {"stars_inferred": 1},
        },
        "labeling_meta": {
            "labeled_by": "multi-llm-consensus",
            "method": "eval/consensus (Wave 1 Section B)",
            "source": source,
            "agreement_per_field": agreement_levels,
        },
    }
