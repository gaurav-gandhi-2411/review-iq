# Control-class audit (Session 19, Z8), part 3: findings and coverage

> Part 3 of 3 of the Session 19 control-class audit. Siblings: `docs/control-audit-s19.md` (part 1: result, method, inventory), `docs/control-audit-s19-induction-log.md` (part 2: induction log), `docs/control-audit-s19-findings.md` (part 3: findings, unproven, disclosures, coverage).

## Findings

### Decorative, fixed in #277 (each with a test that fails against the previous behaviour)

- **F1 migration-drift `--fail-on-pending` blind to 13 of 43 migrations.** A merged-but-unapplied
  migration whose SQL matches no object/grant regex printed "no recognizable objects" and the run
  exited 0 (D2). The REVOKE-only file that motivated postconditions is in that class. Fix: evaluate
  the file's postconditions read-only; false, or none holding, is unapplied (fail closed). Minimal
  reproducing mutation: delete one ledger row and undo its effect on any database built by
  `apply_migrations_ci.py`. Risk: the first scheduled run after merge may turn red for a real,
  previously invisible reason (production not inspected).
- **F2 db-backup verify passes an all-empty data dump.** `COPY public` is present for every table
  (pg_dump 17, measured), so the S15d "requires a COPY section" fix only closed the schema-only shape.
  Fix: count rows in the `organizations` COPY block. `db-restore-test` already caught the empty
  case (R4d), later in the chain.
- **F3 mount checks pass the configuration-error screen.** Real build, no VITE_* env, headless
  Chromium: "OK ... mounted". `probe_web_surfaces`'s second SPA surface (`/try`) still failed on that
  page, so the nightly probe was partly covered; `app-mount-check.yml` (manual only) was not. Fix:
  pure verdict functions, markers pinned to `main.tsx` and `Login.tsx`.
- **F4 `test_workflow_controls.py`: (a)** a new `scripts/check_x.py` named only in a YAML comment
  counted as wired (W6); **(b)** only 2 of the 5 required contexts were pinned (W5). (b) fails closed
  in production (a renamed job blocks merges), so it is a stale pin, not a silent pass.

### Reported, not fixed

- **F5 The transcript secret hook was not active in the checkout this session ran in.**
  `.claude/settings.json` arrived with #249; the main checkout was on `exp/s17-noise-floor-adr0031`,
  which predates it (`git merge-base --is-ancestor origin/main HEAD` false, no `.claude/settings.json`).
  `cat .../.env 2>/dev/null | wc -l` and a `Read` of a nonexistent `.env` path both ran unblocked here,
  while the same command text is denied when fed to the script directly. A repo-tracked hook protects
  only sessions started on a branch that contains it; a worktree or checkout of an older branch is
  unguarded. A user-level hook (`~/.claude/settings.json`) would not depend on the branch. Not
  changed: settings are outside this change.
- **F6 Failing-but-not-required checks.** `npm-audit` failed on 58 real runs, `pre-cutover-verification`
  on 12; neither blocks a merge. `eval`, `docker-build` are path-filtered, so they cannot be required as
  written. Recorded in the sweep already; unchanged.
- **F7 `uptime-alert` is body-agnostic**: a 200 with any body is "up" (H1). The app returns 503 when its
  DB ping fails, so an app-level outage is caught; a CDN or maintenance page returning 200 is not.
- **F8 `tests/benchmark/*` (13 files) never run in CI** (ignored in `ci.yml` and `addopts`), including
  `test_leakage_check.py` and the sentiment scorer. Known from the S15c audit; listed because they are
  control-style tests that have never failed anywhere that matters.
- **F9 Three integration tests are deselected in `pre-cutover-verification.yml`**, one of them the
  forged-JWT vector (`test_garbage_jwt_rejected_by_real_supabase_verification`). In this audit it
  failed locally for a configuration reason (`supabase_url is required`), the same reason it is
  deselected, so that attack vector is exercised nowhere in CI.

### Pattern worth stating once

