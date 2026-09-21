# CI coverage audit (Session 15c, S3c)

Point-in-time audit of what CI actually exercises versus the repo's surfaces, taken at
`origin/main` 049c8c9 plus the `web-build` job added in this PR. Method: read every file in
`.github/workflows/`, `pyproject.toml`, `Dockerfile`, `cloudbuild.yaml`, `web/package.json`,
`web/vercel.json`, `.pre-commit-config.yaml`, and the branch-protection settings via the
GitHub API (read-only). Nothing here was inferred from workflow names; each row cites the
trigger and step actually read. Not covered by this audit: whether each scheduled workflow
is currently passing (run history was not inspected).

**Update 2026-09-20 (Session 15d, U7):** re-taken at `origin/main` f23eb23 plus this PR. The
required-check list changed (now `lint-and-test` + `web-build`), the remaining gaps are ranked by
risk with evidence below, the top two are fixed (`eval` on PRs, `docker-build`), and the
recommended required-check set is at the end. Rows below marked "(S15d)" changed in that update;
everything else is unchanged from the original audit and was not re-verified.

Why this exists: PR #106 (tailwindcss 4) showed CLEAN with green CI while `vite build` failed
(`web/` had no build job at all), and Vercel's `ignoreCommand` (`git diff --quiet HEAD^ HEAD -- .`)
skipped its preview build, so nothing checked it.

## Branch protection (what actually blocks a merge)

`GET /repos/gaurav-gandhi-2411/review-iq/branches/main/protection` -> `required_status_checks`:
`strict: true`, required contexts = **`lint-and-test`, `web-build`** (read 2026-09-20; it was
`lint-and-test` only when this audit was first written), `enforce_admins: true`. Every other job
below is advisory unless GG adds it as required (see the recommendation at the end). "Covered" in
the table means "runs on PRs", not "blocks the merge".

## Trigger summary of every workflow

| Workflow | Triggers | Runs on PRs? |
|---|---|---|
| `ci.yml` (`lint-and-test`, `pip-audit`, `manifest-provenance`, `npm-audit`, `web-build`) | push `main`, `feat/**`; PR to `main`. No `paths` filter, no `paths-ignore` | yes |
| `secret-scan.yml` | PR to `main`; weekly cron | yes |
| `pre-cutover-verification.yml` (`verify`) | PR, **paths-filtered**: `supabase/migrations/**`, `supabase/ci/**`, `app/**`, `tests/integration/**`, itself | only if those paths change |
| `bypassrls-container-check.yml` | push `main`, PR to `main`, 6-hourly cron | yes |
| `eval.yml` (job `eval`) (S15d) | push `main` and PR to `main`, same paths on both (`app/core/**`, `eval/fixtures/**`, `eval/runner.py`, `eval/free_text_scoring.py`, `eval/cassettes/**`, the workflow); nightly cron; dispatch | yes, **paths-filtered** (was: post-merge only) |
| `docker-build.yml` (job `docker-build`) (S15d, new) | PR to `main`, paths: `Dockerfile`, `.dockerignore`, `pyproject.toml`, `uv.lock`, `app/**`, itself, `scripts/smoke_container_health.py` | yes, **paths-filtered** |
| `deploy-cloud-run.yml` | push `main` (paths: `app/**`, `Dockerfile`, `pyproject.toml`, `uv.lock`); daily drift cron; dispatch | no |
| `site-deploy.yml` | push `main` (paths: `site/**`, itself, `scripts/check_site_deploy_content.py`); dispatch | no |
| `migration-drift-check.yml`, `schema-drift-check.yml` | push `main` (paths `supabase/**`), 6-hourly / daily cron | no (compare against prod DB) |
| `security-bypassrls-check.yml`, `model-availability-check.yml` | push `main` / cron | no |
| `app-mount-check.yml` | `workflow_dispatch` only | no |
| `web-surface-probe.yml`, `uptime-alert.yml`, `alert-path-canary.yml`, `failover-probe.yml`, `demo-quota-probe.yml`, `db-backup.yml` | cron / dispatch only (probes of live prod, not of a PR) | no |

## Surface-by-surface coverage

