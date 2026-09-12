# ADR 0015: Judge panel restoration, and a quota-safety gap found before spending on P3

> **Read this first: this document's original "200,000 tokens/day, 1,000 requests/day, shared
> by every call any key on this account makes" framing (below) is WRONG for production's
> gpt-oss models and has been for this document's entire history.** The correct model —
> production's real per-model limits, the real capacity ceiling, and why the wrong framing
> understated total capacity while overstating sustainable throughput — is in the
> **"Correction (Session 13 P3a)"** section at the very end of this document. The
> "Follow-up (Session 10 P4)" and "Correction (same session, P5e)" sections in the middle of
> this document are about a DIFFERENT system entirely (the eval consensus judge panel's
> `qwen` models on a separate benchmark key) — do not confuse them with the Session 13
> correction, which is about production's `openai/gpt-oss-20b`/`120b` models on the shared
> production key that also serves real customers and the demo endpoint.

## Context

ADR 0013 removed `openai/gpt-oss-120b` from the consensus judge panel (self-judging
conflict), leaving `qwen/qwen3.6-27b` as the only calibration-passing, disjoint judge — no
inter-rater kappa/alpha is computable with one rater. P3c requires a genuinely
disjoint-from-production panel with real reliability stats for the held-out Hindi/Hinglish
corpus.

## Panel restoration

