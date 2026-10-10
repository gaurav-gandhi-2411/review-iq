# ADR 0034: panel-2 silver adjudication of the 60 unscored held-out pairs (v1 gate failed; post-hoc scoped v2 gate run)

Status: accepted. **The panel that produced the results below was chosen by a gate written AFTER the pre-registered
gate had failed. Everything under "Result (v2)" is a post-hoc, exploratory analysis relative to the pre-registration.**
Pre-registration and amendment: `docs/specs/s17-panel2-adjudication.md` (Amendment A1). Numbers come from
`eval/consensus/results/panel2_calibration.json`, `panel2_votes.jsonl`, `panel2_silver.json`; every one reproduces at zero cost with
`python -m eval.consensus.panel2 --mode replay --phase all` against `eval/cassettes/panel2_cassettes.json` (verified
byte for byte, run twice). LLM-consensus silver (panel 2), disjoint from production; NOT ground truth.

## Context

ADR 0033: the held-out headline (79.6% [76.2, 82.8], n=70 unseen reviews) is accuracy where panel 1 agreed; 60 of 560
field-pairs (product 16, topics 15, pros 17, cons 12) were unscored, Manski bounds [70.9%, 81.6%]. Panel 1 (Qwen x2,
Gemini) split on them, so an independent, vendor-disjoint panel was needed: not OpenAI/Meta/Google (production
vendors via Groq, the OpenRouter Llama failover, the dormant Gemini fallback), not Alibaba/Google (panel 1), ZDR-routed.

G6a (separate commit): `assert_no_self_judging` now compares vendor family against all production models read from
Settings and fails closed on an unmapped family. Side effect: panel 1's `gemini-3.5-flash-lite` trips it (Google is the
dormant fallback's vendor), so a fresh panel-1 labeling run now fails loud; existing labels are unchanged.

## Decision

1. **v1 gate (pre-registered):** 28 control items (16 existing + 12 new synthetic Hinglish/Hindi), 69 checks per
   candidate, pass = at most 2 misses, need 3 passing candidates from 3 vendors. It FAILED: 2 of 5 passed.
2. **v2 gate (POST-HOC, Amendment A1, orchestrator-authorised, written after seeing the v1 results):** count only the
   headline fields plus `stars` (35 of the 69 checks), still at most 2 misses; panel chosen mechanically (3 lowest
   scoped misses, 3 distinct vendors, ties by lower cost). Justification is relevance, not outcome: `language` is outside
   the headline (alpha 0.380, ADR 0023), none of the 60 pairs is a `language` pair, and 13 of the 17 v1 misses were
   `language`. Nothing else changed (prompt, parameters, equivalence thresholds, resolution rule, targets, cap).

## Result: calibration (140 live calls, 0.016035 USD; all HTTP 200 and parsed)

| candidate (vendor) | v1 misses /69 | v1 pass | v2 scoped misses /35 | v2 pass | missed (field: expected -> got) |
|---|---|---|---|---|---|
| deepseek/deepseek-v4-flash (DeepSeek) | 0 | yes | 0 | yes | none |
| nvidia/nemotron-3-super-120b-a12b (NVIDIA) | 2 | yes | 0 | yes | calh-004, calh-010 language hi -> hi-en |
| thinkingmachines/inkling-small (Thinking Machines) | 3 | no | 0 | yes | cal-004, calh-004, calh-010 language hi -> hi-en |
| z-ai/glm-4.7-flash (Zhipu) | 4 | no | 2 | yes | language x2; cal-010, cal-011 stars -> null |
| mistralai/mistral-small-2603 (Mistral) | 8 | no | 2 | yes | language x6 (hi-en -> hi); cal-010, cal-011 stars -> null |

v2 active panel: deepseek-v4-flash, nemotron-3-super, inkling-small (0 scoped misses each; glm and mistral at 2, so not
selected). v1 outcome stands as reported: 2 of 5 passed, no panel.

## Result (v2, exploratory): target sets

T = 46 reviews with 60 unscored pairs. V = 20 reviews (seed 42) drawn from the 24 unseen reviews panel 1 fully resolved
(the pre-registered pool "all unanimous" had size 1).