Legend: **Covered** = a PR-time job fails on a regression in this surface. **Partial** = some
aspect is checked on PRs, or checked only after merge. **Not covered** = nothing runs.

| Surface | Status | What runs, and where | Gap |
|---|---|---|---|
| `app/` Python: lint, format | Covered | `lint-and-test`: `ruff check .`, `ruff format --check .` (whole repo) | |
| `app/` types | Covered | `lint-and-test`: `mypy app/` (strict) | |
| `scripts/`, `eval/`, `benchmark/`, `tests/` types | Not covered | `mypy app/` only; `pyproject.toml` also excludes `tests/`, `eval/` | No type check outside `app/`. Ruff still lints them. |
| Unit tests (`tests/unit/`, flat `tests/test_*.py`) | Covered | `lint-and-test`: `pytest tests/ --ignore=tests/integration --ignore=tests/benchmark`; `addopts` adds `--cov=app --cov-fail-under=60` | Coverage floor is 60 (percent; the global standard asks 80 for `app/`); not raised here. |
| `tests/benchmark/` (13 files) | Not covered | Excluded by `--ignore=tests/benchmark` in both `ci.yml` and `pyproject.toml` `addopts`; no workflow runs it | Never run by CI. Comment in `ci.yml` calls it "long-running". |
| `tests/integration/` | Partial | `pre-cutover-verification.yml` (ephemeral Postgres 17, all migrations applied, NOBYPASSRLS): public-service + admin-service suites, PR-triggered but only for the path filter above; `bypassrls-container-check.yml` runs `test_role_bypassrls.py` on every PR. | Path-filtered: a change to `web/` or `pyproject.toml`/`uv.lock` alone skips it (dependency bumps that break `app/` at import time are still caught by `lint-and-test`). Deselects: 3 tests (`--deselect` lines in that workflow); `test_resend_e2e.py` ignored entirely. |
| `supabase/migrations/` | Covered (PR) + post-merge drift | `pre-cutover-verification.yml` applies every migration in order on ephemeral PG; `bypassrls-container-check.yml` runs `supabase/push.py`; `lint-and-test` runs `check_migrations_no_bypassrls_grant.py` | Drift vs prod (`migration-drift-check`, `schema-drift-check`) runs only post-merge / cron. |
| Tenant scoping of DB connects | Covered | `lint-and-test`: `check_undocumented_pg_connects.py` | |
| Eval fixtures / scoring (S15d) | Covered (path-filtered gate) | PR-time in `lint-and-test`: `check_eval_results_reproducible.py` (replays the cassettes on every PR and fails if the regenerated results differ from the committed ones), `check_known_gaps_reproducible.py`, `check_grounding_check_reproducible.py`, `check_eval_model_matches_config.py`, `check_no_heldout_leakage.py`, `check_no_hardcoded_metrics.py`, `render_metrics.py --check`. New: the `eval` job runs `python -m eval.runner` on PRs touching the eval inputs and fails when a language or overall score is under its gate. | The path filter means the `eval` check does not appear on unrelated PRs (so it cannot be a required check as written). See "Risk ranking" for why the pre-existing PR replay left a real gap. |
| `web/` lint | Covered (new) | `web-build`: `npm run lint` | Before this PR: not covered. |
| `web/` typecheck | Covered (new) | `web-build`: `npx tsc -b` | Before this PR: not covered. |
| `web/` production build | Covered (new) | `web-build`: `node scripts/validate-env.mjs` then `npx vite build`, then a smoke check that `dist/index.html` references a hashed JS asset that exists and is non-empty | Before this PR: only Vercel built it, and Vercel's `ignoreCommand` can skip it. Build uses placeholder `VITE_*` values, so it proves the bundle compiles, not that prod env is right. |
| `web/` unit / component tests | Not covered | `web/package.json` has no `test` script or test runner; no `*.test.*` files exist | Nothing tests `web/` behaviour. |
| `web/` e2e / a11y | Partial (live only) | Playwright mount check runs against **live prod** in `web-surface-probe.yml` (nightly) and `app-mount-check.yml` (manual) | Never runs against a PR build. No axe/a11y check anywhere (global rule 15d). |
| `web/` dependency audit | Covered | `npm-audit`: `npm ci` + `npm audit --audit-level=high` | `npm ci` also catches lockfile/peer breakage (the #104 class), but only as an audit-job failure, not named as such. |
| Python dependency audit | Partial (non-blocking) | `pip-audit`: `continue-on-error: true` | Informational only. |
| `site/` HTML / links | Not covered on PRs | `site-deploy.yml` deploys on push to `main` and then runs `check_site_deploy_content.py` against the **live** page. `render_metrics.py --check` (PR) verifies the metric blocks in `site/index.html`, `site/docs/index.html` match eval JSON. | No HTML validity, broken-link, or asset-existence check on PRs; a broken `site/` change is found after it is already deployed. |
| `site/` and `web/` colour contrast | Partial | `check_contrast.py` (PR, `lint-and-test`) checks colour **pairs declared in `design/tokens.json`** only (its own docstring: it cannot catch a page that renders other colours) | No rendered-page contrast check. |
| `Dockerfile` (S15d) | Covered (path-filtered) | `docker-build`: `docker build` of the same Dockerfile (no push, no credentials), then the container runs with `--network none` and dummy env and `/health` is probed from inside it. Post-merge, `deploy-cloud-run.yml` still builds it again, deploys with no traffic, smoke-tests, then promotes. | Path-filtered, so not requirable as written. No Dockerfile lint. `FROM ghcr.io/astral-sh/uv:latest` is unpinned: an upstream uv release can fail the build on an unrelated PR. |
| `cloudbuild.yaml` | Not covered on PRs | Only exercised by the post-merge deploy job (`docker-build` builds the Dockerfile directly, not through Cloud Build) | A bad `cloudbuild.yaml` (e.g. substitution or logging option) still fails only after merge. |
| `ops/budget-killswitch/` (Terraform + Cloud Function `main.py`) | Not covered | No `terraform validate/fmt`; `function/main.py` has no test of its own (grep for `killswitch` under `tests/` finds one string mention in `test_check_no_hardcoded_metrics.py`, not an import; ruff lints it) | |
| `ops/runbooks/`, `ops/firebase-hosting/`, `ops/artifact-registry-cleanup.json` | Not covered | Docs / config; nothing validates them | Low risk; recorded for completeness. |
| `scripts/*` | Partial | Gate scripts (`check_*`) run as `lint-and-test` steps; ruff lints all; unit tests exist for `probe_failover`, `probe_web_surfaces`, `slo_report`, the two `check_*_deploy_is_from_main` scripts. | No tests for `check_app_mounted.py`, `probe_demo_quota.py`, `extract_schema_snapshot.py`, `record_cassettes_via_fallback.py`, `p2_*` (not measured beyond a file-name comparison against `tests/`). `toggle_scheduler_jobs.sh` has no shellcheck. |
| Workflow YAML itself | Not covered | No `actionlint` job | Syntax errors in a workflow are found only when it fails to run. Verified clean for this PR by running `actionlint` locally. |
| Secrets in diff | Covered | `secret-scan.yml` (gitleaks, PR diff scan, checksum-pinned binary) | |
| Dependabot PRs | Covered by same jobs | `ci.yml` has no `paths` filter, so npm/uv/actions bumps run every job. `pre-cutover-verification` is the exception (path-filtered, see above). | Dependabot config covers `uv` `/`, `npm` `/web`, `github-actions` `/`. |

## Risk ranking of the remaining gaps (S15d U7b)

Ranked by (chance a bad change gets through) x (damage if it does), using this repo's own history.
Numbers are from `gh run list` / `git log` run 2026-09-20 (commands under each row); "n" is the size
of the sample, not the repo's lifetime.

| # | Gap | Evidence | Verdict |
|---|---|---|---|
| 1 | Accuracy-gate thresholds not enforced on PRs | The published accuracy numbers are what the product is sold on. `c492e4a` (2026-08-22) changed the default model names, invalidated every cassette and never triggered `eval.yml` (path filter); the published table described a configuration that had stopped being deployed for two weeks. `gh run list --workflow eval.yml --limit 100`: 23 push runs (all success), 58 nightly success, **19 nightly failures** (the workflow's own comments cite a 14-night silent-broken stretch). `eval/results.json` and `latest.json` are designated-generated paths, so gate 3 does not count their lines: a regressed regenerated result is the least-reviewed part of a PR. | **Fixed (this PR).** Premise corrected below. |
| 2 | Production image first built after merge | `deploy-cloud-run.yml` push runs (`gh run list --workflow deploy-cloud-run.yml --event push --limit 100`, n=27): 26 success, 1 failure (2026-08-01, the pipeline's first-ever run; cause not examined here). The staged rollout (no-traffic revision, smoke, promote) keeps a bad image from users, so the damage is a red `main` and a blocked deploy queue, not an outage. But `lint-and-test` installs dev dependencies and the image is `uv sync --no-dev`: an `import pytest` in `app/` passes ruff, mypy, an import in the dev env and every unit test, and crash-loops the container (reproduced, below). Only a container start catches that class. | **Fixed (this PR).** Close call with #3. |
| 3 | `site/` has no PR-time validation | Highest blast radius per event: `site-deploy.yml` pushes straight to the live `samidhareviews.xyz` project with no staging step, and `site/` changes are frequent (`gh run list --workflow site-deploy.yml`: 7 deploys since 2026-09-12, 5 of them in the last 4 days). But the recorded incident (`f3e4e2f`) was "never deployed for weeks", not "bad content deployed"; the deploy is followed by `check_site_deploy_content.py` against the live page, so a wrong page is detected within minutes, and the page is static HTML reverted by a revert commit. Zero bad-content deploys in the history read. | **Not fixed.** Next in line: give `check_site_deploy_content.py` a local-directory mode and run it on PRs touching `site/**` (needs a code change to that script, so left out of a CI-only PR). |
| 4 | `web/` has no test runner | `web-build` (lint, `tsc -b`, `vite build`, dist smoke) now covers compile-time breakage, which was the shipped incident class (blank page, #106). Behaviour is untested. | Not fixed. Needs a new dev dependency (vitest); not installed without approval. |
| 5 | `mypy` covers only `app/` | `scripts/`, `eval/`, `benchmark/` are ruff-linted; the CI gate scripts and the eval scorer live there, so a type error in the scorer is possible, but no incident in the history read traces to one. | Not fixed. |
| 6 | `cloudbuild.yaml`, Terraform/Cloud Function under `ops/`, `tests/benchmark/` | No incident found. `cloudbuild.yaml` was changed twice (log-routing options) and fails only post-merge. | Not fixed. |

### Where the expectation was confirmed or overturned

- **Confirmed: `eval.yml` did not run on PRs**, and its path list omitted `eval/cassettes/**` (the store
  the runner replays from). Both fixed.
<!-- METRICS:HISTORICAL -->
<!-- Frozen 2026-09-20 measurements from a deliberately broken throwaway branch (hostile
     prompt edit); not product accuracy claims and never to be re-rendered. -->

- **Overturned in part: "a prompt/config/cassette-invalidating change can merge with no eval gate".**
  `lint-and-test` already replays the cassettes on every PR: `check_eval_results_reproducible.py` runs
  `eval.runner` and compares the regenerated results with the committed ones, and
  `check_eval_model_matches_config.py` covers the config-default class of `c492e4a`. Measured locally: a
  one-line `en.py` prompt change with the results NOT regenerated makes the reproducibility check exit 1.
  What was genuinely missing: that script treats a gate FAIL as "a valid regeneration outcome", so a PR
  that also commits its own regenerated results passes it. With the prompt change plus regenerated
  results plus `render_metrics.py` output, the reproducibility, metric-render, model-match,
  hardcoded-metrics, known-gaps and grounding checks all passed locally (`en: 0.0% -- FAIL`, overall
  29.5%). `eval.runner` exits 1 on that state; the new `eval` job is the only PR check that fails on the
  gate itself.
- **Not clean, and stated plainly:** in the CI run of that hostile PR (#220), `lint-and-test` did fail,
  but on two unit tests that pin real repo data counts (`test_p2_abstention_analysis`,
  `test_p2_panel_contamination_check`), not on any gate check; those tests fail on incidental data
  coupling and a diligent author would update them too. The steps after `Unit tests` (reproducibility,
  metric render) never ran in that job because pytest failed first. The local run of those scripts is the
  evidence for the gap; CI shows the two new jobs failing.
- **Confirmed: the Docker gap is real but bounded** by the staged rollout, and the concrete class it
  closes is the dev-only-import crash loop.

### Hostile checks (each new job fails on a real problem)

| Job | Hostile change | Local result | CI result |
|---|---|---|---|
| `eval` | one-line change to `app/core/prompts/en.py` (cassette keys no longer match) | `EVAL_CASSETTE_MODE=replay python -m eval.runner` exit 1, `en: 0.0% -- FAIL (gate 77%)`, overall 29.5% | #220 run 35501649788: `eval` fail, "Overall accuracy: 29.5% -- FAIL", exit code 1. Clean PR #218 run 35501214153: pass, 78.6% |
| `docker-build` | `app/main.py` imports `pytest` (dev-only) | `ruff check`, `mypy app/main.py` and `import app.main` in the dev env all pass; image builds; container exits with `ModuleNotFoundError: No module named 'pytest'`; probe exit 1 | #220 run 35501649801: `docker-build` fail at the smoke step, container log shows `ModuleNotFoundError: No module named 'pytest'`. Clean #218 run 35501214240: `OK: HTTP 200 {"db": "ok", "db_backend": "sqlite", ...}` |
| `docker-build` | a Dockerfile with `COPY does-not-exist.cfg ./` | `docker build` exit 1: `"/does-not-exist.cfg": not found` | not run in CI (local only) |

<!-- /METRICS:HISTORICAL -->

Wall-clock on GitHub runners, measured from the #218 run: `eval` 11 s, `docker-build` 14 s (the base
image and dependency layers come warm on the runner, so no layer cache was added; the local cold build
was 1 m 44 s). Neither job uses a secret, `|| true` or `continue-on-error` (a static test,
`tests/unit/test_eval_workflow_paths.py`, asserts there is no `continue-on-error` in `eval.yml`).

## Recommended required-check set (S15d U7a)

Currently required: `lint-and-test`, `web-build`. Flakiness figures below are PR runs from
`gh run list --workflow <file> --event pull_request --limit 100` (n = 100 per workflow, 90 for
`pre-cutover-verification.yml`). I sampled 6 failed `ci.yml` runs (all failed in `lint-and-test`) and 2
failed `pre-cutover-verification.yml` runs (both in a test step); I did not re-run any to separate a
flake from a real failure, so "flaky vs real" is NOT MEASURED. Unsampled failures are unattributed.

| Check (context name) | Runs on every PR? | Time | Track record | Recommendation |
|---|---|---|---|---|
| `lint-and-test` | yes | ~1.4 min | 11 failures in 100 PR runs of `ci.yml`; the 6 sampled all failed here | **Required (already).** |
| `web-build` | yes (no paths filter) | ~25-30 s | new; correctly red on #106 | **Required (already).** |
| `bypassrls-check` | yes (`bypassrls-container-check.yml`) | 28-93 s | 100/100 PR runs success; throwaway Postgres, no prod credential | **Add.** Tenant isolation is the highest-consequence property here and it is deterministic. Caveat: `security-bypassrls-check.yml` also has a job named `bypassrls-check` (push/cron only); on PRs only the container one reports, so the name is unambiguous today, but keep them distinct. |
| `diff-scan` (secret scan) | yes (`secret-scan.yml`; `full-history-scan` is skipped on PRs, which GitHub counts as passing if ever required) | ~5 s | 100/100 success; scans only the PR's new commits | **Add.** Cheap; false-positive risk is gitleaks-only and fails loudly on the diff. |
| `manifest-provenance` | yes | ~7 s | no failure seen in the PR runs inspected | **Add.** Repo-local and deterministic; guards published-number provenance. |
| `npm-audit` | yes | ~13 s | passes today; fails on #104 for a real reason (`npm ci` ERESOLVE) | **Do not require.** It goes red when a new advisory lands for a dependency the PR did not touch, so a required version would block unrelated merges (with strict up-to-date) until someone bumps. `web-build`'s `npm ci` already blocks the resolution-failure class. Keep advisory + Dependabot. |
| `pip-audit` | yes | ~12 s | always green: it is `continue-on-error: true` | **Do not require** (decorative: it cannot fail). Making it blocking is a separate decision. |
| `verify` (`pre-cutover-verification.yml`) | **no**, paths-filtered (`supabase/**`, `app/**`, `tests/integration/**`) | ~50 s | 11 failures in 90 PR runs; the 2 sampled failed in a test step | **Cannot be required as written**: a workflow skipped by its path filter reports no check and would block the PR forever. It is the most valuable of the unrequired checks (RLS on an ephemeral Postgres with all migrations). Recommended follow-up: drop the PR `paths:` filter (cost ~50 s per PR), then require it. |
| `eval` (new) | no, paths-filtered | ~11 s | new | **Not as written**, same reason as `verify`. At ~11 s the `paths:` filter buys almost nothing on the PR trigger; if GG wants the accuracy gate enforced, remove the PR `paths:` list (the drift test in `tests/unit/test_eval_workflow_paths.py` would need a one-line change) and then require `eval`. |
| `docker-build` (new) | no, paths-filtered | ~14-20 s | new | **Not as written**, same reason. Same option (drop the PR `paths:` filter, then require), but pin `FROM ghcr.io/astral-sh/uv:latest` in the Dockerfile first, otherwise an upstream uv release would fail every PR. |
| `Vercel`, `Vercel Preview Comments` | yes | n/a | reports "pass" when Vercel's ignoreCommand cancels the build (seen on every PR above) | **Do not require**: a green here means nothing. |

Result: required = `lint-and-test`, `web-build`, `bypassrls-check`, `diff-scan`, `manifest-provenance`
(three additions). Two more (`verify`, `eval`) become requirable after a one-line workflow change each.

**Exact setting path (classic branch protection is what the API returned; no ruleset was queried):**
GitHub -> repo `gaurav-gandhi-2411/review-iq` -> Settings -> Branches -> Branch protection rules ->
`main` -> Edit -> "Require status checks to pass before merging" -> in the search box add each name
above (a name is only searchable once it has reported in the last 7 days) -> Save changes. Keep
"Require branches to be up to date before merging" (currently on) and "Do not allow bypassing the above
settings" (`enforce_admins`, currently on). Each new required check adds its runtime to every PR
because of the strict up-to-date rule (a rebase re-runs all required checks).

## Dependabot #106 and #104 through CI with `web-build` (S15d U7d)

`gh pr update-branch 106` succeeded (merged current `main` into its branch, re-running CI); `gh pr
update-branch 104` was refused: "Cannot update PR branch due to conflicts" (`mergeStateStatus` DIRTY;
it conflicts in `web/package.json` and `web/package-lock.json`), so its checks below are from its
existing head `b28a794`, which predates `web-build` and which GitHub cannot re-run against a
conflicting merge ref. Neither PR was merged or closed.

| Check | #106 tailwindcss 3.4.19 -> 4.3.3 (run 35500967584, merged with main) | #104 typescript 6.0.3 -> 7.0.2 (run 35469874228, stale base) |
|---|---|---|
| `web-build` | **fail** at "Build (vite build)": `[postcss] It looks like you're trying to use tailwindcss directly as a PostCSS plugin ... install @tailwindcss/postcss`; env validation, eslint and `tsc -b` passed before it | **never ran** (no `web-build` job existed on that head). Reproduced its first steps locally instead: `npm ci` on the PR's `web/` fails `ERESOLVE` (`typescript-eslint@8.69.0` requires `typescript >=4.8.4 <6.1.0`, PR installs 7.0.2), so `web-build` would fail at "Install dependencies (npm ci)" |
| `npm-audit` | pass | **fail** at `npm ci`, same ERESOLVE (the run's own log) |
| `lint-and-test` | pass | pass |
| `pip-audit`, `manifest-provenance` | pass | pass |
| `bypassrls-check`, `diff-scan` | pass | pass |
| `mergeStateStatus` | BLOCKED (required `web-build` failing) | DIRTY (conflicts) |

To get a real `web-build` result on #104, someone must comment `@dependabot rebase` on it (not done: it
force-pushes the PR branch, and the task was limited to `update-branch`).
