# CI coverage audit (Session 15c, S3c)

Point-in-time audit of what CI actually exercises versus the repo's surfaces, taken at
`origin/main` 049c8c9 plus the `web-build` job added in this PR. Method: read every file in
`.github/workflows/`, `pyproject.toml`, `Dockerfile`, `cloudbuild.yaml`, `web/package.json`,
`web/vercel.json`, `.pre-commit-config.yaml`, and the branch-protection settings via the
GitHub API (read-only). Nothing here was inferred from workflow names; each row cites the
trigger and step actually read. Not covered by this audit: whether each scheduled workflow
is currently passing (run history was not inspected).

Why this exists: PR #106 (tailwindcss 4) showed CLEAN with green CI while `vite build` failed
(`web/` had no build job at all), and Vercel's `ignoreCommand` (`git diff --quiet HEAD^ HEAD -- .`)
skipped its preview build, so nothing checked it.

## Branch protection (what actually blocks a merge)

`GET /repos/gaurav-gandhi-2411/review-iq/branches/main/protection` -> `required_status_checks`:
`strict: true`, required contexts = **`lint-and-test` only**. Every other job below (including
`npm-audit`, `secret-scan`, `verify`, and the new `web-build`) is advisory unless GG adds it
as required. "Covered" in the table means "runs on PRs", not "blocks the merge".

## Trigger summary of every workflow

