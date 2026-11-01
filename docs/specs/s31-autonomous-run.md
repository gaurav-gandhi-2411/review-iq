# Samidha Reviews — Session 31 Autonomous Run Spec

**Window:** from kickoff (2026-10-23) until **2026-10-27 08:00 IST** (02:30 UTC).
**Owner unavailable:** GG cannot be reached during the window. This spec is the authority. If this spec and an older brief disagree, this spec wins.
**Goal by the 27th:** Amendment 16 evaluated end to end with honest results; the classifier redeployed on pinned numerics with live parity measured; scheduled ops chores closed; backlog advanced; a clean handoff GG can act on in minutes.

---

## 0. Orientation

- Repo: `C:\Users\gaura\ml-projects\review-iq` — GitHub `gaurav-gandhi-2411/review-iq` (public).
- Product: Samidha Reviews. Site `samidhareviews.xyz` (Cloudflare Pages), app `app.samidhareviews.xyz` (Vercel), API `api.samidhareviews.xyz` (Cloud Run `review-iq`, project `reviewiq-prod-260813`, region `asia-south1`, identity `gaurav.gandhi1129@gmail.com`).
- Private services: `review-iq-admin` (IAM-gated), `review-iq-classify` (private, invoker = `review-iq-runner@` only, nothing calls it — router is OFF).
- LLM: Groq free tier. **Primary = dedicated org** (`groq-api-key-samidha`) — production only. **Failover = shared org** (`groq-api-key`). Limits are a rolling 24 h window. Eval budget ledger enforces ≤ 100K tokens/model/trailing 24 h on the failover org.
- Database: Supabase (Sydney). Migrations via `supabase/push.py` as `review_iq_migrator` only.
- Compute: Kaggle (CLI, private kernels, ~30 GPU-h/week BELIEVED, max 2 concurrent sessions). Cloud Build for images (2,500 free min/month, verified S29). Laptop = orchestration only.
- Read first: `docs/STATUS.md`, `docs/HANDOFF_2026-10-20_samidha_pause.md`, `docs/specs/review-intent.md` (Amendments 1–16), `docs/reports/serving/numerics_rerun_s30.md`, `CLAUDE.md`.

### State at handoff (S30 pause)
- Numerics pin (`session.x64quantprecision=1`) implemented in PR **#584** (not merged). Thresholds refit; Amendment 14/15 re-run on pinned numerics: A15-A 295/306 = 0.9641 [0.9368, 0.9798], A15-B +0.0654 [0.0261, 0.1046].
- Open stacked PRs: **#584** (pin, thresholds, re-run) → **#585** (60 s ingest timeout) → **#587** (Amendment 16 spec + census) → **#591** (capacity probe script) → **#592** (draw tooling) → **#593** (draw data + ledger). Pushed branches without PRs: `feat/s30-run4-inputs` → `feat/s30-run4-quantize` → `feat/s30-run4-select-eval` → `feat/s30-run4-kernel`.
- Parked for GG (do not merge): **#396, #576, #578, #579** (encoder-first chain), **#555** (registry policy, enforce scheduled below).
- Amendment 16 pre-registered at commit `03084fc37c3c756b52f0d1ccbfc4eab151af0836`: sealed set `fresh16` = 3,900 items (Block A natural 1,800; Block B ≥4 stars 1,700; Block C >128 tokens 400); training additions `train16` = 2,400; criteria E1–E6.
- Kaggle kernel `review-intent-fresh16-panel` (four-judge, pinned Ollama) was running on 6,300 items (~13.5 h across two sessions). Dataset `review-intent-fresh16`. Private artifacts in `D:/ml-cache/s30/`.

---

## 1. Non-negotiable rules

