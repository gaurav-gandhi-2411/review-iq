# Vercel deploys and Functions Storage (S18 V1)

Trigger: the Vercel dashboard showed Functions Storage 10.93 GB / 10 GB (Hobby limit), and an
`api-deployments-free-per-day` error appeared as a symptom. 58 deployments across 9 projects
average about 190 MB each, which is far too large for a Vite SPA.

## Rules

1. Production deploys come from CI / a push to `main` through the Vercel git integration. Never
   run `vercel deploy --prod` (or `vercel --prod`) from a local tree (CLAUDE rule 31a). Preview
   deploys from local are acceptable.
2. Never run `vercel link` or `vercel deploy` at the repo root. The only deployable surface is
   `web/`. The repo-root `.vercelignore` is an allow-list (ignore everything, re-include `/web`)
   so an accidental root upload cannot carry `data/`, `eval/`, `ops/`, `reports/` or the Python
   project. It is a safety net, not a licence to deploy from the root. Its rules are tested by
   `tests/test_vercelignore.py` using git's own ignore engine.

## The root-link hazard

`C:\Users\gaura\ml-projects\review-iq\.vercel\project.json` (gitignored, local only) links the
project `samidha-reviews-web` (`prj_DuX5cZeLH3sPpEkMXv4AFwtV61LJ`) at the repo ROOT. A CLI
`vercel deploy` from there uploads the whole working tree, including untracked local directories
(data/ 325 MB, ops/ 119 MB, eval/ 80 MB), not just `web/`. The link should live in `web/.vercel`
(gitignored by `web/.gitignore:26`). Relinking is a local-only step done by the orchestrator.

## Evidence (V1b): hypothesis "review-iq is the storage hog"

| # | Claim | Status | Basis |
|---|-------|--------|-------|
| 1 | Dashboard shows Functions Storage 10.93 / 10 GB; 58 deployments, 9 projects, ~190 MB avg | VERIFIED (orchestrator, from the dashboard) | Reported to this task; not re-observable without a token |
| 2 | Local `.vercel/project.json` links samidha-reviews-web at the repo root | VERIFIED | Orchestrator read the file; `.gitignore:22` and `web/.gitignore:26` ignore it |
| 3 | No root `package.json` exists | VERIFIED | `ls` of the repo root in this worktree: none |
| 4 | `web/vercel.json` sets `buildCommand`, `outputDirectory: dist`, an `ignoreCommand`, and an SPA rewrite; no `functions`, no `api/` | VERIFIED | `web/vercel.json` (3 commits: a8231ec, b024370, afed528); `web/package.json` build is `node scripts/validate-env.mjs && tsc -b && vite build` |
| 5 | The repo has no workflow that runs the Vercel CLI; web/ deploys via the Vercel git integration | VERIFIED | grep of `.github/workflows` for vercel: only comments (app-mount-check.yml:9-10 states this, ci.yml:190-204) |
| 6 | Git deployments of review-iq use Root Directory = `web` | BELIEVED | Inference: READY git builds exist, but with no root package.json a root-dir build could not succeed. Confirm in Settings (step 2 below) |
| 7 | Tracked repo is about 25 MB, so a git deployment cannot be about 190 MB | VERIFIED (size) / BELIEVED (what Vercel uploads) | Git deploys build from a clone and output only `dist` |
| 8 | A Vite SPA deployment contributes to Functions Storage | BELIEVED NOT (docs say Functions Storage is Function bundles; static output is Deployment Storage) | See docs table below. The dashboard figure that is over quota is labelled Functions Storage |
| 9 | Heavy uploads come only from CLI `vercel deploy` at the repo root | BELIEVED | Consistent with 2 and 7, but no deployment listing was read; `source: "cli"` vs `"git"` per deployment would confirm it (the sweep report does not print source yet; the dashboard shows it) |
| 10 | gg-portfolio (Next.js, ~10 deployments in 24h, local .next 239 MB) is the better match for the ~190 MB average | BELIEVED | Size match plus Next.js producing Function bundles; needs per-project storage from Usage |
| 11 | The 10.93 GB is mostly held by the Next.js projects, not review-iq | BELIEVED | Decided by step 1 below (one screenshot) |

