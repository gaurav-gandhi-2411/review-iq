# ADR 0012: Coverage-recovery experiment — pre-registration (design only, not run)

## Context

The prompt is frozen for anything scored by the CI gate set (Session 7 P4a, standing). P4b
permits *designing* a coverage-recovery experiment — a prompt change aimed at reducing
hedging on hedge-tracked fields — without running it, gated behind this ADR landing first.

Session 7 P2's abstention analysis (`docs/specs/wave1-coverage-abstention-analysis.md`)
gives the evidence base this experiment must be judged against, per field:

- **`buy_again`**: 5 of 10 real hedges (hedge value != ground truth) are decidable-but-
  hedged — a blind 2-judge consensus panel unanimously resolves them to the fixture's own
  expected value. This is real, panel-confirmed headroom: +11.6pp on the field's own mean
  score if resolved.
- **`sentiment`**: 0 of 9 real hedges are decidable-but-hedged. The panel either also
  hedges (5/9, unanimously) or splits (4/9). There is no panel-confirmed headroom on this
  field, and in the majority of cases the panel's independent judgment sides with the
  model's hedge over the fixture's original directional label — closing this "gap" would
  mean training the model away from what two independent model families consider the more
  defensible answer.

## Decision

**Only `buy_again` qualifies for a coverage-recovery experiment under this ADR.**
`sentiment` does not: P2's evidence says there is no real gap to close, only a
fixture-labeling question that must be resolved separately (re-audit the 5 fixtures where
the panel disagrees with the original ground truth) before any prompt work on sentiment
hedging is justified at all.

### Hypothesis (stated before any run, per rule 78/116)

Adding an explicit instruction to commit to `buy_again: true/false` whenever the review
text names a specific, attributable defect or praise (the pattern shared by all 5
decidable-but-hedged fixtures: `003_prompt_injection`, `005_all_positive`,
`009_competitor_heavy`, `027_harm_in_positive_tone_high`, `028_fit_pain_high`) will resolve
those 5 cases to the panel-confirmed answer, without regressing accuracy on the 33 fixtures
where the model already commits correctly to `buy_again`, and without moving `buy_again`
onto genuinely ambiguous cases (the other 5 real hedges + the 4 already-correct-null cases,
9 fixtures total) into a wrong-but-committed state.

### Success criterion (stated before any run)

- `buy_again` field mean score on the 49-fixture CI-gate set rises from 0.744 toward the
  0.860 ceiling P2 computed, **with zero regression** on any fixture currently scoring 1.0
  on this field (a regression on an already-correct fixture fails the experiment outright,
  regardless of the net score change — this experiment must not trade one failure mode for
  another).
- Overall gate score (currently 79.3%, `eval/results/latest.json` git_sha `70d7424`) must
  not drop below its current value on any language.
- If either bar is missed, the experiment is a negative result and gets written up as one
  (rule 116) — reverted, not iterated on blind.

### Held-out split (stated before any run)

The 49-fixture CI-gate set is the set the prompt is *scored* against, which makes it
unsuitable as a held-out validation set for a change targeting exactly those fixtures'
known failure cases — tuning against the same 5 fixtures that motivated the change is the
contamination pattern CLAUDE.md rule 65c and this repo's own D3 discussion (`eval/README.
md`) both name. The held-out split for this experiment must be drawn from the 83
consensus-grown fixtures currently parked in `eval/fixtures/_pending_groq_cassette/` (not
yet promoted into the active CI gate) — those fixtures were never seen while writing the
proposed prompt change, and already carry real panel-adjudicated ground truth from PR #140.
Any `buy_again` hedges among those 83 give a genuine out-of-sample test of the same
hypothesis before it touches the scored gate set.

## Consequences

- No prompt edit for `buy_again` ships without this ADR merged first (satisfied by this
  PR) AND a subsequent PR reporting the held-out-split measurement's actual result before
  and after, per CLAUDE.md rule 65b's provenance requirement.
- `sentiment` hedging is explicitly NOT authorized for prompt-level intervention under this
  ADR. A future ADR could revisit it, but only after the fixture-relabeling question above
  is resolved — attempting a sentiment prompt change against the current evidence would be
  optimizing against the panel's own judgment, not toward it.
- This ADR does not itself change the prompt, run an eval, or spend any quota. The next
  step (running the held-out measurement) is a separate, explicitly-scoped action.

## Alternatives considered

- **Run the experiment against the 49-fixture gate set directly.** Rejected: contaminates
  the CI gate's own change-detection purpose (`eval/README.md`'s own warning: "this gate is
  a CHANGE DETECTOR, not a quality bar") by tuning toward the exact fixtures used to
  motivate the change.
- **Include `sentiment` in scope, since it's the larger raw hedge count.** Rejected: P2's
  evidence is that the raw count is inflated by the metric-definition bug (7/16 already
  correct) and the real remainder has zero panel-confirmed headroom — including it would
  mean designing an experiment with no evidence it can succeed.