1. **No paid spend** beyond what GG approved: the classifier service (~5 cents/month registry storage), Cloud Build inside its free tier, Cloud Run inside its free tier, Kaggle free GPU. Any action that would bill beyond this → stop that task and park it.
2. **Never print a secret** — no password, token, key or partial value, in any output, file, log, commit or message.
3. **Never bypass, edit or route around** the merge gate, hooks, or a classifier denial. Gate-4 PRs and anything in claude-config → park.
4. **No conflicted PR ever reaches GG.** Every PR left open at handoff is CLEAN or explicitly marked.
5. **Pre-registration integrity.** Never change a registered criterion, threshold, arm, rule or sample size after its data exists. Every deviation is logged as a deviation, in the report and in the progress file.
6. **Sealed sets are one-shot.** Never tune, select or inspect predictions on a sealed set except the registered evaluation. Never retrain after seeing sealed results.
7. **Laptop protection** (it overheated and shut down on 2026-10-20). No local Docker builds, no local Ollama, no training or bulk inference on the laptop. Local = git, gh, unit tests, small scripts. Heavy work → Kaggle or Cloud Build.
8. **Sequential local work.** One task, one subagent, one worktree at a time. Executor → verifier: the verifier independently recomputes every headline number before a results PR merges. Remote Kaggle kernels may overlap local work (max 2 at once).
9. **No long-lived background watchers** (the harness reaps them under memory pressure). Poll with short foreground commands.
10. **Never process-wide kills.** Never touch processes you did not start (other projects share this laptop).
11. **Product state stays frozen for customers:** router OFF, reply drafting OFF, run 4 NOT deployed, no customer-visible behavior change, no edits to the public site or pricing, nothing sent externally (email, posts, issues/PRs on other repos).
12. **Groq:** the primary (dedicated) org carries **zero** eval traffic. Eval traffic only on the failover org, through the budget ledger, ≤ 100K tokens/model/trailing 24 h. Prefer Kaggle local models wherever the task allows.
13. **Disk hygiene:** remove each worktree once its PR merges; delete throwaway clones and scratch output; keep C: free > 100 GB; report footprint at each checkpoint.

---

## 2. Decision policy

GG's tie-breakers, in order: **correctness → product quality → AI/ML rigor → operability → maintainability → scalability → dev productivity → cost.** Choose the simplest option that satisfies the constraints.

| Tier | Meaning | Examples |
|---|---|---|
| **A — decide and act** | Reversible, free, within this spec, no customer impact | Code, tests, docs, fixtures, Kaggle runs, merges the gate passes, rebases, PR splits, repo-doc corrections, dependency patch/minor updates that pass the gate |
| **B — decide, act, and log the rationale** | Production actions explicitly approved in this spec | Classifier redeploy (P3); throwaway probe service create + delete (P4); registry cleanup enforcement under S1's conditions; rollback of `review-iq` or `review-iq-classify` to the last known-good revision during a production incident; rotating a secret you find exposed (never printing it) |
| **C — never; park** | Needs GG | Gate-4 or claude-config merges; enabling the router or reply drafting; deploying run 4; site/pricing/public copy changes; any paid spend; deleting production data or tables; IAM/auth changes beyond this spec; editing GG's personal config; changing registered criteria; external communication; UI changes merged to main |

**When unsure between B and C, treat it as C.**

**Parking procedure:** record the item in the progress file's PARKED section — what it is, why it is blocked, exactly what GG must do (numbered steps), and what was completed up to the block. Then move on immediately to the next task that does not depend on it. Never wait idle on a parked item.

---

## 3. Durable state and resume protocol

- **Progress file:** `docs/autonomy/S31_PROGRESS.md`. Create it in P0. Sections: Current task · Queue table (ID, status, evidence, UTC timestamp) · Decisions log (every Tier B action with rationale) · Deviations · PARKED · Resource ledger (Kaggle GPU-hours per kernel, Groq eval tokens, GCP actions and costs) · Incidents · Daily checkpoints.
- **Update it** on every task state change. Push to branch `autonomy/s31-progress` at least after every task completion and every 2 working hours. Merge it to main through a docs PR at each daily checkpoint (S3).
- **Resume protocol** — after context compaction, a crash, a restart, or any doubt about state: re-read this spec and the progress file; reconcile against `git status`, open PRs, the Kaggle kernel list, and Cloud Run state; continue from the first task not marked done. Never repeat a completed one-shot evaluation. Never re-launch a Kaggle kernel without first checking whether it already finished.

---

## 4. Waiting protocol

- Kaggle kernels, CI runs and Groq windows: poll with single short commands every 10–15 minutes. **Between polls, pick up the next independent task** (primary queue first, then backlog §8).
- If nothing independent remains, wait in sleep chunks of ≤ 9 minutes.
- If the harness reaps a job for memory: re-check state read-only, retry once in the foreground. A second failure → park it.
- If the merge hook times out under load: wait and retry later; never touch the hook.

