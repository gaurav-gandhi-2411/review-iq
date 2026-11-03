# Handoff: Session 35 (autonomous prep, 2026-11-02)

Brief: GG's S35 brief. Standing rules: `docs/specs/s31-autonomous-run.md` sections 1-5 and 9. Record: `docs/autonomy/S35_PROGRESS.md`
(merged with this PR). VERIFIED = observed with a command or artifact; BELIEVED = inferred or stated elsewhere.

## 1. Outcome

- **The served path now implements R1+, the router that was evaluated** (#655, merged). It was the finding that blocked enabling in S34.
  - A CI test compares the served routing with the evaluated R1/R1+ bits on the stored A17/A18 predictions (4,200 + 8,100 items):
    0 divergences. A control (tau 0.0705) shows the test can fail.
  - Live check against the private classify service (#656, merged): 757 of 757 requests OK, **0 R1 flips and 0 R1+ flips**.
- **Nothing customer-visible changed.** Router OFF (`ENABLE_ENCODER_FIRST` unset), drafting OFF. #655 deployed an inert revision
  (`review-iq-00109-rhj`).
- The rest of the enable chain is parked for GG, now pointing at R1+: #578 was fixed to call `route_r1_plus` (it called `route_r1`).
- Trailer-hook fix written and parked: claude-config #82 (draft), hooks selftest 568 of 568.
- 120b secondary: 45 of 200 items done, not scored (one-shot; deadline 2026-11-15).

## 2. Task status

| ID | Task | Status | Evidence |
|---|---|---|---|
| 1a/1b | Trace; service change | service already returns `probabilities`; no redeploy | `app/api/v2/classify.py::TaskDecision`, `reports/serving/classify_redeploy_s31.json` |
| 1c/1d | R1+ behind the flag + CI parity | **merged** #655 `cacfe29` | `tests/unit/test_router_r1_plus_parity.py`, `reports/intent/router_parity/` |
| 1d' | Live routing parity | **merged** #656 `4316f98` | `reports/serving/classify_live_parity_s35.json`, `docs/reports/router-r1-plus-live-parity.md` |
| 1e | Enable chain points at R1+ | #578 fixed + commented; #579, #634 updated; #649 step 2 rewritten | PR comments |
| 2 | Trailer hook | **parked**: claude-config #82 (draft) | selftest 568/568 at `e67d00c` |
| 3a | #356 npm audit | fixed (stale base); #355-#359 updated in order | |
| 3b | #275 Vercel | stale 10-10 run; the PR sits on the dead base of closed #263 | listed in section 6 |
| 3c | PR triage | section 4 | |
| 3d | `D:/ml-cache` | about 2.28 GB -> about 633 MB (duplicates only) | `docs/autonomy/S35_PROGRESS.md` |
| 3e | Local branches | 90 merged branches deleted, 188 kept unjudged; 0 worktrees of mine | `D:/ml-cache/archive/s35_deleted_branch_tips.txt` |
| 4 | 120b secondary | 45/200, not scored | `D:/ml-cache/s34/groq120_f18/gpt-oss-120b.jsonl` |
| 5 | Daily checks + close-out | clean; this PR | section 5 |

## 3. Findings

1. **Line endings in a recorded hash.** The thresholds file's sha256 recorded in the A17/A18 results and reported by the live
   service (`4cf745db...`) is the CRLF hash from a Windows working tree; the git blob (LF) hashes to `56c99aa4...`. Same content,
   so decisions are unaffected. #655's test compares line-ending-independently. Any future hash pin should say which form it uses.
2. **#578 would have shipped R1, not R1+.** Its `plan_ingest` called `route_r1` directly instead of `plan_routes`. Fixed on the branch
   with a test that fails if ingest routes by R1.
3. **Deleted model duplicates.** The Kaggle staging folders under `D:/ml-cache/s31/ds_run2_*` and `s33/ds_run2_f18` no longer hold
   `model.onnx` and `tokenizer.json`; re-copy them from `D:/ml-cache/s25/classify_artifacts_onnx` before a push.

## 4. Open PR triage (one line each)

| PR | Verdict |
|---|---|
| #649, #396, #576, #578, #579, #634 | Wanted: the router enable chain (gate 4 or draft; GG) |
| #637 | Wanted: provenance code for #635, not on main; 431 lines, fails gate 3 |
| #590 | Wanted; gate-4 waiver given via claude-config #81; GG merges |
| #610 | Tailwind 4; needs GG's UI sign-off |
| #588 | Injection-guard eval; waits on GG |
| #485, #472, #502, #508, #512 | Gate 4 or GG confirmation (unchanged) |
| #499 | Partly superseded: pooling fixed the `/health` cost (now 0.57-0.74 s); only the DB-region plan is still a decision |
| #355-#359 | Judge.me; GG applies the #355 migration, then the stack |
| #288, #291-#293 | Shopify; need the app registration |
| #269 | last_seen migration, gate 4 |
| #260 | Legal copy draft; GG |
| #318 | **Stale**: experiment stopped at 11 of 74 pairs; close unless the U4d control is resumed |
| #275 | **Stale base**: rebase onto main or close (its parent's work merged as #440-#447) |
| #267 | **Stale** (BELIEVED): Vercel storage fix from S17; close unless still wanted |
| #644 | A concurrent session's; left alone |
| #651-#654 | Dependabot; parked. A `uv.lock` change deploys production on merge, and #654 is python-dateutil 3.0.0 (major) |

No PR was closed this session (a close was classifier-denied in S34; not retried).

## 5. Production health (VERIFIED)

| Check | 11-02 (S35 start) | 11-02 19:07 UTC (close) |
|---|---|---|
| `/health` | 200, 0.57-0.74 s | 200, 0.62 s |
| API revision | `review-iq-00108-vrk` | `review-iq-00109-rhj` (#655, flags unset) |
| Classify | invoker `review-iq-runner@` only; unauthenticated 403 | unauthenticated 403 |
| failover-probe-nightly | 11-02 02:30 succeeded | same (next run 11-03 02:30) |
| 24 h `llm.rate_limited` / `llm.failover` / `db_timeout` / ERROR | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |

Schedulers: ingest-tick and failover-probe-nightly ENABLED; digest-daily and detector-sweep PAUSED (unchanged).

## 6. PARKED, and GG ACTIONS REQUIRED (minimum, in unblock order)

Updated 2026-11-03 after GG's follow-up (section 8): #82 merged, #318/#275/#267 closed, Dependabot handled.

1. **Live hook conflict (claude-config):** the live `~/.claude` checkout is 4 commits behind `origin/main` and holds another
   session's uncommitted edit that makes `hook_strip_trailer.py` warn-only (never amend), dated 2026-11-03 12:18 IST. #82 (merged)
   amends only a same-call commit instead. Pick one; until the checkout is reconciled and pulled, neither #82 nor the other fixes
   on main are live. Not touched by this session.
2. **Disk:** other projects' items in section 8 (about 33 GB of agentic-shopping-assistant scratch; the 67.5 GB Docker disk image,
   about 38 GB of it slack). Your call; nothing of theirs was touched.
3. **Router, only when a paying customer exists:** merge #396 → #576 and apply the migration → #578 → #579 → #634; re-run
   `scripts/classify_live_parity.py` (routes section) right before turning on `ENABLE_ENCODER_FIRST`. Plan: #649.
4. **#654 (python-dateutil 3.0.0):** parked with a plan, `docs/plans/dateutil-3-upgrade-plan.md`.
5. Older parked items, unchanged: #502 (email confirmation), #588, #590, #610, #485/#508/#512/#499/#472.
6. **120b secondary: nothing to do.** Later sessions continue it once a day:
   `PYTHONPATH=. python <scratch>/s31/with_failover_key.py C:/Users/gaura/ml-projects/review-iq/.venv/Scripts/python.exe -m engine.intent.fresh18_groq120 --subsample reports/intent/fresh18/subsample_120b.json --texts D:/ml-cache/s33/fresh18/fresh18.jsonl --out D:/ml-cache/s34/groq120_f18`
   (the child path must be absolute). Score once when complete or on 2026-11-15.

## 7. Resources

- Groq: 18,369 tokens on the failover org via the ledger (120b secondary); primary org 0.
- Kaggle: 0.
- GCP: one CI deploy (#655, inert); 757 private classify requests (scale to zero); read-only checks.
- Disk: C: free 230.57 GB at start, 207.35 GB at close; this session's scratch is 13 MB, so the drop is BELIEVED to come from
  concurrent work. `D:/ml-cache` 1.65 GB smaller. Explained in section 8.

## 8. Follow-up, 2026-11-03: disk, Dependabot, GG's permissions

### Disk (the warning, explained)

C: free went from 230.57 GB (S35 start) to a low of 185.61 GB, then 195.09 GB after cleanup and other sessions' own deletions.
No earlier per-directory snapshot exists in the progress files, so growth is attributed by file write times (files written in the
36 h before 2026-11-03 07:45 UTC). Sizes VERIFIED; owners inferred from directory names (BELIEVED).

| Item | Size | Written in window | Owner | Action |
|---|---|---|---|---|
| `%TEMP%\claude\asa\*` (venvs `w3c3`, `w3c2`, `v3c3`, `w6`, `w6v`, `w4`, `w10v`, `venv311`, `v4venv`, and more) | 30.33 GB | about 30 GB | agentic-shopping-assistant session (still writing at 07:52 UTC) | reported only |
| `%TEMP%\claude\C--Users-gaura-ml-projects-agentic-shopping-assistant\df92a9fd...`, `asa_v311`, `asa_testdata`, `asa_gcs` | 4.96 GB | about 5 GB | same | reported only |
| `%LOCALAPPDATA%\Docker\wsl\disk\docker_data.vhdx` | 67.46 GB | written continuously | Docker Desktop; running containers are `swasarthi` (Supabase stack) and `mbbs-mentor-db` | reported only. `docker system df`: 29.5 GB live (images 7.2, containers 4.5, volumes 0.8, build cache 17.0 reclaimable), so about 38 GB is disk-image slack that only a compaction returns |
| `%LOCALAPPDATA%\uv\cache` | 43.38 GB | 0.18 GB | shared | not pruned: another session's `uv` process was running |
| `%TEMP%\claude\C--Users-gaura-ml-projects-agentgauge\...` | 4.23 GB | 0 | agentgauge (idle since 11-02) | reported only |
| `%TEMP%\claude\gg-portfolio\*` | 1.36 GB | small | gg-portfolio | reported only |
| `%TEMP%\pytest-of-gaura` | 2.08 GB | 0.36 GB | shared pytest temp | left (may be in use) |
| `%LOCALAPPDATA%\pip\Cache` | 0.97 GB | | pure cache | **deleted** (`http-v2`) |
| `%LOCALAPPDATA%\npm-cache` | 3.42 GB | | pure cache | **`npm cache verify`**: 0.47 GB garbage-collected |
| review-iq's own scratch | 14 MB (this session); 8 older review-iq session folders are empty | | review-iq | removed at session end |
| `~/.claude/projects` transcripts | review-iq 0.48 GB; largest swasarthi 1.66 GB | | per project | left |

Conclusion: review-iq did not cause the drop. The agentic-shopping-assistant scratch (about 35 GB written in the window) accounts
for most of it; the Docker disk image is the largest single file, and its growth is unknown (no earlier size on record).

### Dependabot

- **#653** (9 minor/patch updates): before merging, the `onnxruntime` 1.31.0 to 1.31.1 bump in `requirements-classify.*` was
  reverted on the branch (`d61a200`). Those files pin the stack the offline evaluation and the sealed-set parity check ran with; an
  ORT bump needs a parity re-run before the classify image is rebuilt. `uv.lock` carries no onnxruntime, so the app is unaffected.
  Merged `48a11a6`; deployed `review-iq-00110-6zb`.
- **#651** (setup-uv 10.3.3) merged `d25379b`; it edits the deploy workflow, so CI deployed again: `review-iq-00111-kfc`.
- **#652** (web, 6 minor/patch) merged `4a9b210`; no Cloud Run deploy.
- After the deploys (VERIFIED): 100 percent on `review-iq-00111-kfc`; `ENABLE_ENCODER_FIRST` and `CLASSIFY_SERVICE_URL` unset;
  `/health` 200 (0.56-0.98 s); `/openapi.json` 200; unauthenticated `/v2/reviews` 401; web app 200; 0 ERROR logs since 07:00 UTC.
  Rollback: `gcloud run services update-traffic review-iq --to-revisions=review-iq-00110-6zb=100` (tag `smoketest`), or `00109-rhj`.
- **#654** (python-dateutil 3.0.0, major): parked with `docs/plans/dateutil-3-upgrade-plan.md`. The app uses only
  `dateutil.parser` (in `app/core/csv_ingest.py`); the ingest tests pass 44/44 on 3.0.0 in a throwaway environment.

### GG's permissions, used

- claude-config #82: marked ready, gate eligible, merged `34b525e`. Not live yet: see section 6 item 1.
- #318, #275, #267: closed with a comment each; branches kept.

### Also

- #396: after its branch update, main's `test_no_failure_swallowing_shell_idioms` failed it on `|| true` in the restore step. The
  step now records the exit code instead and still fails unless the psql ERROR count is 0. CLEAN.
