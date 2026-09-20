# Session 15c S2 -- language routing: what it does, what it costs, what to do

Status: analysis and recommendation only. No code, prompt, gate or baseline changed.
Zero quota: every number below is re-derived from RECORDED predictions
(`eval/results/held_out_scoring_v2.json`, predictions git_sha `5c5c8e0`, scorer
`2026-09-20.free-text-v1`) by `scripts/measure_routing_cost.py` -> `eval/results/routing_cost_n106.json`.
CIs: paired percentile bootstrap, 10,000 resamples, seed 42, unclamped. Headline = mean over fields
EXCLUDING the constant `stars` field (GG's decision). Tags: **VERIFIED (how)** = I ran/read it;
**BELIEVED** = inference not tested here.

## Findings first

1. **The misrouting effect on extraction is not distinguishable from zero once the `language` field is
   removed from the headline.** Over the 8 real fields (everything except `stars` and `language`):
   as-deployed 72.40% [69.78, 74.94], language-forced 71.76% [69.36, 74.11], paired delta
   **-0.63pp [-2.77, +1.51]** (23 fixtures up, 30 down, 53 identical). Forcing the "correct" prompt does
   not help on average and the CI rules out a misrouting cost larger than about 2.8pp. **VERIFIED**
   (`uv run python scripts/measure_routing_cost.py`; `headline.ex_stars_ex_language`).
2. **The published "misrouting costs 4.6pp" (ADR 0021 / ADR 0023) is a scoring tautology, not an
   extraction effect.** Recomputed on the corrected scorer over all 10 fields the ADR quantity is
   +4.68pp [+2.87, +6.62], and the `language` field alone supplies 111% of it (the other nine fields net
   to slightly negative). Under `language_forced` the prompt itself says `language: always "hi-en"`, so
   the field is 100% by echo; as-deployed it equals detector-vs-corpus-label agreement exactly. This
   contradicts a prior shipped conclusion (ADR 0021 Decision, ADR 0023 Context item 3) and is surfaced
   here rather than absorbed. (Session 15d: ADR 0021 and ADR 0023 now carry dated Correction
   sections; their original text is unchanged.) **VERIFIED**
   (`adr_0021_quantity_all_10_fields`; per-fixture assertion in the script that as-deployed `language`
   score == 1[detected == corpus label] on all 106).
3. **Including `language` in the headline, misrouting looks like 17.2% [10.9, 23.4] of the gap to 100%;
   excluding it, -2.3% [-10.5, +5.3].** The ex-language number is the honest one, because the
   forced-condition `language` score measures prompt echo of a label, not any model skill. Residual
   (extraction/label/scorer error) is ~100% of the 27.6pp gap. **VERIFIED** (same artifact).
4. **Where the customer actually feels the label is the `language` output field itself, not the other
   fields.** `/v2/extract` overwrites the model's `language` with `detect_language()` output
   (`app/api/v2/extract.py:102`), so the API's `language` value IS the detector's output; its agreement with the
   corpus label is 48.1% (Wilson 95% [38.8, 57.5]) on n=106 (agreement, never accuracy). That label is itself noisy (judge alpha 0.380
   on `language`, ADR 0023), which bounds any accuracy claim about the field. **VERIFIED** (code read;
   artifact) for the mechanism; the alpha figure is quoted from ADR 0023 (**VERIFIED** by file read,
   not recomputed).
5. **The corpus cannot tell you the detector is bad on real traffic.** It holds 101 hi-en and 5 en
   fixtures (always-hi-en scores 95.3% on it -- a base-rate artifact), and the 53 misses are reviews that
   are essentially English with one Hindi token (median 99 characters; e.g. "A bit uncomfortable on ears
   baki to mast haiREAD MORE"). On the separate benchmark set (`benchmark/dataset/gold.jsonl`, 43 real reviews, split by its
   `slice` mining label -- note its LLM `gold.LANG` disagrees with `slice` on some rows, e.g.
   bench-en-001) the same detector agrees with `slice` on 42/43. **VERIFIED** (`detector_baselines`, `detector_external_controls`). The 42/43 is
   optimistic: detector vocabulary was extended in July from a Hinglish benchmark
   (`app/core/language.py:24-38`), **BELIEVED** to overlap that gold in distribution.
6. **Recommendation:** do not invest in a better `detect_language` for extraction accuracy (nothing
   measurable to gain); stop presenting `language` as an accuracy-scored field or expose it with
   uncertainty; and run one cheap experiment (~55-65K tokens) before deciding whether routing can be
   deleted. See S2c.

## S2a -- what the routing decision does, and where it sits

**What `detect_language` returns.** `DetectedLanguage = Literal["en","hi-en","hi","other"]`
(`app/core/language.py:48`). Rules in order (`:62-96`): stripped text shorter than 5 chars -> `other`
(`:73-74`); any Devanagari code point (danda excluded, `:17`) -> `hi` (`:76-77`); any STRONG Hinglish
regex hit (`:19-40`) -> `hi-en` (`:79-80`); three or more WEAK regex hits (`:42-46`, `:82`) -> `hi-en`;
lingua English-confidence < 0.5 -> `other` (`:85-92`); else `en` (`:96`). The lingua detector is built from
only {English, Hindi} (`:57`), and on all 106 Roman-script held-out texts its English confidence is exactly
1.0 (min = max = 1.0), so step 4 never fires on this corpus. **VERIFIED**
(`detector_baselines.lingua_english_confidence`). This is why the label is effectively a lexicon test:
every one of the 53 missed hi-en fixtures has at least one weak marker but no strong one and fewer than
three weak (`missed_vs_caught_hi_en`).

**What chooses the prompt.** `build_prompt(wrapped_review, language)` (`app/core/prompts/__init__.py:12-30`):
`hi` -> `hi.py` (`:24-25`), `hi-en` -> `hi_en.py` (`:26-27`), everything else including `other` -> `en.py`
(`:28-29`). The task brief said `hi` is retired; I found no such record: `hi.py` is still wired, Devanagari
text still routes to it, and the held-out corpus simply contains no `hi` fixtures. Flagged, not assumed.
**VERIFIED** (code read; `grep -i retire` over `app/`, PROMPTS.md, ADR 0018/0019 found nothing).

**Where it runs (all call `detect_language` on the raw review text, before the LLM call):**
`/v2/extract` (`app/api/v2/extract.py:75`, prompt `:95`), `/demo/extract` (`app/api/demo.py:254-256`),
reply drafting (`app/core/reply/engine.py:113`, extraction prompt `:135`, reply prompt `:153`), authenticity
scoring (`app/core/authenticity/engine.py:125`, `app/core/prompts/authenticity.py:119-125`). The router
runs a second, redundant detection on the already-built PROMPT (`app/core/router.py:130`); because the
hi-en prompt text itself contains Hinglish markers it re-detects the prompt's own label (verified by
probe), and the result feeds only logs/metrics.

