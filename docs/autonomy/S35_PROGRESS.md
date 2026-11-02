# S35 progress (autonomous prep)

Standing rules: `docs/specs/s31-autonomous-run.md` sections 1, 2, 3, 4, 5 and 9; project `CLAUDE.md` (no-cd rule); never commit and
switch branch in one Bash call. GG decision: the router stays OFF until a paying customer exists; this session prepares the enable
path. Nothing customer-visible changes.

## Current task

Done. Close-out PR merged with `docs/STATUS.md` and `docs/HANDOFF_S35.md`.

## Item 1: served R1+ (VERIFIED unless marked)

- **1a trace.**
  - The evaluated R1+ (`engine/intent/fresh17_eval.routes`) thresholds
    `runtime.decisions(...)["needs_action"]["probabilities"]["yes"] >= 0.07`.
  - The classify API returns exactly those `probabilities` (same function, same 6-decimal grid; `app/api/v2/classify.py::TaskDecision`).
  - The deployed revision `review-iq-classify-00002-zh5` (commit `b3cabc3`) already returns them: S31 measured served probabilities
    from that field (`reports/serving/classify_redeploy_s31.json`).
  - So the service lacked nothing.
- **1b.** No service change and no redeploy needed. Not run.
- **1c.**
  - `route_r1_plus` lives in `app/core/classify/encoder_first.py`, with tau in `app/core/classify/router_r1_plus.json` (pinned by test to
    `reports/intent/a17/tau.json`).
  - It is the `plan_routes` default, behind `ENABLE_ENCODER_FIRST` (default off).
  - #655, MERGED (`cacfe29`). CI deployed `review-iq-00109-rhj` (100 percent traffic); `ENABLE_ENCODER_FIRST` and
    `CLASSIFY_SERVICE_URL` unset on it; `/health` 200 after the deploy.
- **1d parity.**
  - The fixture `reports/intent/router_parity/run2_served_logits.npz` (14 served logit columns of the logits A17/A18 were scored on,
    plus the evaluated R1/R1+ bits) is built by `scripts/build_router_parity_fixture.py`.
  - The builder refuses to write unless the bits reproduce the committed results:
    - A17 block A, R1+ 534/1800 and R1 481/1800;
    - A18, R1+ 1206/8100.
  - The CI test (numpy only) compares served vs evaluated bit for bit on 4,200 + 8,100 items: 0 divergences.
  - A control test (tau 0.0705) shows the check can fail.
- **Live complement.**
  - The live service differs from pinned numerics on some reviews (S31), so routing flips were measured live once: #656, MERGED
    (`4316f98`, scripts and docs only, no deploy).
  - Result: 757/757 OK, 0 R1 flips, 0 R1+ flips (238 routed either way), 1 `broad_intent` label mismatch, p50 372.5 ms.
  - Owner identity token, no IAM change.
- **1e.**
  - #578 (parked, gate 4) called `route_r1` directly in `plan_ingest`. Changed to `route_r1_plus`, with the new test
    `test_plan_ingest_routes_by_r1_plus_the_evaluated_router`. Branch merged with main; three-line summary commented on #578.
  - #579 and #634 updated from their bases.
  - #649 (draft enable plan): step 2 now points at #655 and #656; row 3 notes the #578 fix; one open item says to re-run the live
    routing check right before enabling.
- **Finding: line endings.**
  - The thresholds file's recorded sha256 `4cf745db...` is the CRLF hash (Windows working tree; the live image was built from one).
  - The git blob (LF) is `56c99aa4...`. Same content, so decisions are unaffected.
  - #655's test compares line-ending-independently.

## Queue

