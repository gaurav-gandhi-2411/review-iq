# Major dependency upgrades: plan (S31 B3, plan only)

The S31 spec allows merging non-major Dependabot PRs and asks for a plan, not a merge, for majors. Status on 2026-10-23.
Usage counts are from a grep of this repository on that date (VERIFIED); behaviour changes are from each project's
release notes as summarised in the Dependabot PR bodies (BELIEVED until a branch run proves them).

| PR | Upgrade | Where it is used | Main risk | Plan |
|---|---|---|---|---|
| #520 | `google-github-actions/setup-gcloud` 2.1.5 -> 3.0.1 | `deploy-cloud-run.yml` and other GCP workflows (with #519) | Runner Node version and input renames in v3; a broken deploy workflow stops every production deploy | Upgrade together with #519 on one branch; run `deploy-cloud-run.yml` by `workflow_dispatch` against a no-traffic tagged revision first; merge only with GG present |
| #519 | `google-github-actions/auth` 2.1.10 -> 3.0.0 | the same workflows (Workload Identity Federation) | Auth is a gate-4 path; a WIF regression blocks deploys and backups | As above; gate 4 -> GG merges |
| #516 | `actions/setup-python` 5.6.0 -> 7.0.0 (5 workflows still on the v5 pin; 4 already on v7) | `ci.yml`, `app-mount-check.yml`, `ui-screenshots.yml`, … | Low: the repo already runs v7 in four workflows | Re-pin the five v5 users to the same v7 SHA already in the repo, in one chore PR; CI proves it. Tier A once Dependabot's own PR is closed or rebased |
| #233 | `structlog` 25.5 -> 26.1 | every app log line (`structlog>=25.1.0`) | Processor/renderer API changes would change the JSON log shape that the log-based alerts (`llm.rate_limited`, `llm.failover`, `db_timeout`) and the S2 health counts read | Branch: upgrade, run the full test suite, and add a test that one rendered production log line keeps `event`, `level`, `timestamp` and the structured fields; then deploy to a no-traffic revision and compare one real log line before merging |
| #232 | `anthropic` 0.102 -> 1.11 | three historical eval scripts only (`eval/consensus/panel.py`, `eval/generate_hindi_fixtures.py`, `eval/label-helper-llm.py`); no app code | None for production; the scripts may break on the 1.x client | Prefer removing the dependency (the scripts are retired; the project's LLM path is Groq and Kaggle Ollama) in a PR that deletes or archives the three scripts; otherwise upgrade and run them with `--help` only. No API key is ever set for this |
| #568 | minor/patch group (fastapi, pydantic, supabase, tokenizers 0.22.2 -> 0.23.3, ruff 0.17, …) | runtime + dev | Not major, but **deferred by S31** until Amendment 16's one-shot evaluation is done: `tokenizers` feeds the classifier inputs, and ruff 0.17 may add lint failures | After P7: rebase, run CI, and re-run `scripts/classify_parity.py` to show the tokenizer bump changes no decision |
| #106 / #610 | Tailwind 3.4 -> 4.3 (`web/`) | the whole web UI | Visual | #610 (draft) carries the upgrade, compatibility CSS and CI-rendered before/after screenshots; GG signs off visually |
