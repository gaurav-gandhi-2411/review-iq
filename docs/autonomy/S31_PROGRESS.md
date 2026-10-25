# S31 autonomous run — progress

Spec: `docs/specs/s31-autonomous-run.md`. Window: 2026-10-23 → 2026-10-27 08:00 IST (02:30 UTC).
All timestamps UTC. VERIFIED = observed this session with the command or artifact named; BELIEVED = not re-observed.

## Current task

P1: panel session 3 running since 2026-10-24 00:02 (kernel version 3, cloned from main with #609's fail-closed resume; still RUNNING after 8 min, so the prior files were found). Next: labels, size rule, then P5. **Kaggle quota reset VERIFIED at Saturday 00:00 UTC** (refused 23:00-ish retries, accepted 00:02).

## Queue

| ID | Status | Evidence | Updated (UTC) |
|---|---|---|---|
| P0 | done | snapshot below | 2026-10-22 21:03 |
| P1 | running — session 3 (pushed 10-24 00:02) | Session 1: 4,600 (llama, gemma) / 4,800 (qwen, mistral) of 6,300, 36,599 s. Session 2 (36,892 s) **re-did session 1's items** instead of resuming (prior dataset mounted flat; see Incidents) — 18,000 overlapping verdicts identical (determinism VERIFIED). `panel_pin` on both status files: "recorded and correct" (same build, same 4 digests). Remaining: 1,700 / 1,700 / 1,500 / 1,500 items. Session 3 needs the #609 kernel and ~4 GPU-h | 2026-10-23 07:30 |
| P2 | **done** | MERGED (squash): #584, #598, #585, #587, #591, #592, #593, #594, #595, #596, #597 — the whole stack, plus #604 (S30 equivalence tooling found unmerged). #578/#579 kept CLEAN and PARKED | 2026-10-23 05:40 |
| P3 | **done** (target 0 not met → PARKED 1) | P3 section; report #603 merged | 2026-10-22 23:00 |
| P4 | **done** | 128 pass (p95 355 ms), 256 FAIL (1,107 ms), 512 FAIL (2,137 ms) → arms **H128, HT128**; probe service deleted and verified; #603 merged | 2026-10-22 22:54 |
| P5 | blocked (P1, GPU) | full dataset staged at `D:/ml-cache/s31/ds_run4_full` (needs `labels_train16.json`); kernel code now on main (#597) | |
| P6–P8 | pending | | |
| P7 (LLM part) | stored, unopened | `review-intent-fresh16-llm-run` v1: 1,800/1,800 rows, Ollama 0.40.6 sha256 matched, gpt-oss:20b digest `f38aa0c5…`; 2,834 s | 2026-10-23 00:05 |
| P7 (run-2 scores) | stored, unopened | `review-intent-fresh16-run2-score` (CPU): 3,900 items, model sha `33158169…`, Xeon 2.2 GHz AVX2; npz key is `pinned` (evaluator expects `logits`: rename only) | 2026-10-23 00:05 |
| S1 | scheduled ≥ 2026-10-25 00:00 | | |
| S2 | done 10-22, 10-23, 10-24 | 10-23 03:31: `/health` 200 1.12 s; classify private; failover-probe 10-23 02:30 OK; 24 h rate_limited/failover/db_timeout/errors all 0 ; 10-24 00:11: `/health` 200 1.21 s, API `review-iq-00102-fdw`, classify private, 24 h counts all 0 | 2026-10-24 00:11 |
| S3 | day 1 done | #608 merged (from `docs/s31-progress-day1`) | 2026-10-23 04:53 |
| B1 | done → #298 PARKED | #599/#600/#601 merged | 2026-10-23 06:35 |
| B2 | done | #602 merged; ingest-timeout test in #585, panel-pin hardening in #592 (both merged). Not audited: migration rehearsal (#396 parked) | 2026-10-23 06:10 |
| B3 | partly done | #598 merged (vite). Dependabot #523, #517 merged. **#568 deferred** to after P7 (bumps `tokenizers` 0.22.2 → 0.23.3 mid-evaluation and ruff to 0.17). #567 already closed. Majors (#516, #519, #520, #232, #233) and Tailwind 4 (#106): not started | 2026-10-23 07:00 |
| B5 | done (#593) | fingerprint coverage 3,483 → 7,383 + check (e); scan 93 min → 2.6 min | 2026-10-23 04:18 |
| B6 | done (#607 merged) | recorder IP redaction; fake on the official OpenAPI shape; visibility semantics → PARKED 4 | 2026-10-23 06:40 |
| B3 (cont.) | Tailwind 4 → **#610 DRAFT** (Tier C, GG signs off) | 4.3.3 via `@tailwindcss/postcss` + `@config`; compatibility CSS for v3 defaults; renames per guide; lockfile npm-audit 0 findings (retires the braces allowlist). CI-rendered before/after (new `ui-screenshots.yml`): 360 px dashboard pixel-identical, dark = light, 1280 px dashboard +9 px height, login input row and a 4 px band on /try differ. Majors → plan #611 | 2026-10-23 07:45 |
| #609 | merged | panel resume fail-closed fix on main (session 3 can clone main) | 2026-10-23 07:40 |
| B7 | done (local) | 19 local branches deleted after a separate check that each PR was MERGED with no local-only commits; remote branches untouched | 2026-10-23 07:50 |
| B4 | folded into the handoff STATUS update | README metric blocks are already render- and drift-checked in CI (`render_metrics.py --check` OK, `check_no_hardcoded_metrics.py` OK); STATUS's S29 classify row is stale and is rewritten at handoff | 2026-10-23 07:50 |
| B-last | needs GPU | | |

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
- 04:37 — Daily progress merges go through a `docs/s31-progress-dayN` branch cut from the live `autonomy/s31-progress` commit: the gate's rule-70a gate 1 does not list `autonomy/`, and the content is docs-only (a `docs/` branch is its natural scope; the live branch keeps the spec's name).
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

0. **GPU work until the Kaggle quota resets** (self-unparking; highest value). Blocked: weekly 30 h reached at 07:28 10-23. Next steps (this session, after the reset): push panel session 3 with `--branch fix/s31-panel-resume-fail-closed` (or main once #609 merges) and `--prior gauravgandhi2411/review-intent-fresh16-panel-out`; check `status.json` `prior_files` within minutes is impossible (no live log), so the fail-closed exit is the guard. Nothing for GG unless the reset does not happen by 2026-10-24 06:00 UTC.
1. **Cloud Run numerics parity** (P3; unblock value: high for Amendment 16 deployment, nil today because nothing calls the service). 5/3,028 decisions differ, all at the abstention boundary; 13% of reviews differ in probability (max 0.31). Steps for GG:
   1. Decide whether exact parity is required, or whether "≤ 5/3,028 boundary flips" is acceptable for the private service.
   2. If exactness is required: approve a debug endpoint (or a one-off Cloud Run job) that reports `/proc/cpuinfo` flags and per-op ORT EP info, so the differing kernel can be found; candidate fixes are `session.intra_op` single-thread determinism checks or a float32 (non-int8) model for the boundary band.
2. **#298 reply prompt v2.2** (medium value; reply drafting is OFF). Evidence #599. Steps for GG:
   1. Read #599's results: invented contacts 3/20 → 0/20 is solid; "not worse" is not established (8B judges saturated).
   2. Either accept c1 alone and merge #298 (it rebases cleanly on main as of 22:00), or let a later session pre-register a judge calibration gate + a larger non-gpt-oss panel and re-judge the SAME 80 drafts (no new Groq spend; ~0.5 Kaggle GPU-h).
4. **Judge.me visibility semantics** (low value now; connector flag OFF). Evidence #607. Judge.me's schema has no `published`; a review is on the storefront when `curated: "ok"` (`not-yet` depends on the store's auto-publish; `hidden` implies `spam`). The mapper counts anything not `hidden` as visible, so unpublished (`spam`) and pending (`not-yet`) reviews are ingested. Steps for GG: decide whether sellers should see unpublished/pending reviews (arguably yes: pending complaints are actionable); if not, a one-line mapper change (`visible = curated == "ok"` when present) plus a test.
3. **Primary checkout cleanup** (low value, 1 min). Session confined to its worktree. In `C:\Users\gaura\ml-projects\review-iq`:
   1. `git restore web/package.json web/package-lock.json` (identical change is in #598).
   2. `git switch main && git pull --ff-only`.
   3. `git branch -D fix/s31-vite-advisories` (local only, never pushed).

## Resource ledger

| Item | Amount | Note |
|---|---|---|
| Kaggle GPU h (S30 carry-over) | 10.17 | panel session 1 (T4×2), 36,599 s |
| Kaggle GPU h (S31, final for this week) | 0.28 + 0.79 + 10.25 = 11.32 | judge, comparator, panel session 2 (wasted, see Incidents). Week total with S30's 10.17 = 21.49 of this project; Kaggle refused at 30.00 (other projects' kernels share the account) |
| Kaggle GPU h (superseded row) | 0.28 + 0.79 + running | reply judge 1,003 s (T4); LLM comparator 2,834 s (T4×2); panel session 2 (~21:08–, running) |
| Cloud Run probe service | created 22:42, deleted 22:53 (verified) | 3 revisions (128/256/512), max 1 instance, ~11 min |
| Groq eval tokens, failover org, trailing 24 h | 120b 59,294; 20b 61,091 | B1 drafts, ledger `D:/ml-cache/s31/reply/ledger.json` |
| Groq primary org | 0 eval tokens | |
| Cloud Build | 1 build, 2 min 6 s | free tier |
| Cloud Run | classify revision 00002 deployed | min 0 / max 2 |
| Artifact Registry | +541.9 MB (classify image b3cabc3 + CI API image) | ≈ $0.15/month total, BELIEVED |

## Incidents

- 07:25 — **Panel session 2 redid session 1 (10.25 GPU-hours lost), then the weekly GPU quota ran out.** Cause (VERIFIED): the prior dataset was created with `--dir-mode zip`, so Kaggle stored `judges.zip` extracted FLAT (`kaggle datasets files` lists `gemma2_9b.jsonl` etc. at the root); `kernel_scale.py` looked only under `**/judges/*.jsonl`, matched nothing, and carried on from item 0 without a word. Session 2's ids were a subset of session 1's and every one of the 18,000 overlapping verdicts was identical. Why monitoring did not catch it: nothing checked that a resumed run actually resumed, and Kaggle exposes no log until a kernel ends. Fix (#609): name-based discovery + fail closed when `--prior` finds nothing + copied counts in `status.json`. Lesson: verify a resume within the first minutes, not at the end — for a kernel that means a cheap pre-flight that exits early.
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

### 2026-10-24 03:30 UTC (day 2)

- **Since day 1:** P2 complete (stack #584–#597 merged). Also merged: #599–#604, #607, #609, #611, Dependabot #517 and #523. #610 (Tailwind 4) is a DRAFT for GG. Panel session 2 redid session 1's items (see Incidents; fixed by #609). The Kaggle quota ran out at 07:28 on 10-23 and reset at 00:00 on 10-24; the reset time was BELIEVED and is now VERIFIED. Panel session 3 has been running since 00:02.
- **Next:** P1 labels and the size rule (session 3 should finish around 04:00) → P5 (H128, HT128) → P6 → P7.
- **Disk:** C: 231 GB free. One extra worktree `.claude/worktrees/review-iq-wt-s31`.
- **Laptop:** load about half (other sessions); none of my processes are heavy.
- **Kaggle GPU this week (new quota):** panel session 3 is running.