A live, free `/v1/models` listing call against Groq (no completion cost) found the current
full catalog: `qwen/qwen3.8-27b`, `canopylabs/orpheus-v1-english` (TTS), `meta-llama/
llama-prompt-guard-2-{86m,22m}` (prompt-injection classifiers, wrong task), `whisper-large-
v3(-turbo)` (speech-to-text), `openai/gpt-oss-*` (production family, excluded), `groq/
compound(-mini)` (Groq's own agentic router, unclear internal model lineage — not used
without verifying what it's built on), `allam-2-7b` (already failed calibration).

**`qwen/qwen3.8-27b`** is the only task-appropriate, non-OpenAI-lineage candidate left. It is
the same vendor/family as `qwen/qwen3.6-27b` (Alibaba Qwen) — not fully cross-vendor-disjoint
— but a genuinely different model checkpoint, which is materially better than zero
independent variance. Disclosed plainly: agreement between `qwen3.6` and `qwen3.8` is weaker
evidence of correctness than agreement between two unrelated vendors would be — the same
caution class as, but smaller in degree than, the `gpt-oss-120b` contamination ADR 0013 fixed.

Calibrated fresh against the 16-item control set (16 calls, ~5-8K tokens, a small, already-
spent cost): **0/33 misses, passed cleanly.** `eval/consensus/results/calibration_report.json`
updated: active panel is now `[qwen/qwen3.6-27b, qwen/qwen3.8-27b]`.

## Quota-safety gap found before any further spend

P3d assumed a lever exists to "set the demo endpoint's global cap to 0" for the duration of
any labeling batch, protecting real customer traffic sharing the same Groq org-level budget
(confirmed prior session: Groq rate-limits at the ORG level, not per-key — a dedicated
benchmark key does not isolate quota). **Verified directly: no such lever exists in
production.** GitHub issue #132 ("global daily quota cap + cost recording") is still **OPEN**;
its described implementation (`DEMO_DAILY_REQUEST_BUDGET`, a `demo_daily_usage` table, a
schema migration making `extraction_costs.org_id` nullable) exists only as an unmerged commit
(`805a9ca` on branch `fix/wave0-demo-quota-cap`) — confirmed NOT an ancestor of `origin/main`
(`git merge-base --is-ancestor 805a9ca origin/main` → not an ancestor), and confirmed absent
from the live tree (`grep -r DEMO_DAILY_REQUEST_BUDGET` → zero matches outside that branch).

Real measured Groq limits for the models live today (from issue #132's own
console.groq.com-verified numbers): **200,000 tokens/day, 1,000 requests/day**, shared by
every call any key on this account makes. 60% of that (P3d's authorized ceiling) is 120,000
tokens/day. The full held-out batch (108 candidates x 2 judges = 216 calls) at production's
own measured per-call token rates (en ~1833, hi-en ~1934 tokens/call) would need roughly
390,000-420,000 tokens total — 2x the entire daily budget, let alone the 60% slice — confirming
P3d's own anticipation that this needs multi-day batching, not a one-shot run.

**Decision: pause the labeling batch here, before further spend, rather than run it with zero
isolation from real customer traffic.** #132 is explicitly named in this session's STOP list
(both directly, and indirectly as a schema migration + RLS change) — deploying it is not this
session's call to make unilaterally, and running an unbounded multi-day batch against a shared
budget with no isolation mechanism, real customers depending on the same key, is exactly the
"scarce shared quota where a failed attempt has real cost" class CLAUDE.md's standing autonomy
policy already reserves for explicit escalation.

## Consequences

- P3's held-out corpus is fully prepared (108 corrected candidates, quarantine directory +
  leakage check, a genuine calibrated 2-judge panel) but **not yet labeled** — the actual
  consensus-labeling run against real reviews is deferred pending GG's decision: (a) accept
  the risk and authorize proceeding without a cap, batched across ~4 days at the 60% ceiling;
  (b) authorize deploying #132 first (its own STOP-gated decision); or (c) defer to a later,
  separately-scoped session.
- Already spent: ~16 calibration calls (qwen3.8-27b only), an estimated 5,000-8,000 tokens —
  small, real, disclosed. No labeling-batch tokens spent beyond this.
- Nothing here blocks landing the zero-quota parts of P3 (corpus yield fix, quarantine
  mechanism, panel restoration) — those are independent, complete, and tested.

## Alternatives considered

- **Proceed anyway, reasoning that 16 calibration calls already happened without visible
  harm.** Rejected: a 16-call calibration burst and a 200+-call multi-day labeling commitment
  are different orders of magnitude and risk profiles; the small already-spent cost is
  disclosed, not used to justify a much larger one by extension.
- **Build and merge #132 to unblock the safety mechanism.** Rejected for this session: #132
  is itself explicitly STOP-listed (a schema migration touching `extraction_costs`, the same
  money-adjacent table ADR 0013/Session 7's P4 already flagged for a separate precision fix
  first) — building it to unblock a different piece of work would be scope creep into a
  decision that needs its own sign-off.

## Follow-up (Session 10 P4) — the 200,000 tokens/day figure was wrong; the real limit is RPD

Session 9 both (a) misread the binding constraint as a daily token budget, and (b) then, on a
single live header observation, concluded the opposite — that the limit was per-minute, not
per-day — and self-imposed a "one batch per day" ceiling anyway out of caution. Neither reading
was verified against sustained, direct measurement. This session did that.

**Method**: two live bursts against the dedicated benchmark key's `qwen/qwen3.6-27b` model,
tracking `x-ratelimit-*` response headers on every call: (1) 12 calls with zero delay, (2) 60
calls with zero delay over 515.6 seconds, zero errors in either.

**Finding — two independent, differently-shaped limits, not one:**

- **Tokens (`x-ratelimit-limit-tokens: 8000`)**: a genuine short rolling window. `remaining-
  tokens` oscillated between 6857 and 7716 throughout the 515s sustained burst — it recovers
  continuously as fast as this workload consumes it, never trending toward zero.
  `reset-tokens` stayed bounded between ~2s and ~60s the entire time, consistent with a ~60-
  second window. **Not the binding constraint for any realistic labeling pace.**
- **Requests (`x-ratelimit-limit-requests: 1000`)**: a 24-hour sliding window, confirmed by an
  exact arithmetic signature, not inferred. `remaining-requests` decreased by exactly 1 on
  every single call with ZERO recovery across the entire 515-second sustained burst (988 → 929,
  monotonic, no upticks) — incompatible with a short window, which would have shown at least
  one slot free up. `reset-requests` grew by **exactly 86.4 seconds per call** (17m16.8s →
  1h42m14.4s, call after call) — and 86400 seconds (24 hours) / 1000 requests = 86.4 seconds
  exactly. This is the daily window's marginal per-request contribution to the reported
  countdown, not a coincidence. **ADR 0015's original "1,000 requests/day" figure was right.
  Its "200,000 tokens/day" figure was not measured this way and does not match what the
  token-dimension headers actually show — retracted, not merely superseded.**

**Consequence for batching**: each labeling item costs 2 Groq requests (2 active judges).
1,000 requests/24h ÷ 2 = **500 items/day theoretical ceiling**, shared with all real production
and demo traffic on the same org-level budget (confirmed prior finding, unchanged). Tokens
never bind before requests do at this per-item cost (~3,800 tokens/item across 2 calls, trivial
against an 8,000/minute continuously-renewing budget). Session 9's "one batch per day, ~25-item
batches" ceiling was **far more conservative than the measured limit requires** — not wrong to
be cautious with an unverified number, but the number itself doesn't survive direct
measurement.

**Revised ceiling (replaces the 100,000-token/50%-of-day figure Session 9 used)**: 50% of the
measured 1,000 requests/24h = 500 requests/day = **250 items/day**, reserving the other half of
the daily budget for real production/demo traffic sharing the same org quota (P4d). The
remaining 83 hi-en held-out candidates (166 requests) fit inside this revised ceiling with
substantial margin, in a single day — not the "multi-day batching" Session 9 anticipated.

**Measurement cost of this finding**: 73 Groq requests spent on the two bursts themselves (12 +
60 + 1 single-call probe), all against the dedicated benchmark key, none against production.
This spend counts against today's revised 500-request/day ceiling like any other labeling
spend, and is disclosed here rather than treated as free because it was "just measurement."

## Correction (same session, P5e) — a third, narrower limit the burst test missed: OTPM

The measurement above used `max_completion_tokens=400` in the burst-test probe to keep the
measurement itself cheap. Real judge calls (`eval/consensus/panel.py::call_judge`) request
2000. That difference hid a real, separate constraint: attempting the actual remaining-83-item
batch at 2000 hit a wall on the very first item — `qwen/qwen3.6-27b` 429'd on nearly every
call with `"Request too large ... on output tokens per minute (OTPM): Limit 1000, Requested
1387"`. This is Groq's per-model Output-Tokens-Per-Minute admission control: it rejects a call
*before running it* based on the requested `max_completion_tokens`, independent of the general
token-bucket headroom measured above (which stayed fully healthy the entire time — this is a
genuinely separate limit, not a restatement of the first one).

**Consequence, disclosed honestly**: the "250 items/day" ceiling above is still correct for the
*request-count* dimension, but was never actually achievable at `max_completion_tokens=2000` --
OTPM would 429 most calls to a given model long before the request-count budget mattered.
Fixed by lowering `max_completion_tokens` to 900 (live-verified to clear the OTPM check;
`reasoning_effort="none"` already disables the thinking-mode overhead 2000 was sized for —
recalibrated afterward, 0/33 misses on both qwen models, no truncation). One further wrinkle,
caught before being wrongly reported: a sustained batch at 900 initially looked like it was
*still* hitting occasional NO_RESPONSE gaps — turned out to be a misread of the append-only
batch log's tail (old failed-attempt entries from before the fix, still sitting in the file,
mixed with new clean entries) — verified properly against the log's actual entries for the new
run specifically: zero gaps across 83 items post-fix. Recorded here so the near-miss on
reporting a second false constraint doesn't get repeated.