**Resolution of the 60 pairs** (primary rule = panel-1 rule with ADR 0030 canonicalisers; for 3 judges "majority of 3"
equals the primary resolved set): r = 29/60 = **0.483**; strict unanimous-of-3 5/60 = 0.083.
Per field (resolved/pairs, primary): product 8/16 (0.50), topics 10/15 (0.67), pros 8/17 (0.47), cons 3/12 (0.25).
Sensitivity (panel-1 literal voting, no ADR 0030 canonicalisation): 26/60 = 0.433, strict 4/60.

**Validation arm V** (concordance of panel 2 with panel-1 silver, 160 pairs): panel 2 resolved 149; concordant 133
= **0.893 among resolved** (0.831 counting unresolved as misses). Below the pre-registered 0.90 (H2), so decision rule 4
applies: the silver labels did not reproduce panel-1 consensus well enough to recommend citing the point estimate. Where
panel 1 was unanimous (115 pairs): 0.956 among resolved (109/114). By field (concordant/pairs): product 17/20, buy_again
19/20, sentiment 19/20, topics 16/20, competitor_mentions 19/20, pros 9/20, cons 14/20, stars_inferred 20/20. `pros` is
the weak field.

**Agreement** (alpha / Fleiss kappa over equivalence-class assignments; T then V): buy_again 0.711/0.709, 0.857/0.855;
sentiment 0.851/0.850, 0.810/0.806; stars_inferred (ordinal alpha) 0.870/0.710, 0.773/0.508; pooled 0.701/0.700,
0.739/0.738. For the free-text fields (product, topics, competitor_mentions, pros, cons) alpha/kappa are about 0 (T:
-0.04 to 0.05; V: product 0.07, pros -0.09, competitor_mentions 1.0) and must NOT be read as "no agreement": class ids
are arbitrary component labels, almost every unit is one dominant class, and kappa/alpha collapse under that
prevalence. The resolution rates above are the meaningful free-text agreement measure.
Self-consistency (same call repeated, 10 reviews x 8 fields, equivalent answers): deepseek 71/80 = 0.89, nemotron 74/80
= 0.93, inkling 76/80 = 0.95.

**Narrowed bounds** (70 unseen reviews, 560 pairs; `heldout_unscored.adjudicated_block`; panel-2 silver treated as truth
on resolved pairs): original Manski [70.9%, 81.6%] narrows to **[73.7%, 79.3%]** (lower 0.7373 CI [0.704, 0.770]; upper
0.7927 CI [0.762, 0.821]); 31 of 60 pairs (5.5% of all 560) remain unresolved. Silver-adjudicated estimate, conditional
on panel-2 agreement: **78.0%** [74.9, 81.0] (bootstrap seed 42, n=70), with 31/60 unresolved beside it. The model's
mean score on the 29 resolved pairs against panel-2 silver is 0.625 (product), 0.473 (topics), 0.505 (pros), 0.712
(cons), well below its 79.6% headline: the pairs panel 1 could not label are harder for the model too.

**Spend:** total 0.046648 USD (368 calls, 231,836 tokens in, 41,808 out): deepseek 0.005246, nemotron 0.010808, inkling
0.025932, glm 0.001826 and mistral 0.002836 (calibration only). Authorised about 1 USD, cap 2.00.

## What this settles and does not

- The Manski interval narrows from a width of 10.7 to 5.6 points, but only because 48% of the unscored pairs got a
  silver label. A point estimate is NOT defensible: it is conditional on panel-2 agreement, the 31 pairs panel 2 also
  cannot resolve are the ambiguous ones (so the selection effect repeats), V concordance (0.893) missed the 0.90 bar,
  and `pros` concordance was 9/20. Do not quote 78.0% as the model's accuracy.
