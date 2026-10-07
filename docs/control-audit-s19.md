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

## Induction log (Z8b)

All commands ran in `C:\Users\gaura\ml-projects\review-iq-wt-z8` with
`C:\Users\gaura\ml-projects\review-iq\.venv\Scripts\python.exe` (no installs; `web/node_modules` was
installed into the worktree with `npm ci`, git-ignored). `$PY` below is that interpreter. "Revert"
lines show the same check passing again and `git status --short` empty. Output is trimmed to the
decisive lines; full transcripts were captured to the session scratchpad, not committed.

### I1 ruff / ruff format / mypy (CI-01..03)

```
printf 'import os\n' > scripts/_z8_bad.py
$ $PY -m ruff check scripts/_z8_bad.py            -> Found 1 error.            exit=1
printf 'x   =   {  "a":1 }\n' > scripts/_z8_bad.py
$ $PY -m ruff format --check scripts/_z8_bad.py   -> 1 file would be reformatted  exit=1
printf 'from __future__ import annotations\n\nx: int = "a"\n' > app/_z8_bad.py
$ $PY -m mypy app/_z8_bad.py   -> error: Incompatible types in assignment ... [assignment]   exit=1
rm of the three files; $PY -m ruff check scripts/ -> All checks passed!  exit=0 ; ruff format --check scripts/ -> 38 files already formatted exit=0
```

### I2 tenant-scoping guard (CI-04)

```
new file app/_z8_unscoped.py: psycopg2.connect(url); cur.execute("select * from reviews")
$ $PY scripts/check_undocumented_pg_connects.py
FAIL: undocumented / unscoped psycopg2 connect sites found:
  app/_z8_unscoped.py:6: f() opens a psycopg2 connection and issues queries, but never calls _set_tenant() ...   exit=1
revert (rm): OK: every psycopg2 connect-and-query call site is tenant-scoped or allowlisted.   exit=0
```

### I3 scheduled-workflow alert script (CI-05)

```
new .github/workflows/_z8_sched.yml with `schedule:` and no alert step
$ $PY scripts/check_scheduled_workflows_alert.py
FAIL: _z8_sched.yml: has a `schedule` trigger but no failure -> GitHub-issue alert path. ...   exit=1
revert: OK: every scheduled workflow in ...\.github\workflows has a failure-alert path.   exit=0
```

### I4 migrations: BYPASSRLS grant and missing postcondition (CI-06, CI-07)

```
printf 'ALTER ROLE review_iq_app BYPASSRLS;\n' > supabase/migrations/29990101000001_z8.sql
$ $PY scripts/check_migrations_no_bypassrls_grant.py
FAIL: migration(s) grant BYPASSRLS outside the allowlist:
  29990101000001_z8.sql:1: grants BYPASSRLS to role 'review_iq_app' and (role, file) is not in ALLOWLIST.   exit=1
$ $PY scripts/check_migrations_have_postconditions.py
FAIL: migration postcondition check:
  29990101000001_z8.sql: no '-- @postcondition:' block and no ALLOWLIST entry ...   exit=1
revert (rm): bypassrls-grant exit=0 ; postconditions: OK: every migration ... declares a well-formed postcondition. exit=0
```

### I5 hand-typed metric (CI-09)

```
append to README.md one line: Our model reaches 97.5 (percent sign) accuracy overall.
$ $PY scripts/check_no_hardcoded_metrics.py
FAIL: hand-typed accuracy-shaped numbers found outside a <!-- METRICS:START/END --> generated block ...
README.md:
  line 517: <the appended line>     exit=1
revert (git checkout -- README.md): OK: no unmarked accuracy-shaped numbers found in tracked Markdown/HTML.  exit=0
```

### I6 render drift (CI-10)

```
sed: 'correct 88.0% of the time for sentiment' -> 'correct 99.0% ...' inside a METRICS block of README.md
$ $PY scripts/render_metrics.py --check
DRIFT: README.md is out of date  ...  FAIL: one or more files are stale relative to the eval JSON ...   exit=1
revert: OK: all metrics blocks match eval/results/*.json.  exit=0   (the consensus_labeling WARN is printed both times)
```

### I7 held-out leakage into a prompt module (CI-11)