**What differs by the label:**

| Behaviour | Depends on label? | Evidence |
|---|---|---|
| Prompt text and few-shots | Yes. en.py, hi_en.py, hi.py are different rubrics (4 / 4 / 2 examples; 6,048 / 6,398 / 2,587 chars for a sample review) | probe over `build_prompt`; **VERIFIED** |
| Field rubric details | Yes. Example: `buy_again` is "false only if reviewer explicitly says they would not repurchase, null if ambiguous" in en.py:12 vs "false ... or implies dissatisfaction" in hi_en.py:22-23. hi_en.py also has SARCASM / SERVICE vs PRODUCT / WARRANTY sections | file read; **VERIFIED** |
| Model tier | **No.** `choose_tier` returns `"small"` for every language (`app/core/routing_policy.py:24-38`); escalation is by confidence/schema/star-sentiment mismatch only (`:41-96`) | **VERIFIED** |
| Cost/latency | Negligible via prompt length (1,751 vs 1,746 mean input tokens small/large tier, `token_cost_measurement_n106.json`); label is a bucket key in cost telemetry (`extract.py:183-195`, `demo.py:297-309`, `app/api/admin.py:76-78,393`) | **VERIFIED** |
| `language` output field returned to the customer | `/v2/extract`: overwritten with the detector label (`extract.py:102`) then saved (`:144-161`). `/demo/extract`: the model's self-report, which the prompt instructs to echo (`en.py:22`, `hi_en.py:13`) | **VERIFIED** |
| Reply drafting | Reply is written "in {language_name}" (`app/core/prompts/reply.py:151`); English-only grounding check (`guardrails.py:138`); on large-model quota exhaustion `hi`/`hi-en` hard-fail with 503 while `en` degrades to the small model with a caveat (`reply/engine.py:23,178-201`) | file read; **VERIFIED** that the branch exists |
| Authenticity prompt | Task template chosen by language (`authenticity.py:119-125`) | **VERIFIED** |
| Stored data | `language` persisted on extractions/corrections and copied into dataset export (`app/core/dataset/builder.py:52,75`) | **VERIFIED** |

