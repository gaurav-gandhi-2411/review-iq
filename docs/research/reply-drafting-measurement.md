# Reply drafting: measured tokens, cost and quality (S19 N4a/N4c)

Status: PRE-REGISTRATION section below was committed BEFORE any judge call ran. Results are
appended in a later commit of the same branch (git history is the proof of ordering).

## 0. Question and scope

Reply drafting (`web/` ReviewDetail "Draft reply" -> `POST /bff/reply` -> `draft_reply`) is live.
Three things were believed, not measured:

1. cost per draft (~$0.000485, from 1,240 in / 500 out ESTIMATED in `docs/research/m3-feature-scoping.md`),
2. whether the small model is acceptable for Hinglish (the 503 decision in the companion code PR),
3. whether drafts invent details (names, order numbers, refunds, timelines). A reply that
   invents a detail is worse than none.

## 1. Pre-registered design

### 1.1 Sample

- Source: `eval/fixtures/_held_out_hindi_hinglish/` (106 fixtures, third-party licensed Flipkart
  reviews). The 36 fixtures in `eval/heldout_exposure_ack.json` (exposed to dev) are excluded, leaving 70.
- 20 drawn with `random.Random(42).shuffle` over the sorted non-exposed ids, first 20 taken.
- Coverage limit, stated up front: the corpus is Hindi/Hinglish. English reviews (the language
  where degradation is already allowed) are NOT sampled, so English tokens/cost may differ.
  Whatever `detect_language` assigns to each review is what the engine uses (some hi-en reviews
  detect as `en`; that is production behavior and is reported, not corrected).
- Extraction fed to the drafter = the fixture's consensus `ground_truth` cons/topics/pros (mirrors
  the UI path, which sends the stored extraction, so no second extraction call). Tone is derived
  from `ground_truth.sentiment` (negative -> apologetic, positive -> appreciative, else professional).
  No brand name, no signature (the UI sends neither).

### 1.2 Arms

- LARGE: production path `draft_reply` with `openai/gpt-oss-120b` (Groq, production key,
  `trains_on_input=False`, enforced by `assert_privacy_safe`).
- SMALL: same code path with `groq_model_large` overridden to `openai/gpt-oss-20b`, i.e. what the
  Hinglish degrade path would serve. Same 20 reviews, same prompts.
- Privacy: held-out text goes only to Groq (production no-train config), or OpenRouter with
  `provider.zdr=true` (fail-closed). No Gemini-direct (free tier may train), no other endpoint.
- Quota guard (read-only): production's same-UTC-day Groq usage read from `extraction_costs`
  before the run; run only if >80K large-pool headroom remains and >=60K stay spare.

### 1.3 Token/cost metrics (N4a)

tokens_in, tokens_out per draft from provider `usage` (tokens_out includes hidden reasoning).
Mean, p50, p95 (nearest-rank, n=20). Cost per draft = `price_extraction`-style math with the
constants in `app/core/pricing.py` (gpt-oss-120b $0.15/$0.60 per M; gpt-oss-20b $0.075/$0.30 per M).
Compared against the earlier estimate (1,240 in / 500 out / $0.000485).

### 1.4 Quality judging (N4c)

Judges (disjoint from production models gpt-oss-120b/20b; same roster as the S18 consensus panel
minus the unusable member):

| Judge | Route | Why |
| --- | --- | --- |
| qwen/qwen3.6-27b | Groq (benchmark key, separate quota) | panel member, calibration-passing |
| qwen/qwen3.8-27b | Groq (benchmark key) | panel member (same vendor as above) |
| google/gemini-3.5-flash-lite | OpenRouter, `zdr: true` | panel's cross-vendor member, but the panel's direct Gemini route is NOT used for licensed text; ZDR route instead |

Each judge sees: the review text, the allowed facts (extraction cons/topics/product), the requested
tone, and ONE draft (arm label hidden). Judges never see each other's output. temperature 0.
Judges were calibrated for extraction labeling, not for reply judging; that is a stated limitation.

Rubric (per draft, per judge, JSON):

1. `invented_details`: list of every specific in the draft that is NOT in the review text or the allowed
   facts. Counts as invented: any person/company/brand name; order, ticket or tracking numbers; a promise
   or offer of refund, replacement, discount, pickup, or compensation; any timeline or deadline
   ("within 24 hours", "by Monday"); a specific phone number, email, URL or contact channel; a policy
   claim; a product fact or cause not stated in the review. NOT counted: generic apology/thanks, a generic
   "please contact our support team" with no channel or timeline, restating what the review says.
   `invented_flag` = list non-empty.
