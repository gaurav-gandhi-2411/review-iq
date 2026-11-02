# Control-class audit (Session 19, Z8)

Doctrine (rule 85a): a control passes for a reason only if you can name its surface and have seen it
fail. Seven controls in this repo were found decorative after being assumed correct (see
`docs/decorative-control-sweep.md`, `docs/ci-coverage-audit.md`, S15d U2d,
`tests/unit/test_workflow_controls.py`). This is one more pass, done by **mutating the real input or
source in a throwaway worktree and running the real check**, not by reading it.

Base: `origin/main` 67ed4bd for the inductions and the first table pass; #277 was pushed on
45c0f5f and this branch is rebased on 4e568a3 (main moved twice during the session). Nothing here touched production, a secret, an LLM, or `main`. Every mutation was local,
reverted (`git checkout --` inside the worktree, or `rm` of a file the mutation created) and the tree
re-verified clean; no broken code was pushed to induce a failure.

Companion fix PR: **#277** (`fix/s19-decorative-control-gaps`), four commits, one per decorative
control found. This document's findings F1-F4 are fixed there; F5-F9 are reported, not fixed.

## Result in one paragraph

**66 control rows**: 47 in tables A and B (CI and workflows, with a before and an after verdict) and
19 in C to E (scripts, test families, hook, one verdict). Tallied from the A and B tables by
`tally.py` (not committed; it only counts the last two cells of each row).

Before this pass, A and B: 22 PROVEN (a real CI failure on record), 8 LOGIC-ONLY, 11
NEVER-SEEN-FAIL, 6 DECORATIVE-SUSPECT. After inducing the real check locally: 21 PROVEN by a real CI
failure, 18 PROVEN by induction here (three only in part: WF-05 engine, WF-08 logic, WF-14 fail-closed
branch), **4 rows DECORATIVE and fixed in #277** (WF-09 migration-drift, WF-15 and WF-16 mount checks,
WF-20 db-backup), 2 DECLARED (cannot fail by design: CI-17, WF-02), 2 UNPROVEN (WF-11, WF-12: need
provider keys, GCP or Cloudflare). In C to E, TS-12 (`test_workflow_controls.py`, two members) was also
decorative and is fixed in #277, TS-10 (`tests/benchmark`) never runs in CI, and HK-01 (the transcript
secret hook) was shown **inactive in the checkout this session ran in** (F5). Four distinct findings
fixed (F1 to F4), five reported (F5 to F9).

## How to read the table

- **Req**: Y if the context is in `required_status_checks` on `main`. Read with
  `gh api repos/gaurav-gandhi-2411/review-iq/branches/main/protection` on 2026-10-07:
  `strict: true`, `enforce_admins: true`, contexts `lint-and-test`, `web-build`, `bypassrls-check`,
  `diff-scan`, `manifest-provenance` (5). Everything else is advisory.
- **CI fails seen**: failed workflow runs in the repo's whole history, from
  `GET /actions/workflows/<wf>/runs?status=failure` (paginated, de-duplicated by run id), with the
  failing step name from `GET /actions/runs/<id>/jobs`. Reason class: **REAL** (the control caught a
  genuine defect), **WIRING** (the control itself was misconfigured, e.g. wrong URL), **INFRA**
  (credential, permission, network, quota). A WIRING/INFRA failure does not count as "shown to fail".
  The cause was read from the run log for the entries marked (log); otherwise it is inferred from the
  step name and stated as inferred.
- **Logic test**: a unit test feeds a bad input to the logic (LOGIC-FAIL-TESTED). That proves the code,
  not the wiring.
- **Induced here**: Z8b induction ID (log below), or N.
- **Before**: PROVEN (real CI failure seen) / LOGIC-ONLY / NEVER-SEEN-FAIL / DECORATIVE-SUSPECT.
  **After**: PROVEN (CI) / PROVEN (induced) / DECORATIVE -> FIXED #277 / DECLARED (cannot fail by design) /
  UNPROVEN (reason).
