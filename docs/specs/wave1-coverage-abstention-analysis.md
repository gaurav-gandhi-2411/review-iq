# Wave 1 P2 — coverage/abstention analysis: what's actually recoverable

Session 7 P2. Source of truth: `eval/results/latest.json` (git_sha `70d7424`, generated
2026-09-10T13:31:35Z, 49 CI-gate fixtures) cross-referenced against
`eval/consensus/results/consensus_labels.jsonl`'s `validate`-mode entries (132 fixtures,
recovered by PR #140 — a 2-judge blind panel, `openai/gpt-oss-120b` + `qwen/qwen3.6-27b`,
neither judge sees the other's output before voting). All 49 active fixtures have panel
labels. Zero Groq/live-API calls — every number below comes from already-committed cassette
and consensus data. Method and script: `scripts/p2_abstention_analysis.py` (this PR).

## P2a/P2b — abstention classification and the metric-definition bug it surfaced

This repo's only two hedge-tracked fields (per `eval/README.md`'s existing coverage
discussion) are `buy_again` (hedge value `null`) and `sentiment` (hedge value `"mixed"`).
Before classifying *why* the model hedges, every hedge output was checked against its own
fixture's ground truth for the degenerate case: **hedge value == ground truth**. That case
isn't an abstention at all — it's a correct answer — but the naive coverage metric quoted in
`eval/README.md` (`buy_again` coverage 81%→50%, `sentiment` coverage 46%) counts it as one
regardless, because it only checks "is the prediction null/mixed," never "does null/mixed
happen to be right here."

**Finding: this metric-definition bug is real and non-trivial.** Of the 49-fixture set's raw
hedge outputs:

| Field | Raw hedge count | Hedge == ground truth (miscounted as abstention) | Real hedge (hedge != ground truth) |
|---|---|---|---|
| `buy_again` | 14 | 4 (28.6%) | 10 |
| `sentiment` | 16 | 7 (43.8%) | 9 |

For `sentiment` specifically, **43.8% of what the existing coverage number counts as
"abstention" is actually the model correctly recognizing genuinely mixed sentiment.** The
46% English sentiment coverage figure in `eval/README.md` should be read as substantially
overstating the real hedge rate until that doc is corrected (flagged, not fixed in this PR —
see Follow-up below).

### Real hedges only — panel-adjudicated decidability

For the 19 *real* hedges (definition above), the blind 2-judge panel's own independent
silver label decides the classification: **decidable-but-hedged** if the panel converges
(unanimous or majority) on a committed, non-hedge answer; **genuinely ambiguous** otherwise
(panel itself hedges, or the two judges split).

| Field | n (real hedges) | decidable-but-hedged | genuinely ambiguous |
|---|---|---|---|
| `buy_again` | 10 | 5 (50.0%) | 5 (50.0%) |
| `sentiment` | 9 | 0 (0.0%) | 9 (100.0%) |

**`buy_again`: real, recoverable headroom exists.** In all 5 decidable-but-hedged cases the
panel's unanimous silver label matches the fixture's original expected value exactly
(`003_prompt_injection`, `005_all_positive`, `009_competitor_heavy`, `027_harm_in_positive_
tone_high`, `028_fit_pain_high`) — two independently-blind model families, neither seeing
the prompt under test's exact wording, both landed on the same answer the model itself
declined to commit to. Resolving these 5 would move the `buy_again` field's own mean score
from 0.744 to 0.860 (+11.6pp, n=43 fixtures carrying this field) — this is the actual
recoverable-headroom number, not the raw 10/43 or 14/43 hedge-rate figures.

**`sentiment`: ~zero recoverable headroom, and the panel disagrees with the *original ground
truth* more than with the model.** In 5 of the 9 real hedges (`014_feature_requests`,
`017_very_long`, `025_competitor_switch`, `027_harm_in_positive_tone_high`,
`028_fit_pain_high`), both panel judges *unanimously* land on `"mixed"` — the same value the
model under test produced — while the fixture's original single-label ground truth says a
clean `positive`/`negative`. In the remaining 4 (`012_sarcasm`, `015_medium_urgency`,
`022_two_star_explicit`, `025_competitor_switch`), the panel itself splits. Re-tuning the
prompt to make the model commit more on sentiment would not be closing a hedging gap — the
evidence says the *opposite* risk is live: the original single-label ground truth may
itself be the less-defensible label in over half of these cases, and a prompt change that
successfully drove the model toward the original label would be optimizing away from what
two independent judges consider more honest. This is exactly the calibration-fitting failure
mode `eval/README.md` already warns about, just discovered from the other direction. **No
prompt work is justified here without first re-auditing these 5 fixtures' ground truth**,
which is a fixture-quality question, not a model-quality one.

