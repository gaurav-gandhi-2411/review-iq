# Decorative-control sweep (Session 15d, U2d)

A control is **decorative** when it can report success (exit 0, green, PASS, OK) without
verifying what its name or docs claim. Five were already known: a renderer that hardcoded a
green PASS badge; `probe_web_surfaces.py` matching a string in the static `<title>` (passed a
blank page); the Slack webhook that was never configured; GitHub-issue alerts that errored on
labels that never existed; and `supabase/push.py` recording a migration whose `REVOKE` silently
did nothing. This sweep looked for the same shape everywhere else. Method: for each control,
name its surface and the shapes it must catch **before** testing it (rule 85a/85b), then feed it
a deliberately violating input and record what it did.

Branch `fix/s15d-decorative-control-sweep`, base `f23eb23`. Nothing here needed a live LLM call,
a production database, a secret, a deploy or a settings change.

## Scope actually swept

Swept (read in full, every step):

- `.github/workflows/*.yml`: all 18 files (`alert-path-canary`, `app-mount-check`,
  `bypassrls-container-check`, `ci`, `db-backup`, `demo-quota-probe`, `deploy-cloud-run`, `eval`,
  `failover-probe`, `migration-drift-check`, `model-availability-check`, `pre-cutover-verification`,
  `schema-drift-check`, `secret-scan`, `security-bypassrls-check`, `site-deploy`, `uptime-alert`,
  `web-surface-probe`) and `.github/actions/schedule-failure-alert/action.yml`.
- Shapes searched in those files: `|| true`, `continue-on-error`, `if: always()`, `exit 0`,
  `set +e`, `-q`/`2>/dev/null`, `if:` conditions on `secrets.`/`env.`/`vars.`, `paths:` filters,
  greps whose empty result passes, alert steps whose status is computed from an earlier step,
  steps that skip when a secret is absent, workflows that pass when their target is unreachable.
  A structural test now pins the first four and the alert-step shape for every workflow
  (`tests/unit/test_workflow_controls.py`).
- `scripts/`: `check_*.py` (all 17), `probe_*.py` (all 3), `render_metrics.py --check`.
  Not swept as controls (measurement or one-off tools, no pass/fail claim): `measure_*`,
  `p2_*`, `injection_landing_sample_size.py`, `record_cassettes_via_fallback.py`,
  `extract_schema_snapshot.py`, `slo_report.py`, `toggle_scheduler_jobs.sh`.
- `tests/`: a whole-tree AST scan for test functions with no `assert`/`raises`/`assert_*` call,
  and a grep for `skip`/`xfail`/`importorskip`/`skipif`.
- `.gitleaksignore`, `web/eslint.config.js`, `web/scripts/validate-env.mjs`, `web/vercel.json`,
  the `web-build` job, and the required-check list (GitHub branch-protection API, read-only).
- Repository secret and variable **names** (`gh secret list`, `gh variable list`; values are not
  readable and were not requested) and recent run conclusions/logs (`gh run list`, `gh run view
  --log`), to tell a step that ran from one that was skipped.

Not swept: `supabase/`, `supabase/push.py`, `_migrations` recording (another agent owns the
migration pipeline); application code paths other than the guards that scan them; dependabot;
anything in `benchmark/`, `eval/` scorers themselves (their correctness is a separate question
from whether the gates around them can pass vacuously); the deployed services.

## Findings, by blast radius