```
copy the text of eval/fixtures/_held_out_hindi_hinglish/hien-0004.json into app/core/prompts/_z8_leak.py
$ $PY scripts/check_no_heldout_leakage.py
FAIL: 1 held-out corpus leakage(s) found:
  hien-0004.json -> app\core\prompts\_z8_leak.py: 'after using 1 month i cant say its so go'   exit=1
revert (rm): OK: checked 106 held-out fixtures against 7 prompt-development files ...   exit=0
```

X1 (a held-out review copied verbatim into `eval/fixtures/hi-en/z8_exposed.json`, id `hien-0001`, not in
the ack ledger): `FAIL: 1 held-out exposure problem(s): hien-0001 overlaps a development set
(prompt_visible_dev_fixture) and is not in eval/heldout_exposure_ack.json ...` exit=1; after `rm` exit=0.
X2 (delete the ledger entry for `hien-0004`, a real overlap): `FAIL ... hien-0004 overlaps a development
set ... and is not in eval/heldout_exposure_ack.json` exit=1; restore exit=0. X3 (add a ledger entry for
`hien-0001`, which does not overlap): `FAIL: 1 held-out exposure problem(s): hien-0001 is in
eval/heldout_exposure_ack.json but no longer overlaps any development set: the ledger is stale.` exit=1.

### I8 eval/config model mismatch (CI-12)

```
config.py: default="openai/gpt-oss-20b" -> "openai/gpt-oss-20b-z8"
$ $PY scripts/check_eval_model_matches_config.py
FAIL: eval/results.json was measured under a different model config than app/core/config.py's current defaults:
  groq_model_small: results.json says 'openai/gpt-oss-20b', config.py's current default is 'openai/gpt-oss-20b-z8'   exit=1
revert (git checkout -- app/core/config.py): PASS: ... matches app/core/config.py's current defaults.   exit=0
```

### I9 contrast (CI-13)

```
tokens.json: dark-bg-ember-accent fg #E8823A -> #3A2A1A
$ $PY scripts/check_contrast.py | grep FAIL
FAIL: contrast check violations found:
  [FAIL] dark-bg-ember-accent: bg=#1C1A17 fg=#3A2A1A ratio=1.26 (min 4.50)   exit=1
I9b: same pair with "minRatio": 1.0 (relaxed below AA)  -> exit=1
revert (git checkout -- design/tokens.json): OK: all 6 required contrast pairs meet AA ...   exit=0
```

### I10 manifest provenance (CI-18)

```
.portfolio/metrics.json source_file -> eval/results/nonexistent_z8.json
$ $PY scripts/check_manifest_provenance.py
FAIL: 1 metric(s) in .portfolio\metrics.json have no committed artifact:
  - reviewiq:extraction-eval: source_file 'eval/results/nonexistent_z8.json' is not a tracked file (missing or gitignored)   exit=1
revert: OK: every .portfolio\metrics.json metric with a file citation is committed ...   exit=0
```

### R1-R3 reproducibility trio (CI-14..16), env `EVAL_CASSETTE_MODE=replay API_KEY=ci-key`

```
R1 baseline: PASS: eval/results.json and eval/results/latest.json reproduce exactly ...  exit=0
   (side effect: the run rewrote eval/report.md, eval/results.json, eval/results/latest.json; reverted with git checkout -- eval/)
   tamper "overall_score" in eval/results/latest.json:
   FAIL: the following file(s) do not reproduce from committed cassettes (excluding generated_at/git_sha): eval/results/latest.json   exit=1
R2 tamper one number in eval/results/known_gaps_n106.json:
   FAIL: eval/results/known_gaps_n106.json does not reproduce from committed fixtures and held_out_scoring_v2.json.   exit=1
R3 tamper one number in eval/results/grounding_check_fpr_n106.json:
   FAIL: eval/results/grounding_check_fpr_n106.json does not reproduce from committed held_out_scoring_v2.json and fixtures.   exit=1
each revert: PASS ... exit=0, tree clean
```

### B1/B2 BYPASSRLS on a throwaway Postgres 17 (WF-06, WF-07)

CI's `bypassrls-container-check.yml` uses `postgres:16` and `pre-cutover-verification.yml` uses
`postgres:17`; everything here ran on 17 (a version difference this audit did not test).
Container `z8-pg` (`docker run -d --name z8-pg -e POSTGRES_PASSWORD=... -p 55432:5432 postgres:17`),
built with `CI_PG_HOST=127.0.0.1 CI_PG_PORT=55432 $PY supabase/ci/apply_migrations_ci.py` (16 migrations
through `push.py` as the non-superuser migrator, exit 0).