- The direction is informative: on the resolved previously-unscored pairs the model scores far below its headline, which
  is consistent with the headline overstating overall accuracy (ADR 0033's selection-effect concern), though the
  narrowed interval still contains the headline value.
- Exploratory: the gate was loosened after seeing results; a replication with a pre-registered scoped gate would be
  needed to treat this as confirmatory.

## Consequences

- No README, site or published-number change; the held-out artifact is untouched, and no marker block was extended.
- Guard side effect: panel-1 judge `gemini-3.5-flash-lite` and the dormant Gemini fallback shared a vendor.
  **Decision (GG, 2026-10-05, option (a)): retire the dormant Gemini fallback and drop the Google judge from any future
  panel-1 run. Implemented in ADR 0035.** The Google judge is kept as data only (`RETIRED_JUDGE_MODELS`); a fresh panel-1
  run (qwen3.6-27b + qwen3.8-27b) passes the guard; existing panel-1 labels are unchanged and were produced with the old
  three-judge roster. The guard's production-model list is now derived from every model-named Settings field, so a Google
  rule returns automatically if a Google provider is ever re-added.
- A second adjudicator for `pros` (the weakest field) would be the next lever; see the `pros` gold investigation below,
  which suggests the matcher, not the adjudicator, is the first thing to fix.

## The `pros` gold investigation (S17 X4c, exploratory, $0)

Two independent signals made `pros` the suspect field: V-arm concordance of 9/20 (the weakest), and split-exclusion
lifting `pros` from 53.9% to 73.8% (VERIFIED from `eval/results/held_out_scoring_v2.json`
`per_field_split_effect`, all 106 reviews, as deployed). Reproduce with `python scripts/analyze_pros_gold.py`
(output `eval/results/pros_gold_analysis.json`, committed; no model call, no published number changes). Unexposed
n=70 unless stated.

**1. How `pros` gold is made.** Gold = panel-1 `voting.vote_list_overlap`: each judge's pros list is normalised
(lowercase, whitespace) into a SET OF WHOLE-PHRASE STRINGS and judges agree when the Jaccard of those sets is at least
0.5. So two judges agree only if at least half of their pros are character-identical after lowercasing; ADR 0030 defines
no matcher for `pros`/`cons` and none is used. Unanimous needs every pair to agree; majority one pair; the stored gold
is the longest list of the agreeing judges (one judge's wording, not a merge). A split is stored as `[]` with the pair
flagged unresolved. Gold origin, unanimous / majority / default (unresolved), on the 70: product 30/25/15, topics
27/28/15, pros 24/29/17, cons 46/12/12, buy_again 48/22/0, sentiment 68/2/0, competitor_mentions 60/10/0, stars_inferred
70/0/0 (stars_inferred is 70 "unanimous" under the +/-1 tolerance). `pros` has the highest default share among the
free-text lists (24.3%; all 106: 28.3%, product 24.5%, topics 23.6%, cons 19.8%) and the lowest unanimous share (34.3%).

**2. The 11 discordant V-arm `pros` pairs** (panel 2 disagrees with, or cannot resolve against, panel-1 gold; hand
classification from the actual texts and judge lists, in `HAND_CLASS` in the script): **5 paraphrase** (same points,
different wording or granularity: hien-0016, 0030, 0046, 0066, 0079), **5 omission** (at least one judge left out a
pro the others list: hien-0056, 0058, 0071, 0089, 0090; two of these also differ in wording), **1 different** (hien-0001,
"mast hai" read as praise by two judges and as "sound quality is great" by a third; gold says "overall good quality").
In 10 of 11 the best single panel-2 judge has token-F1 of at least 0.67 with the gold; the points mostly agree and the
strings do not. Three anonymised examples (review ids, short quotes): hien-0066 text "...sound quality is best and
battery is also good" gold [best sound quality, good battery] vs judges [sound quality is best, battery is good]: the
same two points, zero identical strings, item Jaccard 0.0, token-F1 1.0. hien-0090 text "Ultimate headset Paisa wasool":
gold [good value for money] vs judges [value for money], [Paisa wasool], []: one judge listed no pro. hien-0001 text "A bit
uncomfortable on ears baki to mast hai": gold [overall good quality] vs [mast hai (great otherwise)], [mast hai], [sound
quality is great]: a genuinely ambiguous Hinglish reading.

**3. Does the score measure the matcher?** Model (as deployed) score on the resolved pairs under item-level soft F1,
greedy one-to-one matching of phrases whose token Jaccard is at least t, t from 0.2 to 1.0:

| field (resolved pairs) | current scorer | t=0.2 | 0.3 | 0.4 | 0.5 | 0.6 | 0.7 | 0.8 | 1.0 (exact) | swing max-min | swing 0.3 to 0.7 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| pros (53) | 0.731 | 0.810 | 0.744 | 0.717 | 0.713 | 0.665 | 0.557 | 0.543 | 0.537 | 27.2 pts | 18.6 pts |
| cons (58) | 0.851 | 0.883 | 0.857 | 0.823 | 0.823 | 0.811 | 0.804 | 0.804 | 0.804 | 8.0 pts | 5.4 pts |
| topics (55) | 0.695 | 0.711 | 0.711 | 0.711 | 0.711 | 0.695 | 0.695 | 0.695 | 0.695 | 1.6 pts | 1.6 pts |

The `pros` score moves 18.6 points between two defensible matcher thresholds (27.2 across the sweep), `cons` 5.4 (8.0), `topics` 1.6.
The resolution rate shows the same: panel-1 `pros` pairs resolved at Jaccard thresholds 0.3/0.4/0.5/0.6/0.7: 59/55/53/46/39
of 70 (84% to 56%); `topics` 62/55/55/46/38; `cons` 64/58/58/54/54. A field whose number swings this much with the matcher
is measuring the matcher, and `pros` is the field that does most. (`pros` today is scored by token-F1 over the union of
all words, which is itself a third matcher; it sits at about the t=0.3 value.)

**4. Resolution failures by judge count and cause.** Every one of the 17 unresolved `pros` pairs (70) had all three judges
respond (no pair failed for non-response). 15 of 17 are granularity (some judge pair has token-F1 of at least 0.5 although
item-level Jaccard is below 0.5; across all 106, 26 of 30) and 2 are different pros altogether (hien-0077 and hien-0106
have no overlap at all). So `pros` pairs fail resolution mostly because of set-granularity, not because judges disagree
about the review.

**5. Conclusion and recommendation.** Treat the `pros` gold as UNRELIABLE AS A STRING-MATCH TARGET: the stored label is one
judge's phrasing of a point set, resolution is decided by exact phrase identity, and the model's score against it swings
by 18.6 points with the matcher. That is a statement about the label and metric, not evidence that the model is bad at
pros (the model-versus-gold token overlap is plausibly mostly paraphrase; BELIEVED, not tested against humans).
Exploratory headline (as deployed, 70 unseen reviews, split pairs excluded; label as exploratory, the published headline
is unchanged): all 8 fields 79.6% [76.2, 82.8] (published); **excluding `pros` 80.5% [77.1, 83.7]**; `pros` alone 73.1%
on 53 resolved pairs. Recommended fix, in order: (a) score `pros` as soft recall of the gold points at a pre-registered
item threshold (0.5 token Jaccard), or by a pairwise-containment rule, instead of whole-phrase identity or union token-F1;
(b) resolve `pros` pairs by containment ("each point of judge A is covered by some point of judge B and vice versa at
Jaccard 0.5") rather than exact-string Jaccard; BELIEVED to recover most of the 15 granularity failures, UNVERIFIED until
re-run on the cassettes; (c) a rubric/granularity rule in the judge prompt (one pro = aspect plus valence in at most
four words, no qualifiers), which needs a new panel run and so is not part of this change; (d) until (a) or (b) lands,
report `pros` separately next to the headline rather than inside it. Not applied here: a scorer or headline change
needs GG's review (CLAUDE.md 65c) and a regenerated published copy in the same PR.

## Alternatives

- Stop at the v1 failure (the first report): honest but leaves the 60 pairs unexamined.
- Add or swap candidates until three pass v1: a forking path after seeing results; rejected.
- Relax to two judges: no majority rule and no kappa; rejected.
- Human adjudication: out of scope (GG labels nothing).
