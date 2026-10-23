# S31 autonomous run — progress

Spec: `docs/specs/s31-autonomous-run.md`. Window: 2026-10-23 → 2026-10-27 08:00 IST (02:30 UTC).
All timestamps UTC. VERIFIED = observed this session with the command or artifact named; BELIEVED = not re-observed.

## Current task

P1 waits on panel session 2; P2 merging the stack one strict-mode CI round at a time (#585 next); P7's LLM comparator on Block A running on Kaggle (outputs not opened until P7).

## Queue

| ID | Status | Evidence | Updated (UTC) |
|---|---|---|---|
| P0 | done | snapshot below; spec + this file on `autonomy/s31-progress` | 2026-10-22 21:03 |
| P1 | in progress — panel session 2 running | session 1: 4,600–4,800 / 6,300 per judge, `budget_hit` true, 36,599 s; `panel_pin` → "recorded and correct". Session 2 = kernel version 2 (~21:08) with `--prior …-panel-out`. When done: `panel_pin` on BOTH status files (session-digest equality, added in #592) | 2026-10-22 21:08 |
| P2 | in progress | MERGED: #584 `b3cabc3`, #598 `01c16f6`, #585 `d191b4d3b527a5022f7839da30d9ab39e704a33a` (01:20), #587 (02:13). #591 updated, CI running. #592 retargeted to main after #587's squash (merged main into it; empty commit at 02:58 to trigger the required `diff-scan`/`bypassrls-check`, which do not fire on a base change). #578 and #579 brought up to main (merge commits `cd7ec1d`, `2ed56fe`; conflict in `encoder_first.py` resolved: #578's `plan_ingest` keeps the 60 s ingest timeout), still PARKED. #604 opened: S30 equivalence tooling that never reached main | 2026-10-23 03:00 |
| P3 | **done** (target 0 not met → PARKED 1) | P3 section; report PR #603 | 2026-10-22 23:00 |
| P4 | **done** | 128 pass (p95 355 ms), 256 FAIL (1,107 ms), 512 FAIL (2,137 ms); surviving arms **H128, HT128**; probe service deleted 22:53 and verified absent; report PR #603 | 2026-10-22 22:54 |
| P5 | pending (needs P1 labels) | arms H128, HT128 only | |
| P6–P8 | pending | | |
| P7 (LLM part) | outputs stored, unopened | `review-intent-fresh16-llm-run` v1 COMPLETE: 1,800/1,800 rows, Ollama 0.40.6 sha256 matched, gpt-oss:20b digest `f38aa0c53da5…6fdfe`, clone `01c16f6`. Private: `D:/ml-cache/s31/llm_a_out` | 2026-10-23 00:05 |
| P7 (run-2 scores) | outputs stored, unopened | `review-intent-fresh16-run2-score` v1 (CPU kernel from #604's tooling) COMPLETE: 3,900 items, run-2 model sha `33158169…`, pinned + unpinned logits, Kaggle Xeon 2.2 GHz AVX2 only, 932 s. Private: `D:/ml-cache/s31/run2_f16_out` | 2026-10-23 00:05 |
| B5 | done (on #593) | sealed fingerprints covered 3,483 of 7,383 sealed hashes (no fresh16): rebuilt + new ledger check (e) (`b2b696d`). Kaggle CPU check at `b2b696d`: **ok** (no fresh16 near-duplicate or quote in tracked files), 5,585 s. That runtime would have doubled CI's lint-and-test → lossless candidate filters (`perf(labelling)` on #593): same result in 2 min 34 s, brute-force equality test added. The original B5 item (quote detection) already existed since S23 (12-gram containment ≥ 0.9; gaps: sealed items < 40 normalised chars (73 of 7,383 pre-fresh16 count: 6 < 20, 67 in 20–39), quotes split across non-adjacent lines) | 2026-10-23 02:12 |
| S1 | scheduled ≥ 2026-10-25 00:00 | | |
| S2 | done for 10-22 | 22:10: `/health` 200 1.21 s; classify private; failover-probe daily 02:30 OK; 24 h `llm.rate_limited` 0, `llm.failover` 0, `db_timeout` 0, review-iq ERROR logs 0; open Monitoring incidents NOT readable (gcloud alpha component absent; installing it would modify the system SDK) | 2026-10-22 22:10 |
| S3 | daily 03:30 | first 2026-10-23 03:30 | |
| B1 | **done → #298 PARKED** | PRs #599 (prereg + results), #600 (harness), #601 (judge kernel). c1 met (3/20 → 0/20), c2 unestablished (judge panel saturated) | 2026-10-22 22:35 |
| B2 | in progress | PR #602 (+ tests in #585, #592) | 2026-10-22 22:25 |
| B3 | in progress | #598 merged | 2026-10-22 23:05 |

## P3 — classifier redeploy on pinned numerics (Tier B) — VERIFIED

- Rollback tag: `keep-classify-prev` → `review-iq-classify@sha256:b6d3ef10…2885e` (S29 image).
- `CONFIRM_REGISTRY_COST=yes bash ops/deploy/deploy-classify.sh D:/ml-cache/s25/classify_artifacts_onnx` from a detached checkout of `origin/main` `b3cabc3`, first end-to-end execution: guards passed; Cloud Build `c00a15cd-71b4-400a-8840-cd645da9ba73` SUCCESS in 2 min 6 s; source upload deleted; image `review-iq-classify:b3cabc36d2b8` `sha256:69618259…37eb6`; revision **`review-iq-classify-00002-zh5`** 100% traffic; script's own assertion: unauthenticated POST 403.
- **Live parity, 757 sealed reviews, one per request:** 0 errors, thresholds sha matches, **5 decision mismatches of 3,028** (S29 unpinned image: 81). All 5 are abstention-boundary flips, never a class-for-class swap:

  | id | task | served | offline | abstain_below | served max p | offline max p |
  |---|---|---|---|---|---|---|
  | scale-apparel-0537 | broad_intent | abstained | praise | 0.84719 | 0.84715 | 0.84860 |
  | scale-apparel-0988 | sentiment | abstained | positive | 0.80031 | 0.77214 | 0.83643 |
  | scale-food-0660 | broad_intent | product_issue | abstained | 0.84719 | 0.86874 | 0.82980 |
  | scale-food-1357 | needs_action | no | abstained | 0.88440 | 0.93425 | 0.86668 |
  | scale-food-1357 | sentiment | positive | abstained | 0.80031 | 0.87455 | 0.76694 |

- **Probability difference served vs offline (pinned laptop reference), per task over 757:** bit-identical on 658–662 reviews (87%); p95 of |Δp| 0.0008–0.0016; max 0.194–0.312 (needs_action 0.312). So the pin makes laptop = Kaggle exact (S30) but NOT Cloud Run exact for ~13% of inputs. CPU model of Cloud Run not obtainable without a debug endpoint (would need a code change + deploy). → PARKED item 2.
- Client latency (laptop → asia-south1, includes network): p50 277.7 ms, p95 545.7 ms, max 921.6 ms.
- Server side, Cloud Monitoring, 21:50–22:05, per-minute: p50 150–208 ms, p95 311–456 ms, p99 ≤ 627 ms; memory utilisation p99 0.62 of 2 GiB; max 2 instances.
- Access re-check (22:06): IAM policy has exactly one binding (`roles/run.invoker` → `serviceAccount:review-iq-runner@…`), no allUsers/allAuthenticatedUsers; unauthenticated POST → 403.
- Registry after the push: `cloud-run-source-deploy` 1,529.9 MB (was 988.0 at P0) + `gcf-artifacts` 452.4 MB = 1,982 MB. The new classify image did not share its model layer with the old one (each ~528 MB); keeping both (`keep-classify-prev`) is what the spec requires. Projected: (1.98 − 0.5 free) GB × ~$0.10/GB-month ≈ **$0.15/month total, BELIEVED rate**; about $0.10 of it is the two classifier images. S1 cleanup (≥ 10-25) removes untagged API images.
- Evidence (private): `D:/ml-cache/s31/p3/{live_parity,live_prob_diff,monitoring_parity_window}.json`.
- Cold starts (≥ 15 min idle, service at 0 instances): **12.0 s, 17.3 s, 13.8 s**; warm p50 211 / 124 / 117 ms. All < 60 s ingest timeout.
- Report: `reports/serving/classify_redeploy_s31.{md,json}` (the repo keeps serving reports in `reports/serving/`, not `docs/reports/serving/`), PR #603.

## B1 — reply prompt v2.2 (#298)

- Fixed the tone examples that modelled what rule 7 forbids (`yeh hamare end pe galti thi`, `hum zimmedaar hain`, `… is matter ko fix karenge`, `Hum isko theek karenge`) → acknowledgement + invite-follow-up wording. The probe showed 3 of 5 old phrases also slipped past `check_no_fabrication`; added patterns + tests (75 reply tests pass). #298 rebased on main `b3cabc3`, force-pushed with lease (CC branch, Tier A).
- Pre-registration committed BEFORE any call: `docs/research/reply-v22-eval-preregistration.md` at `0cc5d9aa86169c33655dda8ef4ed4194f57afb26` (branch `docs/s31-reply-v22-prereg`). Harness `eval/experiments/reply_v22_eval.py`, kernel `engine/kaggle/kernel_reply_judge.py` on the same branch.
- Sample check: the runner's draw equals the 20 N4c ids (VERIFIED).
- Drafts: v21 from `b3cabc3`+harness, v22 from #298 rebased; 80/80 ok; failover org only.
- Judges: Kaggle kernel `review-iq-reply-v22-judge-run` v1 (qwen3:8b, gemma2:9b, llama3.1:8b; pinned 0.40.6), dataset `review-iq-reply-v22-judge` (private).

## B2 — control audit (mutation tests)

| Control | Mutations | Result before | Fix |
|---|---|---|---|
| numerics pin | unpinned call site; value "0"; helper sets nothing | 2 killed; **helper-sets-nothing SURVIVED in CI** (onnxruntime not a project dep, both behavioural tests `importorskip`) | fake `SessionOptions` test → killed |
| cost alert | `>`→`>=`; ×2 threshold; sweep ignores fraction; default 0.20→0.5; unknown-plan log dropped; one org failure aborts | **4 of 6 SURVIVED** (5.80/29 is below 0.2 in floating point, so the "boundary" test never hit equality) | 4 tests → 6/6 killed |
| gitleaks | random never-issued `ghp_` token planted in a scratch dir | fires ("leaks found: 1"), as S23's CI proof | none needed |

Remaining: ingest-vs-user timeout split (after #585), leakage near-dup + panel-pin (after #592), migration rehearsal (#396 parked → skip).

## Decisions log (Tier B and judgment calls)

- ~21:05 — **Merging #584 triggered the repo's standard `Deploy to Cloud Run` workflow** (run 39854354767, success): API `review-iq-00099-7z6` at `b3cabc3`. Classify-path code only; router OFF so API behaviour unchanged; `/health` 200. Every merge to main redeploys the API.
- ~21:08 — Panel resume via a new private dataset `review-intent-fresh16-panel-out` (session-1 judges, ids + labels) as `--prior`; kernel skips done ids.
- ~21:10 — Tier B: `keep-classify-prev` tag (spec P3).
- 21:45 — Tier B: P3 deploy started although main's CI run had `npm-audit` red: that job fails on six vite dev-server advisories published 10-20/21 (web/ only, fixed by #598); `lint-and-test`, `web-build`, `pip-audit`, `manifest-provenance` were green. The classify image does not contain web/.
- 21:58 — Live parity 5 ≠ 0 → per spec: recorded, no rollback (nothing calls the service), parked.
- 22:42–22:53 — Tier B: throwaway private probe service `review-iq-classify-probe` (same image digest `69618259…`, 1 vCPU, 2 GiB, min 0, max 1, no IAM bindings, unauthenticated 403), env `CLASSIFY_MAX_LEN` 128 → 256 → 512, then deleted and the service list re-checked.
- 22:44 — The B1 result JSON was moved from `eval/results/` to `reports/research/` before opening PRs: `eval/results/*.json` is not a designated generated path, so a 2,759-line data file would have failed gate 3; `reports/` is designated. B1 split into three PRs (doc+results, harness, kernel), each ≤ 400 reviewable lines. Pre-registration provenance stays at `0cc5d9a` (branch `docs/s31-reply-v22-prereg`, pushed, not merged).
- 23:10 — P7's LLM comparator started early (only needs Block A texts, not labels; a Kaggle slot was free). Its outputs stay unopened until the registered P7 evaluation.
- 22:03 — B1 drafting used the failover org through the pre-registered ledger: 120b 59,294 tokens, 20b 61,091 tokens (both < 90K stop, < 100K cap).

## Deviations

- None to registered criteria.
- **P4 protocol clarification (Amendment 16 probe):** the probe sends 3 unmeasured warm-up requests and starts the measured window at the next whole minute (commit on #591 before any probe ran), so a 12–17 s scale-from-zero cold start cannot become a minute's p95. Criterion, statistic (max over minutes of the per-minute p95) and the 50 measured requests unchanged. H256 fails on both minute buckets' combination (1,107 / 874 ms) and on client p95 (1,181 ms), so the drop does not hinge on this choice.
- **B1:** pre-registered rule formally PASSES but #298 not merged: the 8B judge panel did not apply the rubric (generic thanks flagged; 18–20/20 hard failures in every arm), so c2 "not worse" is unestablished. Treated as a broken measurement (CLAUDE.md 101c), reported in #599.
- Operational: progress timestamps written ~15 min ahead at first (corrected).

## PARKED

1. **Cloud Run numerics parity** (P3; unblock value: high for Amendment 16 deployment, nil today because nothing calls the service). 5/3,028 decisions differ, all at the abstention boundary; 13% of reviews differ in probability (max 0.31). Steps for GG:
   1. Decide whether exact parity is required, or whether "≤ 5/3,028 boundary flips" is acceptable for the private service.
   2. If exactness is required: approve a debug endpoint (or a one-off Cloud Run job) that reports `/proc/cpuinfo` flags and per-op ORT EP info, so the differing kernel can be found; candidate fixes are `session.intra_op` single-thread determinism checks or a float32 (non-int8) model for the boundary band.
2. **#298 reply prompt v2.2** (medium value; reply drafting is OFF). Evidence #599. Steps for GG:
   1. Read #599's results: invented contacts 3/20 → 0/20 is solid; "not worse" is not established (8B judges saturated).
   2. Either accept c1 alone and merge #298 (it rebases cleanly on main as of 22:00), or let a later session pre-register a judge calibration gate + a larger non-gpt-oss panel and re-judge the SAME 80 drafts (no new Groq spend; ~0.5 Kaggle GPU-h).
3. **Primary checkout cleanup** (low value, 1 min). Session confined to its worktree. In `C:\Users\gaura\ml-projects\review-iq`:
   1. `git restore web/package.json web/package-lock.json` (identical change is in #598).
   2. `git switch main && git pull --ff-only`.
   3. `git branch -D fix/s31-vite-advisories` (local only, never pushed).

## Resource ledger

| Item | Amount | Note |
|---|---|---|
| Kaggle GPU h (S30 carry-over) | 10.17 | panel session 1 (T4×2), 36,599 s |
| Kaggle GPU h (S31) | 0.28 + 0.79 + running | reply judge 1,003 s (T4); LLM comparator 2,834 s (T4×2); panel session 2 (~21:08–, running) |
| Cloud Run probe service | created 22:42, deleted 22:53 (verified) | 3 revisions (128/256/512), max 1 instance, ~11 min |
| Groq eval tokens, failover org, trailing 24 h | 120b 59,294; 20b 61,091 | B1 drafts, ledger `D:/ml-cache/s31/reply/ledger.json` |
| Groq primary org | 0 eval tokens | |
| Cloud Build | 1 build, 2 min 6 s | free tier |
| Cloud Run | classify revision 00002 deployed | min 0 / max 2 |
| Artifact Registry | +541.9 MB (classify image b3cabc3 + CI API image) | ≈ $0.15/month total, BELIEVED |

## Incidents

- 21:14 — **Session working directory moved to `web/`** after `cd web && npm …` in one Bash call. The repo's PreToolUse hook runs `python scripts/hook_guard_secrets.py` by relative path, so every tool call was refused. Not routed around: entered ONE linked worktree via the harness (`.claude/worktrees/review-iq-wt-s31`), where the hook path resolves. All further work happens there; the harness refuses `git -C` into the primary checkout. Lesson: never `cd` in a Bash call.
- 01:15 — **Laptop CPU fully loaded by other sessions**: ten `claude` processes and two `python … scripts/g15_power_a7.py --stage rq4` shards (another project). Not touched. The merge hook's gate and even the secrets hook timed out (blocked as designed); #585 merged on a later retry. 03:00: load still about three quarters. Overheating risk is GG's to manage; nothing of this session runs locally beyond short scripts.
- 23:13–00:24 — **Laptop CPU:** the local `leakage_ledger check` (repo-wide near-duplicate scan, now 7,383 fingerprints) ran 70 min at close to one full core. Stopped by its PID (37944, command line verified as mine) and moved to a Kaggle CPU kernel. The merge hook timed out twice during that window (blocked as designed; not touched).
- 23:58 — **Another actor merged #518** (Dependabot setup-node) while #585 was gate-eligible; #585/#587/#591 went BEHIND again and were updated at 00:26. Concurrent merges to main cost one CI round (~45 min) each time.
- 22:01 — **Another actor merged #589** (as gaurav-gandhi-2411) while this session ran; a concurrent session may be active in the primary checkout. Not touched; merges here re-check BEHIND state each time.

## Daily checkpoints

### 2026-10-23 03:30 UTC (day 1)

- **Done:** P0, P3 (redeploy; parity 5/3,028 → PARKED), P4 (H128, HT128 survive), B1 (#298 parked), B5 (fingerprint gap fixed on #593), S2 for 10-22. P7's LLM comparator and run-2 fresh16 scores stored, unopened.
- **Merged:** #584, #598, #585, #587. **Open (mine):** #591, #592, #593, #594–#597, #599–#604.
- **Waiting:** panel session 2 (since ~21:08, 10 h cap ~07:08) → P1 labels → P5 training.
- **Disk:** C: 239 GB free (228 at P0; other sessions freed space), D: 1.4 TB free. One extra worktree `.claude/worktrees/review-iq-wt-s31` (see Incidents).
- **Laptop:** load about three quarters at 03:00, from other sessions (see Incidents); none of this session's processes are heavy.
- **Kaggle GPU (S31):** reply judge 0.28 h; LLM comparator (T4×2) — see ledger; panel session 2 running. CPU kernels (run-2 scoring 932 s, ledger check 5,585 s) use no GPU quota.