```
B1 without the cutover file (the step "Confirm review_iq_app does not hold BYPASSRLS", verbatim query):
   rolbypassrls=[t]  -> step would: exit 1
   $ pytest tests/integration/test_role_bypassrls.py -v -m integration --no-cov
   AssertionError: review_iq_app holds BYPASSRLS -- every RLS policy on every table is bypassed ...
   FAILED ...::TestRoleBypassRLS::test_review_iq_app_does_not_hold_bypassrls   1 failed, 3 passed   exit=1
B2 apply supabase/cutover/20260801000001_statement4_revoke_bypassrls.sql (docker exec psql -f -)
   rolbypassrls=[f]; the same 4 tests: 4 passed   exit=0
D0 baseline `push.py --verify --target ci` (as migrator): summary: PASS=92  exit=0
```

Baseline of the whole public-service integration step (CI flags, 3 deselected, `test_resend_e2e` ignored):
`172 passed, 14 deselected in 263.98s`.

### RLS-1 RLS disabled (TS-06, WF-07)

```
docker exec z8-pg psql -U postgres -c "ALTER TABLE public.extractions DISABLE ROW LEVEL SECURITY;"
$ pytest tests/integration/test_rls_isolation.py test_adversarial_cross_tenant.py test_leads_rls.py test_authenticity_isolation.py
FAILED ...test_rls_isolation.py::TestRLSIsolation::test_org_a_sees_only_own_extraction - Org A must NOT see org B extraction
FAILED ...::test_org_b_sees_only_own_extraction, ::test_org_a_cannot_delete_org_b_extraction,
       ::test_no_org_context_sees_nothing, ::test_org_a_cannot_insert_into_org_b (DID NOT RAISE InsufficientPrivilege),
       ::test_nonexistent_org_id_sees_nothing, TestReviewIqAppCredentialIsolation::test_review_iq_app_cross_org_read_blocked,
       test_adversarial_cross_tenant ...::TestVector4DirectAppRoleConnection (2)
10 failed, 42 passed   (one of the 10, TestVector2ForgedJWT::test_garbage_jwt..., fails for an unrelated reason: supabase_url is required; it is one of the 3 tests CI deselects, see F9)
revert: ALTER TABLE ... ENABLE ROW LEVEL SECURITY  -> 1 failed (the same deselected test), 51 passed
```

### D1 postcondition truth (WF-07, TS-08)

```
GRANT EXECUTE ON FUNCTION public.current_org_id() TO anon;
$ SUPABASE_DIRECT_URL=<migrator> $PY supabase/push.py --verify --target ci
summary: FAIL=1, PASS=91   target=ci; exit 1          (exit=1)
REVOKE ...; again: summary: PASS=92  exit 0
```

### D2 migration-drift (WF-09), before and after #277 (finding F1)

First attempt (delete the newest ledger row only, effect present) exited 0 under both versions: that
is the intended "bookkeeping gap" case. The decisive induction deletes the ledger row of
`20260917000001_migrations_table_anon_grant.sql` **and** undoes its effect:

```
DELETE FROM public._migrations WHERE filename='20260917000001_migrations_table_anon_grant.sql';
GRANT SELECT ON public._migrations TO anon;
--- BEFORE (git show origin/main:supabase/push.py)
$ SUPABASE_DIRECT_URL=<superuser> $PY supabase/push_prefix_z8.py --dry-run --fail-on-pending --target ci
42 already applied, 1 would apply:
  WOULD APPLY (no recognizable objects to check): 20260917000001_migrations_table_anon_grant.sql        exit=0
--- AFTER (fixed push.py)
42 already applied, 1 would apply:
  WOULD APPLY (objects missing or postcondition false -- genuinely unapplied): 20260917000001_...sql
      postcondition migrations_ledger_closed_to_anon_and_authenticated: returned False (must be exactly true)
1 file(s) merged but not applied to this target -- failing (--fail-on-pending).                           exit=1
--- effect restored (REVOKE ALL ... FROM anon, authenticated), row still missing:
  WOULD APPLY, BUT OBJECTS ALREADY EXIST OR POSTCONDITIONS ALREADY HOLD -- likely applied out-of-band     exit=0
ledger row restored: 43 already applied, 0 would apply  exit=0 ; --verify PASS=92 exit=0
```

