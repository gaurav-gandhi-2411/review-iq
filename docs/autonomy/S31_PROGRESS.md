# S31 autonomous run — progress

Spec: `docs/specs/s31-autonomous-run.md`. Window: 2026-10-23 → 2026-10-27 08:00 IST (02:30 UTC).
All timestamps UTC. VERIFIED = observed this session with the command or artifact named; BELIEVED = not re-observed.

## Current task

P3 cold-start samples (need ≥ 15 min idle each); P2 merges serialised behind strict-mode CI; P1 panel session 2 and the B1 judge kernel running on Kaggle.

## Queue

| ID | Status | Evidence | Updated (UTC) |
|---|---|---|---|
| P0 | done | snapshot below; spec + this file on `autonomy/s31-progress` | 2026-10-22 21:03 |
| P1 | in progress — panel session 2 running | session 1: 4,600–4,800 / 6,300 per judge, `budget_hit` true, 36,599 s; `panel_pin` → "recorded and correct" (ollama 0.40.6, sha256 c164ab8b…c0e5e0, 4 model digests). Session 2 = kernel version 2 (~21:08) with `--prior gauravgandhi2411/review-intent-fresh16-panel-out` | 2026-10-22 21:08 |
| P2 | in progress | #584 MERGED `b3cabc36d2b83d3c4a65fc8470e6dc8c764b7a37`. #598 (vite) gate-eligible but went BEHIND when another actor merged #589 at 22:01 → update-branch, CI rerunning. #585/#587/#591 CI green except npm-audit (fixed by #598). Opened #594–#597 for the run-4 branches | 2026-10-22 22:05 |
| P3 | in progress — deployed, parity measured, cold starts pending | see P3 section | 2026-10-22 22:07 |
| P4 | pending (P3 image ready; needs #591 merged or run from its branch) | | |
| P5 | pending (needs P1, P4) | | |
| P6–P8 | pending | | |
| S1 | scheduled ≥ 2026-10-25 00:00 | | |
| S2 | daily | first run due 2026-10-23 | |
| S3 | daily 03:30 | first 2026-10-23 03:30 | |
| B1 | in progress — judges running on Kaggle | pre-registration `0cc5d9aa` (before any call); 80/80 drafts; kernel `review-iq-reply-v22-judge-run` v1 | 2026-10-22 22:04 |
| B2 | in progress | numerics pin + cost alert audited (below); branch `test/s31-b2-mutation` | 2026-10-22 21:40 |
| B3 | in progress | #598 vite 8.3.2 → 8.3.5 (six high advisories, published 10-20/21) | 2026-10-22 22:05 |

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
- Pending: 3 cold-start samples, then the report in `docs/reports/serving/`.

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
- 22:03 — B1 drafting used the failover org through the pre-registered ledger: 120b 59,294 tokens, 20b 61,091 tokens (both < 90K stop, < 100K cap).

## Deviations

- None to registered criteria. Operational: progress timestamps written ~15 min ahead at first (corrected).

## PARKED

1. **Cloud Run numerics parity** (P3; unblock value: high for Amendment 16 deployment, nil today because nothing calls the service). 5/3,028 decisions differ, all at the abstention boundary; 13% of reviews differ in probability (max 0.31). Steps for GG:
   1. Decide whether exact parity is required, or whether "≤ 5/3,028 boundary flips" is acceptable for the private service.
   2. If exactness is required: approve a debug endpoint (or a one-off Cloud Run job) that reports `/proc/cpuinfo` flags and per-op ORT EP info, so the differing kernel can be found; candidate fixes are `session.intra_op` single-thread determinism checks or a float32 (non-int8) model for the boundary band.
2. **Primary checkout cleanup** (low value, 1 min). Session confined to its worktree. In `C:\Users\gaura\ml-projects\review-iq`:
   1. `git restore web/package.json web/package-lock.json` (identical change is in #598).
   2. `git switch main && git pull --ff-only`.
   3. `git branch -D fix/s31-vite-advisories` (local only, never pushed).

## Resource ledger

| Item | Amount | Note |
|---|---|---|
| Kaggle GPU h (S30 carry-over) | 10.17 | panel session 1 (T4×2), 36,599 s |
| Kaggle GPU h (S31) | running | panel session 2 (~21:08–); reply judge (~22:04–) |
| Groq eval tokens, failover org, trailing 24 h | 120b 59,294; 20b 61,091 | B1 drafts, ledger `D:/ml-cache/s31/reply/ledger.json` |
| Groq primary org | 0 eval tokens | |
| Cloud Build | 1 build, 2 min 6 s | free tier |
| Cloud Run | classify revision 00002 deployed | min 0 / max 2 |
| Artifact Registry | +541.9 MB (classify image b3cabc3 + CI API image) | ≈ $0.15/month total, BELIEVED |

## Incidents

- 21:14 — **Session working directory moved to `web/`** after `cd web && npm …` in one Bash call. The repo's PreToolUse hook runs `python scripts/hook_guard_secrets.py` by relative path, so every tool call was refused. Not routed around: entered ONE linked worktree via the harness (`.claude/worktrees/review-iq-wt-s31`), where the hook path resolves. All further work happens there; the harness refuses `git -C` into the primary checkout. Lesson: never `cd` in a Bash call.
- 22:01 — **Another actor merged #589** (as gaurav-gandhi-2411) while this session ran; a concurrent session may be active in the primary checkout. Not touched; merges here re-check BEHIND state each time.

## Daily checkpoints
