# Cloud Run Deploy Runbook

**Service:** `review-iq`
**Project:** `review-iq-prod`
**Region:** `asia-south1` (Mumbai)
**Image registry:** `asia-south1-docker.pkg.dev/review-iq-prod/review-iq/api`

---

## ⚠️ SUPERSEDED (2026-08-01): use `.github/workflows/deploy-cloud-run.yml` instead

A manual deploy against this runbook is exactly how both services ended up running an
untraceable image (`v0-19-0` — no git tag, no CI run, no way to audit or reproduce what was
serving customers; same failure class as rule 31a's `vercel deploy --prod` incident). Every
deploy now happens by pushing to `main` (or `workflow_dispatch`), building and tagging the
image `sha-<commit>`, and staging the rollout (no-traffic → smoke test → promote) the same way
this runbook describes manually below. `scripts/check_cloud_run_deploy_is_from_main.py` verifies
after every deploy (and nightly) that the running image actually traces to a commit on `main`.

This runbook stays as a break-glass reference (e.g. Cloud Build/Actions itself is down) — but
if you're about to run a `gcloud run deploy` command by hand for a routine change, stop and
push to `main` instead. Any manual deploy will be caught within 24h by the nightly drift check
regardless, so it can't silently become the new untracked baseline again.

---

## ⚠️ READ THIS BEFORE RUNNING ANY `gcloud run deploy` COMMAND

`--set-env-vars` and `--set-secrets` **REPLACE the entire set** of env vars / secrets on the
new revision — they do not merge with what the previous revision had. The service currently
carries **17 env vars** (8 plain + 9 secret-backed — full inventory below). An earlier version
of this runbook showed a `--set-secrets` example with only 4 entries; copying that command
verbatim would silently **wipe the other 13** (including `ALLOWED_ORIGINS`, every `RESEND_*`
var, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `UNSUBSCRIBE_SIGNING_KEY`,
`INGEST_TICK_TOKEN`) — CORS breaks, email breaks, the ingest tick worker silently stops
authenticating, and Supabase access breaks, all with no error until something downstream
fails. This bit the 2026-07-07 custom-domain deploy and was avoided by hand; the pattern below
is the fix, not a preference.

**Rule: for any deploy where the revision already has vars/secrets you want to keep, use
`--update-env-vars` / `--update-secrets` (merge — adds/overwrites only the keys you name,
leaves everything else untouched) or omit env/secret flags entirely (reuses the previous
revision's template verbatim). Never use `--set-env-vars` / `--set-secrets` for an incremental
deploy — those are only safe for a from-scratch service creation where nothing exists yet to
lose.**

---

## Standard staged redeploy (the pattern actually in production use)

This is the exact sequence used for the 2026-07-09 deploys (v0-12-0 bulk rate limiter,
v0-13-0 durable ingest queue) — not aspirational, this is what shipped.

```bash
# 0. Build from a CLEAN checkout of the commit being deployed — never the working
#    directory, which may carry uncommitted changes that would silently leak into
#    the image. A throwaway git worktree at the target commit is the safest source:
git worktree add /tmp/build-<sha> <sha>
cd /tmp/build-<sha>

# 1. (Recommended) verify the exact committed tree passes tests before spending a build:
uv run pytest tests/unit -q

# 2. Build and push via Cloud Build (free: 120 min/day)
gcloud builds submit \
  --tag asia-south1-docker.pkg.dev/review-iq-prod/review-iq/api:TAG \
  --region=asia-south1 \
  --project=review-iq-prod

cd - && git worktree remove /tmp/build-<sha> --force

# 3. Deploy as a new revision with ZERO traffic. Omit env/secret flags entirely
#    unless you're actually changing one — that reuses the current revision's
#    template exactly. If you ARE adding/changing a var, use --update-env-vars /
#    --update-secrets (merge), NEVER --set-env-vars / --set-secrets (replace):
gcloud run deploy review-iq \
  --image=asia-south1-docker.pkg.dev/review-iq-prod/review-iq/api:TAG \
  --region=asia-south1 \
  --project=review-iq-prod \
  --no-traffic
  # --update-env-vars="NEW_VAR=value"          # only if adding/changing a plain var
  # --update-secrets="NEW_VAR=secret-name:latest"  # only if adding/changing a secret ref

# 4. Get the new revision name, then tag it so it's reachable at a stable URL
#    without touching the live traffic split:
gcloud run revisions list --service=review-iq --region=asia-south1 --project=review-iq-prod \
  --format="table(metadata.name,status.conditions[0].status)" --limit=3

gcloud run services update-traffic review-iq --region=asia-south1 --project=review-iq-prod \
  --update-tags TAG=REVISION_NAME
# reachable at: https://TAG---review-iq-ajjrytb3na-el.a.run.app

# 5. Smoke-test the TAGGED revision directly — before any real traffic sees it:
curl -sf "https://TAG---review-iq-ajjrytb3na-el.a.run.app/health"
# Exercise whatever the deploy actually changed, not just /health — e.g. a real
# extraction call, an auth-protected endpoint with the correct token, etc.
# (See "What to smoke-test" below.)

# 6. Promote to 100% only once the tagged smoke passes clean:
gcloud run services update-traffic review-iq --region=asia-south1 --project=review-iq-prod \
  --to-revisions=REVISION_NAME=100

# 7. Re-verify on the PUBLIC domain after promotion (not just the tagged URL —
#    confirms the routing itself, not only the container). Note: api.samidhareviews.xyz
#    is fronted by Firebase Hosting's Cloud Run rewrite, not a native `gcloud run
#    domain-mappings` mapping -- see ADR 0009 for the full ingress trace before
#    assuming otherwise:
curl -sf https://api.samidhareviews.xyz/health
```

### What to smoke-test (beyond `/health`)

`/health` only proves the container boots and can reach the DB — it does not exercise auth,
LLM calls, or any new code path a deploy actually changed. Match the smoke to the change:

- Any deploy: `GET /health` → `200`, `db: "ok"`.
- Touches extraction: `POST /demo/extract` with a real review body → `200` with a populated
  extraction (also a live check that the Groq key + language detector are working).
- Touches an internal token-protected endpoint (digest, ingest tick): confirm all three —
  no token → `401`/`503`, wrong token → `401`, correct token → `200` with the expected body.
  (`curl -X POST` needs a body, even empty `-d ''`, or Cloud Run's front end returns `411`.)
- Touches the BFF/web-app path: a real signed-in request through the actual endpoint the
  frontend calls (not just the underlying `/v2` equivalent) — the BFF layer has its own auth
  and response-shape contract that a `/v2` smoke does not cover.

---

## Rollback

```bash
# Roll back to a specific previous revision immediately
gcloud run services update-traffic review-iq \
  --region=asia-south1 --project=review-iq-prod \
  --to-revisions=PREVIOUS_REVISION_NAME=100
```

To find previous revision names:
```bash
gcloud run revisions list --service=review-iq --region=asia-south1 --project=review-iq-prod \
  --format="table(metadata.name,metadata.creationTimestamp,status.conditions[0].status)"
```

**Current rollback chain (as of 2026-07-09, most recent first — verify with the command above
before trusting this, revisions churn):**

| Revision | Image tag | What it shipped |
|---|---|---|
| `review-iq-00026-ldd` | `v0-13-0` | durable batch-row queue + tick worker, BFF wired onto it |
| `review-iq-00025-6h2` | `v0-12-0` | bulk-path Groq rate limiter only (no durable queue yet) |
| `review-iq-00024-f2b` | `v0-11-0` | pre-rate-limiter baseline (custom domain + branding) |

---

## Current env var / secret inventory (reference — 17 total, verified live 2026-07-09)

Verified by reading the actual serving revision (`gcloud run revisions describe`), not assumed
from a prior deploy command. **Re-verify this list the same way before trusting it** — it will
drift as vars are added; this table is a snapshot, not a live source.

**Plain env vars (8):** `DEPLOY_TARGET`, `ENVIRONMENT`, `ALLOWED_ORIGINS`,
`DIGEST_TRIGGER_TOKEN`, `RESEND_FROM_NAME`, `API_PUBLIC_BASE_URL`, `INGEST_TICK_TOKEN`,
`INGEST_TICK_ROWS`.

**Secret-backed env vars (9, via Secret Manager, `latest` version):** `GROQ_API_KEY`,
`GEMINI_API_KEY`, `SUPABASE_DATABASE_URL`, `ADMIN_PASSWORD_HASH`, `RESEND_API_KEY`,
`RESEND_FROM_EMAIL`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `UNSUBSCRIBE_SIGNING_KEY`.

Values are never printed here or in any command output committed to the repo. To adopt a new
secret, grant `roles/secretmanager.secretAccessor` on that specific secret to
`review-iq-runner@review-iq-prod.iam.gserviceaccount.com` (same per-secret least-privilege
pattern as the existing 9), then deploy with `--update-secrets`.

**`DIGEST_TRIGGER_TOKEN` and `INGEST_TICK_TOKEN` are plain env vars, not secrets** — a
deliberate choice: Secret Manager access on this project is at a 9-secret operational ceiling,
and these are shared-secret bearer tokens for internal trigger endpoints (not credentials to
an external system), so a plain env var was judged adequate. If that ceiling is ever revisited,
promoting them to Secret Manager is a drop-in `--update-secrets` change, not a redesign.

**Source of truth:** the live revision, via `gcloud run revisions describe <name>
--region=asia-south1 --project=review-iq-prod --format=json` — inspect `spec.containers[0].env`.
This runbook's table above is a point-in-time copy for quick reference only; when they
disagree, the live revision wins.

---

## Service configuration (current as of v0.13.0 / revision `review-iq-00026-ldd`)

| Flag | Value | Why |
|------|-------|-----|
| `--memory` | 1Gi | argon2 64MB/verify + asyncpg + FastAPI baseline |
| `--cpu` | 1 | 1 vCPU; thread pool = 5 workers |
| `--timeout` | 120s | batch extraction ceiling |
| `--concurrency` | 80 | asyncio; auth queues at thread pool (5 concurrent argon2) |
| `--min-instances` | 0 | scale to zero; cold start accepted at free tier |
| `--max-instances` | 3 | prevents runaway billing |
| service account | `review-iq-runner@review-iq-prod.iam.gserviceaccount.com` | least-privilege runtime identity |

---

## First-deploy note

`--no-traffic` is not supported when creating a brand-new service (no prior revision exists).
For the very first deploy, omit `--no-traffic` — the first revision automatically gets 100%
traffic, and `--set-env-vars`/`--set-secrets` are correct (and required) at that point since
there is nothing yet to merge with. Every deploy after that should follow the staged pattern
above with `--update-*`, never `--set-*`.
