# ADR 0020: "Unanimous" now requires the full invited panel, not just full agreement among survivors

## Context

Session 10 P4/P5e found ~40 held-out fixtures whose consensus records read `"agreement":
"unanimous"` while one judge (`qwen/qwen3.6-27b`) had 429'd on Groq's Output-Tokens-Per-Minute
admission control and never responded for that item. The fix applied then
(`max_completion_tokens` 2000→900) addressed the TRIGGER — it stopped the 429s from happening
— but never asked why the AGGREGATION layer had produced a misleading label in the first
place. Session 11 P2 answers that.

## Finding

`be5e5a9` (2026-07-30) built the `NO_RESPONSE` sentinel specifically to fix a real, different
bug: a judge correctly answering `null` (e.g. `stars: null`, a legitimate value) was being
conflated with a judge that errored and produced no answer at all. That fix is correct and
still works exactly as designed — `_votes_for` maps an errored judge's output to `NO_RESPONSE`,
and every `vote_*` function in `eval/consensus/voting.py` correctly excludes it before tallying
votes.

**The bug is one layer up.** Every `vote_*` function's "unanimous" check compared the top vote
count against the number of RESPONDING judges (`len(present)`), not the number of INVITED
judges (`len(values)`, which includes `NO_RESPONSE` entries). So when 1 of 3 invited judges
errored and the remaining 2 agreed, `top_count (2) == len(present) (2)` — and the function
correctly, faithfully reported "unanimous," because by its own definition, every judge who
responded did agree. The label was truthful about "responders" and silent about "how many
responders there were relative to the panel." A caller reading `agreement: "unanimous"` had no
way to tell 3-of-3 from 2-of-2-with-one-silent-dropout without separately inspecting `votes`
for `NO_RESPONSE` entries — which `build_held_out_corpus.py::build_fixture()` never did.

This was not a hidden accident: `tests/unit/test_consensus_voting.py::
test_missing_judge_output_handled_as_none` explicitly asserted this exact behavior as correct
("Only 2 of 3 judges responded, but both of THEM agree -- unanimous among responders, not
'majority'"). It was a deliberate, tested design choice at the time, for a hypothetical
dissent-vs-no-response distinction that never anticipated a THIRD case: a judge dropping out
from live infrastructure failure mid-batch, at real scale (33 of ~40 batch-2 fixtures showed
this shape when audited — see below), not a one-off.

## Decision

Redefine "unanimous" to require the full invited panel: every `vote_scalar_exact`,
`vote_scalar_tolerant`, and `vote_list_overlap` now compares against `len(values)` (total
invited, including `NO_RESPONSE` entries), not `len(present)` (responding only). A judge
dropping out — for any reason, dissent or infrastructure failure alike — can now produce
`"majority"` or `"split"`, structurally never `"unanimous"`. `vote_list_overlap`'s previous
hardcoded `>= 3` check (assuming every panel has exactly 3 judges) is replaced with the same
`len(present) == total` comparison, fixing a second, narrower instance of the identical
class of bug for panels of any size, not just 3.

The previously-passing test asserting the old behavior is updated to assert the new, correct
behavior, with an explanatory comment pointing here — not deleted, so the history of what
changed and why stays visible. Three new tests added: one exercising each `vote_*` function
directly with a `NO_RESPONSE` judge and full agreement among the rest, and one exercising
`consensus_for_item` end-to-end, all asserting `!= "unanimous"` explicitly (P2b).

## Audit (P2c) — how many existing records carry the old shape

Scanned every consensus record in the repo for `agreement == "unanimous"` with any
`NO_RESPONSE` vote present (the exact bug shape, mechanically, not by sampling):

| Source | Records scanned | Affected (field, fixture) instances |
|---|---|---|
| `held_out_batch_log.jsonl`, raw (all 3 historical runs) | 148 | 198, across 33 fixture ids |
| `held_out_batch_log.jsonl`, deduped to latest-per-id (= current committed state) | 108 | **0** |
| `eval/consensus/results/consensus_labels.jsonl` (main eval set, PR #140 recovery + growth) | 365 | **0** |

**The currently-committed held-out corpus (106 fixtures) and the main eval/fixtures/ set are
both clean.** The raw historical log still contains the bug's fingerprint from the two broken
batch-2 attempts — expected and left in place, since that log is an append-only audit trail by
Session 9's own stated convention, not a correctness-bearing artifact itself. Session 10's
manual relabeling of the ~40 affected fixtures fully addressed the committed-data side of this
before this fix existed; this audit is the mechanical proof of that, not a new cleanup.

## Consequences

- Any future judge dropout (rate limit, timeout, malformed output) can now only ever produce
  `"majority"` or `"split"` — never a false `"unanimous"`, regardless of cause. This is a
  structural guarantee (enforced by the comparison itself), not a policy that depends on every
  caller remembering to check `votes` for `NO_RESPONSE`.
- No fixture data changes as a result of this PR — the audit found nothing currently committed
  to fix. This PR is a code + test + documentation change only.
- The general lesson, not specific to this bug: a design choice made for one anticipated
  failure mode (dissent) can silently misclassify a different, later failure mode (infra
  dropout) that shares the same code path but not the same meaning. The fix is to name what the
  invariant actually should be ("every invited judge, not every judge who happened to answer")
  and make it structural, not to patch the specific trigger that first exposed the gap.

## Alternatives considered

- **Add a `n_responding`/`n_total` field to the consensus record instead of changing what
  "unanimous" means.** Rejected: this would fix visibility for a caller that remembers to check
  it, but `build_held_out_corpus.py` already had `votes` available (with `NO_RESPONSE` entries
  visible) and never checked it — an optional field a caller can ignore repeats the exact
  failure mode, just with one more place to forget to look. Redefining "unanimous" itself makes
  the correct behavior the only behavior.
- **Keep the old semantics and rename the level (e.g. "unanimous_among_responders").**
  Rejected: overwrought for what "unanimous" should just mean in the first place; the burden
  of an unusual label falls on the correct case (full panel) which should be the default and
  the obvious spelling, not the exceptional one.
