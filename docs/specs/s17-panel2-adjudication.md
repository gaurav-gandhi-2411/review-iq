# S17 G6: panel-2 silver adjudication of the 60 unscored held-out field-pairs

**Target user:** a reader of the Samidha Reviews held-out accuracy claim (recruiter, buyer, GG) who needs to know how much the headline depends on the pairs nobody could label.
**Pain point:** the published held-out headline (79.6% [76.2, 82.8], n=70 unseen reviews) is accuracy WHERE the first panel agreed; 60 of 560 field-pairs (10.7%) were unscored and the Manski bounds on the truth are wide ([70.9%, 81.6%], ADR 0033). Panel 1 already split on these pairs, so more of the same panel cannot help.
**Success metric:** the share r of the 60 pairs a second, vendor-disjoint panel resolves; the resulting narrowed Manski interval; panel-2 inter-rater agreement (Krippendorff alpha, Fleiss kappa) and concordance with panel 1 on pairs panel 1 already resolved.
**Who pays:** GG (portfolio credibility); OpenRouter spend authorised up to about 1 USD, hard cap 2.00 USD.

Status: PRE-REGISTRATION. Committed before any completion call to a panel-2 candidate. The only network calls made before this commit were free metadata GETs (`/api/v1/models`, `/api/v1/endpoints/zdr`) confirming the five candidates exist and have ZDR endpoints.

## Context

ADR 0032/0033: the headline excludes (review, field) pairs on which the 3-judge panel (qwen3.6-27b, qwen3.8-27b, gemini-3.5-flash-lite) did not reach unanimous/majority agreement, because the stored gold for those pairs is a schema-valid default, not a label. Unscored pairs by field (unseen reviews): product 16, topics 15, pros 17, cons 12 (buy_again, sentiment, competitor_mentions, stars_inferred 0), over 46 of the 70 unseen reviews. The selection effect is real: split pairs are the hard ones. `eval/heldout_unscored.py` bounds the score without assumptions; a point estimate needs an independent adjudication.

Panel 2 must be independent of (a) production (OpenAI via Groq, Meta Llama via the OpenRouter failover, Google Gemini via the dormant fallback) and (b) panel 1 (Alibaba Qwen, Google). Step G6a (separate commit) hardened `assert_no_self_judging` to compare vendor family against all production models read from Settings.

## Hypotheses (pre-registered, falsifiable)

- **H1** A vendor-disjoint panel resolves a materially larger share of the 60 pairs than 0. Reported as r with a Wilson 95% interval; no threshold is claimed in advance because the panel-1 failure suggests many of these pairs are text-ambiguous. A result of r below 0.25 will be reported as "panel 2 also cannot resolve most of these pairs", and no point estimate will be presented as informative.
- **H2** Panel 2 is a valid adjudicator only if it reproduces panel-1 consensus where panel 1 was clear: concordance on the validation arm (V) of at least 0.90 per field-pair class (primary rule). Below 0.90 the silver labels are reported as unreliable and the narrowed interval is reported but flagged.
- **H3** Agreement on T (the hard pairs) is lower than on V. This is expected by construction; the size of the gap is the finding.

## Design

**Candidates** (OpenRouter, all required to have a ZDR endpoint): `deepseek/deepseek-v4-flash` (DeepSeek), `nvidia/nemotron-3-super-120b-a12b` (NVIDIA), `mistralai/mistral-small-2603` (Mistral), `z-ai/glm-4.7-flash` (Zhipu), `thinkingmachines/inkling-small` (Thinking Machines). Disallowed vendors: OpenAI, Meta, Google (incl. Gemma), Alibaba/Qwen. Every call sends `provider={"zdr": true, "allow_fallbacks": true}`; the runner also checks the candidate against the live `GET /api/v1/endpoints/zdr` list (exact `model_id` match) before the first call. A model with no ZDR endpoint is dropped, not routed around. `assert_no_self_judging(roster, extra_forbidden_families={alibaba, google: panel 1})` must pass for the active panel.