2. `coherent` (bool): fluent, in the review's language/script mix, no nonsensical composition
   (e.g. thanking the customer for a defect).
3. `grounded` (1-5): addresses the specific complaint/praise in the review.
4. `tone_fit` (1-5): matches the requested tone and is appropriate for a public brand reply.

Aggregation (fixed now):

- HARD FAILURE for a draft = `invented_flag` by at least 2 of 3 judges. Also reported: flagged by
  ANY judge (conservative bound).
- NOT COHERENT = `coherent=false` by at least 2 of 3 judges.
- USABLE = not hard failure AND coherent AND median grounded >= 3 AND median tone_fit >= 3.
- Counts with Wilson 95% CIs (`eval/wilson.py`), n=20 per arm. Judge agreement on invented_flag
  reported as pairwise percent agreement (n=20 is too small for a stable kappa; stated, not hidden).
- Every hard failure is listed by fixture id with the judges' invented item. Verbatim review text from
  the licensed corpus is NOT reproduced in this doc or any committed artifact (ids and categories only).
- Committed artifact contains ids, language, model, tokens, caveats, judge verdicts. No review or draft text.

### 1.5 Decision rule for the 503 (N4b), fixed before judging

Degrade Hinglish to the small model ONLY IF, on the SMALL arm restricted to drafts the engine
treats as vernacular (detected `hi`/`hi-en`): coherent >= 90% AND hard-failure count <= the LARGE arm's
on the same reviews AND usable >= 80%. Otherwise keep refusing, but fix the refusal (accurate Retry-After
from the provider, honest client message). n=20 cannot prove equivalence; a pass here is
"no evidence of harm at this n", and the code PR will say so. Prior (existing code comment, fixtures
013/014): the small model produced incoherent Hinglish; I expect the rule to say "do not degrade".

### 1.6 Judge cost (estimate first, cap $0.50)

40 drafts x 3 judges = 120 calls. Per call ~2,000 in / ~600 out (rubric + review + draft + JSON).
- Gemini 3.5 flash lite via OpenRouter ($0.30/M in, $2.50/M out, from OpenRouter /models, 2026-10-08):
  40 x (2,000 x 0.3e-6 + 600 x 2.5e-6) = 40 x $0.0021 = about $0.08 (allow 3x for reasoning tokens: $0.25).
- Qwen on Groq: 80 calls x 2,600 tokens = 208K tokens; priced pessimistically at $0.30/$0.60 per M
  (Qwen prices are not in `app/core/pricing.py`; this is an assumption): about $0.09. The benchmark key
  may be free tier, in which case actual spend is $0.
- Estimated total about $0.20 (ceiling $0.35) against the $0.50 cap. The judge script tracks usage
  and aborts above $0.45 estimated.

## 2. Results

Artifact: `eval/results/reply_drafting_n20.json` (ids, languages, tokens, guardrail caveats, judge
verdict counts; no review or draft text). Code under test: `23c85f4` (main `52cc781` plus the N4b fix,
branch `fix/s19-reply-hinglish-degrade`; the drafting path itself is unchanged by that fix). The harness
scripts (draft runner, judge runner, analysis) lived in the session scratchpad and are NOT committed:
they load secrets from Secret Manager and wrap the committed `draft_reply` / `SecondaryProvider`; the
sampling rule (1.1), the rubric (1.4) and the pricing constants are all in the repo or this doc.

### 2.0 Deviations from the pre-registration (read first)

1. **The Groq production-key arm hit the 120b tokens-per-day cap and was abandoned after 9 drafts.**
   The headroom gate in 1.2 read `extraction_costs`, which showed 0 tokens for `gpt-oss-120b` today
   (2,062 on `gpt-oss-20b`). Groq then answered 429 with `TPD: Limit 200000, Used 199399, Requested 1430`
   at about 20:25 UTC on 2026-10-07. The gate was blind to whatever else draws on the key (reply drafting
   records usage on `usage_records`, not `extraction_costs`; ad hoc sessions record nowhere). So the
   gate under-read, and the run went ahead on a headroom that did not exist. My own draw on that pool was
   about 12.7K tokens (9 successful 120b drafts), so roughly 186K was already used when the run started,
   but I spent part of the last headroom a production request might have used. On the first sign of the cap
   I stopped every call made with the production key (`Used 199399` persisted across 45 s retries). The
   small pool absorbed about 11K tokens from 8 engine-degraded drafts. The 9 clean Groq drafts are reported
   separately in 2.1; no further production-key drafting calls were made.