| # | Finding | Status |
|---|---|---|
| 1 | Branch protection requires only two checks: `lint-and-test` and `web-build`. `secret-scan`, `bypassrls-container-check`, `npm-audit`, `manifest-provenance` run on PRs but a red result does not block a merge. | **GG action** (not changed here) |
| 2 | `web-surface-probe`'s authenticated `/v2/reviews` probe has never run: `PROBE_API_KEY` is not a repository secret, and the script logs "skipping" and exits 0. The RLS default-deny it exists to catch is unwatched. | Remains; pinned; **GG action** |
| 3 | `README.md`'s `consensus_labeling` block (inter-rater stats and agreement rates) has no renderer, contrary to ADR 0002, and `check_no_hardcoded_metrics.py` exempts every marker span. `render_metrics.py --check` said OK for it. | `--check` fixed; block allowlisted with a reason; **content remains hand-typed and UNVERIFIED** |
| 4 | Held-out corpus vs benchmark: the leakage guard compared only the first 40 characters of each review. Fixed to test every window; that immediately found one review quoted in `PROMPTS.md`. A separate measurement found 21 of 90 held-out reviews (40 characters or longer) also present in `benchmark/dataset/gold.jsonl`. | Guard fixed; overlap **surfaced, not resolved** (see below) |
| 5 | `demo-quota-probe.yml`: any probe exit code other than 0/1/2 (killed, missing `uv`) left every handler skipped and the job green; exit 2 from `uv`/argparse was read as a benign 429. | Fixed |
| 6 | `db-backup.yml` verified a dump by finding `CREATE TABLE` and two table names, all present in a zero-row schema-only dump. | Fixed (requires a `COPY` section); restore never rehearsed, **UNVERIFIED** |
| 7 | `check_schema_drift.py` reported "matches production exactly" for two empty snapshots. | Fixed |
| 8 | `check_undocumented_pg_connects.py` (no tests) missed `import psycopg2 as pg` / `from psycopg2 import connect`, and passed on an empty `app/`. | Fixed, tests added |
| 9 | `check_contrast.py` passed an empty pair list and a pair with `minRatio` lowered to 1.0. | Fixed |
| 10 | No PR-time accuracy gate: `ci.yml`'s reproducibility check treats a failing gate as a valid regeneration, and `eval.yml` runs only on push to main (path-filtered) and nightly. | Pinned as a `KNOWN_GAP` test; **GG decision** |
| 11 | `check_eval_results_reproducible.py` overwrites committed results by running `eval.runner`; without `EVAL_CASSETTE_MODE=replay` it would call live providers. | Fixed (refuses without replay) |
| 12 | `check_site_deploy_content.py` cannot detect a no-op deploy that leaves an older page with the same markers live. | Pinned; remains |
| 13 | `model-availability-check.yml` tested only `models.list` membership; a listed model whose `generateContent` returns 404 passed. Related: the failover probe's new `GEMINI_API_KEY` secret 404s/402s on every model (a probe-credential problem, not a code bug). Both verified by the orchestrator. | Fixed in PR #219 (cross-reference, not touched here) |

### Finding 4 detail: what the corrected guard found

