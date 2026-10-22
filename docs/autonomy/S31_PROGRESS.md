# S31 autonomous run — progress

Spec: `docs/specs/s31-autonomous-run.md`. Window: 2026-10-23 → 2026-10-27 08:00 IST (02:30 UTC).
All timestamps UTC. VERIFIED = observed this session with the command or artifact named; BELIEVED = not re-observed.

## Current task

P1 — panel labels (kernel `review-intent-fresh16-panel` reported COMPLETE at P0).

## Queue

| ID | Status | Evidence | Updated (UTC) |
|---|---|---|---|
| P0 | done | snapshot below; spec + this file on `autonomy/s31-progress` | 2026-10-22 21:10 |
| P1 | in progress — panel session 2 running | session 1: 4,600–4,800 / 6,300 per judge, `budget_hit` true, 36,599 s; pin check "recorded and correct" (ollama 0.40.6, sha256 c164ab8b…c0e5e0, 4 model digests). Session 2 = kernel version 2, pushed 21:20 with `--prior gauravgandhi2411/review-intent-fresh16-panel-out` (session-1 judges, private) | 2026-10-22 21:20 |
| P2 | in progress | #584 MERGED → `b3cabc36d2b83d3c4a65fc8470e6dc8c764b7a37` (gate eligible: 198 reviewable + 3,558 generated). #585/#587/#591 updated onto main, CI running. Opened #594–#597 for the run-4 branches (stacked on #593) | 2026-10-22 21:30 |
| P3 | ready (main CI pending) | `keep-classify-prev` tag added to `review-iq-classify@sha256:b6d3ef10…2885e` (S29 image, tag d28cde57b356) | 2026-10-22 21:35 |
| P4 | pending (needs P3 image) | | |
| P5 | pending (needs P1, P4) | | |
| P6 | pending | | |
| P7 | pending | | |
| P8 | pending | | |
| S1 | scheduled ≥ 2026-10-25 00:00 | | |
| S2 | daily | | |
| S3 | daily 03:30 | | |

## P0 snapshot (2026-10-22 21:05 UTC)

- **Git:** primary checkout only (no extra worktrees). `origin/main` = `040ef4d` (#582). Untracked files not mine, left alone: `ERROR`, `WARNING`, `eval/cassettes/no_routing_stage1_cassettes.json`, `eval/results/no_routing_stage1_{ledger,rows}.json`.
- **Stack PRs** (all mergeStateStatus CLEAN, CI all pass + 1 skipping):

  | PR | base | head | head SHA |
  |---|---|---|---|
  | #584 | main | feat/s30-pinned-thresholds | f905b2a39897 |
  | #585 | main | fix/s30-classifier-ingest-timeout | 0d915923eda2 |
  | #587 | main | feat/s30-amendment16 | d2e39c55302e |
  | #591 | main | feat/s30-capacity-probe | ce1023ec7933 |
  | #592 | feat/s30-amendment16 | feat/s30-fresh16-tools | 1ed1d6b701be |
  | #593 | feat/s30-fresh16-tools | feat/s30-fresh16-data | dc2dec6c4aa1 |

- **Run-4 branches (no PR):** inputs `2d6a083b2877` → quantize `5e60c7fe2746` → select-eval `569ff1363134` → kernel `fb3aff643b35`.
- **Parked (do not merge):** #396 BEHIND/draft, #576 BEHIND, #578 BEHIND, #579 CLEAN (base #578), #555 (closed or not listed as open; registry policy, S1).
- **Other open PRs** (not mine this session, untouched unless backlog): docs #586/#589/#590 CLEAN; #588 draft; Dependabot #106 (DIRTY), #232, #233, #516–#520, #523, #567, #568 (BEHIND); older S17–S23 drafts.
- **Kaggle:** `review-intent-fresh16-panel` status COMPLETE. The account also hosts other projects' kernels (ag-r1-*, engine-*, r6-*) that draw on the same GPU quota; none running from this project.
- **Kaggle GPU hours remaining / reset:** not exposed by the CLI (kaggle 2.0.1). BELIEVED: 30 h/week, resetting weekly. Tracked below from per-kernel run times; read the Kaggle settings page figure when possible.
- **Production:** `GET https://api.samidhareviews.xyz/health` → 200 in 1.07 s.
- **Cloud Run** (asia-south1): `review-iq` rev `review-iq-00098-nmw`; `review-iq-admin` `-00079-456`; `review-iq-classify` `-00001-4rt` (the S29 unpinned image). Job `failover-probe` last execution `failover-probe-wv6rb` completed 2026-10-22 02:30:16.
- **Artifact Registry:** `cloud-run-source-deploy` 988.0 MB, `gcf-artifacts` 452.4 MB.
- **Disk:** C: 228 GB free of 952 GB; D: 1.4 TB free. Private data `D:/ml-cache/s30/` (ds_equiv, ds_fresh16, ds_run4_in, equiv, fresh16, kaggle_equiv, onnx, smoke).
- **Laptop:** top CPU processes are claude/msedge/explorer; nothing heavy from this session.

## Decisions log (Tier B and judgment calls)

- 21:25 — **Merging #584 to main triggered the existing `Deploy to Cloud Run` workflow** (run 39854354767, success): API now `review-iq-00099-7z6` at `b3cabc3`. This is the repo's standard CI deploy on push to main, not a manual deploy. The change is classify-path code (the ORT pin and the thresholds file) used only by the private classify service; the router is OFF, so the API's behaviour is unchanged. `/health` 200 after the deploy. Note for later merges: every merge to main redeploys the API.
- 21:35 — Tier B: tagged the serving classify digest `keep-classify-prev` before the P3 redeploy (spec P3/S1).
- 21:20 — Panel resume: the session-1 output went into a NEW private dataset `review-intent-fresh16-panel-out` (ids and labels only) and was passed as `--prior`, so session 2 skips done ids (kernel `pending_chunks(done_ids)`). Pushed with the local `feat/s30-fresh16-tools` copy of `kernel_scale.py` (the one that records the pin) and clone branch `main`, the same as session 1.

## Deviations

## PARKED

## Resource ledger

| Item | Amount | Note |
|---|---|---|
| Kaggle GPU h (S30 carry-over) | 10.17 | panel session 1 (T4×2), 36,599 s |
| Kaggle GPU h (S31) | running | panel session 2 |
| Groq eval tokens (failover org) | 0 | |
| GCP actions | none | |

## Incidents

## Daily checkpoints