- "PROVEN (induced)" means the real script/test/step ran against a violating input and exited non-zero
  and passed after revert. For a CI step it additionally relies on `test_workflow_controls.py`
  (no `continue-on-error`, no `|| true`) for the claim that a failing command fails the job; that
  test was itself induced (W1-W7).

## Inventory

### A. `.github/workflows/ci.yml`

| ID | Control | Surface it covers | A gap at the edge looks like | Req | CI fails seen (reason) | Logic test | Induced | Before | After |
|---|---|---|---|---|---|---|---|---|---|
| CI-01 | `ruff check .` (lint-and-test) | every `.py` in repo, rules in `pyproject.toml` | a path in ruff `exclude`, a blanket `noqa` | Y | 2 (REAL, 2026-09-11) | n/a (tool) | I1 | PROVEN | PROVEN (CI) |
| CI-02 | `ruff format --check .` | every `.py` and the Python code blocks in `*.md` (the 2026-09-19 failure was in `plan.md`) | formatter version drift between local and CI | Y | 24 (REAL, 2026-07-31..09-19) | n/a | I1 | PROVEN | PROVEN (CI) |
| CI-03 | `mypy app/` | `app/` only (strict); not `scripts/ eval/ tests/ benchmark/` | a type error outside `app/` | Y | 2 (REAL, 2026-08-01, 2026-10-05: `dataset.py` annotation) (log) | n/a | I1 | PROVEN | PROVEN (CI) |
| CI-04 | `check_undocumented_pg_connects.py` | `app/**/*.py` psycopg2 connect+query sites; `_set_tenant` anywhere in the body | `_set_tenant` after the query; connection passed as a parameter; aliased import (fixed in S15d) | Y | 2 (REAL, 2026-09-10: `aggregate_extraction_costs_pg` unscoped) (log) | 10 tests | I2 | PROVEN | PROVEN (CI) |
| CI-05 | `check_scheduled_workflows_alert.py` | every workflow with `schedule:` has a step using the alert action (text scan) | alert step gated `if: false` passes this script | Y | 0 | 19 tests | I3, W7 | LOGIC-ONLY | PROVEN (induced); the `if: false` shape is caught by TS-12 instead (W7) |
| CI-06 | `check_migrations_no_bypassrls_grant.py` | migrations: `ALTER/CREATE ROLE .. BYPASSRLS`, multi-line, EXECUTE strings; empty dir fails | `GRANT <role> TO x`, assembled string `'BYPAS'\|\|'SRLS'` (documented) | Y | 0 | 21 tests | I4 | LOGIC-ONLY | PROVEN (induced) |
| CI-07 | `check_migrations_have_postconditions.py` | every migration has a well-formed `-- @postcondition:` or a reasoned ALLOWLIST entry | a postcondition that exists but is not TRUE (truth is `push.py` + pre-cutover, not this) | Y | 0 | 57 tests | I4 | LOGIC-ONLY | PROVEN (induced) |
| CI-08 | `pytest tests/ --ignore=integration --ignore=benchmark` with `--cov-fail-under=60` | `tests/unit`, flat `tests/test_*.py`; floor is 60, measured 82.30 percent (this session, base 67ed4bd, 2078 passed) | a test that asserts nothing (24 of 1860 had no direct assert per S15d); floor far below actual | Y | 9 (REAL: 2026-09-19 router tests; 2026-10-07 structural workflow test) (log) | n/a | A1-A6 | PROVEN | PROVEN (CI); floor itself induced (A6: 31 percent -> exit 1) |
| CI-09 | `check_no_hardcoded_metrics.py` | tracked `*.md`/`*.html`, percent-shaped numbers within a keyword window | decimals-as-words, any marker span (pinned KNOWN_GAP) | Y | 5 (REAL, 2026-07-31..09-20) (log) | 18 tests | I5 | PROVEN | PROVEN (CI) |
| CI-10 | `render_metrics.py --check` | METRICS marker blocks with a renderer vs `eval/results/*.json` | `consensus_labeling` block has no renderer (WARN only) | Y | 7 (REAL, 2026-09-05..09-12) | 50 + 9 + 9 tests | I6 | PROVEN | PROVEN (CI); known WARN remains |
| CI-11 | `check_no_heldout_leakage.py` | held-out reviews vs prompt files (40-char windows), `PROMPTS.md`, plus whole-review overlap with dev fixtures/benchmark gold against the ack ledger | paraphrase (documented); other file types | Y | 0 | 10 tests (+21 for the exposure ledger) | I7, X1, X2, X3 | NEVER-SEEN-FAIL | PROVEN (induced, 4 shapes) |
| CI-12 | `check_eval_model_matches_config.py` | `config.py` defaults vs `eval/results.json` provenance | a deployed env override (docstring says so) | Y | 1 (REAL, 2026-09-05) | 5 tests | I8 | PROVEN | PROVEN (CI) |
| CI-13 | `check_contrast.py` | WCAG AA for pairs listed in `design/tokens.json` only; known-failing pairs must keep failing | any colour not in `contrastPairs` | Y | 0 | 12 tests | I9, I9b | LOGIC-ONLY | PROVEN (induced) |
| CI-14 | `check_eval_results_reproducible.py` (needs `EVAL_CASSETTE_MODE=replay`) | regenerates `eval/results.json`+`latest.json` by cassette replay, compares sans provenance fields; exit 1 if gate fails | rewrites the tracked files as a side effect | Y | 0 | 13 tests | R1 | NEVER-SEEN-FAIL | PROVEN (induced) |
| CI-15 | `check_known_gaps_reproducible.py` | `known_gaps_n106.json` byte-compare after regeneration | | Y | 2 (REAL, 2026-09-17) (log) | n/a | R2 | PROVEN | PROVEN (CI) |
| CI-16 | `check_grounding_check_reproducible.py` | `grounding_check_fpr_n106.json` byte-compare | | Y | 2 (REAL, 2026-09-17) | n/a | R3 | PROVEN | PROVEN (CI) |
| CI-17 | `pip-audit` (`continue-on-error: true`) | Python CVEs, informational | cannot fail by design | N | n/a | allowlisted in TS-12 | n/a | DECORATIVE-SUSPECT | DECLARED |
| CI-18 | `check_manifest_provenance.py` (job `manifest-provenance`) | `.portfolio/metrics.json` `source_file` is a tracked file | a `source_file` with a space or `(` only warns; absent manifest passes; the value is never compared | Y | 0 | 6 tests incl 2 KNOWN_GAP | I10 | NEVER-SEEN-FAIL | PROVEN (induced) for the missing-file shape |
| CI-19 | `npm_audit_gate.py` (job `npm-audit`) | web/ high/critical advisories minus a dated allowlist (braces, expires 2026-12-31); fails closed on unparseable output | not a required check, so red does not block a merge | N | 58 runs (REAL CVEs, 2026-08-16..10-07; plus 6 install/peer failures) | 4 tests | N1 | PROVEN | PROVEN (CI) + induced (4 shapes) |
| CI-20 | web-build: `validate-env.mjs` | 3 VITE_* vars present (CI uses placeholders) | placeholder values always satisfy it; the real check is Vercel's | Y | 0 | n/a | E1 | NEVER-SEEN-FAIL | PROVEN (induced) for missing var |
| CI-21 | web-build: `npm run lint` (eslint) | `web/` `**/*.{ts,tsx}` recommended sets | | Y | 0 | n/a | E2 | NEVER-SEEN-FAIL | PROVEN (induced) |
| CI-22 | web-build: `npx tsc -b` | `web/` | | Y | 0 | n/a | E3 | NEVER-SEEN-FAIL | PROVEN (induced) |
| CI-23 | web-build: `npx vite build` | `web/` bundle compiles | | Y | 1 (REAL, 2026-09-20, tailwind 4 PR) (log) | n/a | E4 | PROVEN | PROVEN (CI) |
| CI-24 | web-build: dist smoke check (index.html + hashed JS, non-empty) | `web/dist` | | Y | 0 | n/a | E5 (shell copied from the step, not extracted from YAML) | NEVER-SEEN-FAIL | PROVEN (induced) |

