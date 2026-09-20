# ADR 0031: `buy_again` English few-shot coverage experiment — pre-registration

Status: **pre-registered before any run** (Session 15c, S6). Results are appended below in a
`## Results` section by the commit that records them; nothing above that heading changes after
the first run. Slipped five sessions (ADR 0012 authorised it, ADR 0016/0017 kept it open).

## Context

On the n=106 held-out corpus, 56 fixtures are routed to the **English** prompt as deployed (the
language detector agrees with the corpus label on only 51 of 106, so most of these are Hinglish
text reaching the English prompt — a fact this experiment inherits, not fixes; see S2).
Recorded baseline on those 56 (`eval/results/held_out_scoring_v2.json`, as deployed):

| gold \ predicted | `null` | `true` | `false` |
|---|---|---|---|
| `null` | 36 | 1 | 0 |
| `true` | 15 | 1 | 0 |
| `false` | 3 | 0 | 0 |

Coverage (committed) = 2/56; wrong-committed = 1 (hien-0038: gold null, predicted true);
hedged while gold is decidable = 18 of 19. The model almost never commits `buy_again` in
English, and the current prompt's definition ("Only false if reviewer explicitly says they would
not repurchase. Null if ambiguous.") plus two of its four examples showing `null` plausibly
explains it. Only the `buy_again` few-shot examples are in scope.

## Hypothesis (stated before any run)

Adding two English few-shot examples that show `buy_again` committed to `true` (specific praise
plus an explicit recommendation) and to `false` (a specific defect plus an explicit statement of
not buying again) — while keeping the two existing `null` examples — raises English `buy_again`
coverage on the held-out set without raising the number of wrong committed answers.

## Design

- **Only** the English few-shot block changes: two synthetic examples are inserted after the
  existing four. The field definition, the system prompt, every other field, the schema, the
  model routing and the hi-en prompt are untouched. `sentiment` is not touched (ADR 0012's
  disjoint-judge bar for `sentiment` remains unmet).
- The examples are **written for this experiment** (invented kettle and backpack reviews). They
  are not drawn from the held-out corpus, and no held-out review text was read to write them; a
  unit test asserts none of their sentences occurs in any held-out or dev fixture.
- Implemented as an experiment module (`eval/experiments/buy_again_fewshot.py`) that builds the
  variant prompt from the production template. **`app/core/prompts/en.py` is not modified** and
  no prompt version is bumped: editing it would invalidate the dev-set cassette replay for every
  English fixture and require live re-recording that does not fit the daily budget. A promotion
  PR (version bump, PROMPTS.md, dev re-record) is a separate step taken only on success.
- Evaluated on the **held-out 106 only**, restricted to the 56 English-routed fixtures (the
  only ones the English prompt sees). Never the 49-fixture dev set. Scored with the corrected
  scorers now on main (`buy_again` is an enum, exact match).
- Same call path as the baseline: sanitize → detected language → wrap → prompt →
  `route_extraction(allow_gemini_fallback=False)`; a separate cassette file
  (`eval/cassettes/buy_again_exp_cassettes.json`) so runs are replayable.

## Split and budget

Two days, one batch per day, each within the 50%-of-day ceiling (100K tokens per model of the
200K TPD pools). The 56 English-routed ids are sorted; day 1 takes the even positions (28), day
2 the odd positions (28). Estimated cost per call ~3.0K tokens (~2.3K prompt with the two added
examples + ~0.8K output) → ~84K on the small model per day plus escalations on the large model
(~59% of calls historically). The runner keeps a running per-model token total and refuses to
start a call that would take either model past 95K. Groq's requests-remaining header is read
before and after each batch as an independent check.

## Success criterion and stopping rules (fixed now)

Let B_cov = 2 and B_wrong = 1 be the baseline coverage count and wrong-committed count on the 56.
Let N_cov and N_wrong be the same counts under the variant over the fixtures run so far.

