"""The Amendment 5 labelling rules as pure functions (docs/specs/review-intent.md, Amendment 5).

TRAINING / VALIDATION items: `llama3.1` and `qwen3` label everything. Per task: both answered and agree -> that
value. Otherwise (disagree, or one or both failed to parse) and mistral was run on the item -> mistral's value if it
equals one of the two judges' values, else the item abstains for that task. Mistral is run on an item only when at
least one task needs it, and then once for all tasks. Gemma is not run on training items.

SEALED TEST items: all four judges, `repilot_report.consensus_label` (a clear majority, otherwise abstain).

Aspect sentiment (T6): only an aspect that BOTH first-pass judges quoted gets a label (agree -> value; differ ->
mistral if it equals one of the two). An aspect quoted by one judge only is not labelled and does not by itself
trigger mistral, so the mistral call rate is defined by the four core tasks plus two-judge aspect conflicts.
Records are judge-file lines (`engine.labelling.judge`): `{"id", "text": {...} | None, ...}`.
"""

from __future__ import annotations

from typing import Any

from engine.labelling.repilot_report import consensus_label

TASKS = ("needs_action", "broad_intent", "sentiment", "urgency")
Rec = dict[str, Any]


def two_judge_label(a: Any, b: Any, tie: Any = None) -> tuple[Any, str]:
    """-> (label or None, how) with how in agree / tiebreak / abstain. None means 'missing' for a, b and tie."""
    if a is not None and a == b:
        return a, "agree"
    if tie is not None and tie in (a, b):
        return tie, "tiebreak"
    return None, "abstain"


def _val(rec: Rec | None, task: str) -> Any:
    t = rec.get("text") if rec else None
    return t.get(task) if t else None


def _aspect_conflicts(a: Rec | None, b: Rec | None) -> bool:
    """True if both judges quoted an aspect and gave it different sentiments."""
    ta, tb = (a or {}).get("text"), (b or {}).get("text")
    if not ta or not tb:
        return False
    sa, sb = ta.get("aspects") or {}, tb.get("aspects") or {}
    return any(sa[k] != sb[k] for k in sa.keys() & sb.keys())


def needs_tiebreak(a: Rec | None, b: Rec | None) -> bool:
    """Run mistral on this item? Yes if any core task lacks two agreeing answers, or an aspect conflicts."""
    for t in TASKS:
        va, vb = _val(a, t), _val(b, t)
        if va is None or va != vb:
            return True
    return _aspect_conflicts(a, b)


def label_aspects(a: Rec | None, b: Rec | None, m: Rec | None = None) -> dict[str, str]:
    ta, tb = (a or {}).get("text"), (b or {}).get("text")
    if not ta or not tb:
        return {}
    sa, sb = ta.get("aspects") or {}, tb.get("aspects") or {}
    sm = ((m or {}).get("text") or {}).get("aspects") or {}
    out = {}
    for k in sorted(sa.keys() & sb.keys()):
        lab, _ = two_judge_label(sa[k], sb[k], sm.get(k))
        if lab is not None:
            out[k] = lab
    return out


def label_item(a: Rec | None, b: Rec | None, m: Rec | None = None) -> dict:
    """-> {task: {"label", "how"}, "aspects": {aspect: sentiment}} for one training/validation item."""
    out: dict = {}
    for t in TASKS:
        lab, how = two_judge_label(_val(a, t), _val(b, t), _val(m, t))
        out[t] = {"label": lab, "how": how}
    out["aspects"] = label_aspects(a, b, m)
    return out


def tiebreak_ids(a: dict[str, Rec], b: dict[str, Rec], ids: list[str]) -> list[str]:
    """Item ids (in the given order) on which mistral must run, from the first-pass outputs."""
    return [i for i in ids if needs_tiebreak(a.get(i), b.get(i))]


def sealed_label_item(recs: list[Rec | None]) -> dict:
    """Four-judge consensus labels for one sealed item (recs: the four judges' records, None = absent)."""
    out: dict = {t: consensus_label([_val(r, t) for r in recs]) for t in TASKS}
    asp: dict[str, str] = {}
    names = {k for r in recs if r and r.get("text") for k in (r["text"].get("aspects") or {})}
    for k in sorted(names):
        votes = [((r or {}).get("text") or {}).get("aspects", {}).get(k) for r in recs]
        if sum(v is not None for v in votes) >= 2:  # quoted by 2+ judges, as in the re-pilot report
            lab = consensus_label(votes)
            if lab is not None:
                asp[k] = lab
    out["aspects"] = asp
    return out