| Workflow | Triggers | Runs on PRs? |
|---|---|---|
| `ci.yml` (`lint-and-test`, `pip-audit`, `manifest-provenance`, `npm-audit`, `web-build`) | push `main`, `feat/**`; PR to `main`. No `paths` filter, no `paths-ignore` | yes |
| `secret-scan.yml` | PR to `main`; weekly cron | yes |
| `pre-cutover-verification.yml` (`verify`) | PR, **paths-filtered**: `supabase/migrations/**`, `supabase/ci/**`, `app/**`, `tests/integration/**`, itself | only if those paths change |
| `bypassrls-container-check.yml` | push `main`, PR to `main`, 6-hourly cron | yes |
| `eval.yml` | push `main` (paths: `app/core/**`, `eval/fixtures/**`, `eval/runner.py`, `eval/free_text_scoring.py`); nightly cron; dispatch | **no** (post-merge only; see gaps) |
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
| Eval fixtures / scoring | Partial | PR-time: `check_eval_results_reproducible.py`, `check_known_gaps_reproducible.py`, `check_grounding_check_reproducible.py`, `check_eval_model_matches_config.py`, `check_no_heldout_leakage.py`, `check_no_hardcoded_metrics.py`, `render_metrics.py --check` (all in `lint-and-test`, cassette replay, $0). The full `eval.yml` regression run is **push-to-main + nightly only**, not on PRs. | A prompt/`app/core/**` change is not gated by the full eval before merge; the reproducibility checks catch drift in committed results but `eval.yml` itself runs after the merge. |
| `web/` lint | Covered (new) | `web-build`: `npm run lint` | Before this PR: not covered. |
| `web/` typecheck | Covered (new) | `web-build`: `npx tsc -b` | Before this PR: not covered. |
| `web/` production build | Covered (new) | `web-build`: `node scripts/validate-env.mjs` then `npx vite build`, then a smoke check that `dist/index.html` references a hashed JS asset that exists and is non-empty | Before this PR: only Vercel built it, and Vercel's `ignoreCommand` can skip it. Build uses placeholder `VITE_*` values, so it proves the bundle compiles, not that prod env is right. |
| `web/` unit / component tests | Not covered | `web/package.json` has no `test` script or test runner; no `*.test.*` files exist | Nothing tests `web/` behaviour. |
| `web/` e2e / a11y | Partial (live only) | Playwright mount check runs against **live prod** in `web-surface-probe.yml` (nightly) and `app-mount-check.yml` (manual) | Never runs against a PR build. No axe/a11y check anywhere (global rule 15d). |
| `web/` dependency audit | Covered | `npm-audit`: `npm ci` + `npm audit --audit-level=high` | `npm ci` also catches lockfile/peer breakage (the #104 class), but only as an audit-job failure, not named as such. |
| Python dependency audit | Partial (non-blocking) | `pip-audit`: `continue-on-error: true` | Informational only. |
| `site/` HTML / links | Not covered on PRs | `site-deploy.yml` deploys on push to `main` and then runs `check_site_deploy_content.py` against the **live** page. `render_metrics.py --check` (PR) verifies the metric blocks in `site/index.html`, `site/docs/index.html` match eval JSON. | No HTML validity, broken-link, or asset-existence check on PRs; a broken `site/` change is found after it is already deployed. |
| `site/` and `web/` colour contrast | Partial | `check_contrast.py` (PR, `lint-and-test`) checks colour **pairs declared in `design/tokens.json`** only (its own docstring: it cannot catch a page that renders other colours) | No rendered-page contrast check. |
| `Dockerfile` | Not covered on PRs | Image is first built by `deploy-cloud-run.yml` (`gcloud beta builds submit`) **after merge to main**, then deployed with no traffic, smoke-tested on `/health`, then promoted. | A broken `Dockerfile` or `COPY` path fails after merge (protected by the staged rollout, not by a PR check). No Dockerfile lint. |
| `cloudbuild.yaml` | Not covered on PRs | Only exercised by the same post-merge deploy job | Same as above. |
| `ops/budget-killswitch/` (Terraform + Cloud Function `main.py`) | Not covered | No `terraform validate/fmt`; `function/main.py` has no test of its own (grep for `killswitch` under `tests/` finds one string mention in `test_check_no_hardcoded_metrics.py`, not an import; ruff lints it) | |
| `ops/runbooks/`, `ops/firebase-hosting/`, `ops/artifact-registry-cleanup.json` | Not covered | Docs / config; nothing validates them | Low risk; recorded for completeness. |
| `scripts/*` | Partial | Gate scripts (`check_*`) run as `lint-and-test` steps; ruff lints all; unit tests exist for `probe_failover`, `probe_web_surfaces`, `slo_report`, the two `check_*_deploy_is_from_main` scripts. | No tests for `check_app_mounted.py`, `probe_demo_quota.py`, `extract_schema_snapshot.py`, `record_cassettes_via_fallback.py`, `p2_*` (not measured beyond a file-name comparison against `tests/`). `toggle_scheduler_jobs.sh` has no shellcheck. |
| Workflow YAML itself | Not covered | No `actionlint` job | Syntax errors in a workflow are found only when it fails to run. Verified clean for this PR by running `actionlint` locally. |
| Secrets in diff | Covered | `secret-scan.yml` (gitleaks, PR diff scan, checksum-pinned binary) | |
| Dependabot PRs | Covered by same jobs | `ci.yml` has no `paths` filter, so npm/uv/actions bumps run every job. `pre-cutover-verification` is the exception (path-filtered, see above). | Dependabot config covers `uv` `/`, `npm` `/web`, `github-actions` `/`. |

## Findings, ranked by how likely they are to ship a broken change

1. **Only `lint-and-test` is a required check.** `web-build`, `npm-audit`, `secret-scan`, `verify` can all
   fail and the PR still merges. GG must add `web-build` (and should consider the others) under
   Settings -> Branches -> `main` protection rule -> "Require status checks to pass before merging".
2. **Full eval is post-merge only** (`eval.yml` has no `pull_request` trigger). Cheap to add ($0
   cassette replay) but changes CI cost/behaviour, so left as a separate decision.
3. **`Dockerfile`/`cloudbuild.yaml` are first built after merge.** A PR-time `docker build` (no push)
   would catch it earlier.
4. **`site/` has no PR-time validation** of HTML/links/assets; only post-deploy content checks.
5. **`tests/benchmark/` and `web/` tests do not exist in CI** (benchmark excluded; web has no test runner).
6. **`mypy` is scoped to `app/`.**

None of 2-6 are fixed by this PR (scope: the `web-build` job only).