## P2c — Hindi sentiment: named risk, not a caveat

Verified directly from the same 49-fixture set, `language == "hi"`, field `sentiment`:

| Fixture | Predicted | Expected | Correct? |
|---|---|---|---|
| `004_hinglish` | positive | mixed | WRONG (confidently) |
| `hi-001` | negative | negative | correct |
| `hi-002` | positive | positive | correct |
| `hi-003` | mixed | mixed | correct |
| `hi-004` | negative | negative | correct |
| `hi-005` | positive | mixed | WRONG (confidently) |
| `hi-006` | neutral | neutral | correct |

n=7 total, 1 hedge (`hi-003`, correctly — see P2a), **6 answered, 4 correct, 2 wrong-
committed: accuracy-on-answered = 66.7%.** Both wrong cases share the same failure
direction: the model says `positive` when the truth is `mixed` — a confidently wrong answer
in the direction of missing negative signal entirely, the worst direction for a business
that would act on this classification.

**Bootstrap 95% CI** (`eval.bootstrap.bootstrap_ci`, seed 42, 10,000 resamples, scores
`[1,1,1,1,0,0]`): **[33.3%, 100%]**. At n=6 this number is compatible with anything from a
coin flip to near-perfect — it is not evidence of a 66.7%-accurate system, it is evidence
that n=6 cannot support any claim about Hindi sentiment accuracy at all.

**Required n to resolve it** (`eval.bootstrap.required_n_for_half_width`, extrapolating
from this sample's own variance, same mechanism `eval/consensus/results/consensus_summary.
json`'s MDE figures use): **~102 fixtures** for a ±10pp half-width, **~410** for ±5pp. Current
real Hindi-sentiment n is 7. This is not a small gap — it is the same order of magnitude as
the corpus-mining yield gap ADR 0004 already names (current real Indic-strata yield: 595
records total, ~12% of the 2,000-record floor MDE≈3.5pp target) — **it is very likely the
same underlying gap**, not a second, independent problem.

**Naming the risk explicitly, per the brief:** a 66.7%-quoted, [33%, 100%]-CI accuracy
figure on Hindi sentiment, with both errors in the same commercially-worst direction, is the
single number most likely to kill an agency pilot that is evaluated on Hindi review volume.
It must not be quoted as a stable product number in any customer-facing material until n is
in the ~100+ range — quoting it today would repeat the exact staleness/overclaim pattern
Session 7 P1 just closed for published metrics, aimed at internal confidence instead of
public copy.

## P4c — what closing this gap for real would take (plan only, zero quota)

Folded in here because it is the same yield problem as above, not a separate one.

- **Candidate source**: the corpus-mining pipeline PR #25 already built
  (`benchmark/vernacular_v2/corpus_pipeline/`) is the only real candidate source in this
  repo — two CC0-1.0-cleared Kaggle corpora, one wired, current real Indic yield 595/2,000
  floor (~30%; ~12% of the *comfortable* 5,000 target). No further candidate sourcing work
  exists beyond what ADR 0005 already priced (additional public sources, licensed data,
  narrowing the claim to Hinglish-only, or accepting the gap).
- **Panel composition**: reuse the existing calibrated 2-judge panel (`openai/gpt-oss-120b`
  + `qwen/qwen3.6-27b`) — `allam-2-7b` was already dropped by the standing calibration
  policy (9/33 control-set misses). No new judge selection work needed.
- **Token cost per 100 fixtures**: `eval/consensus/run_consensus.py`'s existing run recorded
  actual spend for 233 growth candidates → 83 accepted fixtures; that run's real per-fixture
  cost (both judges, one pass) is the only real cost basis in this repo — reuse it rather
  than re-estimate from scratch (not re-derived here to avoid quoting a stale/guessed number;
  pull it from that PR's own cost report before running anything new).
- **Calendar time**: gated entirely by real corpus yield, not compute — at 595/2,000 current
  yield after the sourcing work already done, reaching the ~100-fixture Hindi-sentiment-
  specific floor (a subset of the broader Indic floor) depends on which of ADR 0005's four
  options GG picks. No new timeline estimate is made here; it is a direct function of that
  undecided choice.

## Follow-up done in this PR

`eval/README.md`'s coverage paragraph was corrected in this same PR to note the
already-correct-miscounted-as-abstention effect found above, so future readers don't
inherit the overstated hedge-rate framing.