2. Replacement route (as the brief allowed): the same engine prompts, run through the committed
   `SecondaryProvider` (OpenRouter, `provider.zdr=true`, fail-closed) for `openai/gpt-oss-120b` (LARGE
   arm, n=20) and `openai/gpt-oss-20b` (SMALL arm, n=20). Same weights as Groq, different serving
   stack, and visibly different reasoning verbosity (2.1), so the quality results carry that caveat.
3. `qwen/qwen3.6-27b` no longer exists on Groq (404; `/models` lists `qwen/qwen3.8-27b` only), so the
   judge panel is `qwen/qwen3.8-27b` (Groq, benchmark key), `google/gemini-3.5-flash-lite` (OpenRouter ZDR)
   and, as the third, `deepseek/deepseek-v4-flash` (OpenRouter ZDR, a vendor outside the production and
   panel families). All judges are disjoint from gpt-oss. Two DeepSeek calls returned unparseable JSON and
   were retried; 0 null verdicts in the final 120.
4. The 20-review sample is mostly detected as English: **15 of 20 held-out Hinglish reviews are classified
   `en` by `detect_language`; only 5 are `hi-en`.** The vernacular decision subset (1.5) therefore has n=5.

### 2.1 N4a: measured tokens and cost per draft

Cost = tokens x the `app/core/pricing.py` constants (`gpt-oss-120b` $0.15 in / $0.60 out per M,
`gpt-oss-20b` $0.075 / $0.30; `verified=True`, as_of 2026-09-10) via `compute_cost_usd`. Nearest-rank percentiles.

| Route (model) | n | tokens_in mean / p50 / p95 | tokens_out mean / p50 / p95 | USD per draft mean / p50 / p95 |
| --- | --- | --- | --- | --- |
| Groq production key (120b) | 9 | 1,023 / 918 / 1,381 | 384 / 391 / 575 | 0.000384 / 0.000372 / 0.000552 |
| OpenRouter ZDR (120b) | 20 | 995 / 881 / 1,350 | 251 / 270 / 406 | 0.000300 / 0.000298 / 0.000421 |
| OpenRouter ZDR (20b) | 20 | 1,000 / 869 / 1,351 | 311 / 319 / 590 | 0.000168 / 0.000165 / 0.000253 |
| Groq production key (20b, engine degrade) | 8 | 902 / 890 / 977 | 489 / 358 / 1,545 | 0.000214 / 0.000174 / 0.000529 |

- Production-path figure is the first row: **$0.000384 per draft (n=9)** against the earlier estimate of
  $0.000485 (1,236 in / 500 out): the measured mean is 21% lower, and the estimate sits between the measured
  mean and p95 ($0.000552). The 9 drafts are 7 detected-`en` (921 in / 340 out mean) and 2 `hi-en`
  (1,380 in / 538 out). n=9 is indicative, not a tight interval.
- Input tokens: the estimate's 3.45 characters/token calibration over-counted (hi-en prompts measured
  1,354 on 5 OpenRouter drafts vs 1,585 estimated). Output tokens depend on the serving route: the same
  120b emitted 384 mean on Groq (n=9) and 251 on OpenRouter (n=20, different reasoning default), so only
  the Groq row is a production cost. Output includes hidden reasoning tokens in both.
- At 1,000 drafts per customer per month: about $0.38 (measured, Groq 120b) vs $0.49 (estimate).
  English-detected traffic is cheaper (about $0.00035 on the Groq rows) than Hinglish (about $0.00053, n=2).
- Degrading is cheaper per draft (the 20b rows), but quality below is the constraint, not cost.

### 2.2 N4c: quality (20 held-out reviews, 3 judges, rubric from 1.4)

| Metric | LARGE 120b (n=20) | SMALL 20b (n=20) |
| --- | --- | --- |
| Hard failure (invented detail, >=2 of 3 judges) | **5/20 = 25% (CI95 11-47%)** | **8/20 = 40% (22-61%)** |
| Flagged by any judge (conservative) | 6/20 (15-52%) | 12/20 (39-78%) |
| Not coherent (>=2 of 3) | 0/20 (0-16%) | 1/20 (1-24%) |
| Usable (rubric 1.4) | 15/20 = 75% (53-89%) | 11/20 = 55% (34-74%) |

Wilson intervals (`eval/wilson.py`). The arms overlap heavily at n=20: small is directionally worse, not
statistically separated. Judges agree on the invented flag in 90% of pairs (large) and 60-80% (small): the
small arm's drafts are also harder to judge. Judges were never calibrated for reply judging, so treat the
counts as indicative.