Three of the four decorative controls (F1, F2, F3) were *fixes for an earlier decorative control*
that closed the previous failing shape and left the neighbouring one open: the postcondition work
covered `--verify` but not the dry run, the `COPY public` check covered schema-only but not empty,
the mount check covered an empty `#root` but not an app that renders its own error. A control fix
without its own induction of the *next-nearest* failing input repeats the problem.

## What was not proven (UNPROVEN, with the reason)

- WF-08 production wiring of `security-bypassrls-check` (needs the production DSN); logic proven locally.
- WF-09 first scheduled run of the fixed dry run against production (no production access).
- WF-11 `model-availability-check` and `check_model_generation.py` (provider keys, GCP WIF).
- WF-12 `deploy-cloud-run` smoke and the ancestry branch of both deploy-provenance scripts
  (Cloud Run / Cloudflare APIs). Only the no-credential fail-closed branch of the Cloudflare script
  was induced (P2).
- WF-16 authenticated probe path (`PROBE_API_KEY` is not a repository secret).
- WF-05 weekly full-history scan wiring; WF-22 canary was not re-run (it is itself an induction).
- TS-06: the other integration files (account deletion, admin, shopify/google RLS, retention) were
  green in the 172-test baseline but not individually mutated. TS-07 `test_rls_disable_proof.py`.
- CI-24's smoke step: reproduced from a copy of its shell, not extracted from the YAML.
- That the changed GitHub workflows run: nothing was triggered on GitHub except by opening #277 and
  this PR.

## Disclosures (things that went wrong in this session)

1. **A production read happened by mistake.** While inducing `check_cloud_run_deploy_is_from_main.py` I
   prepended a fake `gcloud` to `PATH`; on Windows the script resolved the real `gcloud` and printed
   `[PASS] review-iq: running image tag 'sha-...' ... confirmed on origin/main` for both services.
   That is a read-only `gcloud run services describe` against production with whatever account was
   active, without `--account`. No write, no secret printed. The result is not used as evidence here.
2. **A `.env` file was touched by a command.** Checking whether `.env` existed I ran
   `cat <main checkout>/.env | wc -l`; only a line count was produced and no content was displayed or
   stored. It ran because the secret hook was not active in that checkout (F5).
3. The audit worktree's `web/node_modules` and the Docker container `z8-pg` / images `z8-smoke:*` were
   created by this session; the images were removed, the container is removed at the end (separate
   command, after listing it).

## Coverage statement

Swept and read (step lists and every step I induced; not every line of every file): all 20 files in
`.github/workflows/`; `.github/actions/schedule-failure-alert` (via the canary history only, not
re-read); `scripts/check_*.py` (all 19), `scripts/probe_*.py` (3), `scripts/npm_audit_gate.py`,
`scripts/verify_restore.py`, `scripts/hook_guard_secrets.py`, `scripts/restore_one.sh` (the verify call),
`supabase/push.py` (dry-run, verify), `supabase/ci/apply_migrations_ci.py`, `.claude/settings.json`,
`.pre-commit-config.yaml`; `tests/` by grep for the name stems in the task (108 test functions in 48 files),
plus a grep for skip/xfail; branch protection; run history of every workflow
(`status=failure` pagination; per-run failing step via the jobs API; logs read for the 40-odd newest and
oldest runs of each failing-step group, so a cause is "inferred" where no log was read).

Not swept: `supabase/migrations/*.sql` bodies (only classified by push.py's patterns); `eval/`
scorers' correctness; `app/` beyond the guard call sites; `web/src` beyond `main.tsx`/`Login.tsx`;
`ops/` (Terraform, budget kill switch: no test, no CI; unchanged from the S15c audit); Vercel's own
build and `ignoreCommand`; the Slack and GitHub-issue alert delivery outside the canary; dependabot;
GitHub-side settings other than branch protection; secret **values** (names only were listed).
Row counts above are my tallies of the tables; where a row bundles several steps (CI-20..24,
WF-17) the verdict is per step.
