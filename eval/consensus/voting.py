"""Per-field consensus voting for the 3-judge panel -- unanimous/majority/split, never blended.

Two kinds of fields need two different notions of "agreement," both implemented here,
never collapsed into one blended confidence score:

  - Scalar/categorical fields (sentiment, urgency, buy_again, language, stars,
    stars_inferred): exact-match voting (stars_inferred gets a documented +/-1
    tolerance -- it's a holistic 1-5 estimate, not a fact read off the text, and the
    existing fixture schema already treats it with the same tolerance in
    `scoring_notes.tolerance_fields`). unanimous = all 3 responding judges agree,
    majority = 2 of 3, split = no agreement (silver label is null, agreement level
    recorded honestly, never resolved by a tiebreaker).

  - Open-list fields (product's near-duplicate phrasing aside, mainly pros/cons/
    topics/feature_requests/competitor_mentions): judges essentially never produce
    byte-identical lists (paraphrasing is expected and fine), so exact-match voting
    would trivially always report "split." Instead we measure pairwise Jaccard overlap
    on normalized (lowercased, whitespace-collapsed) items and classify unanimous (all
    3 pairwise overlaps >= JACCARD_THRESHOLD), majority (>=1 pair meets it -- take that
    pair), or split (no pair meets it). The reported silver value is the LONGEST list
    among the agreeing judges (a representative pick, not a synthetic merge) -- this is
    a fuzzier notion of "agreement" than the scalar fields get, and is documented as
    such; it is NOT run through Krippendorff/Fleiss (see eval/agreement.py's docstring
    for why those need a small fixed category set).

`product` is scalar but free-text -- voted by exact match on a normalized (lowercased,
stripped) key, same three-way logic as the other scalar fields.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Literal

AgreementLevel = Literal["unanimous", "majority", "split", "insufficient"]

# Sentinel for "this judge did not respond at all" (errored / unparseable output) --
# distinct from a judge SUCCESSFULLY returning `None`/null for a field, which is a
# legitimate vote in this schema (e.g. stars=null means "no explicit rating stated",
# buy_again=null means "genuinely ambiguous" -- both are real answers the existing
# fixtures already use, not missing data). Conflating the two would make every field
# where judges correctly agree on null look like "insufficient"/"split" instead of the
# unanimous agreement it actually is. A plain string (not `object()`) so the `votes`
# dict this ends up inside stays JSON-serializable for the results JSONL.
NO_RESPONSE = "\x00__NO_RESPONSE__\x00"

# Fields voted by exact match (after optional normalization).
SCALAR_EXACT_FIELDS: tuple[str, ...] = ("stars", "buy_again", "sentiment", "language")
# stars_inferred gets a +/-1 tolerance cluster instead of exact match (see module docstring).
SCALAR_TOLERANT_FIELDS: dict[str, int] = {"stars_inferred": 1}
# product is scalar but normalized (case/whitespace) before the exact-match vote.
SCALAR_NORMALIZED_FIELDS: tuple[str, ...] = ("product",)
# urgency is ordinal for Krippendorff's alpha purposes (see eval/agreement.py) but the
# per-item silver LABEL is still a plain 3-way exact-match vote -- alpha measures panel
# reliability across all items, this measures agreement on one item.
SCALAR_ORDINAL_FIELDS: tuple[str, ...] = ("urgency",)

LIST_FIELDS: tuple[str, ...] = ("pros", "cons", "topics", "feature_requests", "competitor_mentions")

JACCARD_THRESHOLD = 0.5


def _normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _normalize_list(items: Sequence[str]) -> set[str]:
    return {_normalize_text(i) for i in items if str(i).strip()}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def vote_scalar_exact(
    values: dict[str, Any], normalize: bool = False
) -> tuple[Any, AgreementLevel]:
    """Vote on a scalar field given `{judge_id: value_or_NO_RESPONSE}`.

    Returns (silver_value_or_None, agreement_level). `normalize` lowercases/strips
    string values for the equality comparison (used for `product`); the returned
    silver value is still the ORIGINAL (non-normalized) string from an agreeing judge.
    A value of `None` is a legitimate vote (e.g. stars=null); only `NO_RESPONSE`
    (a judge that errored/returned unparseable output) is excluded.
    """
    present = {jid: v for jid, v in values.items() if v is not NO_RESPONSE}
    if len(present) < 2:
        return None, "insufficient"

    def key(v: Any) -> Any:
        return _normalize_text(v) if normalize and isinstance(v, str) else v

    keys = [key(v) for v in present.values()]
    counts: dict[Any, int] = {}
    representative: dict[Any, Any] = {}
    for v in present.values():
        k = key(v)
        counts[k] = counts.get(k, 0) + 1
        representative.setdefault(k, v)

    top_key = max(counts, key=lambda k: counts[k])
    top_count = counts[top_key]
    n = len(keys)
    if top_count == n:
        return representative[top_key], "unanimous"
    if top_count > n / 2:
        return representative[top_key], "majority"
    return None, "split"


def vote_scalar_tolerant(
    values: dict[str, float | None], tolerance: int
) -> tuple[float | None, AgreementLevel]:
    """Vote on a numeric field where judges within `tolerance` of each other count as agreeing.

    Clusters responding judges' values; if the tightest cluster spans <= `tolerance`
    and contains all/most judges, its median is the silver value. Unlike the scalar
    exact-match vote, an actual `None` here IS treated as absent data (not a legitimate
    vote value) -- this field's prompt instruction is "always populate", so a judge
    returning `None` failed to comply, same as `NO_RESPONSE`.
    """
    present = {jid: v for jid, v in values.items() if v is not None and v is not NO_RESPONSE}
    if len(present) < 2:
        return None, "insufficient"

    vals = sorted(present.values())
    n = len(vals)

    # All judges' values fall within one `tolerance`-wide window -> unanimous.
    if vals[-1] - vals[0] <= tolerance:
        return _median(vals), "unanimous"

    # Look for the largest subset of values within a `tolerance`-wide window.
    best_subset: list[float] = []
    for i in range(n):
        window = [v for v in vals if vals[i] <= v <= vals[i] + tolerance]
        if len(window) > len(best_subset):
            best_subset = window

    if len(best_subset) > n / 2:
        return _median(best_subset), "majority"
    return None, "split"


def _median(vals: Sequence[float]) -> float:
    s = sorted(vals)
    mid = len(s) // 2
    if len(s) % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2


def vote_list_overlap(
    values: dict[str, list[str] | None], threshold: float = JACCARD_THRESHOLD
) -> tuple[list[str] | None, AgreementLevel]:
    """Vote on an open-list field via pairwise Jaccard overlap (see module docstring).

    Only `NO_RESPONSE` (judge errored) is excluded -- a validated judge response always
    has a real list here (pydantic's `default_factory=list` fills in `[]` when a judge
    omits the field, and a judge that returned `null` for a typed list field would have
    failed schema validation entirely, becoming `NO_RESPONSE` upstream, not `None`).
    """
    present = {jid: (v or []) for jid, v in values.items() if v is not NO_RESPONSE}
    if len(present) < 2:
        return None, "insufficient"

    ids = list(present.keys())
    normalized = {jid: _normalize_list(present[jid]) for jid in ids}

    pairs: list[tuple[str, str, float]] = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            score = _jaccard(normalized[ids[i]], normalized[ids[j]])
            pairs.append((ids[i], ids[j], score))

    agreeing_pairs = [(a, b) for a, b, score in pairs if score >= threshold]

    if len(present) >= 3 and len(agreeing_pairs) == len(pairs):
        # every pair meets the threshold -> unanimous
        rep_id = max(present, key=lambda jid: len(present[jid]))
        return present[rep_id], "unanimous"
    if agreeing_pairs:
        a, b = agreeing_pairs[0]
        rep_id = a if len(present[a]) >= len(present[b]) else b
        return present[rep_id], "majority"
    return None, "split"


def _votes_for(field: str, judge_outputs: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    """Build `{judge_id: field_value_or_NO_RESPONSE}` for one field.

    `judge_outputs[jid] is None` means that judge errored/returned unparseable output
    for the WHOLE item -> NO_RESPONSE. Otherwise `out.get(field)` is a real vote, even
    when it's `None` (a legitimate null answer for fields like stars/buy_again).
    """
    return {
        jid: (out.get(field) if out is not None else NO_RESPONSE)
        for jid, out in judge_outputs.items()
    }


def consensus_for_item(
    judge_outputs: dict[str, dict[str, Any] | None],
) -> dict[str, dict[str, Any]]:
    """Compute per-field consensus for one item given `{judge_id: parsed_output_or_None}`.

    `judge_outputs[jid]` is `None` when that judge errored/returned unparseable output
    for the item entirely; otherwise it's the judge's parsed field dict (which may
    legitimately have `None` values for individual fields).

    Returns `{field: {"silver": value_or_None, "agreement": level, "votes": {judge_id: raw_value}}}`
    for every field in SCALAR_EXACT_FIELDS + SCALAR_TOLERANT_FIELDS + SCALAR_NORMALIZED_FIELDS
    + SCALAR_ORDINAL_FIELDS + LIST_FIELDS. `votes` records `NO_RESPONSE` verbatim for
    judges that didn't answer, so downstream consumers can distinguish "didn't answer"
    from "answered null".
    """
    result: dict[str, dict[str, Any]] = {}

    for field in (*SCALAR_EXACT_FIELDS, *SCALAR_ORDINAL_FIELDS):
        votes = _votes_for(field, judge_outputs)
        silver, level = vote_scalar_exact(votes, normalize=False)
        result[field] = {"silver": silver, "agreement": level, "votes": votes}

    for field in SCALAR_NORMALIZED_FIELDS:
        votes = _votes_for(field, judge_outputs)
        silver, level = vote_scalar_exact(votes, normalize=True)
        result[field] = {"silver": silver, "agreement": level, "votes": votes}

    for field, tolerance in SCALAR_TOLERANT_FIELDS.items():
        votes = _votes_for(field, judge_outputs)
        silver, level = vote_scalar_tolerant(votes, tolerance)
        result[field] = {"silver": silver, "agreement": level, "votes": votes}

    for field in LIST_FIELDS:
        votes = _votes_for(field, judge_outputs)
        silver, level = vote_list_overlap(votes)
        result[field] = {"silver": silver, "agreement": level, "votes": votes}

    return result