Verdict: the token-free evidence does not confirm that review-iq holds the storage. It confirms a
real hazard (root link) and a plausible but unproven primary suspect (Next.js projects).

## Documentation findings (V1e)

Fetched 2026-10-07.

| Question | Docs say | Source |
|----------|----------|--------|
| What is Functions Storage? | "Vercel Function bundles stored in each region where Vercel deploys them"; Deployment Storage is "Build outputs and static assets". "A project can have low Deployment Storage and high Functions Storage, or the reverse." | https://vercel.com/docs/deployment-storage |
| How is it counted? | Maximum stored amount per project per billing day, summed across the period (GB-months) | https://vercel.com/docs/deployment-storage |
| Does retained output count? | "Retained deployment output contributes to Deployment Storage while Vercel stores it." | https://vercel.com/docs/deployment-retention |
| Hobby default retention | 30 days for canceled, errored, pre-production and production | https://vercel.com/docs/deployment-retention |
| Do old deployments free storage automatically? | Yes, eventually: after the retention period a background job "typically marks it for deletion within 48 hours"; protected ones are re-evaluated, which "can take up to 30 days". After deletion a deployment stays restorable for 30 days ("Recently Deleted"), after which its resources are permanently removed | https://vercel.com/docs/deployment-retention |
| What is exempt from retention on Hobby? | Last 3 deployments created in the project; last 3 READY production deployments; anything with a production alias; non-production deployments with any custom alias; the latest preview of any still-active git branch | https://vercel.com/docs/deployment-retention |
| What does `api-deployments-free-per-day` count? | Deployments per day (Free): limit 100 per 86400 s, scope `owner`. "Using Next.js or any similar framework to build your deployment is classed as a build. Each Vercel Function is also classed as a build. Hosting static files such as an index.html file is not classed as a build." Hitting it means waiting a day | https://vercel.com/docs/limits |
| Delete API | `DELETE /v13/deployments/{id}` returns `{state: DELETED, uid}` | https://vercel.com/docs/rest-api/deployments/delete-a-deployment |
| Does a static-only SPA count toward Functions Storage? | Not stated explicitly. Inference from the definitions above: no Function bundles, so Deployment Storage only | inferred |
| Community reports | Hobby users report storage staying near 10 GB after deleting deployments, needing recalculation; anecdotal, not docs | https://community.vercel.com/t/hobby-deployment-storage-remains-near-10-gb-after-deleting-deployments/49563 |

Conclusion for V1e (docs plus inference): fixing the root directory stops NEW oversized uploads
but does not shrink existing storage by itself. Existing deployments free space only when they
age out (30 days on Hobby, with the exemptions above, so each project always keeps at least its
last 3 deployments plus anything aliased) or when deleted. Because the quota is already
exceeded, deleting old deployments is needed to recover quickly, and deleting is also what makes
the per-project figure verifiable. Expect the dashboard number to lag (community reports of a
stale figure; unverified). Deleted deployments move to a 30-day recovery window; whether
recovery-window bytes still count toward the quota is not stated in the docs (unknown).

Rate-limit note: every CLI or git push that builds a Next.js app counts as deployments against
the 100 per day; ~10 gg-portfolio deployments per 24h plus preview builds make the symptom
plausible, but 100/day would need far more. The error may instead be reported because the quota
is exceeded. BELIEVED, unverified.

## GG steps (V1c)

UI paths are written from docs and memory of the dashboard; I could not verify them (no browser
or token in this task). If a menu name differs, search settings for the quoted label.

1. Find the culprit first. vercel.com -> your team (gaurav-gandhi-2411s-projects) -> Usage ->
   Deployment Storage -> Functions Storage -> Projects. Screenshot the per-project breakdown (or
   write down the GB per project). This single number decides everything below. Confirm: the
   per-project values sum to about 10.93 GB.