The remaining 83 hi-en candidates completed the same day, in two batches (13 + 70, split only
by the mid-run stop-and-diagnose, not by any real ceiling) — the corpus is now complete at
106/106 hi-en. See ADR 0019 for the full-corpus results.

## Correction (Session 13 P3a) — production's real per-model limits, and the real capacity ceiling

This document's original framing above -- "200,000 tokens/day, 1,000 requests/day, shared by
every call any key on this account makes" -- was applied to production's `openai/gpt-oss-20b`/
`openai/gpt-oss-120b` models (the ones `/v2/extract`, `/v2/extract/batch`, `/v2/ingest/csv`, and
`/demo/extract` all actually call). It was wrong in a specific, correctable way: **the limits are
per-model, not one shared org-wide pool.** Each of the two models gets its own independent
budget. This understated real total capacity (two independent 200K-token/day pools is double one
shared 200K pool) while simultaneously overstating real sustainable throughput (a single-pool
mental model invites assuming you can burst against the full daily figure at any rate; each
model's own 30 RPM / 8,000 TPM per-minute ceiling caps how fast that pool can actually be drawn
down, regardless of how much of the daily budget remains unused).

### The real, per-model limits -- verified two ways

| Model | RPM | RPD | TPM | TPD |
|---|---|---|---|---|
| `openai/gpt-oss-20b` | 30 | 1,000 | 8,000 | 200,000 |
| `openai/gpt-oss-120b` | 30 | 1,000 | 8,000 | 200,000 |

Verified via (1) a real, live `POST /openai/v1/chat/completions` call against each model this
session, response headers captured directly -- `x-ratelimit-limit-requests: 1000`,
`x-ratelimit-limit-tokens: 8000` for both models -- plus reset-time arithmetic that reconciles
exactly: `reset-requests` of 86.4s after 1 used request implies a window of `86.4 x 1000 =
86,400s` = exactly 24 hours (RPD confirmed, not assumed); `reset-tokens` of 585ms after 78 used
tokens against an 8,000 limit implies a ~60-second window (TPM confirmed). And (2) Groq's own
published rate-limits documentation (`console.groq.com/docs/rate-limits`), fetched directly this
session -- same table, same numbers, TPD included (TPD is not independently re-derivable from a
single live response header the way RPD/TPM are, so it rests on source (2) alone, Groq's own
primary docs, not a blended third-party search summary).

### The real ceiling: TPD binds, and it binds far tighter than RPD does