---

## 5. Resource budgets

| Resource | Budget | If exhausted |
|---|---|---|
| Kaggle GPU | ~30 h/week (BELIEVED). In P0 determine hours remaining and the reset time; log every kernel's hours | Park GPU tasks until reset; continue CPU and backlog work |
| Groq eval | ≤ 100K tokens/model/trailing 24 h, failover org only, via the ledger | Defer to the next window; never touch the primary org |
| Cloud Build | Free tier only (2,500 min/month) | Stop and park |
| Cloud Run | Classifier min 0 / max 2 instances; probe services deleted in the same work block | Stop and park |
| Artifact Registry | Classifier image approved (~5 cents/month); no other new images | Stop and park |
| Disk | C: free > 100 GB; scratch < 1 GB | Hygiene first, then continue |
| Laptop heat | Check top processes at each checkpoint | Stop any heavy process you started |

---

## 6. Primary queue (dependency order)

Each task lists its done-criteria. Mark done only with evidence.

### P0 — Startup (first, ~1 h)
- Commit this spec at `docs/specs/s31-autonomous-run.md` (GG placed it in the working tree) and create the progress file; push both.
- Snapshot: git state, every open PR with head SHA and mergeStateStatus, the run-4 branches, the Kaggle kernel list and status, `/health`, Cloud Run services and revisions, registry inventory, disk, laptop top processes.
- Determine Kaggle GPU hours remaining and the weekly reset time.
- **Done:** progress file pushed with the full snapshot.

### P1 — Panel labels
- Fetch `review-intent-fresh16-panel` output. If it stopped early, resume it from its prior outputs (do not restart from zero).
- Run `engine/labelling/panel_pin.py` on its status. **If the pinned build is not recorded → discard and re-run** (pre-registered).
- Build labels (`fresh16_data labels`). Report consensus counts per block and stratum against Amendment 16's size rule, and per-task agreement (alpha, AC1, raw).
- If a size rule is unmet: apply the registered top-up rule if one exists; otherwise record the shortfall, proceed with the honest n, change no criterion.
- **Done:** labels committed (private texts stay off the public repo; only ids/labels/manifests as before), report in progress file.

### P2 — Merge the stack (runs alongside P1 while kernels run)
- Order: **#584 → #585 → #587 → #591 → #592 → #593**, then open PRs for `feat/s30-run4-inputs` → `-quantize` → `-select-eval` → `-kernel`, each ≤ 400 reviewable lines (split by seam if larger).
- Each: update-branch → CI green → gate → SHA-pinned squash merge → CI green on main → remove worktree.
- After #585 merges, rebase parked #578 so it stays CLEAN; keep it parked.
- Any PR the gate classifies as gate-4 → park.
- **Done:** all mergeable PRs merged; parked ones CLEAN with three-line summaries.

### P3 — Redeploy the classifier on pinned numerics (after #584 merges) — Tier B
- `CONFIRM_REGISTRY_COST=yes bash ops/deploy/deploy-classify.sh D:/ml-cache/s25/classify_artifacts_onnx` (or the artifact path the merged code expects), end to end, via Cloud Build.
- Before deploying, tag the currently serving classify digest `keep-classify-prev` for rollback.
- **Live parity:** all 757 sealed reviews, one per request, identity token, against the pinned offline reference. Target 0 mismatches of 3,028 decisions.
  - 0 mismatches → done.
  - > 0 mismatches → record count, max probability difference, which tasks flip; capture the Cloud Run CPU model/flags if obtainable; **no rollback needed** (nothing calls the service); park "Cloud Run numerics parity" for GG with the findings; continue.
- Three cold-start samples (≥ 15 min idle between them), server-side p50/p95 from Cloud Monitoring, peak memory.
- Access re-check: exactly one binding (`roles/run.invoker` → `review-iq-runner@`), no `allUsers`/`allAuthenticatedUsers`, unauthenticated POST → 403.
- Registry size and projected monthly cost after the push.
- **Done:** report in progress file and `docs/reports/serving/`.