- **SUCCESS** (after both days): N_cov > B_cov **and** N_wrong ≤ B_wrong.
- **REVERT** (any time): N_wrong > B_wrong. This is a hard rule from GG: any rise in
  wrong-committed, revert. It is evaluated on the *count*, not the rate, because coverage rising
  inflates the denominator and would hide a rise in a rate. Because the count can only grow, if
  day 1 alone exceeds B_wrong = 1 the experiment stops and **day 2 is not run**.
- **NULL**: N_cov ≤ B_cov with N_wrong ≤ B_wrong after both days. Reported as a finding.
- Wrong-committed means: predicted non-null and field score exactly 0.

Secondary, reported but not gating: coverage on the 19 gold-decidable fixtures; accuracy on
answered; exact McNemar test on per-fixture commit changes; sentiment predictions unchanged
count (a leakage check, since only `buy_again` examples changed); tokens per model per day.

## Consequences and honest limits

- Baseline wrong-committed is already 1 of 2, so the REVERT bar (no additional wrong commit) is
  demanding: 0 wrong among every new commit. That is the rule as given; it is stated here so a
  revert is not read as a surprise.
- The test set is Hinglish text through the English prompt. A gain here says the English
  prompt commits more on this distribution; it does not say what a corrected router would do.
- n=56 with 19 decidable is small; a SUCCESS is a coverage gain on this sample, not a
  population estimate. Any promotion still needs the dev-set re-record and a fresh eval.
- If NULL or REVERT: the next levers (definition wording, a third example type, or routing)
  are not tried blind (rule 116).

## Alternatives considered

- Edit `app/core/prompts/en.py` directly: rejected (dev cassette invalidation, above).
- Use held-out fixtures as examples: rejected (contamination).
- Run all 56 in one batch: rejected (~170K tokens on the small model, over the ceiling).

## Results

Everything above this heading was committed at `f026c88` (2026-09-20 12:43 IST) before any call.

### Day 1 — 2026-09-20, 28 of 56 fixtures (INTERIM; the verdict needs day 2)

Source: `eval/results/buy_again_exp_day1.json` (git sha `f026c88`, which contains the runner and
this ADR), cassette `eval/cassettes/buy_again_exp_cassettes.json`. Replayable at zero quota with
`EVAL_CASSETTE_MODE=replay ... buy_again_fewshot.py replay --day 1`.

| Day-1 fixtures (n=28) | Baseline (recorded) | Variant |
|---|---|---|
| `buy_again` committed | 1 | **4** |
| wrong-committed (count) | 0 | **1** (hien-0018: gold null, predicted true) |
| committed and correct on the gold-decidable fixtures (n=10) | 1 (hien-0069) | 3 (hien-0039, -0069, -0103); 7 decidable fixtures still hedged |

Commit changes: 0 fixtures lost a commit, 3 gained one (exact McNemar p = 0.25, so not
significant at n=28 by itself). The pre-registered rule on the total count: N_wrong so far is
1, equal to B_wrong = 1, so the run is **INTERIM, not REVERT**. That margin is thin: the
baseline's own wrong commit (hien-0038) is a day-2 fixture, so if the variant repeats it the
total reaches 2 and the rule REVERTs regardless of coverage.

Two things to read with the numbers, not after them:

- The variant added one *new* wrong commit on a fixture the baseline had correctly left null
  (hien-0018), so the gain is not free even on day 1.
- `sentiment` changed on 3 of 28 fixtures (all `mixed` → `positive`, including hien-0018). Only
  `buy_again` examples were edited, but the examples' other fields evidently moved sentiment: a
  side effect the pre-registration flagged as a leakage check and did not gate on.

Budget (ceiling 100K/model/day): estimated 81,285 tokens on `gpt-oss-20b` (40.6% of its 200K
pool; 75,258 from recorded final-model tokens plus 6,027 estimated for two escalated small-tier
attempts) and 9,470 on `gpt-oss-120b` (2 escalated calls). Groq requests remaining, before →
after: `gpt-oss-20b` 999 → 969, `gpt-oss-120b` 999 → 999 (rolling window; 120b's 2 requests had
already replenished). Day 2 runs on the next budget day; it is NOT run in the same UTC day.
