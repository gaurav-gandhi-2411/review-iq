# S20 S4: labelled held-out English fixtures for stage 2 of the no-routing experiment

Status: plan plus a USD 0.0166 pilot. The pilot STOPPED at its pre-registered early gate (urgency
agreement below the floor on the first 20 reviews). No fixtures are committed; the 19 silver
fixtures from the pilot sit in the session scratchpad for GG's decision.

Labelling convention for every number below: **VERIFIED** (command or file given) or **BELIEVED**
(inference, stated as such). Provenance for the pilot numbers is
`eval/results/s20_english_labelling_pilot.json` (script
`eval/experiments/english_labelling_pilot.py`, seed 42).

## S4a. How many English reviews exist, and how many does stage 2 need

### What exists

| Quantity | Value | Status |
|---|---|---|
| Held-out fixtures | 106 | VERIFIED: `ls eval/fixtures/_held_out_hindi_hinglish/*.json` |
| Ground-truth language en / hi-en | 5 / 101 | VERIFIED: `held_out_scoring_v2.json` records, `gt_language` |
| Usable in stage 1 | 3 (`hien-0039`, `hien-0056`, `hien-0069`) | VERIFIED: `no_routing_stage1.load_plan()` |
| Not used | 2 (`hien-0033`, `hien-0088`) | VERIFIED, reason below |

The two not used are ground-truth `en` but the deployed language router detected `hi-en`, so the
recorded as-deployed result already came from the hi-en prompt and there is no en-prompt result to
pair against (`load_plan` keeps only gt `en` AND detected `en`). Independently, both are exposed:
`hien-0033` is a prompt-visible dev fixture and `hien-0088` is in the benchmark gold
(`held_out_scoring_v2.json`, `exposure` field). The three used are unexposed (`exposure: []`).

Stage 1 itself, recomputed from the saved rows (VERIFIED: `english_deltas` / `delta_stats` from
`eval/experiments/no_routing_stage1.py` over `eval/results/no_routing_stage1_rows.json`, rows
written at `ba5b12c`; that rows file is untracked in the main checkout, and the `report` mode was
never saved to `no_routing_stage1_report.json`, which does not exist):

| Stratum | n | Mean paired delta (hi-en prompt minus en prompt, 8-field headline) | Sample SD |
|---|---|---|---|
| CI-gate en (dev, contaminated for the en prompt) | 27 | +8.47 pp | 15.2 pp |
| Held-out en | 3 | -1.70 pp | 1.9 pp |
| All | 30 | +7.45 pp | 14.7 pp |

### Sample size to certify the -3 pp margin

Rule (from `no_routing_stage1.py`): adopt the unified prompt only if the lower 95 percent bound of
the mean paired delta is at least -3 pp. With true mean `mu`, per-fixture delta SD `sd`, and n
fixtures, normal approximation:

- 50 percent power (the expected lower bound just clears): `n = (1.96 * sd / (mu + 3pp))^2`
- 80 percent power: `n = ((1.96 + 0.8416) * sd / (mu + 3pp))^2`
- undefined when `mu <= -3 pp` (non-inferiority cannot hold).

SD inputs are the recorded design values (BELIEVED to transfer to English; they were measured on
the Hinglish held-out set): 12.9 pp (106 fixtures, panel-split pairs excluded) and 18.1 pp (the 54
fixtures whose two runs differ), from `eval/results/no_routing_stage1_design.json`
(VERIFIED: `delta_sd_held_out`). The stage-1 English SD (14.7 pp, n=30, contaminated) falls between.

Total fixtures n, as "50 percent / 80 percent power" (VERIFIED: `python
eval/experiments/english_labelling_pilot.py power`):

| True mean delta | SD 12.9 pp | SD 18.1 pp |
|---|---|---|
| -1 pp | 160 / 327 | 315 / 643 |
| 0 pp | 72 / 146 | 140 / 286 |
| +1 pp | 40 / 82 | 79 / 161 |
| +2 pp | 26 / 53 | 51 / 103 |
| +4 pp | 14 / 27 | 26 / 53 |

The python (the whole calculation):