**Failure mode of a mislabel, measured on extraction** (recorded, n=106; per-field paired delta of
language-forced minus as-deployed, pp, [95% CI]): product +2.8 [-0.9, +7.5]; buy_again -10.4
[-22.6, +1.9]; sentiment +0.9 [-5.7, +7.5]; topics -1.1 [-5.1, +2.6]; competitor_mentions +0.3
[0.0, +0.9]; pros -0.7 [-4.2, +2.8]; cons +1.2 [-1.9, +4.8]; stars_inferred +1.9 [0.0, +4.7];
stars 0 (constant); language +51.9 [+42.5, +61.3] (tautology). No real field's CI excludes zero (competitor_mentions
and stars_inferred have a lower bound of exactly 0.0 but move on only 1-2 fixtures). The one visible pattern is `buy_again`: the forced hi-en prompt is worse on 28 fixtures and
better on 17, consistent with the differing `buy_again` rubric above -- **BELIEVED** mechanism (the
sign is measured, its cause is a reading of the two prompts, not an ablation), and its CI includes 0.
So the routing decision is really a choice between two different field rubrics, not just "language
handling". Unmeasured and plausibly the larger real cost: reply-language and reply-guardrail behaviour
for a Hinglish review labelled `en` (**BELIEVED**; no recorded data covers replies).

## S2b -- how much of the headline is misrouting

All numbers: `eval/results/routing_cost_n106.json`. n=106; 55 mismatched (53 gt hi-en detected en; 2 gt
en detected hi-en), 51 matched.

| Headline | as-deployed | language-forced | paired delta | misrouting share of gap to 100% |
|---|---|---|---|---|
| 9 fields (ex stars, INCLUDES `language`) | 69.70% [67.29, 72.09] | 74.90% [72.76, 76.98] | +5.20pp [+3.19, +7.36] | 17.2% [10.9, 23.4] |
| 8 fields (ex stars, ex `language`) -- **honest** | 72.40% [69.78, 74.94] | 71.76% [69.36, 74.11] | -0.63pp [-2.77, +1.51] | -2.3% [-10.5, +5.3] |
| 8 fields, OLD strict scorer (sensitivity) | 66.85% | 66.08% | -0.77pp [-2.84, +1.30] | n/a |

Both first-row values reproduce the recorded `overall_score_excluding_constant_fields` (0.69701 /
0.74902) exactly (asserted in the script). The strict-scorer row is derived as
(10 x overall_strict - stars - language) / 8, assuming stars and language are exact-match under both
scorers (**BELIEVED**, per ADR 0030 which only changes product/topics/known-gaps); it shows the conclusion is
not an artifact of the scorer fix. **Which is honest:** the 8-field row. The 9-field misrouting effect
(+5.20pp) is 0.519/9 = +5.77pp of `language` echo minus 0.56pp of real-field loss; reporting it as
"misrouting cost" would be the tautology in finding 2.

Split by mismatch status (8-field headline; delta = forced minus as-deployed):

| Subset | n | as-deployed | forced | delta [95% CI] | up / down / zero |
|---|---|---|---|---|---|
| matched | 51 | 71.17% | 71.17% | **exactly 0** | 0 / 0 / 51 |
| mismatched | 55 | 73.54% | 72.31% | -1.22pp [-5.19, +2.96] | 23 / 30 / 2 |
| gt hi-en, detected en | 53 | 73.37% | 72.46% | -0.91pp [-4.97, +3.34] | 23 / 28 / 2 |
| gt en, detected hi-en | 2 | 77.87% | 68.36% | -9.51pp (n=2, CI not informative) | 0 / 2 / 0 |

Sanity check passed: on all 51 matched fixtures the predictions are byte-identical, no second call was
made, and the maximum per-field |delta| is 0.0. **VERIFIED** (asserted in the script). The two gt-en
fixtures are hien-0033 and hien-0088, both of which contain real Hindi tokens ("vasool", "yaar"); their
label is arguable, which is the label-noise point again.