**Judge call** (identical to panel 1 so results are comparable): `JUDGE_SYSTEM_PROMPT` / `JUDGE_USER_TEMPLATE` from `eval/consensus/panel.py`; only the review text is shown (no production prompt, no gold defaults, no panel-1 votes, no model predictions). temperature 0, `response_format` json_object, `max_tokens` 2000, reasoning disabled via `reasoning: {"enabled": false}` where the model accepts it, `usage: {"include": true}`. A call-configuration failure (a model emitting no parseable JSON on the first calibration item because of a request parameter, as with panel 1's qwen reasoning mode) may be fixed by changing request parameters for that model, before any judgement outcome is inspected; any such change is recorded under Deviations. Judging unit: one review, all fields, one call.

**Calibration gate.** Every candidate runs `control_set.json` (16 items, 33 field checks) plus `control_set_hinglish.json` (12 new synthetic items, 36 field checks, written for this study, each with a `why_unambiguous` note, not drawn from any corpus; a unit test asserts none of their sentences occurs in any held-out/dev/benchmark fixture). 69 checks per candidate; pass = at most 2 misses (the panel-1 `MAX_ALLOWED_MISSES`). The active panel is the 3 passing candidates with the fewest misses from 3 distinct vendors (tie-break: lower mean cost per call, then id order). If fewer than 3 pass, STOP and report; the gate is not relaxed.

**Target sets.**
- **T**: the 46 unseen held-out reviews with at least one unscored headline field (60 unscored pairs).
- **V**: the validation arm, 20 reviews drawn with `random.Random(42).sample(sorted_ids, 20)`. Pre-registered fallback, fixed before any panel-2 output was seen: the stated pool ("unseen reviews where panel 1 resolved every headline field unanimously") has size 1 (checked from `labeling_meta.agreement_per_field`; 24 unseen reviews have every headline field resolved, only 1 has every one unanimous), so V is drawn from the 24 unseen reviews where every headline field was resolved by panel 1 (unanimous or majority). Concordance is reported on all V pairs and separately on the subset where panel 1 was unanimous.

Headline fields: product, buy_again, sentiment, topics, competitor_mentions, pros, cons, stars_inferred (`headline_fields` in `eval/results/held_out_scoring_v2.json`).

**Equivalence classes and the resolution rule.** Per (review, field), judges' answers are clustered by an equivalence relation, then resolved with the panel-1 rule: with 3 judges, UNANIMOUS = all three equivalent, MAJORITY = at least one pair equivalent, SPLIT otherwise; resolved = unanimous or majority; NO_RESPONSE counts as a non-agreeing invited judge (ADR 0020). Reported side by side: strict (unanimous-of-3) and majority-of-3 (which, for 3 judges, is identical to the panel-1 resolved rule; it is the primary set). Relations (thresholds fixed here, not tuned after seeing results):
- `product`: ADR 0030 `canonical_product` equality (null spellings collapse; no substring or synonym credit).
- `topics`: ADR 0030 `canonical_topic` applied to each item, then Jaccard of the sets >= 0.5 (`voting.JACCARD_THRESHOLD`, panel 1's value).
- `competitor_mentions`: ADR 0030 `canonical_competitor`, Jaccard >= 0.5.
- `pros`, `cons`: ADR 0030 defines no matcher; panel-1 normalised-item Jaccard >= 0.5 is used.
- `buy_again`, `sentiment`: exact. `stars_inferred`: +/-1 cluster (`voting.vote_scalar_tolerant`).
Sensitivity variant: `panel1_literal`, i.e. `voting.consensus_for_item` unchanged (product by lowercased exact string, no ADR 0030 canonicalisation), reported for comparability with panel 1.
The silver value of a resolved pair is the agreed value (longest list among the agreeing pair for list fields; the median for `stars_inferred`; the original string of an agreeing judge for `product`).

**Agreement.** Krippendorff alpha (nominal; ordinal for `stars_inferred`) and Fleiss kappa (`eval/agreement.py`) over equivalence-class assignments: for each (review, field) the judges' answers are mapped to a class id (connected components of the equivalence relation, class ids assigned by first appearance, so the statistic measures how often judges fall in the same class). Per field and pooled, on T and on V. Alpha with free-text class labels is meaningful only as a within-item concordance measure (class ids are arbitrary across items; the nominal metric does not use them across items), and is reported with that caveat. Self-consistency if budget allows: one repeat (same request, replicate key 1) of the first 10 T reviews per active judge; reported as the fraction of (review, field) pairs whose two runs are equivalent.

**Narrowing the bounds** (new function in `eval/heldout_unscored.py`; no published number changes). For the 70 unseen reviews and 8 headline fields (560 pairs): r = panel-2-resolved pairs / 60. A review's score is the mean over its 8 fields of: the existing as-deployed field score for panel-1-resolved pairs; the as-deployed prediction scored against the panel-2 silver value (`score_fixture` with that field's gold replaced) for pairs panel 2 resolves; for pairs neither panel resolves, 0 (lower bound), 1 (upper bound), or excluded (point estimate). Reported: (a) narrowed Manski interval; (b) the point estimate "silver-adjudicated estimate, conditional on panel-2 agreement" with a bootstrap CI (10,000 resamples over reviews, seed 42, `eval.bootstrap.bootstrap_ci`) and the unresolved fraction beside it. A point estimate is a silver estimate, never ground truth, and the selection effect repeats on the pairs panel 2 also cannot resolve; the report will say so in those words.

**Outputs.** `eval/consensus/results/panel2_calibration.json`, `panel2_votes.jsonl` (raw votes with model id, tokens, cost), `panel2_silver.json` (resolved pairs, silver label, the narrowed interval, agreement stats), cassette `eval/cassettes/panel2_cassettes.json` (replay reproduces every result at zero cost). Every silver value is labelled "LLM-consensus silver (panel 2), disjoint from production; NOT ground truth".

## Decision rules

1. Fewer than 3 candidates pass calibration from 3 distinct vendors: stop and report; do not run T or V.
2. Cumulative OpenRouter cost (from `usage.cost`) reaches 2.00 USD: stop immediately and report what exists.
3. A silver label is used in the narrowed interval only if the pair is resolved under the primary rule.
4. If V concordance is below 0.90 (H2), the narrowed interval is still reported but the report states the silver labels did not reproduce panel-1 consensus and recommends against citing the point estimate.
5. No README or site number is changed by this work; a published-number change needs GG's review.

## Budget

About 140 calibration calls + 198 T/V calls (66 reviews x 3 judges) + 30 self-consistency calls = about 370 calls, about 0.9M tokens, expected 0.5-1 USD. One batch, paced (reviews sequential, 3 judges concurrent, 0.5 s between reviews). Retry with exponential backoff (2/4/8 s) only on HTTP 429/5xx and transport timeouts, at most 3 retries; a call that returned HTTP 200 is never retried, even if its content fails to parse (counted as NO_RESPONSE). Cost is logged per call from OpenRouter usage accounting.

## Limits

- LLM-consensus silver is not ground truth; three LLMs agreeing can all be wrong, and shared pretraining data makes errors correlated even across vendors.
- Resolution is conditional on agreement: pairs panel 2 also cannot resolve remain unobserved, and are exactly the ambiguous ones.
- 12 new Hinglish control items are synthetic, written by the same author as the run; they test gross competence, not edge-case judgement.
- n is small (46 + 20 reviews, 60 pairs); per-field intervals are wide, and Krippendorff alpha on free-text class ids is a within-item concordance measure only.
- The equivalence thresholds for pros/cons (Jaccard 0.5) are panel 1's, not validated against human judgement.
- ZDR routing may restrict a model to a single provider, so quantisation or deployment differences from the vendor's own API are possible and not controlled.

## Deviations

Appended before the first live completion call (commit with `eval/consensus/panel2.py`); any later deviation is appended with its reason before the dependent run.

- **D1 (format leniency, no effect on judgement):** the response parser first tries the panel-1 strict parser, then strips `<think>...</think>` blocks and takes the outermost `{...}` object. It never edits a field value. Reason: reasoning-capable candidates may wrap JSON in prose even with `response_format` set; counting that as a judgement miss would test the wrapper, not the judge.
- **D2 (agreement-class coding):** closed-set fields (`buy_again`, `sentiment`) use the answer itself as the category (an explicit null answer is its own category); `stars_inferred` is ordinal over 1..5; free-text fields use equivalence-component ids. The pooled figure treats all fields as nominal with field-prefixed categories. This only fixes details the spec left implicit.
- **D3 (budget accounting):** the 2.00 USD cap is checked against cumulative recorded cost (cassette total), not per process.