### P4 — Capacity probe for longer inputs — Tier B
- Deploy throwaway **private** probe services from the same image with `CLASSIFY_MAX_LEN=256` and `=512` (no new image). Run `scripts/classify_capacity_probe.py`.
- Budget (registered): server-side p95 < 1 s and memory < 0.85 × 2 GiB.
- **Delete each probe service in the same work block; verify deletion.**
- Arms that fail the budget are dropped per Amendment 16. If every longer arm fails, the 128-token arm stands alone; record it.
- **Done:** budget table, surviving arms, probe services verified deleted.

### P5 — Train run 4 (Kaggle)
- Build the full Kaggle dataset; run the run-4 kernel for the surviving registered arms. Up to 2 kernels concurrently. Log GPU-hours.
- **Done:** all arm checkpoints and validation metrics stored privately; hashes recorded.

### P6 — Select and quantize (validation only)
- Apply the registered validation-only selection rule. Quantize the winner with the layer-sensitivity procedure; numerics pinned. Validation agreement with fp32 must meet the registered threshold; if it does not, follow the registered fallback; if none, record and use the fp32-closest acceptable variant documented as a deviation.
- The verifier independently recomputes the selection from the stored validation outputs.
- **Done:** chosen arm and quantized model hash recorded; no sealed data touched.

### P7 — One-shot fresh16 evaluation
- Score `fresh16` exactly once on pinned numerics (Kaggle CPU kernel, not the laptop).
- LLM comparator on Block A: gpt-oss-20b zero-shot on Kaggle Ollama pinned 0.40.6 (sha256-verified), same settings as Amendment 15 (reasoning effort low, max_tokens 1500). Report empty completions; must be 0 or explained.
- Compute E1–E6 with intervals exactly as registered.
- The verifier independently recomputes E1–E6 from the prediction files before the results PR merges.
- If a bug is found in **scoring code** after scoring: fix it, log the deviation, re-score. If the bug is in the **model or data**: do not retrain; report.
- **Done:** results PR merged (gate permitting), report in `docs/reports/amendment16-results.md`.

### P8 — Decide what follows
- **All E1–E6 pass:** write the enable plan (deploy run 4, router enable checklist, monitoring, rollback) as a doc and park it for GG (Tier C). Change nothing in production.
- **Any criterion fails:** report it as a negative result, plainly. Diagnose on validation/dev data only. Write an Amendment 17 proposal. Execute Amendment 17 autonomously **only if all hold**: the fix is specifiable before any data; it is pre-registered and committed before any draw; it uses fresh items from untouched pools via the ledger; GPU budget allows; it can finish before the stop rule. **At most one follow-up cycle.** Otherwise park it.
- Update `docs/STATUS.md`.

---

## 7. Scheduled tasks

### S1 — Registry cleanup enforcement (#555) — on or after 2026-10-25 00:00 UTC — Tier B
Enforce only if every condition holds; otherwise park:
1. Locate the dry-run decisions in logs. If they cannot be located, simulate the policy against a fresh registry inventory instead.
2. Zero selected digests are referenced by any serving revision, Cloud Run job, or `keep-*` tag.
3. Tag rollback images first: current and previous digests of `review-iq`/`review-iq-admin`, `review-iq-classify` (`keep-classify-prev` from P3), and the failover-probe image.
4. Switch the policy to enforce. Verify all services healthy afterwards; list what was deleted.

### S2 — Daily production health (once per UTC day)
`/health` on the API; classify service private and present; failover-probe job's last execution; open Cloud Monitoring incidents; 24 h counts of `llm.rate_limited`, `llm.failover`, `db_timeout`; Groq eval ledger. Anything abnormal → §9.

### S3 — Daily checkpoint (03:30 UTC = 09:00 IST, each day)
Checkpoint entry in the progress file; merge the progress docs PR; disk footprint; laptop top processes; Kaggle GPU-hours used vs remaining.

---

## 8. Backlog — work these whenever the primary queue is waiting or blocked (priority order)