**What the failures are** (categories read from the judges' own flagged strings, not independently
adjudicated; ids only, no corpus text):

- Placeholder contact address, unambiguous: an invented `support@example.com`-style address in LARGE
  hien-0039 and hien-0079 and SMALL hien-0025 and hien-0079 (flagged by 3 of 3 judges each). Strict
  email-only count: **2/20 per arm (10%, CI 3-30%)**. This is the "invented detail" the brief calls worse
  than no reply: it sends a customer to an address the brand does not own.
- Unsupported commitments or fault admission: LARGE hien-0010, hien-0049 (promises a technical-team
  follow-up, "always improving"), hien-0025 (contact by "email or phone", "will look into it promptly");
  SMALL hien-0011 ("this was our fault", "we will fix it"), hien-0030 (future design commitment), hien-0060.
- Unsupported advice or product facts: SMALL hien-0039 and hien-0049 (volume/EQ troubleshooting not in the
  review), hien-0010 (names a feature not in the review). Some commitment and request-for-order-details
  flags are arguably over-strict under the rubric (a request for information asserts no fact); the email
  category is not.
- Incoherent (small): hien-0072 (3 of 3 judges), hien-0058 and hien-0065 (1 of 3). All three are Hinglish
  reviews detected as `en`, i.e. served by the small model through the English degrade path today.

**Guardrail coverage (existing regex and keyword checks):** 13 draft-level hard failures across both arms;
only 1 carried any caveat (an unrelated "ungrounded" keyword caveat on LARGE hien-0039), so the guardrails
caught **0 of 13** for the right reason. `app/core/reply/guardrails.py` has no email, phone or URL pattern.

### 2.3 N4b decision, applying the pre-registered rule (1.5)

Vernacular-detected subset (n=5, CIs span roughly 12-88%, so this cannot show equivalence either way):

| Check | Required | SMALL | LARGE | Result |
| --- | --- | --- | --- | --- |
| Coherent | >= 90% | 5/5 | 5/5 | pass |
| Hard failures vs large | <= large | 3 | 2 | **fail** |
| Usable | >= 80% | 2/5 | 3/5 | **fail** |

Decision: **do not degrade Hindi/Hinglish to the small model; keep refusing, with a correct 503.** Code PR:
branch `fix/s19-reply-hinglish-degrade`. Over all 20 reviews the direction is the same (small 8/20 hard
failures vs 5/20). This is a no-evidence-of-safety call at n=5, not proof of harm; revisit if the sample grows.

### 2.4 Findings that change priorities

1. **The English degrade path already serves Hinglish.** 15/20 held-out Hinglish reviews detect as `en`, so on
   a 120b quota cap they get the small model (with only a "reduced-capacity" caveat), and the 3 incoherent
   small drafts are exactly these. The language detector's known undercount is now a reply-quality problem,
   not only an extraction one; the refusal is only as good as the detector.
2. The 120b free-tier pool was near-exhausted by other draw on the production key (199,399 of 200,000 when
   the cap hit). Reply drafting plus extraction plus ad hoc runs on one key, with no ledger outside
   `extraction_costs`, is the same quota-isolation gap as the earlier Groq incident.
3. Add email/phone/URL to the fabricated-commitment guardrail, and consider blocking (not caveating) an
   invented contact address: the product rule is that an invented detail is worse than no reply.
4. Sibling gaps found, not fixed: non-quota Groq errors (5xx, connection) leave the engine as a 500; the web
   client discards `Retry-After` (no CORS `expose_headers`) and always says "try again in a minute"; the v2
   batch endpoint drops failed items without indices.

### 2.5 Spend

- Judges: 120 calls, 60.7K tokens in / 26.8K out; ESTIMATED $0.082 (Qwen at an assumed $0.30/$0.60 per M, the
  other two at Gemini 3.5 flash lite OpenRouter rates, so an upper bound; the Groq benchmark key may be free
  tier). Cap $0.50; the estimate-first figure in 1.6 was $0.20.
- OpenRouter drafting, about 80 draft calls (including a stalled small-arm run that was killed and re-run):
  about 80K tokens in / 22K out, ESTIMATED under $0.03.
- Groq production key: 9 + 8 drafts plus 5 probe calls, about 24K tokens total (see 2.0).
- Total ESTIMATED under $0.12. OpenRouter billing was not queried, so this is an estimate, not a bill.