How many migrations are in this blind class (computed with `push._expected_objects` and
`push._expected_grant_states` on every file): **13 of 43**: `20260511000001`, `20260511000002`,
`20260511000003`, `20260511000005`, `20260613000001`, `20260619000001`, `20260621000002`,
`20260710000001`, `20260911000001`, `20260912000002`, `20260912000003` (the REVOKE that silently did
nothing in production), `20260917000001`, `20260920000003`.

### D3 schema drift end to end (WF-10)

```
$PY scripts/extract_schema_snapshot.py <dsn> > snapA.json   (123886 bytes)
check_schema_drift.py snapA snapA -> OK: migration-built schema matches production exactly ...  exit=0
ALTER TABLE public.extractions ADD COLUMN z8_drift text;  extract -> snapB
check_schema_drift.py snapB snapA -> FAIL: 1 schema difference(s) ... [columns] ('extractions','z8_drift'): present in PRODUCTION, absent from the migration-built schema   exit=1
DROP COLUMN; extract -> snapC; snapC vs snapA -> OK exit=0
two '{}' snapshots -> "ephemeral snapshot has no `rls_enabled` rows -- it was not taken from a real schema" ... exit=1
```

### W0-W7 `test_workflow_controls.py` (TS-12, WF-23)

Baseline 11 passed (15 after #277). Each mutation, then `git checkout -- .github`:

```
W1 `|| true` appended to the "Unit tests" run in ci.yml   -> FAILED test_no_failure_swallowing_shell_idioms  exit=1
W2 continue-on-error: true on the ruff step               -> FAILED test_continue_on_error_only_where_allowlisted
W3 always() dropped from eval.yml's alert step            -> FAILED test_alert_step_runs_after_a_failed_step
W4 job lint-and-test renamed                              -> FAILED test_required_status_check_jobs_exist_in_ci_yml[lint-and-test]
W5 job diff-scan renamed in secret-scan.yml               -> 11 passed  exit=0   (a required context unpinned: F4b)
W6 new scripts/check_z8_new.py mentioned only in a ci.yml COMMENT -> 11 passed exit=0   (F4a: passes without being wired)
   (without the comment it fails: FAILED ...test_every_check_script_is_wired_into_a_workflow_or_declared_manual)
W7 alert step `if: false` in eval.yml                     -> test FAILED, but scripts/check_scheduled_workflows_alert.py exit=0
```

After #277: W5 -> `FAILED test_required_status_check_jobs_exist[diff-scan]` exit=1; W6 -> `FAILED
test_every_check_script_is_wired_into_a_workflow_or_declared_manual` exit=1; revert 15 passed.

### A1-A6 application-level controls (TS-01..05, CI-08)

```
A1 new app/api/_z8_route.py calling extract_with_llm with no injection controls
   FAILED test_injection_controls_wiring.py::test_every_extraction_call_site_goes_through_the_controls  1 failed, 4 passed
A2 app/api/demo.py quota-check except-branch `return False` -> `return True`
   FAILED test_demo_quota.py::test_quota_check_db_error_fails_closed   1 failed, 13 passed
A3 app/core/storage_pg.py:45 `_set_tenant(cur, org_id)` -> `pass`
   FAILED test_storage_pg.py::test_record_quota_request_pg_sets_rls_context  1 failed, 50 passed
   AND scripts/check_undocumented_pg_connects.py: app/core/storage_pg.py:33: record_quota_request_pg() ... never calls _set_tenant()   exit=1
A4 injection_guard classifier error returns False (fail open)
   FAILED test_classifier_exception_fails_closed, test_non_numeric_response_fails_closed   2 failed, 4 passed
A5 hook_guard_secrets.py: `.env` pattern neutered
   FAILED 7 tests in test_hook_guard_secrets.py (e.g. test_pre_denies_secret_printing[...])   7 failed, 22 passed
A6 pytest tests/unit/test_wilson.py (default addopts, --cov-fail-under=60)
   FAIL Required test coverage of 60% not reached. Total coverage: 31.39%   exit=1
each revert: the same file passes (14 / 51 / 6 / 29 / 5 passed), tree clean
```

Full unit step as CI runs it, base 67ed4bd: `2078 passed in 566s`, total coverage 82.30 percent.

### E1-E5 web-build steps (CI-20..24), env = the job's placeholder VITE_* values

```
E0 baseline: validate-env OK; npm run lint exit 0; npx tsc -b exit 0; npx vite build built in 3.96s; smoke: OK: dist/index.html -> dist/assets/index-Sbid6pOz.js
E1 unset VITE_API_URL: FAIL: missing required env var(s) for web/ build: VITE_API_URL   exit=1
E2 new web/src/_z8.tsx with an unused const: 2:9 error 'unused' is assigned a value but never used  @typescript-eslint/no-unused-vars   exit=1
E3 new web/src/_z8.ts `export const z8: number = "not a number";`: error TS2322 ... exit=2 ; removed: exit=0
E4 `import './z8_missing_module';` prepended to web/src/main.tsx: vite build -> unresolved import error   exit=1 ; git checkout -- web/src/main.tsx
E5 mv the hashed asset: FAIL: dist/assets/index-Sbid6pOz.js missing or empty  exit=1 ; restored: OK exit=0 ; rm dist/index.html: FAIL: dist/index.html missing exit=1
```

### G1-G3 eval gate (WF-01), replay env

```
G0 baseline `python -m eval.runner`: exit=0
G1 PASS_THRESHOLD 0.76 -> 0.80 : exit=1
G2 ground_truth.product overwritten in 132 dev fixtures: hi-en FAIL (gate 75) ... exit=1
G3 eval/cassettes/cassettes.json moved away: en 0.0 FAIL, hi-en 0.0 FAIL  exit=1  (loud, not a silent pass or a live call)
each revert: exit=0
```

### K0-K2 docker-build (WF-03)

```
$ docker build -q -t z8-smoke:base .  -> exit 0 ; docker run --network none ... ; docker exec -i z8-smoke python - < scripts/smoke_container_health.py
OK: HTTP 200 {"db": "ok", "db_backend": "sqlite", "provider": "not_configured", "status": "ok"}   smoke exit=0
append `import pytest` (dev-only) to app/main.py; rebuild; run; smoke exit=137   (CI's 2026-09-20 failure was also exit 137)
git checkout -- app/main.py ; rebuild ; smoke exit=0 ; my three images removed
```

### S1 gitleaks (WF-04, WF-05), local gitleaks 8.21.2 = the version pinned in `secret-scan.yml`

```
throwaway local branch z8-gitleaks-tmp, one commit adding scripts/_z8_secret.txt with a fake ghp_ token
$ gitleaks detect --source . --log-opts="<base>..<head>" --no-banner -v
Finding: token = "ghp_R4nd0m..."  RuleID: github-pat  File: scripts/_z8_secret.txt   leaks found: 1   exit=1
same command on <base>..<base>: no leaks found   exit=0
checked out the audit branch; the tmp branch (1 commit, 34f1854, mine, never pushed) deleted in a separate command
```

### C1 site content (WF-13), real `check_site_deploy_content.py` pointed at local servers

```
blank <div id="root"> shell : FAIL ... missing expected markers ['#F6C042', '#E8823A', 'id="problem"', 'id="pricing"', 'id="how-it-works"']  exit=1
repo site/ directory        : OK: ... contains all 5 expected markers.  exit=0
directory listing (no index): FAIL ... exit=1
```

### M1-M3 SPA mount check (WF-15, WF-16): the F3 evidence

`web/` built twice with `vite build`, served locally with an SPA fallback, checked with the real
`check_app_mounted.py` in headless Chromium (same installed chromium Playwright uses in CI):

```
BEFORE #277
real build WITH placeholder VITE_* env     : OK: ... mounted React and rendered 'Samidha Reviews'.   exit=0
real build WITHOUT any VITE_* env          : OK: ... mounted React and rendered 'Samidha Reviews'.   exit=0   <-- passes the failure it exists for
static shell, brand only in <title>        : FAIL: ... #root is empty after networkidle ...          exit=1
AFTER #277
real build WITH env                        : OK ...  exit=0
real build WITHOUT VITE_* env              : FAIL: ... rendered the app's own configuration-error screen ('is misconfigured', web/src/main.tsx): a required VITE_* variable is missing in this deployment, so the app did NOT start.   exit=1
static shell                               : FAIL ...  exit=1
```

Why: `web/src/main.tsx` renders `<h1>Samidha Reviews is misconfigured</h1>` when a required variable is
missing, and both scripts match any `<h1>` whose text contains "Samidha Reviews".

### H1-H3 workflow step scripts executed verbatim from the YAML (`runstep.py` extracts `run:` with PyYAML)

```
H1 uptime-alert.yml "Probe /health" vs local servers
   200 {"status":"ok"}               status=200
   503 {"status":"unhealthy"}        First probe: HTTP 503 / Retry probe: HTTP 503   status=503   (DOWN)
   200 "<html>Maintenance page</html>" status=200   (counted as up: F7)
   connection refused                status=000000 (curl -w prints 000 and `|| echo "000"` appends another) -> not 200 -> DOWN
H2b db-backup.yml "Verify dump integrity" vs REAL pg_dump 17 output of three databases
   BEFORE #277  full data dump  -> Dump passed all checks            step exit=0
                schema-only (-s) -> missing expected token 'COPY public'   step exit=1
                every table empty -> Dump passed all checks. Uncompressed lines: 68   step exit=0   <-- F2
   AFTER  #277  full data dump  -> [ok] public.organizations has 1 row(s) ... passed   exit=0
                schema-only     -> exit=1 (same message)
                every table empty -> ::error::public.organizations has 0 rows in the dump -- an empty-looking backup. Aborting.  exit=1
H3 demo-quota-probe.yml "Run demo quota probe" with a fake `uv` on PATH
   uv exit 0 -> exit_code=0 | exit 1 -> 1 | exit 2 without the probe's own 429 line -> 99 | exit 137 -> 137 | exit 2 with the 429 line -> 2
```

### R4 restore verifier (WF-21), real `pg_dump` of `z8-pg` restored into scratch databases, `verify_restore.py` verbatim

```
R4a faithful restore of a dump with one organization row: restore ERROR lines: 0 ... RLS tables dump/restored: 16/16; policies: 35/35  RESTORE VERIFIED  exit=0
R4b DELETE FROM organizations in the restored copy: MISMATCH: public.organizations: dump has 1 rows, restored 0   exit=1
R4c ALTER TABLE extractions DISABLE ROW LEVEL SECURITY after a clean restore: MISMATCH: RLS-enabled tables: dump 16, restored 15   exit=1
R4d an all-empty backup restores faithfully: MISMATCH: public.organizations has 0 rows in the dump (empty-looking backup)   exit=1
scratch databases dropped
```

### N1 npm audit gate (CI-19), real `npm audit --json` output (network) fed to `evaluate()`

```
real audit, today: []  (high/critical nodes: braces, chokidar, fast-glob, micromatch, tailwindcss: all via the dated braces allowlist)
real audit, date 2027-01-01: ['braces: allowlist for GHSA-vfj7-8cjw-p6xm expired 2026-12-31: ...']
injected un-allowlisted high advisory: ['z8-fake-pkg: GHSA-zzzz-zzzz-zzzz (high) is not allowlisted']
{"error": ...} (no vulnerabilities key): ["unparseable npm audit output (keys: ['error']); failing closed"]
main() when the second npm audit call itself failed (network blip): FAIL: npm audit gate / unparseable ... failing closed  exit=1
```

### Y1/Y2/P2 probes and fail-closed branches

```
probe_demo_quota.py --url <local server>: 200 valid body -> exit 0 | 500 -> exit 1 | 429 -> exit 2 | 200 with HTML -> JSONDecodeError traceback exit 1 | refused -> ConnectError exit 1
probe_failover.py with GEMINI_API_KEY and SECONDARY_PROVIDER_API_KEY unset: 2 of 2 failover path(s) BLOCKING the job: ... NOT CONFIGURED (NOT acknowledged)  exit=1
check_prod_deploy_is_from_main.py with CLOUDFLARE_* unset: FAIL: could not determine the production deployment's commit hash ... treating as unverified, not as a pass   exit=1
```

### HK-01 hook logic (5 payloads piped to `scripts/hook_guard_secrets.py`)

```
PreToolUse Bash "cat /c/x/review-iq/.env 2>/dev/null | wc -l" -> {"permissionDecision": "deny", ... names a secret-bearing file ...}
PreToolUse Bash "ls -la"                                        -> no output (allow)
not-JSON payload                                                -> deny: "hook_guard_secrets could not evaluate this call (JSONDecodeError); denied."
PostToolUse output containing "postgresql://u:<password>@db.example.com:5432/x" -> {"decision": "block", "reason": "SECRET EXPOSED ..."}
(one malformed Read payload of mine also denied for the same JSON reason; not counted)
```

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
