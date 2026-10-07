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

(appended after the run)
