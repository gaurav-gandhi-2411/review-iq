# S18 D2: `pros` soft-recall scoring (pre-registration)

**Target user:** a reader of the Samidha Reviews held-out accuracy claim (recruiter, buyer, GG) who needs to know whether the model's `pros` extraction is scored by a measure that does not depend on how the judges happened to word and split their phrases.
**Pain point:** `pros` gold is unanimous in only 34.3% of reviews with the highest default share (24.3%); the model's `pros` score swings 27.2 points across matcher thresholds (0.3 to 0.7); 15 of 17 unresolved `pros` pairs are granularity disagreements, not disagreements about content (ADR 0034, S17 X4c). The exact-phrase scorer in the headline therefore measures wording as much as extraction.
**Success metric:** a `pros` score whose swing between thresholds 0.4 and 0.6 is at most 5.0 points (V-a) and that treats the production model and independent judges alike (V-b), reported beside, not inside, the headline.
**Who pays:** GG (portfolio credibility); cost is 0 USD (committed artifacts and cassettes only, no model call).

Status: PRE-REGISTRATION. Committed BEFORE any soft-recall number is computed in this PR. The evidence in ADR 0034 (resolution rates, the 27.2-point swing of the item-level soft-F1 sweep, the granularity classification) was already known when this was written; no soft-RECALL value had been computed. Nothing below may be changed after the first computation; a change is an amendment appended at the end, with the reason.

## Context

The headline averages eight fields over 70 unseen reviews, scoring only pairs where the three-judge panel agreed (ADR 0033). `pros` is scored by `eval.runner._fuzzy_list_score` (token F1 over phrases) against a gold list that is itself a panel consensus over phrase lists. Panel agreement on a list is computed by an item-level Jaccard match, so a pair where the judges said the same thing in differently-sized phrases is recorded as a split and is unscored, and a pair where they happened to word it alike is scored against exact phrases. GG decision: score `pros` by soft recall at a fixed 0.5 token-Jaccard threshold and report it SEPARATELY beside the headline until the scoring is validated.

## Definition (fixed now)

For one review, gold pros `G` (list of phrases) and predicted pros `P` (list of phrases):

1. **Normalise** each phrase: Unicode NFKC, lowercase, every character in a Unicode punctuation or symbol category (P*, S*) and the underscore replaced by a space, split on whitespace into a **token set**. No stemming, no stopword removal, no transliteration folding. A phrase with an empty token set never matches anything.
2. **Match**: a gold phrase `g` is matched if some predicted phrase `p` has token-set Jaccard `|g ∩ p| / |g ∪ p| >= 0.5`. Matching is not one-to-one: one predicted phrase may match several gold phrases.
3. **Soft recall** of the review = (matched gold phrases) / `|G|`. **Undefined and excluded when `G` is empty**; the number of such reviews is reported. If `P` is empty and `G` is not, recall is 0.
4. **Soft precision** (secondary, never in the headline) = (predicted phrases matching some gold phrase) / `|P|`, undefined and excluded when `P` is empty; count reported.
5. **Aggregate**: mean over reviews where recall is defined; 95% CI by the percentile bootstrap over reviews (`eval.bootstrap.bootstrap_ci`, 10,000 resamples, seed 42).
6. **Population ("resolved gold pairs")**: the unseen reviews (the published cell, `exposure == []`, n = 70) whose `pros` pair is not in `unresolved_fields`; scored as deployed. This is the same population on which the headline scores `pros`.
7. **Threshold**: 0.5, fixed. The sweep 0.3, 0.4, 0.5, 0.6, 0.7 is computed and reported for transparency and for V-a only; it cannot change the choice of 0.5.

## Validation criteria (pre-registered; evaluated after implementation; not tuned)

- **V-a, sensitivity.** The absolute difference of the model's mean `pros` soft recall between Jaccard thresholds 0.4 and 0.6, on the resolved gold pairs (population above), must be **<= 5.0 percentage points**. Else the scoring is NOT validated.
- **V-b, concordance (does the matcher treat the model and independent judges alike).** Define the per-review **match decision** `D_X` of a source `X` against the panel-1 gold: `D_X = 1` iff the soft recall of `X`'s pros against the panel-1 gold (threshold 0.5) is `>= 0.5`, else `0`. Sources: the production model (as-deployed recorded prediction) and each of the three active panel-2 judges (`deepseek/deepseek-v4-flash`, `nvidia/nemotron-3-super-120b-a12b`, `thinkingmachines/inkling-small`, repetition 0 votes in `eval/consensus/results/panel2_votes.jsonl`). For each judge `j` and each of two sets, the concordance is the share of reviews with `D_model == D_j` among reviews where both are defined (panel-1 gold non-empty and resolved for `pros`; the judge returned a parseable output with a `pros` field). Sets: the **validation arm** (the 20 `V_review_ids` of `panel2_silver.json`) and the **target set** (the 46 reviews panel 2 judged in phase `main_T`; only reviews whose panel-1 `pros` pair is resolved have a gold to compare to). **V-b passes iff every one of the 3 judges reaches >= 85% on BOTH sets.** Secondary, reported but not a criterion: the pooled concordance, the base rate of `D = 1` per source (a concordance driven by a near-constant `D` is flagged), and Cohen's kappa.
- **V-c, resolved versus unresolved gap (reported, no pass/fail).** Model `pros` soft recall on (i) resolved gold pairs (population above) and (ii) the unresolved `pros` pairs of the same unseen reviews, scored against the panel-2 silver `pros` for the pairs panel 2 resolved (the stored gold of an unresolved pair is a default, not a label, and is not used). As a sensitivity, (iii) the same unresolved pairs scored against each panel-1 judge's own list in turn (mean over judges). n is small on (ii); the CI is reported as is.

## Decision rules

1. If **V-a or V-b fails**: the `pros` soft recall stays reported separately, labelled NOT validated, and the headline keeps the existing scorer.
2. If **both pass**: say so in the report and the rendered line. **This PR does not change the published headline composition**; whether `pros` moves into the headline is a separate decision by GG.
3. In every case the headline text states that it still scores `pros` with the exact-phrase scorer, whose result swings 27.2 points across matcher settings (the figure rendered from `eval/results/pros_gold_analysis.json`, not typed).
4. No existing published number changes in this PR; `pros_soft_recall` is an additive block in the artifact.

## Limits

- Token-set Jaccard is lexical: it cannot see synonyms, Hindi/Hinglish transliteration variants, or a paraphrase with no shared token. It under-credits real agreement in vernacular text, and it was not tuned on any of these reviews.
- Non-one-to-one matching can credit several gold phrases from one long predicted phrase; a verbose prediction can raise recall. This is why precision is reported, and why recall alone is never a headline.
- Excluding reviews with empty gold removes the cases where the right answer is "no pros", which the exact scorer scores. Their count is reported.
- Gold is LLM-consensus silver (panel 1), not human ground truth; the panel-2 judges are LLMs; V-b measures consistency of the matcher across sources, not correctness.
- V-b's `D` is a coarse binary; with a high base rate of `D = 1` concordance is easy. The base rate and kappa are reported for that reason.
- The validation-arm reviews can include reviews the development process had seen; V-b uses the recorded model outputs only to compare decisions, not to score the model.
- Threshold 0.5 was set by GG before this analysis; the 0.3 to 0.7 sweep cannot override it, and a V-a failure is a finding about the scorer, not a reason to pick another threshold.
