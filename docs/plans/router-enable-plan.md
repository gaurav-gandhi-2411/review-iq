# Router enable plan (encoder-first R1+): PARKED for GG

Status: **plan only.** The router flag stays OFF. Enabling it changes live production behaviour, so it is GG's decision. Nothing in this
file has been executed. Written in S34 (2026-11-01) after Amendment 18 passed.

## 1. Why this exists: the evidence

| Criterion (sealed set) | Result | Source |
|---|---|---|
| A17 F1: R1+ recall, block A (fresh17, n = 260 gold-yes) | 0.962, Wilson lower bound 0.931 >= 0.90: **met** | `reports/intent/fresh17/result.json` |
| A17 F3: routed share, block A | 0.297 (Wilson [0.276, 0.318]) <= 0.30: **met, thin** | same |
| A17 F4: R1+ minus LLM recall, block A | +0.058, CI [0.019, 0.100] > -0.05: **met** | same |
| A17 F2: R1+ recall on S4 (4-5 star), absolute 0.85 bar | 0.895, lower bound 0.831: **FAILED** (stands, not changed) | same |
| **A18 P1: R1+ minus gpt-oss-20b recall on S4 gold-yes (fresh18, n = 301)** | **+0.083, CI [0.040, 0.126] > -0.05: PASS**; McNemar 35 vs 10, p = 0.00025 | `reports/intent/fresh18/result.json`, verified 31/31 (`docs/reports/a18-fresh18-results.md`, #648) |
| A18 report-only: R1+ recall on S4 | 284/301 = 0.944, Wilson [0.911, 0.964] (above A17's 0.85 bar on fresh data) | same |
| A18 secondary: R1+ vs gpt-oss-120b on a 200-item subsample | **NOT RUN YET**: blocked by the shared Groq pool (triage-iq recording). Deadline 2026-11-15 | section 7 |

**What P1 means, stated plainly (from the amendment):** R1+ is no worse than the LLM path the product can run on its own hardware
(gpt-oss-20b). It does **not** show the router is no worse than the strongest LLM (120b). In S32's exploratory data R1+ and 120b tied
on S4 (138/147 each, CI [-0.048, +0.048]). Labels are SILVER (four-judge LLM panel; needs_action alpha 0.680 on fresh18, 0.740 on fresh17).

## 2. What the router does when enabled

At ingest, every review is first scored by the private classifier service. If R1+ says "route", the review gets the full LLM extraction
as today. Otherwise it is stored **classified-only** (no LLM call) and extracted once if its owner opens it. Expected effect (BELIEVED, from
the sealed sets): about 70 percent of reviews skip the ingest LLM call (routed share 0.297 on block A, 0.149 on 4-5 star reviews).
Complaints the router misses wait in the skipped list until opened, as the price of the skip: on fresh18 S4, 17 of 301 gold-yes (5.6 percent).

## 3. Thresholds (fixed by the amendments; no retuning)

- Model: served run 2, static int8 ONNX, pinned ORT numerics; thresholds `app/core/classify/thresholds_run2_onnx_static_int8_x64.json`
  (sha256 `4cf745db3af848464cbd7961c536919dee3be7a78691a5c4aca8a356ab724ec2`, the file both A17 and A18 used).
- R1 = needs_action is yes or abstained, OR urgency candidate is high, OR urgency abstained (`app/core/classify/encoder_first.py::route_r1`).
- **R1+ = R1 OR p(needs_action = yes) >= tau, tau = 0.07** (`reports/intent/a17/tau.json`).

## 4. Steps, in order (each its own PR; every gate-4 PR is merged by GG)

| # | Step | PR | Gate | Notes |
|---|---|---|---|---|
| 0 | Migration rehearsal CI on a restored backup | #396 (draft) | gate 4 (CI + migration path) | Rehearses #576 without a prod credential |
| 1 | `classified_reviews` table migration FILE | #576 | gate 4 (schema) | Expand-only; RLS by org; text kept only while classified-only. GG applies it to prod |
| 2 | **R1+ in the served path** (NEW, not yet written) | to write | normal | `route_r1_plus(tasks, tau)` = R1 OR `tasks["needs_action"]["probabilities"]["yes"] >= tau`, tau read from the committed file; `plan_routes`/`plan_ingest` default to it; parity test: served R1+ == `engine/intent/fresh17_eval.routes` R1+ on the fresh18 run-2 logits, item for item. **The served code today is R1 only; enabling without this step would deploy a router that was not the one evaluated.** |
| 3 | Ingest keeps skipped reviews instead of dropping them | #578 | gate 4 (writes reviews) | Flag OFF = byte-for-byte today's behaviour |
| 4 | Open a classified-only review to extract it once | #579 (stacked on #578) | gate 4 | Both routes 404 while the flag is off |
| 5 | Web: list of skipped reviews, open to analyse | #634 (draft, stacked on #579) | UI review | Behind `VITE_ENABLE_SKIPPED_REVIEWS` (default off) |
| 6 | Turn it on | env only | GG | `CLASSIFY_SERVICE_URL` = the private `review-iq-classify` URL; `ENABLE_ENCODER_FIRST=true`; `VITE_ENABLE_SKIPPED_REVIEWS=true` on the web build |

Merge order: #396, then #576 (and apply the migration), then step 2, then #578, then retarget #579 to main and merge, then #634.
Rebase each after the previous one merges.

The classifier service is already deployed and private: `review-iq-classify` (revision `review-iq-classify-00002-zh5`, asia-south1),
invoker = `review-iq-runner@` only (checked 2026-10-31 and 2026-11-01). Nothing calls it today.

## 5. Rollout and monitoring

1. **Shadow week (recommended first):** enable on one internal org only, or log the decision without acting on it, and compare the
   skipped share with the 0.297 / 0.149 expectations.
2. Then all orgs. Watch:
   - `review_iq_encoder_first_total{outcome}` (routed / skipped / fail_open). Alert if fail_open > 1 percent of decisions over 1 h
     (the classifier is unhealthy and every review goes to the LLM: safe, but the saving is gone), or if the skipped share leaves
     [0.55, 0.85] for a day (input drift).
   - Log `encoder_first.fail_open` (error type, count).
   - Classifier service p95 latency against the ingest timeout (`INGEST_TIMEOUT_S`); timeouts fail open.
   - **Recall audit:** weekly, an LLM pass on a random 200 of the skipped reviews. Any needs_action = yes is a miss; alert if the
     estimated miss rate on skipped reviews is above 2 percent (fresh18 implies 0.25 percent: 17 of the 6,894 skipped S4 items were gold-yes).
3. Re-evaluate after a model or threshold change; any change needs a new amendment and a fresh sealed set.

## 6. Rollback

- **Instant:** set `ENABLE_ENCODER_FIRST=false` (or unset `CLASSIFY_SERVICE_URL`) on `review-iq`: a new revision, and ingest goes back to
  "extract every review". No data is lost: reviews skipped while it was on stay in `classified_reviews` and can still be opened, or
  bulk-extracted with a one-off job.
- Web: rebuild with `VITE_ENABLE_SKIPPED_REVIEWS` unset.
- The migration is expand-only; the table can stay.

## 7. Open items before enabling

1. **120b secondary (A18):** 200-item subsample, registered (`reports/intent/fresh18/subsample_120b.json`), runner merged (#646).
   It is blocked while triage-iq's recorder holds the shared 120b pool. Run:
   `PYTHONPATH=. python <with_failover_key.py> <venv python> -m engine.intent.fresh18_groq120 --subsample reports/intent/fresh18/subsample_120b.json --texts D:/ml-cache/s33/fresh18/fresh18.jsonl --out D:/ml-cache/s34/groq120_f18`
   (bounded: about 48 items per 24 h), then
   `fresh18_eval score ... --llm120 D:/ml-cache/s34/groq120_f18/gpt-oss-120b.jsonl`. Deadline 2026-11-15; partial = INCOMPLETE with n.
2. Step 2 (served R1+) is unwritten.
3. Value (BELIEVED): about $1.74 per Starter customer per month of LLM cost on paid Groq. Recommendation: enable when there is a paying
   customer whose volume makes that saving matter. The evidence above holds until then.
