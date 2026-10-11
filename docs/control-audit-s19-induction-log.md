# Control-class audit (Session 19, Z8), part 2: induction log

> Part 2 of 3 of the Session 19 control-class audit. Siblings: `docs/control-audit-s19.md` (part 1: result, method, inventory), `docs/control-audit-s19-induction-log.md` (part 2: induction log), `docs/control-audit-s19-findings.md` (part 3: findings, unproven, disclosures, coverage).

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