```python
import math


def n_required(mu, sd, power, margin=0.03):
    gap = mu + margin
    if gap <= 0:
        return None
    z = 1.959964 + (0.841621 if power == 0.8 else 0.0)
    return math.ceil((z * sd / gap) ** 2)
```

Sanity check by simulation (VERIFIED, same command): paired bootstrap, seed 42, 2000 simulated
studies, each with 1000 resamples, deltas drawn Normal(mu, sd), success = the 2.5th percentile of
resampled means is at least -3 pp.

| Cell | n | Predicted | Simulated |
|---|---|---|---|
| mu 0, SD 12.9, 50 percent | 72 | 0.50 | 0.517 |
| mu 0, SD 12.9, 80 percent | 146 | 0.80 | 0.806 |
| mu 0, SD 18.1, 80 percent | 286 | 0.80 | 0.793 |

### How many MORE are needed

Existing clean English: 3 (stage-1 held-out). Subtract them; the rest must be NEW labelled reviews.
One of the three (`hien-0056`) is called `hi-en` unanimously by the new panel (see pilot), so 3 is
an upper bound.

| Scenario | Total n | New fixtures needed (n minus 3) |
|---|---|---|
| mu 0, SD 12.9, 50 percent | 72 | 69 |
| mu 0, SD 12.9, 80 percent | 146 | 143 |
| mu 0, SD 18.1, 80 percent | 286 | 283 |
| mu +2, SD 12.9, 80 percent | 53 | 50 |
| mu -1, SD 12.9, 80 percent | 327 | 324 |

Recommendation (BELIEVED): label N = 145 new (about 80 percent power at mu 0 and the recorded
SD 12.9, minus the 3 existing), keep 285 as the stretch if the real SD is nearer 18.1. The N = 71
tier is a 50 percent coin flip at mu 0 and is only a futility screen, not a certification.
A true mean below about -1 pp cannot be certified at any affordable n; stage 2 would then correctly
return "not shown non-inferior".

## S4b. Labelling plan

### Source pool and disjointness

Pool: `eval/data/flipkart_candidates.jsonl` (gitignored, built by `eval/data/sample_flipkart.py`
from three Flipkart Kaggle datasets with DbCL-1.0 or CC0-1.0 licences, per `eval/data/README.md`).
VERIFIED counts (`english_labelling_pilot.py select`, stages recorded in the provenance JSON): 14,552
rows, 14,446 detected `en`, 6,664 in the 100-600 character window, 22 dropped as exact-seen, 6,642
eligible, so N up to 285 uses at most 4.3 percent of the eligible pool.

Selection (all zero-cost, deterministic):

1. Language: candidate `language == "en"` (lingua detector; a detector error is caught later,
   because the panel also labels `language` and non-`en` consensus items are dropped).
2. Length: 100-600 characters. Reason: the 27 dev `en` fixtures have median 283 characters
   (VERIFIED, fixture scan) while 14,446 pool rows have median 88; sub-100-character reviews are
   "good product" non-tests.
3. Disjointness, three layers:
   - Exact normalized-text match against `eval/heldout_exposure.py` sets: dev fixtures
     (`eval/fixtures/{,hi-en/,hi/}`), `benchmark/dataset/gold.jsonl`, and the 106 held-out fixtures.
   - Substring containment of the whole normalized review in the concatenated normalized text of
     every tracked text file under `app/ docs/ tests/ eval/ benchmark/ scripts/ web/`. This covers
     prompts, few-shots, all cassettes, consensus results, benchmark candidates and silver files.
     In the pilot it removed 3 more reviews the exact-match layer could not see.
   - Dedup on normalized text inside the pool.
4. Order: sort by normalized text (stable), shuffle with `random.Random(42)`, take the first N.
   Re-running reproduces the same list.

Scope statement (rule 85b): the exact layer matches whole normalized reviews only; a paraphrase of a
review inside a few-shot is invisible to it (same caveat as ADR 0032). The substring layer sees
verbatim embedding anywhere in tracked text but NOT untracked/ignored files; the ignored
`benchmark/vernacular_v2/*.jsonl` silver and prediction files were not scanned (BELIEVED low risk:
they derive from the same Flipkart pool but were never shown to a prompt). The scan should be
re-run at labelling time on the commit that will own the fixtures.