Attribution: of the 27.60pp gap between as-deployed and 100% (8 fields), misrouting accounts for
-2.3% [-10.5, +5.3] and residual error for 102.3%. Read this as: **there is no detectable misrouting
loss in extraction quality on this corpus; the ~28pp gap is extraction, gold-label and scorer error, not
routing.** Caveats, all **BELIEVED** unless stated: (a) one cassette recording per condition at
temperature 0.0 (`app/core/providers/groq.py:106`, **VERIFIED**), so prompt-perturbation noise is inside the
delta; (b) "correct routing" is defined by the noisy corpus label, so the forced condition is not a
proven optimum; (c) residual mixes real extraction error with silver-label error (agreement is
"majority" not "unanimous" on product/pros/cons per fixture metadata), so it is an upper bound on
model error.

## S2c -- recommendation (report only; nothing implemented)

**What a better detector could reach, measured offline** (`detector_baselines`,
`detector_external_controls`; all variants use only the production regexes, no new vocabulary, no fitted
threshold; these are diagnostics, not proposals):

| Rule | Held-out n=106 agreement with corpus label (Wilson; label alpha 0.380, not accuracy) | recall hi-en (n=101) / en (n=5) | Benchmark set recall vs `slice` hi-en (n=21) / en (n=22) | CI-gate en recall (n=27) |
|---|---|---|---|---|
| always hi-en | 95.3% [89.4, 98.0] | 101/101, 0/5 | 21/21, 0/22 | 0/27 |
| always en | 4.7% [2.0, 10.6] | 0/101, 5/5 | 0/21, 22/22 | 27/27 |
| production detector | 48.1% [38.8, 57.5] | 48/101, 3/5 | 20/21, 22/22 | 27/27 |
| strong regex only | 47.2% [37.9, 56.6] | 47/101, 3/5 | 20/21, 22/22 | 27/27 |
| strong or >=1 weak marker | 95.3% [89.4, 98.0] | 101/101, 0/5 | 21/21, 22/22 | 27/27 |
| strong or >=2 weak markers | 51.9% [42.5, 61.2] | 52/101, 3/5 | 21/21, 22/22 | 27/27 |

Reading: (i) "improving the detector" on the 106 is dominated by a corpus artifact -- agreement there is
decided by whether one-token reviews count as Hinglish, the exact ambiguity ADR 0023 identified. The
">=1 weak marker" variant catches every held-out hi-en and creates no false positive on 49 external English
texts (22 benchmark `slice`=en, 27 hand-built CI-gate), but also flags all 5 held-out gt-en fixtures (and one of the weak markers, "value for money", is
ordinary English, so a 49-text negative set is not enough to trust it). **BELIEVED**: any lexicon change
would be tuned on the same 106 that are the held-out measurement, i.e. contamination. Given finding 1
there is also nothing demonstrated to buy with it.

