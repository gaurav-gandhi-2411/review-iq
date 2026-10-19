# Samidha Reviews: status

Updated at the end of every session. Last update: 2026-10-20 (S29, autonomous; S28 on 2026-10-19; S27 on 2026-10-18; S26 and S25 the same day, S24 on 2026-10-17; S23 text below is kept where still true and corrected where S24 changed it). Labels: VERIFIED = observed this
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
| Trigger tokens | ROTATED in S24 (A4). `INGEST_TICK_TOKEN`, `DIGEST_TRIGGER_TOKEN`, `DETECTOR_SWEEP_TRIGGER_TOKEN` are now Secret Manager refs pinned to explicit versions (the plain env vars were removed in the same `gcloud run services update`), and the three Scheduler headers were PATCHed to the new values. Values were generated in memory by `scripts/rotate_trigger_tokens.py` (#532) and never printed, logged, put in argv or written to a file. Live revision `review-iq-00086-v2v`. A first attempt failed at the Cloud Run update because the runtime service account lacked `secretAccessor` on the new secrets; per-secret grants were added and the script re-run. That failed attempt had already dropped the plain vars from the service spec, so the "old plain value now returns 401" check could not be run as a direct test | VERIFIED: a random token returns 401 on all three endpoints, the new ingest token returns 200, secret versions read back equal to what was written (compare only, nothing shown). BELIEVED: the old exposed values are rejected, because nothing accepts them any more (structural, not directly tested). The script disables the previous secret versions only after its checks pass (destroying them is a separate step); older Cloud Run revisions still hold the old plain values in their specs, so they must not be re-routed to |
| Uptime monitoring | Monitoring as code (#500, #501 merged): HTTPS check `api-health-https` (6 regions, body must contain `"status":"ok"`, 10 s timeout), availability policy (2 regions, 120 s), NEW failure-rate policy (pass fraction below 0.97 over 900 s; replay of 2026-10-09..12 data opens 2 incidents, silent on 5 probe-side blips and 3 single-region bursts), NEW certificate-expiry policy (under 14 days; currently 50.8 days). The old HTTP check was deleted after the new one proved green. Known gaps: a single-region failure never alerts; one email route. Secret Manager data-access logging: ENABLED in S24 (A5) for `secretmanager.googleapis.com` only (DATA_READ, via an etag-guarded `set-iam-policy` that added `auditConfigs` and changed no binding), so secret reads from 2026-10-17 on are logged; earlier reads stay unlogged. Fit to the free Cloud Logging allotment (50 GiB per project per month) was checked against the project's measured ingestion for the last 30 days (`logging.googleapis.com/billing/bytes_ingested`); the result is in the A5 session record, not a committed artifact. Watch it for one month, since secret-read volume is new | VERIFIED (orchestrator record). A temporary failing check opened an incident about 4 min 22 s after creation; inbox RECEIPT is NOT proven (GG to confirm `s23-alert-test`). #502 (retire `uptime-alert.yml`) is HELD until then |
| `/health` 503s | 23 in 2026-10-10..12 (about 10 bursts of a few to about 20 s), all `reason=db_timeout` with an empty error. ROOT CAUSE NOT PROVEN. Healthy ping 1.77 s fixed cost. Measured from a Cloud Run job in Mumbai (`reports/ops/db_latency_s23.json`, PR #499, open, not on main): DB is the Sydney pooler (transaction mode, port 6543); one TCP round trip p50 218.8 ms; new connection p50 1773.8 ms (connect 1333.3 + `SELECT 1` 440.5); n 60. #494 merged (8 s guard, stage/connect_ms/elapsed_ms diagnostics, `health.db_slow`). S24: #513 (pool module) and #514 (`/health` pooled) are MERGED; #515 (all call sites, gate 4) stays parked for GG. The real-Postgres tenant-isolation suite (`tests/integration/test_pooled_connection_isolation.py` on #515's branch) was RUN in S24 against ephemeral postgres:17 in CI: 6 of 6 cases pass (scope leak to the next borrower, rolled-back uncommitted write, interleaved open transactions across orgs on two physical connections, randomized borrow sequence, failed statement then reuse, cross-org INSERT rejected on a reused connection). Measured live after the merges (S24, from GG's workstation, client wall clock, n 30 each, 1 s apart): `/health` p50 558 ms, p95 2712 ms, max 15599 ms; the no-DB `/openapi.json` p50 699 ms, p95 1087 ms. So the DB ping now adds no visible time over the network floor, against 1.77 s per probe before pooling (Mumbai job measurement). The 15.6 s max is one outlier (BELIEVED cold start, not diagnosed). NOT proven: that the running image contains #514 (the image push time, 07:41Z, is 1 min 41 s after the #514 merge, which fits but does not prove it), and no in-Mumbai after-measurement exists; the workstation numbers include the internet path. A Mumbai region move is a PLAN only (`docs/ops/db-region-migration-plan.md` in #499) | VERIFIED measurement artifact (on the #499 branch); cause BELIEVED (connection setup cost), unproven |
| Demo allowance | Trailing-24 h token ledger (in-memory, per instance) with `DEMO_MAX_SHARE`; sees only the demo's own tokens, not eval or production extraction (a persisted shared ledger needs an RLS policy change, gate 4). The "resets at midnight UTC" text is gone (#493 merged) | VERIFIED merged |
| Classification engine | `engine/` on main; not deployed anywhere (deployment waits for the review-intent model) | VERIFIED |
| Private classify service (S29) | `review-iq-classify`, asia-south1, revision `review-iq-classify-00001-4rt` (image digest `sha256:b6d3ef10...`), 1 vCPU / 2 GiB, min 0, max 2, concurrency 4. PRIVATE: the IAM policy has exactly one binding, `roles/run.invoker` for `review-iq-runner@`; no allUsers or allAuthenticatedUsers; an unauthenticated POST returns 403. **Nothing calls it**: `ENABLE_ENCODER_FIRST` is off and `CLASSIFY_SERVICE_URL` is unset on `review-iq`. Live parity found a numerics difference (81 of 3028 decisions differ; see Served model). Registry now 914.9 MB of unique layer bytes, 481.5 MB of it this image: about 4 cents a month (BELIEVED price) | VERIFIED (gcloud, Cloud Monitoring, live runs; `D:/ml-cache/s29/`, not committed) |
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

### Moat comparison (plain-prompted LLMs against the encoder): 20b ARMS DONE, 120b PENDING A VALID KEY

All labels are the four-judge panel consensus (SILVER), never human gold. Pre-registered harness: #477; amendments in
`docs/specs/review-intent.md` (7 and 8 run 3, 9 the local 20b arm, 10 the few-shot arm, 11 the compare rule).

- **20b zero-shot, local (Amendment 9, merged #525 to #527).** gpt-oss-20b on Ollama on two Kaggle T4s, digest equal to
  the registered manifest, all 757 sealed items. At n=300 the registered rule decided broad_intent and urgency
  (encoder ahead) and left needs_action and sentiment undecided, so the run continued to 757. Groq-vs-local
  agreement on the 502 overlapping ids is in `reports/intent/moat_local20b/agreement.json`. VERIFIED, committed
  artifacts under `reports/intent/moat_local20b/`.
- **20b few-shot, local (Amendment 10, merged #530).** 7 worked examples from TRAINING items only. The stopping rule
  fired at n=300 and the run stopped there. **RETRACTED reading:** that run said few-shot was worse on every task, but 27 of
  300 few-shot (and 6 of 300 zero-shot) completions hit the 600-token cap with empty content and scored wrong, so the
  difference was largely a harness artefact. The artefacts stay on main, labelled cap 600, under
  `reports/intent/moat_local20b_fewshot/`.
- **20b re-run without truncation (Amendment 12, merged #538).** Canary rung 1 (reasoning effort low, max_tokens 1500) gave 0 of
  30 empty; the few-shot arm was re-run from the start on Kaggle (all 757 sealed ids; the stopping rule left sentiment and
  urgency undecided at n=300) with a zero-shot arm of identical settings on the first 300. Empty or unparseable completions:
  0 of 757 and 0 of 300. VERIFIED, `reports/intent/moat_local20b_fewshot_v2/`. Findings on matched ids (silver, n about 250 to
  280 per task): few-shot is worse than zero-shot on needs_action, better on urgency, not separated on broad_intent accuracy
  and sentiment. Against the encoder (Amendment 11 rule): the encoder beats both arms on broad_intent, beats zero-shot on
  urgency, and is NOT separated from zero-shot on needs_action or from either arm on sentiment. So the moat claim stands for
  broad_intent and urgency only; the earlier "encoder ahead on every task" was built on arms with empty answers. Caveats: reasoning
  effort low is itself a change (the zero-shot v2 arm differs from the cap-600 zero-shot in both directions); n is 300. The two
  other S25 caveats are resolved by Amendment 13 (next bullet): the effort field is honoured, and both v2 arms (the ones every
  verdict here uses) ran on ONE Ollama build; only the cap-600 arms ran on others.
- **One Ollama build, and `reasoning_effort` tested (Amendment 13, S26, registered before any row; #542).** Builds actually used
  (from the committed `run_status.json` files): cap-600 zero-shot 0.40.3, cap-600 few-shot 0.40.5, BOTH v2 arms 0.40.6. The
  kernel now downloads the v0.40.6 release asset from GitHub and refuses to continue unless its sha256 equals the published
  digest (the Kaggle run matched it). Test (20 zero-shot items, same items at none/low/medium/high, completion tokens): mean 558 (default), 379
  (low), 549 (medium), 822 (high); high longer than low on 20 of 20 items, exact sign test p 2e-06, median 377.5 against 739.5
  (ratio 1.96); criterion (p below 0.05 and ratio at least 1.25) MET. VERIFIED, `reports/intent/moat_effort_check/`. So "reasoning
  effort low" in Amendment 12 was a real setting, and the default behaves like medium. Not shown: that answer quality is unchanged
  across settings, or the same effect on the few-shot prompt. Consequence for comparisons: the v2 few-shot vs v2 zero-shot pairs
  are same-build, same-settings and stand; any v2-versus-cap-600 difference mixes truncation, effort and build and is not separable.
- **The moat claim, as far as the evidence goes (S26; silver labels, first 300 sealed ids, matched per task, v2 arms, 20b only).**
  Accuracy edge over gpt-oss-20b zero-shot: broad_intent 0.916 against 0.747 (paired difference +0.168 [0.117, 0.220]) and urgency
  0.942 against 0.851 (+0.091 [0.044, 0.142]); the registered rule says the encoder beats it on both. No accuracy edge on
  needs_action (0.957 against 0.957, difference 0.000 [-0.028, 0.028]) or sentiment (0.960 against 0.941, +0.020 [-0.004, 0.047]):
  NOT separated, so there the reasons to prefer the encoder are cost, latency and independence from a provider quota, not accuracy.
  Source: `reports/intent/moat_local20b_fewshot_v2/moat_compare_v2.{json,md}` (code commit in its header). 120b: PENDING (no key), so
  nothing is claimed against it. Cost and latency per 1,000 reviews, both models, all four tasks in one pass:

  | | encoder (static-int8 ONNX, own Cloud Run service) | gpt-oss-20b via Groq, effort low |
  |---|---|---|
  | cost per 1,000 reviews | about $0.0044 outside the free tier, $0 inside it (up to about 1.3 million reviews a month in the free vCPU allowance). BELIEVED: Cloud Run unit prices ($0.000024 per vCPU-s, $0.0000025 per GiB-s, $0.40 per million requests) are recalled, not verified against Google's page this session; the 137.2 ms is VERIFIED local-container p50 | $0.133 (default effort $0.187, high $0.266). Price VERIFIED in `app/core/pricing.py` (as of 2026-09-10); completion tokens VERIFIED (mean 379.1 at low, effort check); prompt tokens ESTIMATED at 260 (911 characters, not tokenised) |
  | latency per review | batch 1 p50 137.2 ms, p95 245.8 ms in a 1 vCPU container (S25, local Docker); 161.5 / 302.6 ms client side over HTTP (S26 rehearsal). Cloud Run NOT measured | NOT measured on Groq for 20b. Measured only locally: the mixed T4 run took 2086 s for 1,057 reviews on two servers, about 4 s per review per server (mostly few-shot, about 1,112 tokens). One Groq sample exists (120b failover, 259 in/437 out): 4.75 s |
  | quota | none | about 640 tokens per review means about 313 reviews a day on a 200,000-token daily pool (the figure in this file's failover row, BELIEVED per account) |

  Ratio of cost: about 30 times, on estimates marked above; it moves with the Cloud Run prices and the prompt-token estimate, not with the
  direction. This is a cost and quota argument at the model-serving layer; it says nothing about Groq's price changing.
- **Moat table (S27 consolidation; silver labels, first 300 sealed ids matched per task, v2 arms, 20b zero-shot only; 120b PENDING).**
  Source `reports/intent/moat_local20b_fewshot_v2/moat_compare_v2.{json,md}`. Accuracy, paired item bootstrap 95 percent:

  | task (matched n) | encoder | gpt-oss-20b | encoder minus 20b | reading |
  |---|---|---|---|---|
  | broad_intent (273) | 0.916 | 0.747 | +0.168 [0.117, 0.220] | accuracy edge |
  | urgency (275) | 0.942 | 0.851 | +0.091 [0.044, 0.142] | accuracy edge |
  | needs_action (281) | 0.957 | 0.957 | 0.000 [-0.028, 0.028] | not separated: edge is cost, latency, quota |
  | sentiment (253) | 0.960 | 0.941 | +0.020 [-0.004, 0.047] | not separated: edge is cost, latency, quota |

  Cost per 1,000 reviews (all four tasks in one pass): encoder about $0.0044 outside the free tier (BELIEVED unit prices), 20b $0.133 at
  low effort (price VERIFIED, completion tokens VERIFIED, prompt tokens ESTIMATED): about 30 times. Latency: encoder 137 ms p50 per
  review (local container); 20b not measured on Groq. Details and caveats in the S26 bullet above.
- **Encoder-first routing (Amendment 14, S27; draft #559, flag OFF, verdict: DO NOT ENABLE).** Registered (commit ed74662 on #559's branch; the spec text
  lands on main with #561) before any router output. Capacity arithmetic is in the product-wide capacity section below. Measured on the 757 sealed ids
  with the served ONNX decisions: rule R1 (route when needs_action is yes or abstained, or urgency is high-candidate or abstained) sends 208
  = 0.275 [0.244, 0.308] to the LLM (expected 0.25 to 0.45). Quality criteria (silver labels): C1 urgent reviews, safety-stratum test (130
  independent gold urgency=high groups, ENRICHED not a base rate): R1 routing recall 1.0 [0.971, 1.0], MET. C4, the urgent items the
  gpt-oss-20b zero-shot path also caught (44 groups, 720 LLM rows, 0 empty): 1.0 [0.920, 1.0], MET. C3, gold needs_action=yes on the sealed
  set: 106 of 111 = 0.955 [0.8989, 0.9806], the lower bound misses the 0.90 bar by 0.0011, MISSED. By the registered rule R1 must not be
  enabled; no criterion was changed after the result. S28 error analysis of the 5 misses (`reports/intent/fresh15/c3_miss_votes.json`): the
  four-judge panel did NOT split on them (2 are 4-0 yes, 3 are 3-1 yes; across all 111 gold-yes items 90 are 4-0 and 21 = 18.9 percent are 3-1),
  the encoder was confident on all five (p(yes) 0.035 to 0.099), so they are not all ambiguous labels. Amendment 15 (S28, registered before any
  fresh item was drawn; merged #561, result #565) is the larger re-test on 2,000 FRESH items (666 apparel, 667 beauty, 667 food; English
  only because the Hinglish pool was exhausted; zero overlap with every earlier set by hash, exact text and near-duplicate, and the set is
  now on the sealed list, `leakage_ledger check` ok), labelled by the four-judge panel (SILVER). 1,924 reached consensus, **306 are gold
  needs_action=yes** (expected 270 to 360). **A (absolute) MET:** R1 routes 292 of 306 = 0.954, Wilson 95 [0.925, 0.973], lower bound >= 0.90.
  **B (non-inferior to the all-LLM path, margin 0.05) MET:** the stand-in for the all-LLM path (gpt-oss-20b zero-shot answering the same
  needs_action question, effort low, Ollama 0.40.6 pinned and digest-checked; 0 empty of 2,000 rows) catches 275 of 306 = 0.899 [0.860, 0.928];
  delta recall_R minus recall_L = +0.056, paired bootstrap 95 [0.013, 0.098]. Router recall on 4-0 positives 0.966, on 3-1 positives 0.918;
  8 of the 14 misses are 4-0 yes and 6 are 3-1 (S29 CORRECTION: this text said 9; `result.json` always held 225/233 and 67/73). R1 routes 0.2695 of this set (multiplier 2.92 at an open rate of 0.1). `reports/intent/fresh15/`.
  Deviations and limits: the PANEL ran on an unpinned Ollama build (a queued earlier kernel version without the pin ran; the old kernel does
  not record the build), as the earlier panels did; the comparator is the 20b only (no 120b key) and the production extraction has no
  needs_action field; silver labels; corpus samples, not a customer stream. Under the registered rule this removes C3 as the reason Amendment
  14 says the router must not be enabled, and enables NOTHING: persisting skipped reviews (a migration, gate 4) and the on-open extraction
  path do not exist, and turning it on is a separate product decision.
  **S29 error analysis of the 14 misses** (`reports/intent/fresh15/miss_analysis.json`, #570; descriptive, nothing tuned; review text stays private,
  the file holds ids, tallies, lengths and one reader's tags, BELIEVED): 9 are 4 or 5 star reviews that praise the product and mention a defect, a fit
  problem, a return or an exchange (recall on gold-yes at >= 4 stars 58/67 = 0.866 [0.764, 0.928] against 0.979 [0.952, 0.991] at <= 3 stars);
  7 are over the 128-token cut (recall 60/67 = 0.896 [0.800, 0.949] over it, 232/239 = 0.971 within it; for one the only cue lies past the cut);
  4 (three of them 3-1) are taste complaints with no request, label-ambiguous and not worth chasing. Encoder p(yes) on the misses 0.013 to 0.123.
  The 14 ids are now development-exposed and must not be counted in a later evaluation. **PROPOSAL, not started (a pre-registered Amendment 16,
  written before any new data):** (1) targeted training examples from NON-sealed pools: mixed praise-plus-problem reviews at >= 4 stars and long
  reviews with the cue late, labelled by the four-judge panel on Kaggle; (2) a head-and-tail truncation ablation on validation only; (3) a NEW fresh
  set (not fresh15; the remaining pool size must be checked first) with criteria fixed in advance: routing recall lower bound >= 0.90 overall AND
  >= 0.85 on the >= 4 star stratum (today 0.764), routed share <= 0.30, no task's validation macro-F1 down by more than 0.02, and non-inferiority
  to the all-LLM path as in Amendment 15; (4) evaluated on the numerics the router will actually run on (next section), not the laptop's.
- **Compare rule, Wilson lower bound, matched coverage (Amendment 11).** Merged as #535 (statistics, spec) and #536 (loading,
  writer, cap-600 report with a notice); #531 closed as superseded. #531 failed gate 3 because of the 577-line module, not the
  generated files, which were already under the designated path.
- **Run 3 seeds (#529).** Merged as generated artifacts under `reports/intent/run3_seeds/`.
- **120b: PENDING.** The benchmark Groq key returns HTTP 401 and no workaround was attempted. The 120b arm needs a
  valid key in an org separate from the failover pool, or another provider. No 120b comparison number exists, and
  nothing here says 120b loses or wins.

### Served model: `POST /v2/classify` is DEPLOYED PRIVATELY (S29), flag OFF, nothing calls it (S24 A6; split in S26)

**S29 state.** GG approved the deploy (about 4 cents a month of registry storage). #545 (image, CMD `app.classify_service`) was rebased and merged; #571
(deploy script via Cloud Build, runbook with these numbers; gate 4) is open for GG. The image was built in CLOUD BUILD, not locally (laptop overheat
rule): Google's pricing page (read 2026-10-20) gives 2,500 free build-minutes a month on the default pool; October's 43 builds used about 30; this build
took about 2 minutes (a first attempt failed in 45 s: the Cloud Build docker client has no `--build-context`, so the weights are a local image named
`artifacts`). The uploaded 392 MB weights tarball was deleted from the Cloud Build bucket afterwards. Deployed with the script's flags by image digest;
access asserted (one invoker, no public principal, 403 unauthenticated; the project Owner can invoke without a binding, which is standard).
- **Live parity target (0 mismatches) NOT met.** 757 sealed reviews, one per request, identity token: 0 errors, `thresholds_sha256` equal, **81 of 3028
  decisions differ (67 of 757 reviews)**, all at a class/abstained boundary. Per-review maximum probability difference vs the offline reference: median
  0.008, p95 0.143, max 0.405. The service is deterministic (8 worst items x 3 calls identical) and the laptop reproduces the offline reference exactly
  (0.0, 0 mismatches), so the cause is the host CPU's int8 kernel. On 200 sealed reviews ONNX Runtime's `session.x64quantprecision=1` run on the laptop
  matches Cloud Run (median difference 0, p95 0.0009, 1 label mismatch of 800) against 21 mismatches with the default. The mechanism (a saturating AVX2
  u8s8 path on the laptop that the Cloud Run CPU does not use) is BELIEVED; the Cloud Run CPU flags were not read. Effect on the router, silver labels,
  nothing tuned: sealed 757 routed share 0.2761 served vs 0.2748 offline; needs_action=yes recall 104/111 = 0.937 [0.876, 0.969] vs 106/111 = 0.955;
  Amendment 15's criterion A recomputed on the deployed service's decisions for the 2,000 fresh items is 293/306 = 0.9575 [0.9287, 0.975] (still met).
  OPEN decision before the router can be enabled: make `x64quantprecision=1` the reference everywhere and regenerate the validation predictions and
  thresholds and the sealed evaluation, or accept CPU-dependent decisions and gate on the served-numerics evaluation.
- Cloud Run measurements (VERIFIED): cold start 15.2 s for the first request after scale-to-zero (one sample); warm p50 124 ms / p95 136 ms (30 short
  requests, client side); 757 real reviews p50 308 ms / p95 616 ms client side; server side p50 about 206 ms / p95 about 476 ms (Cloud Monitoring, parity
  runs); memory p99 utilization 0.62 of 2 GiB. `RemoteClassifier`'s timeout is 10 s, so the first call after scale-to-zero fails open to the LLM path
  (safe, wastes the saving): raise it or set min-instances before enabling. Rollback: `gcloud run services delete review-iq-classify` (runbook).

**S26 state.** #533 was split along its seams and closed as superseded (all 47 files accounted for by blob comparison; 5 changed on
purpose). Merged, each through the gate, flag `ENABLE_V2_CLASSIFY` still default OFF: #541 thresholds/errors/flag/metrics, #544 runtime,
#547 endpoint + serving spec, #548 quantization script, #549 evaluation scripts/pred files/report, #550 parity check, #551 benchmark.
GG's decisions were implemented: own service at 2 GiB, 1 vCPU, min-instances 0; weights baked in (both sha256 pinned in the deploy
script); `requirements-classify.txt` hash-pinned (`uv pip compile --generate-hashes`, install with `--require-hashes`).
Parked as drafts (deploy config, gate 4): #545 image and hashed requirements, #546 deploy script + live batch-1 parity check + runbook.
VERIFIED this session, in the built hash-pinned image run at `--cpus 1 --memory 2g` (local Docker, NOT Cloud Run): 757 sealed reviews sent one
per HTTP request: 0 errors, 0 mismatches of 3028 decisions, `thresholds_sha256` equal to the committed file's; client latency p50 161.5 ms,
p95 302.6 ms, cgroup peak 846 MB, not OOM-killed (the check is `scripts/classify_live_parity.py`; result kept at
`D:/ml-cache/s25/live_parity_rehearsal_s26.json`, not committed). The headline precision/coverage at batch 1 is unchanged from S25.
S27 (GG decisions): the service is PRIVATE (`--no-allow-unauthenticated`, invoked only by `review-iq-runner@` with a Google identity
token, no API key, no DB credential). Merged #554: `app/classify_service.py` (only `POST /v2/classify` and `/healthz`, no docs surface,
model loaded at startup, shares `run_classify` with the unchanged public route). #545 and #546 reworked for it and kept as DRAFTS; the
deploy is DEFERRED by GG until a product path calls the classifier, so nothing was pushed or deployed and no yes/no is asked now.
VERIFIED locally (Docker, 1 vCPU, 2 GiB, hash-pinned image of the private app, NOT Cloud Run): 757 sealed reviews one per HTTP request,
0 errors, 0 mismatches of 3028 decisions, thresholds sha equal; client p50 179 ms, p95 397 ms; cgroup peak 783 MiB, no OOM. Compressed
image size 526 MB (sum of the local image's compressed layer blobs, 386.7 MB of it the model; the registry was not pushed to). A push
would add about 0.5 GB to a registry that now holds 0.79 GB against 0.5 GB free: about 5 cents a month (BELIEVED price). Cloud Run p50,
p95 and cold start are NOT measured. The deploy script asserts no public principal in the IAM policy and a refused unauthenticated request.

**S25 state (measured on the #533 branch, now merged in pieces; flag off, nothing deployed).** Served model switched from torch dynamic
int8 to **static int8 on ONNX Runtime with no PyTorch in the image** (`docs/specs/review-intent-serving.md`, numbers in
`reports/serving/v2_classify_onnx.{md,json}` on the PR branch, silver labels, local Docker 1 vCPU, NOT Cloud Run). VERIFIED:
- Batch independence: 0 of 3028 sealed task decisions differ between one review per forward, padded batches of 128 and of 8
  (max probability difference 1e-6); 0 of 757 items differ over HTTP inside the container; token ids from the `tokenizers`
  library equal the transformers tokenizer on 763 texts. The torch variant had 166 of 3028 flips.
- Honest history: the three pre-registered static candidates all missed the 0.90 validation-agreement criterion (0.86, 0.85,
  0.83); the cause was the second feed-forward MatMul (GELU outliers under per-tensor uint8). A mixed-precision candidate was
  chosen on validation only under a rule fixed before its numbers were read (Addendum 1); post-hoc candidates are labelled so.
  Chosen: validation agreement 0.9842 (torch dynamic: 0.9000), test 0.9794.
- Quality at batch 1 vs the torch numbers (different model, thresholds refit): coverage higher on all four tasks; precision about
  equal, urgency slightly lower (still above the 0.95 target). Intervals in the report.
- Container at 1 vCPU / 1 GiB: no OOM, peak RSS about 0.9 GB; cold start 7.5 s (torch 21.3 s); batch 1 p50 137 ms / p95 246 ms
  (torch 803 / 1200 ms); image 1.38 GB on disk (torch 2.21 GB). It fits a 1 GiB container but with about 120 MB headroom:
  co-hosting in the existing `review-iq` service is not advisable; a separate service (1Gi minimum, 2Gi comfortable).
- A defect found and fixed on the way: ONNX Runtime sized its thread pool from the host's cores, not the CPU quota (over a second
  per review in a 1 vCPU container); the backend now reads the cgroup quota.
- BELIEVED / not measured: Cloud Run cold start with a real image pull, CPU throttling, int8 kernel differences on other CPUs.

Below is the S24 torch-variant record, kept as history.

Flag `ENABLE_V2_CLASSIFY`, default off (404 before auth). Outputs needs_action, broad intent, sentiment and urgency
with per-class thresholds and an explicit `abstained` (urgency high is chosen by its validation-fitted threshold,
never argmax). S24 variant: run 2, `int8_ffn_full` (no doc designated one; chosen by best agreement with the fp32
CPU model under the served decision rule, a margin that is within noise). Run 3 is not served. Numbers are in
`reports/serving/v2_classify_bench.{md,json}` on the PR branch (code commit 8953ecf), all on a loaded workstation in
local Docker with 1 vCPU, NOT on Cloud Run:

- Memory: OOM-killed at 1 GiB on the first request (twice); runs at 2 GiB, peak RSS 1559 MB, cgroup peak 1087 MB.
  It needs its own service with at least 2 GiB. VERIFIED locally; which Cloud Run CPU and memory pairs are offered is
  BELIEVED.
- Cold start 21.3 s, of which model load 16.3 s. Batch 1: p50 803 ms, p95 1200 ms (n 757). Image 2.21 GB on disk.
- Parity with the offline evaluation: run in batches of 128 in pred-file order, the served code reproduces all 3028
  sealed decisions with 0 mismatches. Served one review per forward (what the endpoint does), 166 of 3028 differ, all
  answer-versus-abstain, 0 class flips, 0 urgency-high flips. Cause: torch dynamic int8 quantises activations per batch
  tensor, so a logit depends on its batch-mates. Bit-for-bit per-review parity is therefore NOT achievable; thresholds
  are bit-for-bit equal to a recomputation from the offline artifacts. In the container, 164 differ, a different set
  by 14 borderline items (torch builds differ).
- Gates: size fails (1532 hand-written plus 2214 generated lines) and the new Dockerfile is arguably gate 4; the PR body
  proposes a split. Unblocks: GG chooses the weights delivery (bake-in or bucket with a checksum pin), a separate
  service at 2 GiB or more, and adds the requirements to the lock.

## Capacity is product-wide: free inference serves about two Starter customers at best (S28)

Free inference is ONE ceiling for the whole business, not a per-customer allowance. `eval/results/capacity_model.json` says so itself: the
single production Groq key's per-model pools (200K tokens/day each, 8K TPM; the 120b pool binds) are shared by every consumer: customer
extraction, CSV ingest, the public demo, and any live eval or CI call. Tokens per extraction are measured (below), so the figures are the TOTAL
for all customers together:

| | extractions/day | extractions/month (30 d) | Starter customers at full quota (5,000/month) |
|---|---|---|---|
| hard limit, no router (today) | 140.6 | 4,217 | 0.8 |
| 100K working budget, no router | 70.3 | 2,109 | 0.4 |
| hard limit, encoder-first R1 (open rate 0.1, multiplier 2.88; NOT enabled) | 404.8 | 12,143 | 2.4 (about 2) |
| 100K budget, R1 | 202.4 | 6,071 | 1.2 |

VERIFIED arithmetic on `capacity_model.json` and the measured routing share (`reports/intent/encoder_first/` on #559); the open rate is an
ASSUMPTION. So today the whole business can extract 4,217 reviews a month on free inference, less than one Starter customer; with the router
(not enabled: Amendment 15 now meets its criteria, but persistence of skipped reviews and the on-open path do not exist) about two. **Growth (25,000/month) is not deliverable on free inference**: one
Growth customer needs 5.9 times today's hard limit, and 2.06 times the router's entire total.

Paid Groq (pay per token), at MEASURED tokens per review: 106 held-out fixtures replayed from cassettes at $0
(`eval/results/token_cost_measurement_n106.json`, `docs/cost-model.md` section 1): gpt-oss-20b (40.6 percent of traffic) 1,751 tokens in and 774
out, gpt-oss-120b (59.4 percent) 1,746 in and 647 out. At the verified unit prices in `app/core/pricing.py` (20b $0.075 in / $0.30 out per M;
120b $0.15 / $0.60; as of 2026-09-10) that is $0.000363 and $0.000650 per extraction, blended $0.000534 (recomputed this session). Per customer
at full quota: **Starter 5,000 reviews = $2.67 a month; Growth 25,000 = $13.35 a month.** With R1 at an open rate of 0.1 only 34.75 percent of
reviews are extracted: Starter $0.93, Growth $4.64, plus the encoder service at about $0.0044 per 1,000 reviews (BELIEVED Cloud Run prices):
$0.02 and $0.11. The same $0.000534 sits behind the margins in `docs/cost-model.md`. S29 CORRECTION: the S28 text here quoted Starter $59 and Growth $99 from a superseded
table in section 4 of that file; the source of truth `docs/pricing.json` and the live site say Starter $29 (INR 1,499), Growth $79 (INR 4,999), Scale $199
(INR 12,999), so the full-quota cost is 9.2, 16.9 and 26.8 percent of the price (India: 17.0, 25.6, 39.3). NOT verified: Groq's paid-tier rate
limits (TPM, requests) at these volumes, any price change after 2026-09-10, and that 106 fixtures represent the production stream. The public
pricing page and prices are unchanged and are GG's decision.

**Paid Groq from the first payment (GG decision, S29; ADR 0041 and `ops/runbooks/groq-paid-tier.md`, #574; nothing activated).** Free tiers until the first
paying customer; from their first payment their inference runs on paid Groq (Developer plan on the dedicated organisation), funded by the subscription.
VERIFIED from Groq's own pages (2026-10-20): token prices equal `app/core/pricing.py`; the upgrade needs a payment method and bills monthly in arrears
with early charges at $1, $10, $100, $500, $1,000 (India: $1, $10, then each $100); a hard monthly spend limit exists and, at the limit, EVERY key in the
organisation gets `400 blocked_api_access`. NOT verified: the Developer plan's per-model rate limits (the public page shows one unlabelled table, 30 RPM
/ 1K RPD / 8K TPM / 200K TPD, that equals what ADR 0037 called free; if it is also the paid limit, one Starter customer at quota needs about 237K
tokens a day on 120b, over its 200K pool) and a cached-input price (not listed). Per-customer cost alert at 20 percent of plan price: #573, flag OFF; at
FULL quota Growth India, Scale and Scale India are above that line by the price file's own numbers, at its typical 40 percent utilisation none is.

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

S24 closure of the two follow-ups (2026-10-17): the three trigger tokens were rotated and moved to Secret Manager
(A4, see the production table), and Secret Manager DATA_READ audit logging was enabled (A5). The admin-password
incident itself is closed; the remaining items below are open for GG.

Open: (1) NOT checkable: 2026-08-13 to 2026-09-12 and any secret read before 2026-10-17 (Secret Manager data-access
audit logging started in S24). (2) DONE in S24: trigger tokens rotated. (3) Doc correction #512, rotation script #508, runbook wording #485: open, gate 4,
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
| Parked PRs (GG merges; S24 merged #505 to #507, #513, #514, #521, #525 to #527, #529, #530, #532) | #485 (runbook wording), #499 (DB latency report and region plan), #508 (admin rotation script), #512 (admin doc correction), #472 (read-only role), #502 (retire `uptime-alert.yml`; waits on GG's `s23-alert-test` email confirmation), claude-config #49 and #50 (billing-blocked CI; only GG merges), #545 and #546 (private classify image and deploy script, drafts, deploy DEFERRED), #559 (encoder-first, draft, verdict do not enable), #298 (reply prompt v2.2: no eval run, and its tone examples contradict its own rule 7; #537 was a duplicate of it plus cassettes and is closed, rebased branch `fix/s26-reply-placeholders`). #515 was merged by GG. S25 merged #535, #536, #538, and the S25 docs PR; #531 closed as superseded. S26 merged #541, #544, #547 to #551 (the #533 split), #542 (Amendment 13) and this docs PR; #533 and #537 closed. #318 (U4d twin control, parked) gained one commit with progress that had been left uncommitted in a worktree |
| Local branch hygiene (S26, S27, S29) | S29: 254 -> 221 local branches. Every branch tip was first written to ONE private bundle `D:/ml-cache/archive/review-iq-local-branches.bundle` (verified; all 254 tips equal the bundle's heads, list in `review-iq-local-branches.tips.txt`), then 33 branches whose whole net diff reverse-applies to current main (work already merged) were deleted (tips in `D:/ml-cache/archive/s29_deleted_branch_tips.txt`). The other 190 with unmerged work were kept, unjudged. The dropped food-delivery set is NOT pushed anywhere (see GG list). Earlier: (S27: 249 local branches after deleting the merged `feat/s27-classify-private`; tips recorded in `D:/ml-cache/archive/s27_deleted_branch_tips.txt`). S26: Local branches 502 -> 248: deleted 259 + 7 that were fully merged (154 by ancestry, 105 + 7 squash-merged with the branch tip equal to the merged PR's head commit, checked against `gh pr list --state merged`). Kept: 3 `archive/s25-*`, 26 with an open PR, 3 checked out, and 216 holding unmerged work (not judged). The record of the first 259 tips was overwritten by a second run of the same script (my mistake); they are all merged, so each is recoverable from its PR head on GitHub. The stray `review-iq-wt-c2-before_held_out.json` (a 2026-09-12 held-out eval output, outside any repo) was MOVED, not deleted, to `D:/ml-cache/archive/` |
| Docker build cache (S28) | Pruned with `docker builder prune --filter until=168h` (only cache unused for a week): build cache 28.85 GB -> 23.62 GB, 5.23 GB reclaimed (VERIFIED, `docker system df`); 13.35 GB of the rest is still reported reclaimable but was used within a week, so it was left for the other sessions | VERIFIED |
| Artifact Registry (S27) | CLEANED. Before: unique layer bytes 4,216.7 MB (reconstructed from the registry manifests; equals the registry's own 3,742.6 + 474.3), 74 + 3 images, billed above the 0.5 GB free tier: about 37 cents a month (BELIEVED price, $0.10 per GB-month). 69 images in `cloud-run-source-deploy` deleted: each referenced by no serving revision or job and not among the 3 newest per image (re-checked against a fresh inventory right before deleting; the list is `D:/ml-cache/s27/ar_deleted_s27.jsonl`, local only). Kept there: 5 images, 311.2 MB. `gcf-artifacts` (474.3 MB, the gen1 billing-kill-switch function builds) left untouched on purpose. After: 785.6 MB, about 3 cents a month. `review-iq` stayed healthy (`/health` 200, revision 00092). The registry's own size field still shows the old figure (it lags). Policy: #555 merged `ops/artifact-registry-cleanup.json` (keep tags `keep-*`, keep the 3 newest per image, delete older than 30 days) and it was applied to `cloud-run-source-deploy` in DRY-RUN; the failover-probe job image carries the tag `keep-failover-probe`. TO DO on or after 2026-10-25 (one week of dry-run; NOT yet due in S28 or S29, 2026-10-20; S29 added the 481.5 MB classify image, repository now 914.9 MB): check the dry-run decisions for a wrongly selected digest, then switch to enforce (`--no-dry-run`). I could not find the dry-run entries in Admin Activity audit logs (searched cleanup-policy methods, last day, nothing), so where they are logged must be confirmed first. A serving revision rolled back to an image older than the 3 newest needs a `keep-` tag before enforcement | VERIFIED (gcloud, manifests); enforcement NOT done |
| Moat comparison, 120b arm | A valid Groq benchmark key in an org separate from the failover pool, or another provider |
| `/v2/classify` deployment | DEPLOYED privately in S29 (see Served model). Open: merge #571 (gate 4, deploy script and runbook); the int8 numerics decision; the 10 s client timeout vs a 15.2 s cold start. Rollback: `gcloud run services delete review-iq-classify`. Nothing calls it |
| Encoder-first prerequisites (S29, all flag OFF) | Migration `classified_reviews` #576 (gate 4, NOT applied; rehearsed on a restored backup: applied as `review_iq_migrator`, 4 postconditions PASS, 4 undo cases PASS to FAIL, no table lost rows; workflow run `39331316224` on the throwaway #575, closed). Persist side #578, on-open path #579 (stacked on #578). The rehearsal workflow itself is still the draft #396: merging it makes every migration PR rehearse automatically. The router also needs the numerics decision and a product decision |
| Cost alert and paid Groq (S29) | ADR 0041 and runbook #574; cost alert code #573 (flag OFF, needs `ORG_PLANS_JSON` at first sale because the app holds no subscription data). Nothing to do until the first sale |
| Error analysis of the router misses (S29) | #570 (analysis report, descriptive); the pre-registered Amendment 16 is a proposal in the encoder-first bullet, not started |
| DB region (Sydney pooler, Cloud Run in Mumbai) | GG decides among options A to D in #499; Mumbai move is a plan only, about 20 to 30 min write freeze (BELIEVED), the backup lacks `auth.users` |
| Engine deployment | Waits for the review-intent model |
| Tailwind 4 (#106) | Plan in `docs/ops/tailwind4-migration-plan.md`, before the braces allowlist expires 2026-12-31 |

## GG must do

S29, in unblock order:

1. **Dropped food-delivery data is still reachable on GitHub.** Its ids and labels were in the closed PRs #486 and #487 since 2026-10-12 (GitHub keeps
   `refs/pull/N/head` for a closed PR even after the branch is deleted, so deleting the S29 archive branches did not remove them). Only GitHub Support can
   purge that (contact support, ask to remove the cached views and pull refs of #486 and #487). Nothing of it is on main; a bundle is kept privately.
2. Merge #571 (gate 4; the classify service is already live) and, when you want to move toward encoder-first, #576 (migration, gate 4) and #396 (rehearsal).
3. Decide the int8 numerics reference (see Served model) before the router can be enabled; the router stays OFF.
4. At the first sale follow `ops/runbooks/groq-paid-tier.md` (Groq console steps, spend limit, read and record the real rate limits).
5. On or after 2026-10-25: #555 enforcement check (pending).

Regenerated each session (S23 list, from the orchestrator record):

In unblock order (S24; trigger-token rotation and Secret Manager audit logs are done and no longer on this list):

1. Confirm the `s23-alert-test` email arrived; then #502 retires `uptime-alert.yml`.
2. Merge the gate-4 PRs: #485, #508, #512, #499, then claude-config #49 and #50.
3. #472 read-only role steps, then remove `SUPABASE_DIRECT_URL` from GitHub secrets.
4. Provide a valid Groq benchmark key in an org separate from the failover pool (or choose another provider) to run the 120b arm.
5. Decide the DB region move (#499); confirm in the Groq console which org each key is in.
6. Reply prompt: #298 needs its two tone examples fixed and a reply eval run (shared Groq quota) before it can merge. `/v2/classify` deploy is DEFERRED (nothing to decide now).
7. Decide on a persisted shared demo/prod token ledger (needs an RLS policy change, gate 4).
8. Check the Console cost view (no billing export exists) and the new audit-log volume after a month.
9. PID 15252 (a `python -` spinner, not Claude's) was no longer running when checked in S24; nothing to do unless it returns.