### Panel (disjoint from production by family, blind by construction)

| Judge | Family | Why |
|---|---|---|
| `mistralai/mistral-small-3.2-24b-instruct` | Mistral | cheapest ZDR instruct model of a third vendor |
| `qwen/qwen3-235b-a22b-2507` | Alibaba Qwen | non-reasoning, strong, cheap; a different checkpoint from the Groq qwen3.6/3.8-27b judges that labelled the held-out set |
| `deepseek/deepseek-v3.2` | DeepSeek | independent lineage, reasoning disabled per request |

- Disjoint from production extractors (gpt-oss-20b/120b = OpenAI, failover llama-3.3-70b = Meta):
  none of the three is OpenAI or Meta. `panel.assert_no_self_judging()` compares only against the
  configured Groq model ids, which none of these equal. Partial overlap with the existing held-out
  panel: Qwen (different checkpoint). Calibration agreement with the old gold is therefore not fully
  independent evidence (BELIEVED).
- ZDR: VERIFIED on 2026-10-09 against `GET https://openrouter.ai/api/v1/endpoints/zdr` (940
  endpoint rows): Mistral Small 3.2 (DeepInfra, Mistral, Venice, Parasail), Qwen3-235B-2507 (Nebius,
  Venice, Google, DeepInfra, Parasail), DeepSeek V3.2 (Mara, Phala, Venice, Google, SiliconFlow,
  DeepInfra, others) all have ZDR endpoints. Every request also sends `provider.zdr=true` and
  `data_collection=deny`; the responses show actual providers DeepInfra (Mistral, Qwen), SiliconFlow
  (DeepSeek), one Qwen call on Venice.