The corrected guard reports `hien-0014` (text: "...Boat ko thoda Volume Me sudhar karna
chahiye...") quoted in `PROMPTS.md` line 51, in a note about benchmark fixture `bench-hien-011`
(commit `d87c3e5`, 2026-07-26, six weeks before the held-out corpus was built in `60eac99`). It is
a changelog note, not text fed to a model, and `app/core/prompts/` has no overlap, so it is
allowlisted with that reason. The broader measurement is not something any control checks:

```
.venv\Scripts\python.exe -P <scratch>/overlap.py
held-out texts >= 40 chars: 90; dev-visible files scanned: 113
held-out reviews present in a dev-visible file: 21   (all in benchmark/dataset/gold.jsonl
                                                      and candidates_for_review.jsonl)
```

Whether a review sitting in the internal benchmark gold set weakens the held-out claim depends on
whether the benchmark informed prompt development, which this sweep did not establish. It is
recorded here rather than absorbed, because it bears on a shipped claim ("the prompt has never seen
the held-out corpus").

## Control table

Legend for the **can it pass without verifying?** column: **YES** = shown or demonstrated; **YES
(pinned)** = a known gap now held by a `KNOWN_GAP` test; **no** = the hostile input was rejected;
**UNVERIFIED** = not exercised (needs production, secrets, network or a browser).

| control | what it claims | surface actually covered | can it pass without verifying the claim? | evidence (command + result) | action |
|---|---|---|---|---|---|
| Required-check list | merges are gated by CI | two contexts: `lint-and-test`, `web-build` | **YES** for everything else (a red `secret-scan` does not block) | `gh api repos/gaurav-gandhi-2411/review-iq/branches/main/protection` returned `contexts: ["lint-and-test","web-build"]`, `enforce_admins: true`, `required_approving_review_count: 0` | GG: consider requiring `secret-scan` and `bypassrls-container-check` (neither has a path filter, so they always report; `pre-cutover-verification` is path-filtered and cannot be required) |
| `ci.yml` `lint-and-test` | lint, types, guards, tests | every step is a plain command; no `continue-on-error`, no `\|\| true` | no | read in full; `test_continue_on_error_only_where_allowlisted` and `test_no_failure_swallowing_shell_idioms` pass | test added |
| `ci.yml` `pip-audit` | dependency CVE scan | Python deps, non-blocking | **YES (by declaration)**: `continue-on-error: true`, "informational only" | read; allowlisted in the new test with that reason | pinned |
| `ci.yml` `npm-audit` / `web-build` | npm CVE gate; lint, typecheck, build, smoke | `web/`; `validate-env.mjs` runs on **placeholder** env values in CI | `validate-env` step: yes (placeholders always satisfy it; the real check is Vercel's own build, not observable from CI). Build/lint/typecheck steps: no | read `ci.yml` lines for `web-build` and `web/scripts/validate-env.mjs` | remains; the Vercel-side check is UNVERIFIED |
| `ci.yml` `manifest-provenance` | every manifest metric has a committed artifact | `.portfolio/metrics.json` `source_file` existence in `git ls-files` | YES (pinned): a `source_file` containing a space or `(` is only warned; an absent manifest exits 0; the value is never compared | `pytest tests/unit/test_check_manifest_provenance.py`: 6 passed, incl. two `KNOWN_GAP` cases | tests added; the value itself is protected by `render_metrics.py --check`, not by this |
| `eval.yml` | eval gate on prompt/config changes | replays cassettes; triggers: push to main on `app/core/**`, `eval/fixtures/**`, `eval/runner.py`, `eval/free_text_scoring.py`; nightly; dispatch. **Not on pull_request** | YES (pinned) for PRs: a PR that lowers scores is caught only after merge | read; `KNOWN_GAP_failing_accuracy_gate_still_passes_reproducibility` | GG decision (finding 10) |
| `eval.yml` Slack step | notifies Slack | skips cleanly when `SLACK_WEBHOOK_URL` unset | YES: secret is not set (`gh secret list` shows 5 names, no `SLACK_WEBHOOK_URL`); workflow comment already says it has never sent | `gh secret list --repo gaurav-gandhi-2411/review-iq` | remains; the GitHub-issue path is the real alert |
| `schedule-failure-alert` action + `alert-path-canary.yml` | a failure produces a visible GitHub issue | deliberately fails daily, then checks the issue was updated in the last 900 s | no | issue #190 `updatedAt` 2026-09-20T08:34:23Z via `gh issue list --label ci-alert`; label `ci-alert`, `security`, `demo-watch`, `incident` all exist (`gh label list`) | none |
| Alert callers (12 workflows, 13 steps) | red scheduled run opens an issue | alert step must run after a failed step | YES if a caller omitted `always()`: `check_scheduled_workflows_alert.py` passes a step with no `if` and one gated `if: false` | battery script: "no alert at all" -> FAIL; "alert step gated if: false" -> PASS; "alert step with NO if" -> PASS; "alert in a comment" -> FAIL; "echo of gh issue create" -> PASS. All 10 computed-status steps in the real workflows have `always()` (`test_alert_step_runs_after_a_failed_step`, bound is 8); the 3 literal-failure steps are canary and demo-quota-probe | test added (the script itself stays text-level, as its docstring says) |
| `secret-scan.yml` diff scan | new commits contain no secret | gitleaks `base..head` of the PR, with `.gitleaksignore` | no for the scanner; the gate is **not required** (finding 1) | `gitleaks dir plant` on a file with a planted `ghp_` token: exit 1, "leaks found: 1"; `gitleaks detect --source .` on this repo: 935 commits, exit 1 with one finding for a fingerprint not in the ignore file (local refs include unmerged branches, so this is not the CI result); last 4 scheduled full-history runs on GitHub: success (`gh run list --workflow secret-scan.yml --event schedule`) | none |
| `.gitleaksignore` | allowlist of reviewed false positives | 20 fingerprints, no per-entry reasons; 16 of 20 reference commits on `origin/main`, 4 reference pre-squash commits that are not | grows silently | `ignore_check.py`: `entries=20 exist=20 on_origin_main=16` | `test_gitleaksignore_does_not_grow_silently` (bound 20, format check) |
| `security-bypassrls-check.yml` (production DB) | `review_iq_app` lacks `BYPASSRLS` in production | 2 of 4 tests in `test_role_bypassrls.py`, read-only, `Production` environment secret | no | `gh run view 35499723558 --log`: "collected 4 items / 2 deselected / 2 selected ... 2 passed, 2 deselected" (ran, not skipped); `SUPABASE_DATABASE_URL` exists at Production scope (`gh secret list --env Production`) | none |
| `bypassrls-container-check.yml` | same, on migrations applied to a throwaway Postgres | applies migrations via `push.py` then the cutover file | no for the assertion (4 passed, run 35500751480); depends on `push.py`'s recording (out of scope) | `gh run view 35500751480 --log`: "4 passed in 0.08s" | none here |
| `pre-cutover-verification.yml` | integration suite on a migration-built database | PR-triggered, **path-filtered** (`supabase/migrations`, `supabase/ci`, `app`, `tests/integration`) | no; an edit to only `supabase/cutover/**` does not trigger it, but `bypassrls-container-check.yml` (no filter) applies that file | read; no `pytest.skip`/`importorskip`/`xfail` anywhere in `tests/` (`grep`: no matches) | none |
| `migration-drift-check.yml` | merged migrations are applied in production | `push.py --dry-run --fail-on-pending` against production; fails closed on a missing secret | depends on `_migrations` recording, which the other agent is fixing | last push run failed (`gh run list`); UNVERIFIED | out of scope |
| `schema-drift-check.yml` + `check_schema_drift.py` | migration-built schema equals production | 9 snapshot sections; `postgres`-owner grants and `_migrations` excluded; migrator grants excluded by a documented known gap | YES for two empty snapshots (before) | `pytest tests/unit/test_check_schema_drift.py`: 11 passed incl. `test_main_returns_1_for_two_empty_snapshots`; workflow is currently red (issue #174), fails loud | fixed |
| `db-backup.yml` | nightly encrypted, verified dump | dump integrity by token grep; encryption; artifact upload | YES for a zero-row dump (before); run for real never rehearsed as a restore | `run_backup_verify.py` against a synthetic schema-only gzip: step exit 0 before the edit, 1 after; a dump with a `COPY` section: 0 both times | fixed; **restore drill UNVERIFIED** |
| `demo-quota-probe.yml` | nightly demo endpoint check, debounced | handlers for exit 0, 1, 2 | YES for any other code (before) | `run_step.py` with a fake `uv`: exit 0 -> `0`; exit 1 -> `1`; exit 2 with a 429 line -> `2`; exit 2 without it -> `99`; exit 137 -> `137` (then caught by the new step) | fixed |
| `web-surface-probe.yml` + `probe_web_surfaces.py` | nightly check of marketing, dashboard, API, `/try`, authenticated path | 4 surfaces (SPA surfaces via headless browser) plus the authenticated path only if `PROBE_API_KEY` is set | **YES for the authenticated path**: not configured | `gh secret list` (no `PROBE_API_KEY`); `tests/test_probe_web_surfaces.py::test_run_probe_skips_authenticated_path_when_key_not_set` already pins the skip | remains; GG must provision the key (a production write, not done here) |
| `failover-probe.yml` + `probe_failover.py` | live call to each failover path | Gemini and SecondaryProvider; fails closed on an unconfigured key | no (orchestrator also verified the new `GEMINI_API_KEY` secret 404s/402s on every model: a credential problem, loud not decorative) | issue #215 open, seven red nights; `SECONDARY_PROVIDER_API_KEY` not in `gh secret list` | none (loud, not decorative) |
| `uptime-alert.yml` | prod `/health` is watched | HTTP 200 on `/health`; `app/api/ops.py` returns 503 when the DB ping fails | no (a probe crash is treated as DOWN) | read; live behaviour UNVERIFIED | none |
| `model-availability-check.yml` | configured LLM models still exist | model names parsed from `config.py` **defaults**, not the deployed env; membership in the provider's `models.list` output only | **YES (sixth decorative control; verified by the orchestrator, fixed in PR #219, not by this branch)**: `gemini-2.5-flash-lite` is listed with `generateContent` advertised, yet `generateContent` returns 404, so the check stays green for a model that cannot serve | orchestrator's live call (not repeated here: no network/secrets by hard rule); this sweep only read the workflow | cross-reference PR #219 |
| `site-deploy.yml` + `check_site_deploy_content.py` | the deploy shipped current content | fixed marker list on the served page | YES (pinned): a stale previous deploy with the same markers passes | `pytest tests/unit/test_check_site_deploy_content.py`: 6 passed incl. `KNOWN_GAP_a_stale_previous_deploy...` | pinned; the ancestor-of-main step covers the commit |
| `app-mount-check.yml` / `check_app_mounted.py` | SPA actually mounts | real Chromium, non-empty `#root` and an `h1` marker; manual dispatch and nightly probe only, not on deploy | no | read; not run (needs network and a browser) | UNVERIFIED |
| `deploy-cloud-run.yml` + `check_cloud_run_deploy_is_from_main.py` | smoke test then provenance check | tagged revision `/health`; live revision's commit is an ancestor of main; fails closed | no | read; existing `tests/test_check_cloud_run_deploy_is_from_main.py`; live UNVERIFIED | none |
| `check_scheduled_workflows_alert.py` | every scheduled workflow has an alert path | text scan, per file | YES (documented): `if: false` and a step with no `if` both pass | battery output above | covered for this repo by the new workflow test |
| `check_migrations_no_bypassrls_grant.py` | no migration grants `BYPASSRLS` | `ALTER/CREATE ROLE ... BYPASSRLS`, multi-line, `EXECUTE` strings; empty dir fails | no for those shapes; YES for `GRANT <bypass-role> TO x` and an assembled `'BYPAS' \|\| 'SRLS'` (documented) | battery: direct, multi-line, `ALTER USER`, `EXECUTE` -> hits; indirect grant and assembled string -> `hits=[]`; empty dir -> "refusing to pass on an empty scan" | none (documented limits) |
| `check_undocumented_pg_connects.py` | every psycopg2 query site is tenant-scoped | `app/**/*.py`; structural (`_set_tenant` called anywhere in the body) | YES (pinned) for `_set_tenant` after the query, or a connection passed in as a parameter; **was YES** for aliased imports and an empty scan | new tests fail against the old script: `git checkout origin/main -- scripts/check_undocumented_pg_connects.py` then pytest: 4 failed, 6 passed; restored after | fixed + 2 `KNOWN_GAP` tests |
| `check_no_hardcoded_metrics.py` | no hand-typed accuracy numbers | `*.md`/`*.html` percent-shaped numbers near a keyword | YES (pinned): decimal fractions, "78.6 percent", any marker span whatever its name | `pytest tests/unit/test_check_no_hardcoded_metrics.py`: 18 passed incl. 3 `KNOWN_GAP` | pinned |
| `render_metrics.py --check` | committed copy equals the JSON | marker blocks with a renderer; **was** silent on unknown names, unnamed/unterminated markers, missing target files | YES before (finding 3) | `pytest tests/unit/test_render_metrics.py`: 53 passed after (9 of them new); real repo: `OK` with one WARN naming `consensus_labeling` | fixed |
| `check_no_heldout_leakage.py` | held-out reviews never enter prompt files | now every 40-character window of each review, `app/core/prompts/**/*.py` and `PROMPTS.md` only | **was YES** (prefix-only); scope is still narrow (no other file types or directories) | finding 4; `pytest tests/unit/test_check_no_heldout_leakage.py`: 10 passed | fixed |
| `check_contrast.py` | palette meets WCAG AA | pairs documented in `design/tokens.json` only | **was YES** (empty list, relaxed `minRatio`); still does not see what pages render | `pytest tests/unit/test_check_contrast.py`: 12 passed | fixed; `check_site_responsive.py` (real render) is not in CI |
| `check_eval_results_reproducible.py` | committed eval results are machine-generated | regenerates via cassette replay and compares | no: tampering `overall_score` in `latest.json` -> exit 1; but **could** clobber files without replay | sandbox copy: baseline `PASS`, after `sed` edit `FAIL ... eval/results/latest.json`, exit 1 | env guard added, `main()` tests added |
| `check_known_gaps_reproducible.py`, `check_grounding_check_reproducible.py` | committed artifacts reproduce | regenerate and byte-compare | no | sandbox copy: edited one number in each committed file; both exit 1 | none |
| `check_eval_model_matches_config.py` | eval was measured under the deployed models | `config.py` defaults vs `results.json` provenance | no for defaults; blind to a deployed env override (its docstring says so) | read; not hostile-tested | none |
| `check_prod_deploy_is_from_main.py` | live site commit is on main | Cloudflare deployment metadata; fails closed | no | read; existing tests; live UNVERIFIED | none |
| `probe_demo_quota.py` | demo endpoint returns a valid extraction | one POST; body must contain `sentiment` | no | read; existing `tests/unit/test_probe_demo_quota.py` | none |
| Test suite | tests assert behaviour | 1860 test functions scanned | a few call helpers or only "must not raise" | AST scan: 24 of 1860 have no direct `assert`/`raises`/`assert_*`; spot-checked `test_storage_pg.py::*_sets_rls_context` (assert via `_assert_sets_rls_context`), `test_valid_credentials_passes` and `test_migrate_is_idempotent` (must-not-raise by design) | none; the other 20 not individually reviewed |
| Skipped/xfailed tests | no control hidden behind a skip | `tests/` | no | `grep -rn "pytest.mark.skip\|pytest.mark.xfail\|importorskip\|pytest.skip\|skipif" tests` returned nothing | none |
| `web/eslint.config.js` | `npm run lint` | `**/*.{ts,tsx}` with recommended sets; no rule disabled; 6 `eslint-disable`/`@ts-*` comments in `web/src` | no | read | none |

## What changed and what did not

Fixed, each with a test that fails against the previous behaviour: `render_metrics.py --check`,
`check_no_heldout_leakage.py`, `check_schema_drift.py`, `check_undocumented_pg_connects.py`,
`check_contrast.py`, `check_eval_results_reproducible.py`, `demo-quota-probe.yml`, `db-backup.yml`.

Pinned as `KNOWN_GAP` tests and left unfixed on purpose: findings 2, 10, 12, the
`manifest-provenance` free-text escape, the metrics scanner's blind shapes, the structural limits
of the tenant-scoping guard. A fix for each needs something this sweep may not touch (a production
credential, a settings change, the live deployment, or a redesign that changes published
behaviour).

Explicitly not done: no published metric or copy was changed (`git diff origin/main --
README.md site` is empty); branch protection and repository settings were only read; the
`consensus_labeling` block was not rewritten or re-classified.

## Suggested owner actions

1. Decide whether `secret-scan` and `bypassrls-container-check` become required checks.
2. Provision `PROBE_API_KEY` (a dedicated synthetic org and key; see the comment in
   `scripts/probe_web_surfaces.py`), then make its absence a failure rather than a skip.
3. Write the `consensus_labeling` renderer (source data is
   `eval/consensus/results/consensus_summary.json`) or re-mark the block as historical; then delete
   the allowlist entry in `scripts/render_metrics.py`.
4. Decide whether the held-out/benchmark overlap (finding 4) matters for the held-out claim.
5. Decide whether a PR-time accuracy threshold gate is wanted (finding 10).
6. Rehearse a restore from a nightly backup; the dump verification is still only a content grep.