2. samidha-reviews-web root directory. Projects -> samidha-reviews-web -> Settings -> Build and
   Deployment -> Root Directory. Expected value: `web`. If empty, type `web`, tick "Include source
   files outside of the Root Directory" only if the build needs it (it does not), and Save.
   Confirm: Deployments tab -> latest deployment -> Build logs show `web` paths and `vite build`.
3. Delete the stray project `gg-portfolio-wt-ambient-perf` (id
   `prj_qZvLSYmR3sZNEF5M7uhZKfPI6bJz`, 2 deployments, both ERROR, named after a worktree
   folder). Projects -> that project -> Settings -> General -> scroll to Delete Project -> type
   the project name -> Delete. Confirm it disappears from the team Projects list. Check first
   that no domain is assigned to it (Settings -> Domains is empty).
4. Restrict preview builds. For samidha-reviews-web: Settings -> Git -> Ignored Build Step; the
   committed `web/scripts/vercel-ignore.sh` is wired via `ignoreCommand` in `web/vercel.json`,
   so the dashboard field should read "Automatic" or be empty (the file wins). For each Next.js
   project (gg-portfolio `gaurav-gandhi` and the others): Settings -> Git -> Ignored Build
   Step, and Settings -> Git -> "Production Branch"; limit previews with Ignored Build Step set
   to "Only build production" (Behavior dropdown) or a command such as
   `[ "$VERCEL_GIT_COMMIT_REF" != "main" ] && [ -z "$VERCEL_GIT_PULL_REQUEST_ID" ]` (exit 0 skips
   the build). Keep PR previews only for samidha-reviews-web and triage-iq.
5. Optionally shorten retention for the heavy project: Settings -> Security -> Deployment
   Retention Policy -> choose a shorter duration for Preview -> Save. (Docs path, unverified.)
6. Token for the sweep (short-lived). Account Settings -> Tokens -> Create Token. Name
   `cc-sweep-2026-10`, Scope: team `gaurav-gandhi-2411s-projects`, Expiration: 1 day. Copy it once.
   In YOUR OWN PowerShell (never paste it into chat):
   ```powershell
   $env:VERCEL_TOKEN = Read-Host -AsSecureString | ConvertFrom-SecureString -AsPlainText
   $env:VERCEL_TEAM_ID = "team_xxx"   # Settings -> General -> Team ID
   ```
   Then start Claude Code from that shell so it inherits the variables. Delete the token in
   Account Settings -> Tokens when the sweep is done.
7. Sweep, two separate runs (rule 55d):
   ```powershell
   python scripts/vercel_deployment_sweep.py                # dry run, writes reports/vercel-sweep-plan.json
   # read the report; edit delete_ids in the plan file to what you approve
   python scripts/vercel_deployment_sweep.py --apply --approved-list reports/vercel-sweep-plan.json
   ```
   The first run ends with `DRY RUN ... Nothing deleted`. Any deployment printed `size unknown`
   was NOT sized and will not be deleted.

## The sweep tool (V1d)

`scripts/vercel_deployment_sweep.py`, tests in `tests/test_vercel_deployment_sweep.py` (fake HTTP
layer). Endpoints (documented): `GET /v10/projects`, `GET /v7/deployments`, `GET
/v13/deployments/{id}`, `GET /v2/deployments/{id}/aliases`, `DELETE /v13/deployments/{id}`.

Policy per project: KEEP the protected id `dpl_Bbe7PvBVd2rcSmLAQAmUc3jyk3LA`, the newest READY
production deployment, the 2 newest by creation, anything in flight, anything of unknown size,
and anything with an alias (strict by default; `--relax-branch-aliases` lets non-production
deployments holding only `*.vercel.app` aliases be deleted). Everything else is DELETE.

Documented-vs-believed: the schema of `lambdas[]` items documents only `id` and `output`. The
`size` field is BELIEVED (not documented), so a missing size fails closed to KEEP. If every
deployment prints `size unknown`, the field does not exist as assumed: stop and use the
dashboard breakdown instead. The projects endpoint pagination cursor (`from=pagination.next`) is
also BELIEVED from the parameter description.