**Option (ii), unified prompt / no routing -- what evidence exists.** The hi-en prompt's result is
recorded for 103 of 106 fixtures (101 gt hi-en via the forced condition, 2 gt en that the detector sent
to hi-en). Over those 103, "always send hi-en" minus as-deployed on the 8-field headline is
**-0.47pp [-2.66, +1.78]** and minus language-forced is +0.18pp [0.0, +0.52]
(`policy_always_hi_en_prompt_recorded_subset`). So for Hinglish-heavy traffic, removing the router is
indistinguishable from keeping it. **What cannot be tested without quota:** the hi-en prompt on genuine
English reviews. The only recorded evidence is n=2 (-9.5pp, not informative) and its 3 missing held-out
en/en fixtures. A real unified prompt (ADR 0023's proposal) has zero recorded data. Token cost: ~2.4-2.5K
tokens per call (`token_cost_measurement_n106.json`), 100K/day/model ceiling
(`eval/results/capacity_model.json`), so the 27 hand-built CI-gate English fixtures (10-field ground truth, en-prompt result already
recorded in `eval/results/latest.json`) + the 3 held-out en/en fixtures = 30 calls = ~75K tokens (one
day, ~75% of one model's ceiling). A full 106-call unified-prompt run = ~265K tokens (about three
days). The 22 real English benchmark reviews are NOT a substitute: their LLM labels cover only
SENT/URG/LANG, not the 8-field headline.

**Option (iii), keep routing but stop exposing/relying on a discrete label.** Cheapest and directly aimed
at the only measured defect. `language` is (a) the one field whose "accuracy" moves by 52pp, (b) equal to
detector output by construction, and (c) scored against a gold label with alpha 0.380 -- a discrete
en/hi-en value cannot be defended as an accuracy claim. Additive, non-breaking options: return
`language` plus a `code_mixed: bool` (or the weak/strong marker counts as a confidence), and drop
`language` from the headline metric definition (it is already a tautology under forced routing). No
prompt change and no eval baseline change is needed to do this; changing the scored field set is a
baseline-definition decision for GG (escalation item 3), not made here.

**Recommendation.**

1. **Do not spend on detector accuracy for extraction quality.** Measured effect is -0.63pp
   [-2.77, +1.51]; the routing-cost story in ADR 0021/0023 should be treated as superseded by finding 2
   (errata added in Session 15d as dated Correction sections in both ADRs).
2. **Ship (iii) as a small additive API change** if the `language` field is a customer-visible claim, and
   report the headline ex-`language`.
3. **Settle (ii) with the cheapest decisive experiment, staged:**
   - Stage 1 (~75K tokens, 30 calls, 1 day): run the existing `hi_en` prompt on the 27 CI-gate `en`
     fixtures and the 3 unrecorded held-out en/en fixtures; compare per-fixture to the already-recorded
     en-prompt results. The CI-gate set overlaps the en prompt's few-shots (ADR 0021), which favours
     the en prompt, so this test is conservative for no-routing. Futility rule: if the mean paired
     8-field delta (hi-en minus en) is below -5pp, stop -- routing stays and (i) becomes the
     conversation.
   - Stage 2 (only if stage 1 passes; ~3 days at the ceiling): author the unified en+hi-en prompt and run
     it on all 106 held-out (~265K tokens) plus the 30 English fixtures (~75K).
   - **Decision rule (non-inferiority, pre-registered):** adopt no-routing only if the lower 95% bound of
     (unified minus routed) is >= -3pp on the 8-field headline for BOTH strata (Hinglish, n>=103; English,
     n>=30). Note the power limit: the per-fixture SD of the delta on the held-out set is 11.2pp
     (**VERIFIED**, computed from the recorded field scores), so n=30 cannot certify a -3pp margin
     (standard error ~2.0pp); stage 1 is a futility screen, not a
     proof, and the English stratum would need roughly 50 fixtures for a 3pp margin (**BELIEVED**,
     back-of-envelope from the held-out SD: (1.96 x 0.112 / 0.03) squared ~= 53).
4. **Untested and potentially the real cost:** reply-language/guardrail behaviour under mislabel. It
   cannot be measured from recorded data; if a reply-quality measure is wanted it needs its own fixture
   set, and that, not extraction, is where a label fix would pay.

## Verification ledger

| Claim | Status | How |
|---|---|---|
| Script reproduces recorded headline (0.69701 / 0.74902) | VERIFIED | asserted in `scripts/measure_routing_cost.py`; run output |
| 51 matched fixtures: identical predictions, delta exactly 0 | VERIFIED | assertions in the script; `sanity` block |
| as-deployed `language` score == detector-agrees indicator, 106/106; forced == 1.0, 106/106 | VERIFIED | assertions in the script |
| Routing effect on 8 fields -0.63pp [-2.77, +1.51] | VERIFIED | artifact `headline.ex_stars_ex_language` |
| ADR 0021 quantity is 111% `language` | VERIFIED | artifact `adr_0021_quantity_all_10_fields` |
| `choose_tier` ignores language | VERIFIED | `routing_policy.py:38` |
| `/v2/extract` overwrites `language` with detector output | VERIFIED | `extract.py:102` |
| lingua step is a no-op for Roman-script text on this corpus | VERIFIED (106 texts) | artifact `detector_baselines.lingua_english_confidence` |
| Strict-scorer sensitivity | BELIEVED-derived | needs stars/language exact-match assumption |
| buy_again drop is caused by the rubric difference | BELIEVED | sign measured, cause inferred from prompt text |
| Reply-language cost of a mislabel | BELIEVED | code paths read, no measurement |
| Alpha 0.380 on `language` | VERIFIED by reading ADR 0023 | not recomputed here |
| Detector 42/43 vs benchmark `slice` is optimistic | BELIEVED | lexicon was extended from that benchmark family; `slice` is a mining label, not the LLM `gold.LANG` |
| Token/cost estimates for stage 1/2 | VERIFIED inputs, BELIEVED arithmetic (30 x 2.5K; 106 x 2.5K) | 2.4-2.5K tokens/call from `token_cost_measurement_n106.json`; 100K/day from the task brief |
| `hi` prompt "retired" | NOT FOUND | `hi.py` still wired; discrepancy with the task brief flagged |

Reproduce: `uv run python scripts/measure_routing_cost.py` (deterministic apart from the `generated_at`
and `git_sha` provenance fields; no network, no DB, no quota).
