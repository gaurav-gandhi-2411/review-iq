# ADR 0013: Consensus panel self-judging contamination -- finding, correction, and fix

## Context

Session 7 P2 used the 2-judge consensus panel recovered by PR #140 (`openai/gpt-oss-120b`,
`qwen/qwen3.6-27b`) as a blind adjudicator to classify model abstentions as genuinely-ambiguous
vs. decidable-but-hedged, concluding: `buy_again` has real recoverable headroom (5/10 real
hedges decidable), `sentiment` has ~zero (0/9 decidable -- the panel either also hedges or
sides with the model's hedge over the fixture's own directional ground truth).

Session 8 P2 was tasked with checking whether that panel-vs-gold agreement has two opposite
readings: (i) the gold labels are wrong, or (ii) the panel is contaminated (shares family/
lineage with the production model, so agreement is not independent evidence).

## Finding -- VERIFIED, reading (ii)

`eval/consensus/panel.py`'s own docstring documents the correct rule: never use review-iq's
own production tiered-router model as a judge (a real conflict of interest), and correctly
excluded `llama-3.3-70b-versatile` while it held that role. Production migrated to
`openai/gpt-oss-20b`/`openai/gpt-oss-120b` on 2026-08-16 (Groq's `llama-3.1-8b-instant`/
`llama-3.3-70b-versatile` deprecation). The exclusion rule was never re-applied: `openai/
gpt-oss-120b` had been on the judge candidate list since before that migration for an
unrelated reason, and after the migration it silently became review-iq's own `groq_model_large`
-- confirmed directly, exact ID match, not a family/lineage inference: `eval/results/latest.
json`'s `groq_model_large` field and `eval/consensus/results/consensus_summary.json`'s
`active_panel` both name `openai/gpt-oss-120b`.

Measured effect on real data (`scripts/p2_panel_contamination_check.py`, run against
already-committed `eval/results/latest.json` + `eval/consensus/results/consensus_labels.
jsonl`): the contaminated judge's vote matched the model-under-test's exact `sentiment: mixed`
hedge in **9 of 9** real-hedge disagreements it saw -- a rate far more consistent with "same
model, same reasoning pattern on the same text" than independent judgment.

## Corrected re-analysis

Restricting to the ONE genuinely disjoint judge (`qwen/qwen3.6-27b` -- `allam-2-7b`, the third
candidate, failed calibration independently for unrelated reasons and was never active this
run) changes both fields' numbers, in **opposite directions from what contamination alone
would predict**:

| Field | Real hedges | Contaminated 2-judge "decidable" | Disjoint 1-judge "decidable" |
|---|---|---|---|
| `buy_again` | 10 | 5/10 (50%) | **9/10 (90%)** |
| `sentiment` | 9 | 0/9 (0%) | **4/9 (44%)** |

For `buy_again`, the contaminated judge's own hedging (matching the model's `null` on 4 of the
10 cases) had been dragging the reported "consensus" down to split/no-decisive-silver on cases
where the disjoint judge alone actually had a clear, ground-truth-matching answer all along --
the true recoverable headroom was **understated**, not overstated, by the contaminated number.

For `sentiment`, in every one of the 4 now-decidable cases, the disjoint judge's answer
**matches the fixture's original ground truth exactly** (`012_sarcasm`, `015_medium_urgency`,
`022_two_star_explicit`, `025_competitor_switch` -- all originally-labeled negative/positive,
all correctly resolved by `qwen` alone). This is evidence the ORIGINAL LABELS were right and
the model under test is genuinely under-committing on decidable cases, not evidence the labels
are wrong. Session 7 P2's "sentiment has zero recoverable headroom" conclusion is **retracted**.

One case (`007_buy_again_ambiguous`, sentiment field) is the sole instance where the disjoint
judge agrees with the *contaminated* judge (`mixed`) against the original label (`neutral`) --
a real, narrow signal that this one label may be debatable (mixed vs. neutral is a genuinely
fine-grained distinction), not evidence of a systemic gold-label defect. **No fixture is
relabeled by this ADR or any script in this PR** -- per the standing instruction, a relabeling
decision ships as its own PR with the adjudication evidence attached, never silently.

The remaining 5/9 sentiment cases and 1/10 buy_again case stay genuinely ambiguous even under
the disjoint judge alone -- this is a single-rater result, not a validated multi-judge kappa
(kappa is undefined for one rater; see Consequences).

## Decision

1. `openai/gpt-oss-120b` removed from `eval/consensus/panel.py`'s `JUDGE_MODELS` roster.
2. `panel.assert_no_self_judging()` (new): a runtime check against the CURRENT production
   `groq_model_small`/`groq_model_large` config, called from `run_consensus.py`'s
   `get_active_panel()` before every real labeling run. Converts what was institutional memory
   (a docstring warning, correctly followed once, silently unenforced after) into a check that
   fails loudly on any future production-model migration, rather than repeating this incident
   under a new model name.
3. `docs/specs/wave1-coverage-abstention-analysis.md` (PR #148) corrected in a follow-up
   section rather than rewritten in place, preserving the original (now-superseded) numbers
   for the historical record per this repo's honest-documentation convention.
4. `docs/architecture/adr/0012-*.md`'s exclusion of `sentiment` from the P4b coverage-recovery
   experiment is reconsidered separately (see that ADR's own follow-up) -- this ADR documents
   the finding, not a new experiment authorization.

## Consequences

- The active judge roster is now effectively **one** calibration-passing, disjoint judge
  (`qwen/qwen3.6-27b`). No inter-rater kappa/alpha is computable with one rater -- any number
  reported above is a single-rater check, weaker evidence than a validated multi-judge
  consensus, and should be read that way until a second genuinely disjoint judge is added.
- Sourcing a second disjoint, calibration-passing judge is now a named prerequisite for P3's
  held-out Hindi/Hinglish corpus (which needs its own panel excluding production-shared
  families from the start) -- not solved in this ADR, deferred to that work with its own
  quota budget.
- This is a fixture-*adjudication*-provenance defect (the panel used to judge decidability),
  not a fixture-*ground-truth* defect -- the original fixture labels held up well under the
  corrected check (9/10 and 4/9 confirmed, only 1 case questioned). The distinction matters:
  this finding does not call the underlying eval fixtures' quality into question broadly.

## Alternatives considered

- **Keep gpt-oss-120b as a "tie-breaker" judge, just don't trust its vote alone.** Rejected:
  with only 2 judges, gpt-oss-120b's vote is never a tie-breaker, it is half the panel --
  there is no way to structurally discount it without effectively running a 1-judge check
  anyway, which is what removing it makes explicit instead of leaving implicit.
- **Silently relabel the fixtures the disjoint judge disagrees with the original label on.**
  Rejected outright per the standing instruction -- one case (`007`) is flagged as worth a
  human/adjudicated look, not silently changed.
