# Samidha Reviews: status

Updated at the end of every session. Last update: 2026-10-13 (S23; work dated 2026-10-12/13). Labels: VERIFIED = observed this
session or by a committed artifact (command and output exist); BELIEVED = inferred or stated by someone else,
not observed. Synthetic = produced on generated data, never a statement about real customers.

## Live in production (Cloud Run `review-iq`, asia-south1)

| Item | State | Evidence |
|---|---|---|
| Serving revision | `review-iq` 100 percent on `review-iq-00080-4bx` at 2026-10-12 about 14:40 UTC (every merge to main deploys a new revision); `review-iq-admin` on `review-iq-admin-00063-tgq`. `DEMO_MAX_SHARE=0.1` on the service (set 2026-10-12) | VERIFIED `gcloud run services describe` (S23, per the orchestrator's session record; not re-run in this docs pass) |
| Primary LLM | Groq, key secret `groq-api-key-samidha`. Groq limits are per account pool, and the benchmark key shares the Personal account with the production keys (GG, Groq console, #510), so eval and prod draw on the same pools | VERIFIED env; sharing per GG and #510. BELIEVED: the benchmark and failover keys share one org (identical org id in two 429 bodies) |
| Failover | Secondary Groq key (`groq-api-key`), `openai/gpt-oss-120b`, `SECONDARY_PROVIDER_KIND=groq` (ADR 0038). The variable has NO default now: unset with a secondary configured fails loudly at startup (#509 merged). Nightly probe is a Cloud Run Job `failover-probe` plus Scheduler `failover-probe-nightly` (02:30 UTC) reading Secret Manager directly (#503, #504 merged); alert via a log-based metric to the existing email channel. Runs seen: one PASS, one real FAIL on quota (shared pool near 199K of 200K), one forced failure that opened the alert | VERIFIED executions. After the next CI deploy ships the Dockerfile COPY line, re-run `PROBE_SOURCE=image bash ops/probes/apply-failover-probe.sh` (open) |
| 429 visibility | Every final Groq 429 logs `llm.rate_limited` (WARNING), each failover logs `llm.failover` (#491 merged and deployed); before it a 429 left no event | VERIFIED merged. Prod log search 2026-10-10..12 05:07 UTC found 0 x 429 and 0 failovers, but prod made only 3 Groq calls then, none clearly in the pool-full period: the clean log says little |
| Gemini fallback | **Removed from app code** (#360); `GEMINI_API_KEY` is still bound on the service until GG does the ADR 0035 clean-up | VERIFIED merged and deployed |
| OpenRouter | Env unbound; the runner service account's `secretAccessor` binding on `secondary-provider-api-key` was removed 2026-10-12. The secret is kept (1 version) | VERIFIED |
| Supabase service-role key (leaked-key incident) | CLOSED 2026-10-10 | VERIFIED: removed from Cloud Run (step d); GG disabled legacy API keys (step f); probe `python -I g4_jwt_dead.py` returned HTTP 401 `Legacy API keys are disabled` on `/rest/v1/` for the legacy service_role JWT (role service_role, ref enqpluazgxewepchdeut, exp 2094); before step f the same probe returned 200. Zero consumers (no Cloud Run service or job bound it; code only falls back to it when the anon key is empty, and `SUPABASE_ANON_KEY` is set); secret `supabase-service-role-key` (1 version) deleted, list now shows 0. Anon path: auth endpoint with the publishable key returns 400 `invalid_credentials` for bad credentials (key accepted), `/api/health` 200. BELIEVED: a full login with a real account was not run (no credentials). The Secret Manager value is assumed to be the leaked JWT (same ref and role; not provable) |
| Migrations | 46/46 applied; `20261009000003` (weekly_digest CHECK + `list_orgs_with_weekly_digest`) applied 2026-10-10 as review_iq_migrator; `push.py --verify` PASS=99 | VERIFIED 2026-10-10; Migration Drift Check and Schema Drift Check workflows green after apply |
| Schedulers | ingest-tick ENABLED (every 2 min); digest-daily and detector-sweep PAUSED; failover-probe-nightly ENABLED; no weekly job | VERIFIED (orchestrator record, 2026-10-12) |
| Admin service | `review-iq-admin` has NO IAM bindings: unauthenticated gets 403; an identity token without Basic gets 401 from the app (Basic is the second factor). The earlier "IAM-gated" claim was FALSE from 2026-08-13 16:43:54Z (`allUsers` run.invoker) to 2026-10-12 07:48:06Z, about 60 days. See the incident section. Doc correction is #512 (draft, gate 4) | VERIFIED 2026-10-12 (audit log, live requests) |
| Admin secret pinning | `ADMIN_PASSWORD_HASH` is pinned to Secret Manager version 3 on BOTH services (explicit version: `latest` returns FAILED_PRECONDITION when the newest version is disabled). Versions 1 and 2 destroyed 2026-10-12 14:42 UTC. All other secret refs are still `latest` | VERIFIED |
| API keys and orgs | 0 active API keys (6 unused keys revoked 2026-10-12 14:45 UTC; none used since 2026-07-07; nothing in the repo, workflows or secrets references any). 19 organizations, all GG's or test fixtures | VERIFIED; org ownership confirmed by GG |
| Trigger tokens | `INGEST_TICK_TOKEN`, `DIGEST_TRIGGER_TOKEN`, `DETECTOR_SWEEP_TRIGGER_TOKEN` are PLAIN env vars on the service (not Secret Manager refs) and their values appeared in an agent transcript on 2026-10-12: treat as EXPOSED. Rotation plus a move to Secret Manager is open (Cloud Scheduler headers must change with them) | VERIFIED as plain env; the exposure is a fact of the transcript. OPEN, GG decision |
| Uptime monitoring | Monitoring as code (#500, #501 merged): HTTPS check `api-health-https` (6 regions, body must contain `"status":"ok"`, 10 s timeout), availability policy (2 regions, 120 s), NEW failure-rate policy (pass fraction below 0.97 over 900 s; replay of 2026-10-09..12 data opens 2 incidents, silent on 5 probe-side blips and 3 single-region bursts), NEW certificate-expiry policy (under 14 days; currently 50.8 days). The old HTTP check was deleted after the new one proved green. Known gaps: a single-region failure never alerts; one email route; Secret Manager data-access audit logging is NOT enabled, so secret reads are unlogged | VERIFIED (orchestrator record). A temporary failing check opened an incident about 4 min 22 s after creation; inbox RECEIPT is NOT proven (GG to confirm `s23-alert-test`). #502 (retire `uptime-alert.yml`) is HELD until then |
| `/health` 503s | 23 in 2026-10-10..12 (about 10 bursts of a few to about 20 s), all `reason=db_timeout` with an empty error. ROOT CAUSE NOT PROVEN. Healthy ping 1.77 s fixed cost. Measured from a Cloud Run job in Mumbai (`reports/ops/db_latency_s23.json`, PR #499, open, not on main): DB is the Sydney pooler (transaction mode, port 6543); one TCP round trip p50 218.8 ms; new connection p50 1773.8 ms (connect 1333.3 + `SELECT 1` 440.5); n 60. #494 merged (8 s guard, stage/connect_ms/elapsed_ms diagnostics, `health.db_slow`). Pooling is in draft PRs #513 (pool module), #514 (`/health` pooled), #515 (all call sites, gate 4): unit-tested, the real-Postgres isolation test is written but NOT run. A Mumbai region move is a PLAN only (`docs/ops/db-region-migration-plan.md` in #499) | VERIFIED measurement artifact (on the #499 branch); cause BELIEVED (connection setup cost), unproven |
| Demo allowance | Trailing-24 h token ledger (in-memory, per instance) with `DEMO_MAX_SHARE`; sees only the demo's own tokens, not eval or production extraction (a persisted shared ledger needs an RLS policy change, gate 4). The "resets at midnight UTC" text is gone (#493 merged) | VERIFIED merged |
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

**S23 (2026-10-12/13).** The sealed set is 757 items after leakage-ledger exclusions (763 minus 6: 4 exact and
2 near-duplicate hits in benchmark and vernacular_v2; ledger and near-duplicate scan are CI-enforced, #467, #474).
Run 2 (same recipe, up to 30 epochs, validation-only early stopping, best epoch 7, int8 export, CPU latency) is
merged (#478, #479, #481). The out-of-domain food-delivery auxiliary set (for delivery/service) was DROPPED on
2026-10-12 (panel alpha 0.45, scraped from Google Play) and deleted from Kaggle and disk (BELIEVED per the
orchestrator record; #486 and #487 are closed). Run 3 (safety stratum added to training) is a NEGATIVE RESULT and
environment-confounded; its code and results are open PRs (#495 to #498), not on main, so the run 3 numbers below
come from the artifacts on the #498 branch (`reports/intent/eval_run3.json` and `model_report.md` there).
A 5-seed paired re-run per arm (Amendment 8, #505) and per-class-threshold serving as the default (#506, #507)
are open PRs. The comparison against plain-prompted LLMs (the moat) is NOT complete: see its subsection.

## Sealed results: review-intent encoder (silver labels, never human truth)

Source: `reports/intent/model_report.md` on main (runs 1 and 2; `eval_run1.json`, `eval_run2.json`; evaluation
commit `a74d535`) and on the open #498 branch (run 3). Convention: macro-F1 [95 percent bootstrap, item level; the
cluster interval resamples products and is within 0.005 of the item interval on every core task in the report].
A class with fewer than 20 sealed positives is reported as insufficient data. Single seeds: run-to-run differences
are not separated from seed noise (see the run 3 control below).

| Task (n) | Run 1 (6 epochs) | Run 2 (best epoch 7) | Run 3 (safety stratum in training) |
|---|---|---|---|
| needs_action (717) | 0.923 [0.892, 0.948] | 0.929 [0.900, 0.952] | 0.911; diff vs run 2 -0.018 [-0.039, 0.002] |
| broad_intent (700) | 0.825 [0.781, 0.866] | 0.844 [0.805, 0.880] | 0.837; diff -0.007 [-0.046, 0.030] |
| sentiment (673) | 0.920 [0.890, 0.945] | 0.939 [0.912, 0.961] | 0.933; diff -0.006 [-0.026, 0.016] |
| urgency (703) | 0.893 [0.856, 0.926] | 0.894 [0.856, 0.926] | 0.885; diff -0.008 [-0.040, 0.021] |
| aspect:beauty:results_efficacy (192) | 0.862 [0.784, 0.928] | 0.926 [0.871, 0.975] | not tabulated here |
| aspect:food:taste (109) | 0.855 [0.765, 0.928] | 0.863 [0.778, 0.936] | not tabulated here |

Run 2 conservative p0.95 (threshold chosen on validation, applied once to the sealed set): needs_action coverage
0.894 at precision 0.988; broad_intent 0.790 at 0.977; sentiment 0.831 at 0.989; urgency 0.832 at 0.990. Majority-class
macro-F1 for the same four tasks is 0.458, 0.283, 0.446, 0.469 and TF-IDF + LR is 0.807, 0.698, 0.821, 0.768
(`model_report.md`, baselines). Run 2 CPU latency (Ryzen 7 6800H, batch 1, int8_linear): p50 78.9 ms, p95 114.6 ms;
fp32 p95 154.2 ms (the Kaggle-Xeon figure in the features table is a different machine).

Run 3 verdict (pre-registered Amendment 7; `model_report.md` on the #498 branch): criterion A (every core task within
-0.02 of run 2) PASS; criterion B (urgency=high recall up with an interval excluding 0, precision not down more than
0.05) FAIL. The `run2_local` control (run 2 recipe re-run on this machine, no safety data) differs from run 2 by
-0.026 macro-F1 on broad_intent, so seed and environment noise is the size of the effects. NEGATIVE RESULT, not
promoted.

**Safety stratum (urgency=high; enriched, keyword-mined, silver labels; never pooled with the sealed set above; not
a base rate).** Binding n = independent positive groups: 131 before ledger exclusions, 130 evaluated after (nominal
positive items 143 before, 142 after). The pre-registered bar of 138 independent positives (`docs/specs/review-intent.md`)
is NOT met. Train plus validation hold 174 positives (BELIEVED, orchestrator record). Group-bootstrap 95 percent
intervals, `model_report.md` on the #498 branch:

| Run | Operating point | Recall | Precision |
|---|---|---|---|
| run 2 | argmax | 0.119 [0.068, 0.179] | 0.838 [0.636, 1.000] |
| run 2 | threshold chosen on safety validation | 0.865 [0.804, 0.918] | 0.643 [0.572, 0.708] |
| run 3 | argmax | 0.877 [0.823, 0.933] | 0.669 [0.598, 0.733] |
| run 3 | threshold chosen on safety validation | 0.931 [0.886, 0.971] | 0.635 [0.569, 0.694] |

Run 3 minus run 2 (paired): argmax recall +0.758 [0.688, 0.832], precision -0.169 [-0.325, 0.017] (past the 0.05
guard); matched-threshold recall +0.065 [0.016, 0.122], precision -0.008 [-0.057, 0.038]. Serving finding: int8
collapses argmax recall on urgency=high (0.035 to 0.154 across variants, against 0.877 in fp32) while threshold
recall survives (0.650 to 0.750), so rare classes must be served with per-class thresholds, never argmax.

### Insufficient data (fewer than 20 sealed positives; no metric is given)

- Intent classes delivery (1 sealed positive) and service (0); sentiment neutral (11) and mixed (19); urgency high
  on the sealed set (4). Source: per-class table, `model_report.md` (run 2).
- Aspect tasks: beauty value (14), food ingredient_quality (17), food efficacy_claims (13), food quantity (3) have no
  metric at all; macro-F1 is withheld for beauty texture, skin_reaction, fragrance and food value (fewer than 2
  classes with 20 positives), accuracy only. Classes under 20: texture negative (6) and neutral (4), skin_reaction
  positive (12) and neutral (1), fragrance negative (8) and neutral (2), food value negative (11) and neutral (2),
  results_efficacy neutral (4), food taste neutral (0).
- delivery and service stay insufficient after the auxiliary-data attempt was dropped; they need real customer
  reviews (Judge.me ingestion), not out-of-domain data.

### Moat comparison (plain-prompted LLMs against the encoder): NOT COMPLETE

The pre-registered harness (#477: coverage at 0.95 precision, stopping rule at n=300) is on main; no result artifact
is committed and no final comparison number exists. Why it is incomplete (orchestrator record, BELIEVED, no committed
artifact): gpt-oss-20b has 502 Groq rows and gpt-oss-120b about 425; the first about 100 rows per model predate the
committed stopping rule; the benchmark Groq key now returns HTTP 401, so the 120b arm is blocked until GG provides a
valid key in an org that is not the failover pool; the 20b arm is being restarted entirely on Kaggle (local
gpt-oss-20b) for a consistent paired comparison, with the Groq rows kept as a cross-check; the few-shot arm has not
been run. Interim clean n=300 verdicts, not a result: 20b, encoder ahead on broad_intent and urgency, needs_action
and sentiment overlap; 120b, encoder ahead on broad_intent and sentiment, needs_action and urgency overlap.

## Model: what each feature needs, honest gaps

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
| Review intent encoder, sealed set (runs 1 to 3) | Macro-F1 per task, bootstrap intervals, silver labels | Pre-registered Amendments 5 and 7 | See "Sealed results": run 2 needs_action 0.929, broad_intent 0.844, sentiment 0.939, urgency 0.894; run 3 within noise of run 2 | MEASURED against silver labels; run 3 NEGATIVE RESULT; delivery/service insufficient data |
| Calibration vs prompted LLM (the headline) | Coverage at 95 percent precision, paired items | Claim only with intervals that separate | (Review-text moat comparison NOT complete, see its subsection.) BANKING77: 0.964 [0.869, 1.000] against 0.131 [0.000, 0.488]; accuracy +0.131 [0.071, 0.196]. CLINC150 vs qwen: n 109, difference +0.055 [0.000, 0.119], underpowered | PROVEN on a public benchmark (n 168); not on review text; not on the public site |
| Judge.me ingestion | Completeness, idempotency, edit/delete correctness, freshness, failure handling | 100 percent completeness; 0 duplicates after 5 re-runs; 100 percent edits and deletions after reconciliation; p95 freshness within the interval | All met on fixtures (synthetic): completeness 100 percent across 7 sizes and 3 orders; 0 duplicates; edit lag 1 run, deletion lag 12 h (unknown order); freshness p95 5.75 h at a 6 h schedule | CODE MERGED OFF; migration and real-store check pending |
| Campaign (suspicious-pattern) detector | Alert-level recall, false alerts per product-month, time to detect | Recall above the best baseline with CI excluding zero, at most 1 false alert per product-month, wording test passes | SYNTHETIC: recall 0.880 [0.855, 0.903] vs best baseline 0.782; 0.060 false alerts per product-month on assumed-organic real streams; median 5 campaign reviews before the alert | USEFUL by the rule; draft PRs; flag stays off; no real-world claim |
| Merge gate | Fails closed | A killed or slow hook must block | claude-config #44, #45 and #46 are MERGED (the hook fails closed; the standing waiver for gates 1 and 3 exists in the repo). BELIEVED not yet live in GG's checkout: it has uncommitted edits to files the merge also changes, so I did not pull it. #48 (gate-4 evidence) is a draft in SHADOW mode with head-SHA pinning: it logs verdicts and enforces nothing; shadow verdicts are to be reported for 2 weeks before enforcement is proposed | PARTLY LIVE |
| U4d injection twin control | Landing rate per attack type with exact McNemar test | All 74 pairs, 3 types | 52 of 74 pairs after day 3; partial, not a finding | PAUSED (resumes when the trailing-24h eval cap has room; Groq TPD is a rolling window, there is no 00:00 UTC reset) |

## Measured (other)

| Claim | Number | Source |
|---|---|---|
| Held-out accuracy, full pipeline vs plain-prompted Llama 3.3 70B | <!-- METRICS:HISTORICAL -->79.6% vs 78.3%<!-- /METRICS:HISTORICAL --> (extraction accuracy is not a moat) | BELIEVED: figures as stated in the S21 brief; no committed artifact in `eval/results/` was found that contains the plain-Llama number, so it is unreproduced here |
| Groq free limits | 1,000 req/day and 8,000 tokens/min per model (20b, 120b, qwen3.8-27b) | VERIFIED response headers 2026-10-10 |
| Groq daily token pool | 200K/model, ROLLING 24h window (not a 00:00 UTC reset) | VERIFIED Groq 429 body 2026-10-12 00:07 UTC: "Limit 200000, Used 199445, Requested 972 ... try again in 3m0s". Eval/benchmark cap: 100K/model/rolling 24h (#488, #489, `eval/rolling_window.py`); every benchmark Groq call goes through the machine-wide `~/.groq_budget` ledger (#510). 2026-10-11: the benchmark key drained the pool (about 300K tokens on 120b, 230K on 20b) and the failover probe then saw a 429. 2026-10-12 header test: primary and secondary keys keep SEPARATE counters; the benchmark key returned HTTP 401 (revoked or rotated), so it could not be compared |
| Failover latency (Groq to Groq, 120b) | 4.75 s for one 259-in/437-out call | one VERIFIED sample |
| Load test (mock LLM, one local uvicorn worker) | about 326 req/s at 0 ms provider latency, no error knee to c=128 | PR #323 (waiting for the standing waiver); DB path and Cloud Run unmeasured |
| Usage | 19 orgs, all GG's or test fixtures (GG confirmed 2026-10-12); 1 with a notification email, 0 with an alert preference enabled, 0 with an extraction in the last 7 days (as of 2026-10-10); 0 active API keys (2026-10-12) | VERIFIED read-only counts 2026-10-10; keys and ownership 2026-10-12 |

## Incident: admin password exposure (2026-10-12)

What happened: the live admin password (secret version 2, created 2026-10-12 07:35 UTC) appeared in an agent
transcript. Separately, `review-iq-admin` had been reachable by anyone (`allUsers` run.invoker) since 2026-08-13, so
the "IAM-gated" statement in the docs was false for about 60 days. Basic auth stayed in force throughout.
Timeline, UTC 2026-10-12 unless stated (VERIFIED from the audit log and Secret Manager, orchestrator record):

| Time | Event |
|---|---|
| 2026-08-13 16:43:54 | `allUsers` bound to `review-iq-admin` (audit log) |
| 07:35 | Secret version 2 created (the value later exposed) |
| 07:48 | `allUsers` removed; the policy is now empty |
| 07:52 | Version 2 disabled |
| 08:14 | Version 3 created by GG running `scripts/rotate_admin_password.py` (#508, draft) in his own terminal; the new value was never seen by Claude |
| 08:15 | Version 1 disabled |
| 14:42 | Versions 1 and 2 destroyed; both services pinned to version 3 by explicit version |
| 14:45 | Six unused API keys revoked (none used since 2026-07-07) |

Closed (VERIFIED): version 3 is enabled and 1 and 2 are destroyed; both services serve a revision pinned to version 3;
the admin service answers 403 unauthenticated and 401 with an identity token but no Basic credential; two literals
found in repo history (a CI throwaway in `pre-cutover-verification.yml` and a 9-character value in
`secret-rotation.md`) do not verify against version 3 (HTTP 401 from the live endpoint); whether either was the
pre-rotation password cannot be proven. 30-day misuse check (request logs reach back only to 2026-09-12): no
successful or guessed admin login and no mutation on `/admin`; only scanner probes on the PUBLIC service, all 404,
from Google ranges.

Open: (1) NOT checkable: 2026-08-13 to 2026-09-12 and any secret read (Secret Manager data-access audit logs are not
enabled). (2) The three trigger tokens are plain env vars and were also exposed in a transcript (see the production
table); rotation is a GG decision. (3) Doc correction #512, rotation script #508, runbook wording #485: open, gate 4,
human merge. (4) Billing: no export in the project; per-SKU cost must be read in the Console. Cloud Run requests rose
about 13x from 2026-10-05 (BELIEVED: the new uptime checks). (5) Guard: claude-config #49 and #50 (a hook that blocks a
random token printed beside the words password/secret/key/token, including in the assistant's own message) are open;
the Stop entry was added to the user settings with GG's authorization, but the live hook script is the older version
without Stop support, so that registration is inert until #49 and #50 are deployed. (6) Process gaps this session: a
`gh`/Cloud Run listing once printed plain env values (the trigger tokens); conflict markers were pushed to the #502
branch for a few minutes (fixed in `c969e11`, nothing merged).

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
| Gemini clean-up (ADR 0035) | GG: GitHub secret `GEMINI_API_KEY` and Secret Manager `gemini-api-key` still exist. OpenRouter binding is already removed (see production table) |
| `SUPABASE_DIRECT_URL` (write-capable GitHub secret) | Removal waits on #472 (read-only role, draft, GG's steps) |
| S23 PRs | Draft, gate 4, GG merges: #485, #499, #508, #512, #513, #514, #515, #472, claude-config #49 and #50. Open for review: #495 to #498 (run 3; #498 is on an `exp/` branch and gets renamed), #502 (held), #505 to #507 |
| Moat comparison, 120b arm | A valid Groq benchmark key in an org separate from the failover pool, or another provider |
| DB region (Sydney pooler, Cloud Run in Mumbai) | GG decides among options A to D in #499; Mumbai move is a plan only, about 20 to 30 min write freeze (BELIEVED), the backup lacks `auth.users` |
| Engine deployment | Waits for the review-intent model |
| Tailwind 4 (#106) | Plan in `docs/ops/tailwind4-migration-plan.md`, before the braces allowlist expires 2026-12-31 |

## GG must do

Regenerated each session (S23 list, from the orchestrator record):

1. Confirm the `s23-alert-test` email arrived; then #502 retires `uptime-alert.yml`.
2. Decide rotation of the three trigger tokens and their move to Secret Manager (Cloud Scheduler headers change with them).
3. Merge the gate-4 PRs: #485, #508, #512, #499, #515 (and #513, #514), claude-config #49 and #50.
4. #472 read-only role steps, then remove `SUPABASE_DIRECT_URL` from GitHub secrets.
5. Provide a valid Groq benchmark key in an org separate from the failover pool (or choose another provider).
6. Decide the DB region move (#499) and whether to enable Secret Manager data-access audit logs.
7. Confirm in the Groq console which org each key is in.
8. Decide on a persisted shared demo/prod token ledger (needs an RLS policy change, gate 4).
9. Check the Console cost view (no billing export exists).
10. A `python -` spinner process (PID 15252, started 19:36 local, about 3 CPU-hours) is not Claude's as far as known; check it.