### B. Other workflows

| ID | Control | Surface | A gap at the edge looks like | Req | CI fails seen (reason) | Logic test | Induced | Before | After |
|---|---|---|---|---|---|---|---|---|---|
| WF-01 | `eval.yml` `python -m eval.runner` (replay) | PRs/pushes touching `app/core/**`, eval fixtures, runner, cassettes; nightly. Path-filtered so it cannot be required | a PR outside those paths; a threshold that a score sits just above | N | 66 runs: 58 scheduled/live-era (INFRA/quota, inferred), 2026-09-20 PR run REAL (replay: one language scored zero) (log) | `test_eval_workflow_paths` (4) | G1-G3 | PROVEN | PROVEN (CI) + induced |
| WF-02 | `eval.yml` Slack step | skips cleanly when `SLACK_WEBHOOK_URL` is unset (secret list: not set) | never sends | N | 9 early runs failed in this step (`unknown url type ''`, 2026-06-14) (log) | n/a | n/a | DECORATIVE-SUSPECT | DECLARED (known) |
| WF-03 | `docker-build.yml` build + `--network none` + `/health` smoke | PRs touching Dockerfile, `pyproject.toml`, `uv.lock`, `app/**` (path-filtered) | a dev-only import; a boot path that dials out | N | 1 (REAL, 2026-09-20: smoke step, container exit 137) (log) | 10 tests (`test_smoke_container_health`) | K0-K2 | PROVEN | PROVEN (CI) + induced (same exit 137) |
| WF-04 | `secret-scan.yml` `diff-scan` (gitleaks 8.21.2, `base..head`) | new commits of the PR, `.gitleaksignore` (20 fingerprints, bound pinned) | a secret outside the diff range; a fingerprint added to the ignore file | Y | 2: 2026-09-10 false positive (benchmark file; fingerprint then ignored); 2026-09-21 a **deliberately planted** `_s16_induced_secret.txt` (log) | n/a | S1 (same pinned version, local) | PROVEN | PROVEN (CI, induced in CI by S16) + induced |
| WF-05 | `secret-scan.yml` `full-history-scan` (weekly) | full history | same engine; the weekly run is the only reader | N | 0 of 1 scheduled run | n/a | S1 (engine) | NEVER-SEEN-FAIL | PROVEN (induced, engine only); scheduled wiring UNPROVEN |
| WF-06 | `bypassrls-container-check.yml` (job `bypassrls-check`) | throwaway Postgres 16, `push.py --target ci`, cutover file, `test_role_bypassrls.py` (4 tests: 2 read pg_roles, 2 cross-org sweeps) | tests run as the `postgres` superuser here; they only read `pg_roles` | Y | 1 (WIRING, 2026-08-17: test used a production hostname, "Network is unreachable") (log) | 4 tests | B1, B2 | NEVER-SEEN-FAIL (real) | PROVEN (induced on Postgres 17: without the cutover file `rolbypassrls=t`, 1 failed 3 passed) |
| WF-07 | `pre-cutover-verification.yml` (verify) | PR-triggered, path-filtered; every migration as the non-superuser migrator, `push.py --verify`, BYPASSRLS confirm step, public and admin integration suites (3 tests deselected, `test_resend_e2e` ignored) | a PR touching only `supabase/cutover/**` (covered by WF-06); the 3 deselected vectors | N | 12 (REAL: 8 public-suite test failures on PRs, 2 migration-apply, 1 BYPASSRLS-confirm step, 1 admin suite) | n/a | D0, D1, B1, RLS-1 | PROVEN | PROVEN (CI) + induced |
| WF-08 | `security-bypassrls-check.yml` (production DB, read-only) | `test_role_bypassrls.py` 2 of 4 tests against prod | prod wiring cannot be exercised locally | N | 25 (INFRA/WIRING, 2026-08-16/17: "Network is unreachable" to the DB host; never a real BYPASSRLS finding) | same tests | B1 (logic only) | NEVER-SEEN-FAIL | PROVEN (logic, induced); production wiring UNPROVEN |
| WF-09 | `migration-drift-check.yml`: `push.py --dry-run --fail-on-pending` vs production | merged-but-unapplied migrations; "genuinely unapplied" decided by regex-recognized objects/grants only | **13 of 43 migrations match no pattern: a pending one exits 0** | N | 68 runs (63 scheduled). The one log read (2026-08-17 dispatch) is INFRA, IPv6 "Network is unreachable"; the 2026-10-05 scheduled log was empty; causes of the rest inferred; the latest 6 runs are green | 0 before | D2 | DECORATIVE-SUSPECT | **DECORATIVE (F1) -> FIXED #277**, induced before/after |
| WF-10 | `schema-drift-check.yml` + `check_schema_drift.py` | migration-built schema vs production, 9 sections | two empty snapshots (fixed S15d) | N | 74 runs: 39 `Compare` (REAL drift, inferred: issue #174), 26 `Extract production` (INFRA, inferred), 9 compare+alert | 16 tests | D3 | PROVEN | PROVEN (CI) + induced (extra column -> exit 1) |
| WF-11 | `model-availability-check.yml` + `check_model_generation.py` | configured Groq/Gemini models: listed AND `generateContent` works | model names come from `config.py` defaults, not the deployed env | N | 26 (22 at the Groq step, one log read: generate probe HTTP 403, 2026-09-21; 3 Gemini; 1 GCP auth INFRA) | 16 tests | N | LOGIC-ONLY | UNPROVEN (needs provider keys + GCP WIF) |
| WF-12 | `deploy-cloud-run.yml` smoke of tagged revision + `check_cloud_run_deploy_is_from_main.py` | `/health` 200 on a no-traffic revision; image tag decodes to a commit that is an ancestor of main | | N | 26: 14 scheduled-drift (inferred INFRA; the one dispatch log read is `run.services.get` PERMISSION_DENIED 2026-08-15), 10 Cloud Build INFRA (logs read), 2 other; never a real "not from main" | 11 tests | P1 (see disclosure) | LOGIC-ONLY | UNPROVEN live; **P1 ran the real script against production by mistake (read-only), see Disclosures** |
| WF-13 | `site-deploy.yml`: `check_site_deploy_content.py` | 5 markers in the served page; fails closed on fetch error | stale previous deploy with the same markers (pinned KNOWN_GAP) | N | 0 of 12 | 6 tests | C1 | NEVER-SEEN-FAIL | PROVEN (induced vs local server) |
| WF-14 | `site-deploy.yml`: `check_prod_deploy_is_from_main.py` | Cloudflare deployment commit is on main; fails closed | | N | 0 of 12 | 9 tests | P2 (no credentials -> exit 1) | LOGIC-ONLY | PROVEN (fail-closed branch, induced); the ancestry branch UNPROVEN |
| WF-15 | `app-mount-check.yml` + `check_app_mounted.py` (manual dispatch only) | headless Chromium: `#root` non-empty and an `h1` containing "Samidha Reviews" | **passes the app's own configuration-error screen** | N | 0 of 2 | 0 before | M1-M3 | DECORATIVE-SUSPECT | **DECORATIVE (F3) -> FIXED #277** |
| WF-16 | `web-surface-probe.yml` + `probe_web_surfaces.py` | 4 surfaces; SPA surfaces via Chromium; authenticated path only if `PROBE_API_KEY` set (**not set**: secret list) | misconfigured screen counted on the dashboard surface; authenticated RLS default-deny unwatched | N | 43 (41 probe step, 2 plus alert), 2026-08-01..09-12, since green 5 nights (cause not log-verified; first nights were dashboard/infra) | 16 tests | M1-M3 | DECORATIVE-SUSPECT | dashboard mount: **DECORATIVE (F3) -> FIXED #277**; authenticated path DECLARED (known, secret unset) |
| WF-17 | `uptime-alert.yml` | HTTP 200 on `/health`, one retry | a 200 with a maintenance HTML page is "up" | N | 40 (32 at the `Probe` step, inferred from the workflow comment to be the GITHUB_OUTPUT newline crash of 2026-08-14; 2 `Handle DOWN` designed red; 3 recovery; no log read) | 0 | H1 | LOGIC-ONLY | PROVEN (induced: 503 and refused -> DOWN); F7 surface note |
| WF-18 | `failover-probe.yml` + `probe_failover.py` | Gemini and secondary provider; 3 states, NOT_CONFIGURED blocks | credential-only failures look like code failures | N | 13 (INFRA: key 404/402/absent, per `docs/decorative-control-sweep.md`; no log read) | 22 tests | Y2 | PROVEN | PROVEN (CI) + induced (no keys -> exit 1) |
| WF-19 | `demo-quota-probe.yml` + `probe_demo_quota.py` | nightly POST `/demo/extract`; exit-code handlers 0/1/2/other | | N | 1 (designed-red step, 2026-10-05; cause not read) | 5 tests | H3, Y1 | PROVEN | PROVEN (CI) + induced (5 exit-code cases, 5 server modes) |
| WF-20 | `db-backup.yml` "Verify dump integrity" | non-empty, valid gzip, 3 tokens, `COPY public` | **an all-empty data dump passes** (pg_dump writes COPY for empty tables) | N | 38 (REAL: `missing expected token 'CREATE TABLE'`, 2026-06-13..10-05; green since 2026-10-05; 2 logs read, rest inferred) | 1 string-grep test | H2b | DECORATIVE-SUSPECT | **DECORATIVE (F2) -> FIXED #277** |
| WF-21 | `db-restore-test.yml` + `verify_restore.py` | restore a real artifact into a throwaway PG17; dump-vs-restored tables, rows, RLS, policies, `--require-nonempty organizations` | | N | 1 (2026-10-05: log shows "RESTORE VERIFIED" then exit 1; inferred: psql ERROR lines during restore were non-zero) | 4 tests | R4a-R4d | PROVEN | PROVEN (CI) + induced (row loss, RLS loss, empty dump all fail) |
| WF-22 | `alert-path-canary.yml` + `schedule-failure-alert` action | deliberately fails daily and checks a GitHub issue was updated in 900 s | | N | 1 (2026-09-13, `Verify the alert issue was actually created or updated`; per the sweep doc the labels did not exist; log tail unread) | n/a | n/a (is itself an induction) | PROVEN | PROVEN (CI) |
| WF-23 | alert caller steps in 12 workflows | red scheduled run opens an issue | `if: false`, missing `always()` | N | 1 structural test failure on a PR 2026-10-07 (REAL) | TS-12 | W1-W3, W7 | PROVEN | PROVEN (induced) |

### C. Scripts and guards outside CI wiring

| ID | Control | Wired where | Verdict |
|---|---|---|---|
| SC-01 | `check_site_responsive.py` (real render) | manual by declaration (`test_workflow_controls`) | NEVER-RUN in CI; DECLARED manual |
| SC-02 | `p2_panel_contamination_check.py` | no workflow; experiment tool, 1 test file | LOGIC-ONLY, not a gate |
| SC-03 | `render_metrics.py` marker coverage | CI-10 | see CI-10 |
| SC-04 | `.pre-commit-config.yaml` (ruff, ruff-format, whitespace, EOF, gitleaks) | local only; nothing verifies the hooks are installed; CI re-runs ruff and gitleaks | NEVER-SEEN-FAIL, not enforced; redundant with CI-01/02, WF-04 |
| SC-05 | `.gitleaksignore` growth bound (20) | TS-12 | LOGIC-ONLY (the test exists; the bound was not mutated here) |

### D. Pytest control-style tests (names matching guard, gate, control, drift, provenance, postcondition, undo, contrast, leak, exposure, reproducible, structural; 108 test functions in 48 files)

| ID | Control (test family) | Surface | Induced | Verdict |
|---|---|---|---|---|
| TS-01 | `test_injection_controls_wiring.py` (AST: every extraction call site goes through `injection_controls`) | `app/**/*.py` direct, aliased and attribute calls | A1 (new unguarded call site -> fails) | PROVEN (induced) |
| TS-02 | `test_injection_guard.py` (fails closed on classifier error / non-numeric reply) | `app/core/injection_guard.py` | A4 (fail-open -> 2 fail) | PROVEN (induced) |
| TS-03 | `test_demo_quota.py::test_quota_check_db_error_fails_closed` | `app/api/demo.py` | A2 (fail-open -> fails) | PROVEN (induced) |
| TS-04 | `test_storage_pg.py::*_sets_rls_context` | `app/core/storage_pg.py` write paths | A3 (drop `_set_tenant` -> unit test and CI-04 both fail) | PROVEN (induced) |
| TS-05 | `test_hook_guard_secrets.py` | `scripts/hook_guard_secrets.py` | A5 (neuter `.env` pattern -> 7 fail) | PROVEN (induced) |
| TS-06 | integration RLS suite: `test_rls_isolation`, `test_adversarial_cross_tenant`, `test_leads_rls`, `test_authenticity_isolation` (+ others in `tests/integration`) | real Postgres, role `review_iq_app` | RLS-1 (DISABLE RLS on `extractions` -> 9 fail; restore -> pass) | PROVEN (induced); the other 11 integration files not individually mutated |
| TS-07 | `test_rls_disable_proof.py` (meta-test: the suite can catch a bypass; rolls back) | needs superuser DSN | not induced; ran in the 172-test baseline | UNPROVEN (meta), passes |
| TS-08 | `test_push_postconditions.py` (unit: 35 on main, 41 with #277; plus `tests/integration/test_push_postconditions.py`) | `supabase/push.py` apply/verify/dry-run | D1 (grant to anon -> `--verify` exit 1), D2, F1 | PROVEN (induced); dry-run branch was DECORATIVE (F1) |
| TS-09 | `test_check_*.py` unit files (contrast, heldout, schema_drift, scheduled alert, bypassrls grant, postconditions, model config, eval repro, manifest, site content, pg connects) | logic of the CI-xx / WF-xx scripts | covered by the CI-xx induced runs | LOGIC proven; wiring per row |
| TS-10 | `tests/benchmark/*` (13 files incl. `test_leakage_check.py`, `test_sentiment_scorer.py`) | excluded by `--ignore=tests/benchmark` in `ci.yml` and `pyproject.toml addopts`; no workflow runs them | N | **NEVER-RUN in CI** (F8) |
| TS-11 | experiment-tooling tests (`no_routing_stage1`, `injection_twin_control`, `noise_floor`, `buy_again_fewshot`, `heldout_*`) | the eval tooling, not product gates | N | LOGIC-ONLY, out of control scope |
| TS-12 | `test_workflow_controls.py` | workflow YAML shapes (continue-on-error allowlist, `\|\| true`, alert `always()`, wired scripts, required jobs) | W0-W7 | PROVEN (induced); two members were passing for the wrong reason (F4) -> FIXED #277 |
| TS-13 | skipped/xfail tests | `grep` over `tests/` | n/a | none present (re-checked) |

### E. Hooks

| ID | Control | Surface | Induced | Verdict |
|---|---|---|---|---|
| HK-01 | `.claude/settings.json`: `hook_guard_secrets.py` (Pre and PostToolUse) + `permissions.deny Read(**/.env*)` | commands/paths naming a secret file with a content verb; tool output holding a service-role JWT, a DSN with a password, API-key shapes; fails closed on unparseable payload | logic: 5 payloads fed directly (below); wiring: **not active in this session's checkout** | logic PROVEN (induced); wiring UNPROVEN and shown inactive on an older branch (F5) |

### Required-check surface

`lint-and-test`, `web-build`, `manifest-provenance`, `diff-scan`, `bypassrls-check` are required. **Not
required** though they have failed for real: `npm-audit` (58 real failures), `pre-cutover-verification`
(12), `docker-build`, `eval` (path-filtered, so cannot be required as written). `bypassrls-check` is
the job id in two workflows; on PRs only `bypassrls-container-check.yml` reports it, which is the intent.

> Part 1 of 3 of the Session 19 control-class audit. Siblings: `docs/control-audit-s19.md` (part 1: result, method, inventory), `docs/control-audit-s19-induction-log.md` (part 2: induction log), `docs/control-audit-s19-findings.md` (part 3: findings, unproven, disclosures, coverage).