**B1 — Finish the reply prompt (#298).**
Fix the two tone examples (around lines 24 and 123) that match the fault-admission patterns rule 7 forbids. Pre-register the eval in a doc **before running**: arms v2.1 (baseline) and v2.2, each on the large (120b) and small (20b) models via the failover org inside the budget; the same 20 held-out fixtures; three judges from families other than gpt-oss, run on Kaggle Ollama pinned; hard failure = flagged by ≥ 2 of 3. Both versions are re-judged by the same panel (the old baseline used different judges). **Success:** invented contact details 0/20 in both v2.2 arms AND v2.2 hard failures not worse than v2.1 on each model. Pass → merge #298 (reply drafting stays OFF). Fail → report and park.

**B2 — Control audit of new controls.** Prove each fails when broken (mutation tests): numerics-pin test, ingest-vs-user timeout split, leakage-ledger near-duplicate check, panel-pin check, cost-alert threshold, gitleaks, migration rehearsal. Merge the tests that prove failure. Any control that does not fail when broken is decorative — fix it and record it.

**B3 — Dependency hygiene.** pip-audit and npm audit; merge non-major Dependabot PRs that pass the gate; majors → plan only. Tailwind 4 migration (#106, the braces advisory allowlist expires 2026-12-31): implement on a branch with screenshots in light/dark at 360 and 1280 px, open the PR, **do not merge** (UI change, Tier C).

**B4 — Docs truthfulness sweep.** Check every number in `README.md`, `docs/STATUS.md`, `docs/reports/*` and `site/` against its source artifact. Fix repo docs (Tier A); report site discrepancies only (Tier C).

**B5 — Leakage ledger gap.** Add detection for a sealed review quoted inside a longer text (substring/containment), with tests; re-run against all sets; report any new hits.

**B6 — Judge.me connector.** Extend contract fixtures from Judge.me's public docs; no real store.

**B7 — Branch and disk hygiene.** Delete local branches proven merged; keep archives private (bundles on D:), never push them.

**B-last — Deepen verification** of primary results: seed variance on validation; bootstrap by product where product ids exist.

---

## 9. Scenario playbook

| Situation | Response |
|---|---|
| Context compaction or restart | Resume protocol (§3) |
| Classifier/permission denial | Park; never route around |
| Gate-4 PR | Park with a three-line summary |
| CI fails | Diagnose; flaky → re-run once, then fix the test in its own PR; real → fix |
| Merge conflict | Resolve; never leave a conflicted PR |
| Main moved | update-branch, re-pin SHA |
| Kaggle kernel fails | Read the log; transient → retry once; quota → park GPU work until reset; code bug → fix and re-run (never on sealed data after scoring) |
| Kaggle quota exhausted | Park GPU tasks; continue CPU and backlog |
| Groq 429 | Respect the rolling window; defer; never use the primary org |
| Panel pin not recorded | Discard and re-run (registered rule) |
| All longer-input arms fail budget | Proceed with the 128-token arm; record |
| Size rule unmet | Registered top-up if one exists; else honest n, no criterion change |
| Result looks too good | Check for leakage via the ledger and the scoring code before reporting |
| `/health` failing or production errors spike | Diagnose from logs; if a recent deploy caused it, roll back to the last known-good revision (Tier B); never delete data; log as an incident |
| Classify service broken | Nothing depends on it — fix or redeploy; no customer impact |
| Secret found exposed | Rotate via Secret Manager without printing it (Tier B); log the incident |
| Anything would bill money | Stop the task; park |
| Disk below 100 GB free | Hygiene first |
| Laptop heavy CPU from your own process | Stop it; move the work to Kaggle |
| Another session's processes | Do not touch; report |
| Ambiguous decision | Apply §2; if still unsure, it is Tier C — park and move on |
| Queue and backlog exhausted early | B-last, then write the handoff early and stop |

---

## 10. Stop rule and final handoff

- Do not start any job expected to run past **2026-10-27 06:00 IST**, except a Kaggle kernel that is safe to leave running (record it).
- By **2026-10-27 08:00 IST**: write `docs/HANDOFF_2026-10-27_s31.md`, update `docs/STATUS.md`, merge both through the gate. Then stop starting work.
- The handoff contains:
  1. Done / in progress / not started, against this spec's IDs.
  2. Headline results with intervals, each marked VERIFIED or BELIEVED.
  3. Every PR touched: merged (SHA), open (head SHA, state), parked (three-line summary).
  4. **PARKED list ordered by unblock value**, each with exact numbered steps for GG.
  5. Decisions log (all Tier B actions and rationale) and deviations.
  6. Incidents.
  7. Resource ledger: Kaggle GPU-hours, Groq eval tokens, GCP actions and projected cost.
  8. Kaggle kernels still running.
  9. Disk footprint.
- Print the same summary in the terminal.
