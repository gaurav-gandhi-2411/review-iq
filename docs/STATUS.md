# Samidha Reviews: status

Updated at the end of every session. Last update: 2026-10-11 (S22). Labels: VERIFIED = observed this
session or by a committed artifact (command and output exist); BELIEVED = inferred or stated by someone else,
not observed. Synthetic = produced on generated data, never a statement about real customers.

## Live in production (Cloud Run `review-iq`, asia-south1)

| Item | State | Evidence |
|---|---|---|
| Serving revision | 100 percent of traffic on the latest revision (`review-iq-00066-8w2` at the time of writing; every merge to main deploys a new revision; the `smoketest` tag, 0 percent, trails by one) | VERIFIED `gcloud run services describe` 2026-10-11; `/health` 200; `/demo/extract` 200 with a correct extraction |
| Primary LLM | Groq, **dedicated org** (`groq-api-key-samidha`) | VERIFIED |
| Failover | Shared-org Groq, `openai/gpt-oss-120b` (`SECONDARY_PROVIDER_KIND=groq`, ADR 0038) | VERIFIED offline with an invalid primary key |
| Gemini fallback | **Removed from app code** (#360); `GEMINI_API_KEY` is still bound on the service until GG does the ADR 0035 clean-up | VERIFIED merged and deployed |
| OpenRouter | Unbound; the 11 revisions that bound `secondary-provider-api-key` were deleted 2026-10-10. The secret itself still exists | VERIFIED: 0 remaining revisions reference it |
| Supabase service-role key (leaked-key incident) | CLOSED 2026-10-10 | VERIFIED: removed from Cloud Run (step d); GG disabled legacy API keys (step f); probe `python -I g4_jwt_dead.py` returned HTTP 401 `Legacy API keys are disabled` on `/rest/v1/` for the legacy service_role JWT (role service_role, ref enqpluazgxewepchdeut, exp 2094); before step f the same probe returned 200. Zero consumers (no Cloud Run service or job bound it; code only falls back to it when the anon key is empty, and `SUPABASE_ANON_KEY` is set); secret `supabase-service-role-key` (1 version) deleted, list now shows 0. Anon path: auth endpoint with the publishable key returns 400 `invalid_credentials` for bad credentials (key accepted), `/api/health` 200. BELIEVED: a full login with a real account was not run (no credentials). The Secret Manager value is assumed to be the leaked JWT (same ref and role; not provable) |
| Migrations | 46/46 applied; `20261009000003` (weekly_digest CHECK + `list_orgs_with_weekly_digest`) applied 2026-10-10 as review_iq_migrator; `push.py --verify` PASS=99 | VERIFIED 2026-10-10; Migration Drift Check and Schema Drift Check workflows green after apply |
| Schedulers | ingest-tick ENABLED; digest-daily and detector-sweep PAUSED; no weekly job | VERIFIED |
| Classification engine | `engine/` on main; not deployed anywhere (deployment waits for the review-intent model) | VERIFIED |
| Judge.me connector | Code merged behind `ENABLE_JUDGEME_CONNECTOR` (default off): spec, client, mapper and scans, sync engine (#351-#354). Migration and the rest of the stack are open PRs | VERIFIED merged; flag off; nothing applied |

## Model: what is proven, what is in progress, what each feature needs, the honest gaps

**Proven (public benchmarks, sealed test, 3 seeds, bit-reproducible across two Kaggle sessions).**
A fine-tuned multilingual-e5-base encoder with a linear head, calibrated on validation:
known-intent macro-F1 and accuracy on CLINC150, BANKING77 and MASSIVE-en (`docs/reports/e2-benchmark.md`, every
number rendered from `reports/engine/e2/*.json`); precision at coverage with a conservative, validation-style
threshold rule; open-set rejection (Mahalanobis best AUROC on all four unknown sets); int8 CPU serving inside
the 100 ms p95 target on one thread once the threshold is recalibrated on the int8 model. Against a prompted
LLM on BANKING77, paired on the same 168 items, it leads on accuracy (0.917 against 0.786, difference +0.131
[0.071, 0.196], McNemar p 0.00011) and, the headline, on calibration: at a 95 percent precision requirement it
answers 96.4 percent of inputs against 13.1 percent for gpt-oss-120b (coverage intervals [0.869, 1.000] and
[0.000, 0.488], oracle points, public benchmark, not review text). On CLINC150 the paired comparison against qwen
is UNDERPOWERED (n 109, difference +0.055 [0.000, 0.119]). Source: `docs/reports/e2-benchmark.md`
(`reports/engine/e2/paired_*.json`), merged in #386.

**In progress.** Review intent on real review text. Pilot 1 passed no task. Re-pilot 1 (fresh items, v2
definitions, four model families) gave: sentiment alpha 0.759 and urgency 0.683 USABLE-WITH-CAVEAT; 10-class
intent 0.653 FAIL; explicit-no-repurchase 0.487 FAIL; aspect presence FAIL; aspect sentiment usable for beauty and
food; mining did not fill the rare classes. Amendment 4 (committed before any run) coarsens the taxonomy to
`needs_action` (yes/no) and a five-value broad intent, keeps sentiment, urgency and beauty/food aspect sentiment,
and re-pilots on 360 fresh items (zero overlap with earlier pilots, verified). Re-pilot 2 (VERIFIED, `reports/labelling/pilot3/repilot2.json`, #389): no task PASSes; five are
USABLE-WITH-CAVEAT (needs_action alpha 0.789 / consensus 0.960; 5-class intent 0.711 / 0.903; sentiment 0.719 /
0.883; urgency 0.704 / 0.930; aspect sentiment beauty 0.749, food 0.744). delivery and service have 4 consensus
items each: reported as insufficient data. Amendment 5 (#390, merged before any scale run) sets the scale-labelling
rule: two-judge training labels with a mistral tiebreak, a four-judge sealed test, about 5,000 items, gate
unchanged. Scale labelling is DONE (VERIFIED, #465): 5,000 items; sealed set of 763 labelled by four judges, none of the tasks
PASSes the label-quality gate (all USABLE-WITH-CAVEAT). The e5-base multi-task model (run 1, 6 epochs, val macro-F1 still
rising) reproduces the panel consensus on the sealed set: needs_action accuracy 0.960, conservative p0.95 coverage 0.917
at precision 0.983. These are agreement with silver labels; the four labelling judges score 0.97 to 0.99 against the
other three, so the encoder is a cheap copy of the panel, not better than it. delivery, service, urgency high and
several aspects are insufficient data. Source: `reports/intent/model_report.md`.

**What each feature needs.**
- Per-review classes (defect, delivery, refund request...): the customers' own reviews (Judge.me ingestion, a
  real store) because the rare classes do not occur in public corpora; a re-pilot; a human audit of the consensus
  labels if a precision number is to be published as true precision rather than precision against panel labels.
- Campaign (suspicious-pattern) alerts: a plumbing change so extraction rows carry ratings and a verified flag
  (production rows have neither today); real timestamps at hour resolution; a real store to see real campaigns.
- Judge.me: a free development store, the private token, and one recording session to re-validate every metric
  on real responses; Judge.me's terms read by a person.
- Serving: a new Cloud Run service, only after the review-intent model exists.

**Honest gaps.**
- Every review-intent number so far is against panel consensus (silver labels), not the truth.
- The public benchmarks are English-dominant, short, clean intent sentences; seller reviews are longer, noisier
  and partly Hinglish. The multilingual result (in-language data matters) is the only direct evidence on that.
- The campaign detector and the Judge.me connector are evaluated on synthetic injections and fake-server
  fixtures. They test the cases we modelled.
- The reference figure for open-set rejection from the earlier private project (23 to 34 percent at 0.95
  retention) is a different task; beating it on public data is context, not a head-to-head.
- Extraction accuracy is not a moat: the full pipeline and a plain prompted Llama 3.3 70B were within two points
  on the held-out set (BELIEVED, brief figure; no committed artifact contains the plain-Llama number).

## Features: metric, target, current value, status

| Feature | Metric | Target (pre-registered) | Current value | Status |
|---|---|---|---|---|
| Known-intent classifier (public data) | Macro-F1 with accuracy beside it, 3 seeds | Beat the baseline ladder; reproduce published accuracy | CLINC150 0.966, BANKING77 0.936, MASSIVE-en 0.880 macro-F1 (e5-base); accuracy 0.967, 0.936, 0.900 | PROVEN on public data, `docs/reports/e2-benchmark.md` |
| Precision at coverage (the product claim) | Coverage where precision reaches 0.95; precision at coverage 0.9, 0.8, 0.7 | Claim only with a threshold chosen on other data | BANKING77: oracle coverage 0.974; threshold chosen on half then read on the other half with the conservative rule: precision 0.961, coverage 0.952, target hit in 0.96 of splits. CLINC150 holds 0.95 at full coverage. MASSIVE-en: 0.876 oracle, 0.825 conservative, hit rate 0.92 | MEASURED on public data; not yet on review text |
| Open-set / novel issue | Rejection recall at 0.95 validation retention, AUROC | Beat calibrated softmax with CI excluding zero; known retention at least 0.90 | Mahalanobis best AUROC on all four unknown sets; rejection recall 0.43 (MASSIVE-en) to 0.93 (CLINC150 out-of-scope) | MEASURED on public data |
| CPU serving (int8 ONNX) | p50 and p95 latency on one thread, cost per 1,000 | p95 under 100 ms | e5 p50 44.5 ms, p95 65.1 ms; MiniLM p50 16.2 ms, p95 22.1 ms (Kaggle Xeon 2.2 GHz); about 0.0013 and 0.0005 USD per 1,000 (arithmetic from remembered Cloud Run rates, BELIEVED) | MEASURED in process; not deployed |
| Review intent, pilot 1 | Krippendorff alpha, clear-consensus fraction (the coverage ceiling) | alpha 0.80 and consensus 0.80 to pass | Intent 0.693 / 0.893, sentiment 0.772 / 0.923, urgency 0.716 / 0.916, buy_again 0.416, mismatch 0.392, aspect presence 0.23 to 0.33 | GATE NOT PASSED |
| Review intent, re-pilot 1 (random 300) | alpha, clear-consensus fraction | alpha 0.80 and consensus 0.80 to pass | Sentiment 0.759 / 0.923, urgency 0.683 / 0.897 (both USABLE-WITH-CAVEAT); 10-class intent 0.653 / 0.863 FAIL; explicit-no 0.487 / 0.957 FAIL | NOT PASSED; led to the coarse taxonomy (Amendment 4) |
| Review intent, re-pilot 2 (random 300, coarse taxonomy) | alpha, clear-consensus fraction | alpha 0.80 and consensus 0.80 to pass | needs_action 0.789 / 0.960; 5-class intent 0.711 / 0.903; sentiment 0.719 / 0.883; urgency 0.704 / 0.930; beauty 0.749 / 0.908, food 0.744 / 0.844 | NO PASS; five USABLE-WITH-CAVEAT; delivery/service insufficient data; `reports/labelling/pilot3/repilot2.json` |
| Calibration vs prompted LLM (the headline) | Coverage at 95 percent precision, paired items | Claim only with intervals that separate | BANKING77: 0.964 [0.869, 1.000] against 0.131 [0.000, 0.488]; accuracy +0.131 [0.071, 0.196]. CLINC150 vs qwen: n 109, difference +0.055 [0.000, 0.119], underpowered | PROVEN on a public benchmark (n 168); not on review text; not on the public site |
| Judge.me ingestion | Completeness, idempotency, edit/delete correctness, freshness, failure handling | 100 percent completeness; 0 duplicates after 5 re-runs; 100 percent edits and deletions after reconciliation; p95 freshness within the interval | All met on fixtures (synthetic): completeness 100 percent across 7 sizes and 3 orders; 0 duplicates; edit lag 1 run, deletion lag 12 h (unknown order); freshness p95 5.75 h at a 6 h schedule | CODE MERGED OFF; migration and real-store check pending |
| Campaign (suspicious-pattern) detector | Alert-level recall, false alerts per product-month, time to detect | Recall above the best baseline with CI excluding zero, at most 1 false alert per product-month, wording test passes | SYNTHETIC: recall 0.880 [0.855, 0.903] vs best baseline 0.782; 0.060 false alerts per product-month on assumed-organic real streams; median 5 campaign reviews before the alert | USEFUL by the rule; draft PRs; flag stays off; no real-world claim |
| Merge gate | Fails closed | A killed or slow hook must block | claude-config #44, #45 and #46 are MERGED (the hook fails closed; the standing waiver for gates 1 and 3 exists in the repo). BELIEVED not yet live in GG's checkout: it has uncommitted edits to files the merge also changes, so I did not pull it. #48 (gate-4 evidence) is a draft in SHADOW mode with head-SHA pinning: it logs verdicts and enforces nothing; shadow verdicts are to be reported for 2 weeks before enforcement is proposed | PARTLY LIVE |
| U4d injection twin control | Landing rate per attack type with exact McNemar test | All 74 pairs, 3 types | 52 of 74 pairs after day 3; partial, not a finding | PAUSED (resumes when the trailing-24h eval cap has room; Groq TPD is a rolling window, there is no 00:00 UTC reset) |

## Measured (other)

| Claim | Number | Source |
|---|---|---|
| Held-out accuracy, full pipeline vs plain-prompted Llama 3.3 70B | <!-- METRICS:HISTORICAL -->79.6% vs 78.3%<!-- /METRICS:HISTORICAL --> (extraction accuracy is not a moat) | BELIEVED: figures as stated in the S21 brief; no committed artifact in `eval/results/` was found that contains the plain-Llama number, so it is unreproduced here |
| Groq free limits on both orgs | 1,000 req/day and 8,000 tokens/min per model (20b, 120b, qwen3.8-27b) | VERIFIED response headers 2026-10-10 |
| Groq daily token pool | 200K/model, ROLLING 24h window (not a 00:00 UTC reset) | VERIFIED Groq 429 body 2026-10-12 00:07 UTC: "Limit 200000, Used 199445, Requested 972 ... try again in 3m0s". Eval/benchmark cap: 100K/model/rolling 24h (`eval/rolling_window.py`) |
| Failover latency (Groq to Groq, 120b) | 4.75 s for one 259-in/437-out call | one VERIFIED sample |
| Load test (mock LLM, one local uvicorn worker) | about 326 req/s at 0 ms provider latency, no error knee to c=128 | PR #323 (waiting for the standing waiver); DB path and Cloud Run unmeasured |
| Usage | 19 orgs, 1 with a notification email, 0 with an alert preference enabled, 0 orgs with an extraction in the last 7 days | VERIFIED read-only counts 2026-10-10 |

## Open, and what blocks each

| Item | Blocked on |
|---|---|
| claude-config #48 (shadow mode + SHA pinning) | Owner review of the pin (every gated merge will need `--match-head-commit`); then 2 weeks of shadow verdicts before any enforcement proposal |
| Size-only review-iq PRs | Done by splitting, not waiving: #278, #323, #271, #273, #263, #383, #389 and the campaign stack are merged as sub-400-line pieces (46 PRs merged since 07:00 UTC 2026-10-11, counted with gh). Not mergeable by gate: #429 and #432 (single JSON files over 400 lines, drafts), #318 and #268 (7,168 and 1,120 lines of one JSON file each; fragment splits were withdrawn because they leave invalid partial JSON on main) - these need the standing waiver live in the checkout |
| Judge.me migration (#355) | GG: gate 4, apply with push.py; then #356 to #359 |
| Recency dashboard | #263 is merged as #440-#447. #269 (migration, gate 4) and #275 remain open |
| #262 split into #360 to #369 | #361 is gate 4 (workflows, runbooks) and gates #362 onward; #367 and #368 are regenerated artifacts over the size cap |
| Campaign detector stack | Merged (#372 to #379, #449 to #456); detector flag stays off. Real validation needs are in `docs/reports/campaign-real-validation-needs.md` (#388) |
| Weekly digest Scheduler job and #298 | Deferred by GG |
| Shopify stack #291 to #293 | GG: needs the Shopify app registration |
| OpenRouter key rotation, Gemini clean-up (ADR 0035) | GG |
| Engine deployment | Waits for the review-intent model |
| Tailwind 4 (#106) | Plan in `docs/ops/tailwind4-migration-plan.md`, before the braces allowlist expires 2026-12-31 |

## GG must do

See the end of the latest session report; this table is regenerated each session.