- Blind: the prompt is `eval/consensus/panel.py` `JUDGE_SYSTEM_PROMPT` + `JUDGE_USER_TEMPLATE`
  (independent wording, deliberately not `app/core/prompts/`; no extractor output, no few-shots, no
  rating, no product metadata, no other judge's answer, one independent call per judge). Temperature
  0, JSON mode, 700 completion tokens, reasoning off.
- Rejected: any OpenAI or Meta model (self-judging); allam-2-7b (already failed calibration,
  ADR 0016); Gemini via OpenRouter (the repo's existing Gemini judge already covers that family).

### Calibration (run, VERIFIED: `calibrate`, `calibration.json` in scratchpad)

Run on the 5 English held-out fixtures plus the 16-item `control_set.json` (21 items, 63 calls).

| Check | Result | Pre-registered rule | Verdict |
|---|---|---|---|
| Control misses (of 33 checks) | Mistral 2, Qwen 0, DeepSeek 0 | each at most 2 (existing `MAX_ALLOWED_MISSES`) | pass |
| Alpha sentiment, 21 items | 0.895 | at least 0.67 | pass |
| Alpha urgency (ordinal), 21 items | 0.935 | at least 0.67 | pass |
| Resolved fraction (sentiment and urgency) | 1.00 | at least 0.85 | pass |

Mistral's 2 misses are one item (`cal-003`, the fire hazard) where it returned `topics` as a string,
so schema validation failed and the whole output counted as missing: a format failure, not a
judgment error, but the judge sits exactly at the limit.

Panel silver vs the existing 3-judge consensus gold on the 5 held-out English fixtures, over fields
where the old gold is resolved: sentiment 5/5, urgency 5/5, buy_again 5/5 match; language 3/5 match.
The 2 language mismatches are `hien-0056` (old gold `en`, new panel `hi-en` unanimous) and
`hien-0088` (old `en`, new `hi-en` by majority). This is a real disagreement about two reviews'
language, not noise: BELIEVED that `en` vs `hi-en` is ambiguous for English text with Indian-English
spelling, and that the language label should not be a stage-2 stratifier. n=5, so these are
anecdotes.

### Inter-rater statistics and pre-registered thresholds

Set before any paid call (constants `MIN_ALPHA`, `MIN_ALPHA_FIELDS`, `MIN_RESOLVED_FRACTION`,
`EARLY_CHECK_N` in the script):

- Krippendorff alpha per field (nominal: sentiment, buy_again, language; ordinal: urgency,
  stars_inferred) with a seeded item-bootstrap interval (seed 42, 500 resamples); Fleiss kappa on
  the fully-covered subset and pairwise Cohen kappa for nominal fields; kappa for ordinal fields is
  reported pairwise as an unweighted cross-check only (alpha is primary, per `eval/agreement.py`).
- Acceptance: alpha at least 0.67 on sentiment AND urgency (Krippendorff's tentative floor), and at
  least 85 percent of items with sentiment and urgency resolved (unanimous or majority).
- Early stop: after the first 20 labelled reviews, re-check; if it fails, spend nothing further.
- Final acceptance for fixtures: the same thresholds on the full labelled set; the target (reported,
  not gated) is alpha at least 0.80, the level the existing Hindi/Hinglish panel reached on
  urgency (0.83) and sentiment (0.83, from `consensus_summary.json`).
- Language field: alpha on a near-constant field is dominated by prevalence; report it, never gate
  on it.

### Disagreement handling (ADR 0032 convention)

- Per field the existing voting rules apply (`eval/consensus/voting.py`): unanimous needs all three
  judges; majority is 2 of 3; split yields no label.
- A split pair is stored as a schema-valid DEFAULT and named in
  `labeling_meta.unresolved_fields`; scoring must skip it. It is never resolved by a tiebreak,
  never adjudicated by a fourth model, and never discarded (discarding biases toward easy cases).
- A judge whose output fails schema validation counts as NO_RESPONSE, so that item-field can be at
  best "majority", never "unanimous" (ADR 0020).
- Items whose consensus language is not `en` are dropped from the English fixture set and counted.
- Tier: all fixtures are `silver`, `quarantined: true`, `blind: true`. Not human ground truth.
  They live outside `eval/fixtures/`; promotion to a quarantined held-out directory is GG's call
  and should come with the exposure check re-run (`eval/heldout_exposure.py`).

### Cost table

Token sizes measured from the real labelling prompt (VERIFIED: pilot responses, 41 calls per model,
`per_model` in the provenance JSON): prompt about 636-641 tokens (instructions about 2,500
characters plus a review of mean 222 characters), completion about 122-127 tokens. Prices from
`GET https://openrouter.ai/api/v1/models` fetched 2026-10-09 (USD per 1M tokens, in/out); the
metered column is what OpenRouter actually charged through the ZDR endpoints used (`usage.cost`).

| Model | List price in/out | Mean tokens in/out | USD per fixture at list | USD per fixture metered |
|---|---|---|---|---|
| Mistral Small 3.2 | 0.094 / 0.25 | 641 / 127 | 0.0000918 | 0.0000712 |
| Qwen3-235B-2507 | 0.09 / 0.55 | 636 / 122 | 0.0001242 | 0.0001206 |
| DeepSeek V3.2 | 0.259 / 0.42 | 637 / 125 | 0.0002174 | 0.0002127 |
| Panel of three | | | 0.000433 | 0.000405 |

| N fixtures | Calls | USD at list price | USD at metered rate | With 1.5x safety |
|---|---|---|---|---|
| 71 | 213 | 0.031 | 0.029 | 0.046 |
| 145 | 435 | 0.063 | 0.059 | 0.094 |
| 285 | 855 | 0.124 | 0.115 | 0.185 |

Even N = 285 costs well under USD 0.20 on the cheap panel, so cost is not the constraint; label
quality is. A stronger panel (BELIEVED: 10 to 30 times the price per token) would still be under
USD 5 at N = 285.

## Pilot result (VERIFIED: provenance JSON, `eval/results/s20_english_labelling_pilot.json`)

- Selected 101 reviews (71 target plus 30 spare); labelled the first 20 (60 calls); metered total
  spend across calibration and labelling USD 0.016584 against the USD 1.60 cap (abort line 1.50).
- Early-gate statistics on the 20:

| Field | Krippendorff alpha (item-bootstrap 95 percent interval) | Fleiss kappa | Pairwise Cohen kappa |
|---|---|---|---|
| sentiment | 0.878 (0.68 to 1.00) | 0.876 | 0.81 to 0.91 |
| urgency (ordinal) | **0.618 (0.18 to 0.85)** | n/a | 0.37 to 0.86 |
| buy_again | 1.000 | 0.550 | 1.00 |
| stars_inferred (ordinal) | 0.868 (0.66 to 0.96) | n/a | 0.65 to 0.79 |
| language (reported, not gated) | 0.310 | 0.298 | 0.00 to 0.64 |

  Resolved fraction for sentiment and urgency: 1.00 (14 of 20 urgency unanimous, 6 majority).
- **Gate verdict: FAILED on urgency (0.618 is below 0.67); spending stopped as pre-registered.**
  The sentiment alpha and the resolved fraction pass.
- Diagnosis: all six non-unanimous urgency items are the same boundary case, a positive review with
  one mild complaint ("only one problem", "READ MORE", poor mic). Qwen3-235B calls these `medium`
  where Mistral and DeepSeek call them `low` (Qwen vs Mistral kappa 0.37, Qwen vs DeepSeek 0.49;
  Mistral vs DeepSeek 0.86). The rubric ("a concrete, fixable defect is medium") does not say
  whether a minor flaw inside praise counts. Urgency is also 42 of 60 votes `low`, so alpha is very
  sensitive to a handful of items (interval above is wide). BELIEVED: this is rubric ambiguity plus
  small n, not a defective judge; NOT verified, because proving it needs more labelled items.
- Of the 20, 19 became silver `en` fixtures (1 dropped: consensus language not `en`). Agreement
  levels for open-list fields are lower: pros 9 of 20 split, cons 6 split, product 4 split, topics 3
  split (VERIFIED, `agreement_levels`). Those splits are stored as defaults and flagged.
- Saved outside the repo (scratchpad `s20\labelling\`): raw panel outputs `raw_label.jsonl`,
  `raw_calibration.jsonl`, `calibration.json`, `silver_en_fixtures.json` (19 items), `selection.json`
  (101 items), `spend_ledger.json`.

### Decision for GG

1. Fastest and defensible: continue to N = 71 (about USD 0.03 more) but score stage 2 on urgency
   only where the panel is unanimous, treating majority urgency as unresolved. Sentiment, buy_again
   and stars_inferred pass the floor. Requires GG to amend the gate (not done here, since that would
   be moving a pre-registered threshold after seeing the result).
2. Cleaner: add one tie-break sentence to the urgency definition in the judge prompt ("a mild
   complaint inside an otherwise positive review is `low`"), re-run calibration plus the same 20,
   and require the pre-registered alpha floor to pass. Changing the prompt changes the rubric for
   this corpus relative to the Hinglish held-out set (BELIEVED: acceptable, since stage 2 only
   compares two extraction prompts on one corpus, but it should be disclosed).
3. Either way, drop `language` from stage-2 stratification and keep the three existing clean
   English fixtures with the caveat above.

Command to resume after a decision (reads the key from Secret Manager into the process only):
`python eval/experiments/english_labelling_pilot.py label --n 71` then `... report`.

## Amendment 2026-10-09: urgency rubric v1.1

Added AFTER seeing the pilot's urgency alpha of 0.618, so any alpha measured with it is
**exploratory, not confirmatory**. One sentence is appended to the urgency definition in the judge
prompt (inserted after the `"low"` line); nothing else in the rubric changed:

> If a review names a cost, safety, health or deadline consequence it is at least medium; use high
> only when the reviewer states ongoing harm or an unmet urgent need; when torn between two adjacent
> levels choose the lower one.

Applied in `eval/experiments/english_labelling_free_panel.py` (`v11_user_template()`, which patches
`panel.JUDGE_USER_TEMPLATE` in memory and asserts the anchor occurs exactly once; `panel.py` itself
is untouched). The pre-registered 0.67 floor is not moved.

## Free-panel re-run (OpenRouter `:free` models only, USD 0)

Provenance: `eval/results/s20_english_labelling_free_panel.json` (script
`eval/experiments/english_labelling_free_panel.py`, seed 42, run 2026-10-09, git SHA recorded in the
JSON). Commands: `... free_panel.py run --canary`, then `... run`, then `... report`.

- Panel (disjoint from OpenAI/Meta; each id confirmed present in `GET /api/v1/models`, ending in
  `:free`, with `response_format`): `nvidia/nemotron-3-super-120b-a12b:free`,
  `google/gemma-4-31b-it:free`, `dots-studio/dots-3-note-preview:free`. No provider pinning, because
  `:free` endpoints are not ZDR; the held-out reviews are CC0 and no customer text was sent.
- Items: 14 drawn with `random.Random(42)` from the 51 pool items (first 71 of `selection.json`)
  not in the pilot's 20; the code asserts zero overlap with the pilot's item ids (result:
  `pilot_item_overlap: []`). All 14 received at least one request, but only 11 got any text response before the request cap.
- Requests: 44 of the 44 cap (plus about 5 probe requests earlier the same day, so near the believed
  50/day limit). Outcome of the 44: 17 text responses (15 parsed valid, 2 dots outputs truncated at
  1500 tokens and failed schema validation), 7 HTTP 200 with EMPTY content (dots 5, nemotron 2),
  20 errors (gemma 429 x11, dots `body_error` x3, `URLError` x6: nemotron 3, dots 2, gemma 1). No
  daily-cap error was seen; the run stopped at the self-imposed cap.
- Valid labels per model: nemotron 8, gemma **0**, dots 7. Gemma was rate limited on every attempt
  (upstream 429); after its first item exhausted 3 tries it was skipped (circuit breaker) and only
  re-probed once per item in a spare-quota pass, still 429. So this is effectively a 2-model panel.
- Items with at least 2 valid labels: **6** of 14. Urgency unanimous 6 of 6 (4 low, 2 medium),
  sentiment unanimous 6 of 6.
- Krippendorff alpha on those 6 items (VERIFIED: `python
  eval/experiments/english_labelling_free_panel.py report`, read from the JSON `stats`): urgency
  nominal 1.0, urgency ordinal 1.0, sentiment nominal 1.0. Bootstrap CI NOT computed: n = 6 is below
  the n >= 8 rule.
- Reading: 1.0 on 6 items from 2 raters is a feasibility signal at best. It is NOT distinguishable
  from the pilot's 0.618 (pilot interval 0.18 to 0.85, n = 20, 3 raters): with 6 units and no
  middle-class boundary cases in the sample, perfect agreement is expected often even when the true
  alpha is near 0.6. The pilot's disagreement came from Qwen vs Mistral/DeepSeek, and none of those
  judges was in this panel, so this run does not test whether the tie-break sentence resolves that
  split. It does NOT show that v1.1 helps, and it is not a certification.

### Daily-cap arithmetic (BELIEVED limit 50 requests/day across all `:free` models)

| Target | Labels (3 models) | Requests, zero failures | Days at 50/day | Days at observed yield |
|---|---|---|---|---|
| 143 items | 429 | 429 | 9 | about 25 (yield 15 valid / 44 requests = 34 percent) |
| 283 items | 849 | 849 | 17 | about 50 |

A 2-model panel needs 286 to 566 requests, 6 to 12 days at zero failures. It is not sensible as the
labelling panel: two raters cannot form a majority, so every disagreement is unresolved (the ADR 0032
convention), and the observed free-model failure rate (empty or truncated output, upstream 429) makes
the zero-failure day counts unreachable. The free tier is fine for feasibility probes like this one
but not for the full fixture set. Options for GG (none taken here): the paid cheap panel already
costed above (USD 0.06 to 0.12 for 145 to 285 items at metered rates), or a one-off credit purchase
that raises the free daily limit (BELIEVED per OpenRouter docs, not checked here). This run spent
USD 0 (`total_cost_usd_reported` 0 in the JSON).