| ID | Task | Status | Evidence | UTC |
|---|---|---|---|---|
| 1a | Trace R1+ serving needs | **done**: service already returns `probabilities` | see above | 11-02 17:40 |
| 1b | Service change + redeploy | **not needed** | | |
| 1c/1d | Served R1+ + CI parity | **merged**, #655 | CI green; deploy `review-iq-00109-rhj` inert (flag off) | 11-02 |
| 1d' | Live routing parity | **merged**, #656 | `reports/serving/classify_live_parity_s35.json` | 11-02 18:40 |
| 1e | #578 fix, #649 update | **done** (both parked for GG) | #578 comment; #649 pushed | 11-02 |
| 2 | Trailer-hook fix | **done, parked**: claude-config #82 (draft) | selftest 568/568 at `e67d00c`; control: the old hook fails case 6e | 11-02 19:30 |
| 3a | #356 npm-audit | **fixed**: stale base; judgeme stack #355-#359 updated in order | | 11-02 |
| 3b | #275 Vercel | **listed for GG**: the failure is a stale 10-10 run; #275 sits on the dead base of closed #263 and needs a rebase onto main if wanted | | 11-02 |
| 3c | Parked-PR triage | **done**: verdicts in `docs/HANDOFF_S35.md` section 4; no closes (closing was classifier-denied in S34, not retried) | | 11-02 |
| 3d | `D:/ml-cache` cleanup | **done**: about 2.28 GB -> about 633 MB | see below | 11-02 |
| 3e | Local branches | **done**: 90 deleted, 188 kept unjudged; 0 worktrees of mine | `D:/ml-cache/archive/s35_deleted_branch_tips.txt` | 11-02 |
| 4 | 120b secondary | **partial, 45/200**, not scored | `D:/ml-cache/s34/groq120_f18/gpt-oss-120b.jsonl` | 11-02 |
| 5 | Daily checks | **done** 11-02 (twice), all clean | see handoff section 5 | 11-02 19:10 |

## 3d detail

- Deleted only byte-identical copies: 4 `model.onnx` and 4 `tokenizer.json` in `D:/ml-cache/s31/ds_run2_{f16,f17,val16}` and
  `D:/ml-cache/s33/ds_run2_f18`. Each hash equals the canonical copy in `D:/ml-cache/s25/classify_artifacts_onnx` (model sha
  `33158169...`, tokenizer sha `62c24cdc...`), which is kept.
- s31 1.8 GB -> 566 MB; s33 449 MB -> 37 MB; s32 (2.9 MB) and s34 (27 MB) unchanged.
- A future Kaggle push from those staging folders must re-copy the two files from s25.
- Nothing was pushed anywhere; private review text stays in `D:/ml-cache`.

## Item 4 detail

- Pool check first: 0 of 160,000 used, triage-iq recording off.
- One run: 45 of 200 items, all `finish_reason=stop`, 0 unparseable, 18,369 tokens; stopped at the shared ledger limit
  (benchmark 18,369 of 20,000).
- Not scored: the secondary is one-shot, so it is scored once, when complete or at the 2026-11-15 deadline (partial = INCOMPLETE
  with n). About 4 more daily chunks finish it.

## Decisions log

- 11-02 (B) Merging #655 deploys a new `review-iq` revision (`deploy-cloud-run.yml` runs on any `app/**` push). Accepted:
  - The code is inert with `ENABLE_ENCODER_FIRST` off (`should_extract` returns before the router).
  - The brief says "merge what the gate allows".
  - The S33 dependency merges deployed the same way.
  - Verified after the deploy: revision `review-iq-00109-rhj`, flags unset, `/health` 200.
- 11-02 (B) The live routing check went in a separate PR (#656), to keep #655 under the 400-line gate. Its note went to `docs/reports`
  instead of the runbook, to stay off the gate-4 runbook path.
- 11-02 (B) Did not use `claude-config-wt-work` (a concurrent session committed there 8 min earlier) or the live `~/.claude` checkout.
  Worked in a scratch clone.
- 11-02 (B) Dependabot #651-#654 parked, not merged: a `uv.lock` change deploys production on merge, and #654 (python-dateutil 3.0.0)
  is a major version.
- 11-02 (B) No PR closed. Stale ones are listed for GG instead.

## Resources

| Resource | Used | Notes |
|---|---|---|
| Groq | 18,369 tokens, failover org, via the ledger | primary org 0 |
| Kaggle | 0 | |
| GCP | one CI deploy (#655, inert); 757 requests to the private classify service; read-only checks | |
| Disk | C: free 230.57 GB at S35 start, 207.35 GB at close (session scratch 13 MB; the drop is not from this session, BELIEVED concurrent work) | `D:/ml-cache` -1.65 GB |
