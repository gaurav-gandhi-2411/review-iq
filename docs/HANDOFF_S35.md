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

1. **Router, only when a paying customer exists:** merge #396 → #576 and apply the migration → #578 → #579 → #634; re-run
   `scripts/classify_live_parity.py` (routes section) right before turning on `ENABLE_ENCODER_FIRST`. Plan: #649.
2. **claude-config #82** (trailer hook amends only a commit made in the same call).
3. **Close or revive the stale PRs:** #318, #275, #267. Dependabot #651-#654 when you are ready for a production deploy.
4. Older parked items, unchanged: #502 (email confirmation), #588, #590, #610, #485/#508/#512/#499/#472.
5. **120b secondary: nothing to do.** Later sessions continue it once a day:
   `PYTHONPATH=. python <scratch>/s31/with_failover_key.py C:/Users/gaura/ml-projects/review-iq/.venv/Scripts/python.exe -m engine.intent.fresh18_groq120 --subsample reports/intent/fresh18/subsample_120b.json --texts D:/ml-cache/s33/fresh18/fresh18.jsonl --out D:/ml-cache/s34/groq120_f18`
   (the child path must be absolute). Score once when complete or on 2026-11-15.

## 7. Resources

- Groq: 18,369 tokens on the failover org via the ledger (120b secondary); primary org 0.
- Kaggle: 0.
- GCP: one CI deploy (#655, inert); 757 private classify requests (scale to zero); read-only checks.
- Disk: C: free 230.57 GB at start, 207.35 GB at close; this session's scratch is 13 MB, so the drop is BELIEVED to come from
  concurrent work. `D:/ml-cache` 1.65 GB smaller.