At this project's real measured tokens/extraction -- small tier (`gpt-oss-20b`) 2,524.8 tokens
mean, large tier (`gpt-oss-120b`) 2,393.9 tokens mean, observed mix 40.6%/59.4%
(`eval/results/token_cost_measurement_n106.json`, PR #169, n=106) -- the binding constraint is
**tokens/day, not requests/day**, and it binds an order of magnitude tighter than a naive "1,000
requests/day per model" reading suggests. Computed reproducibly, zero quota, by
`eval/capacity_model.py`:

| | Per-minute ceiling | Per-day ceiling |
|---|---|---|
| **Real (TPM/TPD-bound, both tiers combined at observed mix)** | **5.62 extractions/min** | **140.6 extractions/day** |
| Binding tier | large (`gpt-oss-120b`) | large (`gpt-oss-120b`) |
| If RPD/RPM were the binding constraint instead (they are not) | 50.5/min | 1,682.5/day |

**This ceiling is shared across every consumer of the single production `GROQ_API_KEY`: real
customer traffic, the public demo endpoint, and any live (non-cassette-replay) eval/CI call
against these two models. It is the entire business's combined daily capacity on the free tier
today, not a per-customer number.** ~140.6/day x 30 ~= **4,217 extractions/month, total, across
every customer combined.**

**This is smaller than a single Starter-tier customer's monthly allotment** (P6b's published
Starter tier: 5,000 reviews/month) -- meaning on the current free tier, this business cannot
actually deliver even ONE fully-utilized Starter customer's quota without the free tier being the
bottleneck, let alone multiple customers plus demo traffic plus the Free tier's own 1,000/month
allotment. This is not a future concern -- it is the current, measured reality, and it makes P3c's
Developer-plan upgrade decision materially more urgent than "the day a customer signs" framing
alone conveys: it needs to be ready to flip within that same day, not researched from scratch
then.

### What this means for a CSV/batch job (P3b)

`app/core/csv_ingest.py::MAX_ROWS` currently caps a single CSV upload at 500 rows (not 5,000 -- a
5,000-row single upload isn't possible today regardless of quota). At the measured 5.62
extractions/minute ceiling, **assuming the job has exclusive access to the full shared quota
(best case -- real elapsed time is longer whenever other traffic competes)**:

- **500 rows (today's actual max)**: ~88.9 minutes (~1.5 hours).
- **5,000 rows (hypothetical, if the row cap were ever raised)**: ~889 minutes (~14.8 hours).

`GET /v2/ingest/{job_id}` now returns `estimated_seconds_remaining` (this PR), computed from
`app/core/capacity.py`'s hardcoded, CI-regression-tested constant (`eval/` is not shipped in the
production Docker image, so this can't be read live at runtime -- see that module's docstring) --
replacing the previous unbounded "processing" status with an honest, explicitly-labeled
best-case estimate.

### Consequences

- The capacity numbers behind D2/P6b's pricing tiers (Free 1,000/mo, Starter 5,000/mo, Growth
  25,000/mo, Scale 100,000/mo, Agency 200,000+/mo) must be understood against this ceiling: they
  describe what a customer *may use up to*, not what the free-tier infrastructure can currently
  *deliver* if fully utilized. This does not mean the pricing page is wrong to publish -- "early
  access pricing" already frames these as aspirational/promotional -- but it does mean the
  Developer-plan upgrade (P3c) is a same-day-as-first-real-customer necessity, not a someday
  optimization.
- `eval/capacity_model.py` is the reproducible source of truth for this ceiling going forward;
  re-run it whenever the tier-routing mix or the measured tokens/extraction figures change
  materially, and update `app/core/capacity.py`'s hardcoded constant to match (a regression test
  fails CI if they diverge by more than 1%).

### Alternatives considered

- **Silently correct the original "200,000 tokens/day, shared" text in place rather than
  appending a correction section.** Rejected -- this document's own established convention
  (visible in its own "Follow-up"/"Correction" section headers from Session 10) is to append
  corrections, not rewrite history; a reader tracing why a past decision was made needs to see
  what was believed at the time, not just the final corrected number.
- **Treat this as confirmation of the brief's "~3.5 extractions/minute" illustrative figure
  rather than recomputing independently.** Rejected: the brief's figure appears to assume a
  single shared TPM pool across both models at a blended ~2,246 tokens/extraction (8,000 / 2,246
  ~= 3.56/min) -- a reasonable back-of-envelope simplification, but this project's own two
  independent per-model pools support somewhat higher combined throughput (5.62/min) once
  computed properly against the real tier-mix and real per-tier token averages. Reported the
  more precise, reproducible number rather than matching the brief's illustration by
  construction.
