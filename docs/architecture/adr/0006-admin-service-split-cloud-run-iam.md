# ADR 0006: `/admin/*` Isolation via a Separate, IAM-Gated Cloud Run Service

**Status:** Accepted — implemented in this ADR's own PR (S0 remediation, blocks Wave 2).
**Date:** 2026-07-31
**Scope:** Closes finding P0.2 of the Wave 1 S0 remediation pass: `/admin/*` (org and API-key
CRUD, `app/api/admin.py`) is presently reachable from the public internet, gated only by HTTP
Basic auth (`app/auth/admin.py::require_admin`).

## Context

Verified directly (`gcloud run services get-iam-policy review-iq --region=asia-south1 --project=review-iq-prod`):
the deployed `review-iq` Cloud Run service has `allUsers` bound to `roles/run.invoker` — the
*entire* service, every route including `/admin/*`, is publicly network-reachable. Cloud Run's
IAM binding is service-wide, not path-scoped — there is no way to gate one route prefix of an
existing service by IAM while leaving the rest public. Closing this for `/admin/*` specifically
requires either (a) a second, separately-deployed Cloud Run service that mounts only the admin
routes, gated by IAM, or (b) an API-gateway/reverse-proxy layer in front of the single service
that enforces path-based auth before requests reach it.

`require_admin` itself (HTTP Basic, constant-time username compare + argon2id password verify)
is reasonably well-built as an *application-layer* control, but it is the *only* control — a
leaked or brute-forced password is the sole thing standing between the public internet and
org/API-key CRUD. The user's own instruction for this remediation: default to removing
`/admin/*` from public reachability entirely, and report which admin operations genuinely need
public exposure. Read every route in `app/api/admin.py`: create org, get org, create/list/rotate/
revoke API key — all five are operator-only actions (GG managing customer accounts), invoked
manually or via a future internal tool, never by a paying customer or any part of the public API
surface. **Answer: zero admin operations need public exposure.**

## Decision

Split into two Cloud Run services from one codebase (no code fork — same container image,
different env vars):

- **`review-iq`** (existing, public): everything except `/admin/*`. `SERVICE_ROLE=public`
  (new setting, default) skips mounting `admin_router`. Continues to authenticate to Postgres
  as `review_iq_app` (see the accompanying role-separation migration in this same PR — BYPASSRLS
  removed from this role as part of the same remediation).
- **`review-iq-admin`** (new): mounts *only* `ops_router` (health check, needed for Cloud Run's
  own readiness probes) and `admin_router`. `SERVICE_ROLE=admin`. Deployed with
  `--no-allow-unauthenticated` — reachable only by an IAM principal explicitly granted
  `roles/run.invoker` on this specific service (GG's own `gcloud` identity, invoked via
  `gcloud run services proxy` or a signed OIDC-authenticated request — see the PR's escalation
  steps for the exact invocation command). Authenticates to Postgres as the new `review_iq_admin`
  role (BYPASSRLS, used only here — see the migration's role-topology comment).
  `require_admin`'s HTTP Basic auth stays in place underneath IAM as defense-in-depth (cheap,
  already built, protects against the case of a compromised/misconfigured IAM grant) — it does
  not replace IAM, IAM replaces public reachability.

## Alternatives considered

- **Identity-Aware Proxy (IAP).** Rejected in favor of plain Cloud Run IAM for this specific
  case: IAP is designed for *human, browser-based* access (Google Workspace/OAuth login,
  session cookies) to internal web UIs. `/admin/*` here is called programmatically
  (`curl`/scripts against a JSON API by one operator), which is exactly what Cloud Run IAM +
  `gcloud auth print-identity-token` already serves directly, with materially less setup
  (no OAuth consent screen, no IAP-specific brand/audience configuration) and no added cost.
  IAP would be the better choice if `/admin/*` grows into a browser-facing internal dashboard
  used by multiple human operators — revisit then, not now (single-operator, script-driven
  today).
- **Keep one service, add IP allowlist + rate limiting + a second factor to `require_admin`,
  per the "if it must stay public" fallback the user offered.** Rejected: the user's own
  instruction stated a default of removing public reachability entirely, and the zero-public-need
  finding above means there is no requirement forcing the fallback path. Building IP-allowlist +
  MFA machinery for a control that doesn't need to be public at all would be effort spent on the
  wrong layer — Cloud Run IAM already provides all three properties (network-level gate,
  cryptographic identity, no shared secret to leak) for less code.
- **Path-based routing via an API gateway (e.g., Cloud Endpoints / a reverse-proxy Cloud Run
  service in front of both).** Rejected as unnecessary complexity for a two-route split — a
  second minimal Cloud Run service from the same image is simpler to deploy, reason about, and
  roll back than introducing a new gateway component.

## Consequences

- **New deploy artifact**: a second Cloud Run service (`review-iq-admin`) and a second Secret
  Manager secret (`review_iq_admin`'s DB credential) — see the PR's numbered escalation steps.
  Neither can be created by this session (`gcloud` console access, Secret Manager writes).
- **Operational cost**: near-zero — Cloud Run's free tier easily covers a second low-traffic
  service; GG is the only caller.
- **Rollback**: if the split proves cumbersome, the fallback is IP-allowlist + MFA on the
  single-service `require_admin` path (the alternative considered above), not reverting to
  unauthenticated-by-default public reachability.
- **Sequencing dependency**: the migration's `ALTER ROLE review_iq_app NOBYPASSRLS` must not be
  applied until *after* `review-iq-admin` is deployed and verified working (it now owns the one
  remaining legitimate use of a bypass-holding role reachable from a request-serving path) — see
  the PR body's numbered apply sequence.

## Who holds access to `review-iq-admin`

Updated 2026-08-01, correcting a real gap found in production: `review-iq-admin` was deployed
2026-07-31 with `--no-allow-unauthenticated` but **zero IAM bindings at all** — not even GG's
own identity. That's an availability defect, not a security win: nobody, including the intended
operator, could reach `require_admin`'s Basic auth underneath without first fixing the binding.

Fixed via:
```
gcloud run services add-iam-policy-binding review-iq-admin \
  --project=review-iq-prod --region=asia-south1 \
  --member=user:gaurav.gandhi2411@gmail.com --role=roles/run.invoker
```

**Access roster (the only entry, by design — single-operator, script-driven, per the
Alternatives section above):** `user:gaurav.gandhi2411@gmail.com` (GG's own canonical Google
account) holds `roles/run.invoker` on this service. No other user, group, or service account is
bound. Invocation is via `gcloud auth print-identity-token` (default audience — a user account
cannot use `--audiences` with a custom value, that flag requires a service account) piped into
an `Authorization: Bearer` header, or equivalently `gcloud run services proxy review-iq-admin`.

Verified live, both directions, 2026-08-01:
- Without the identity token: Google's own IAM-layer 403 (`Error: Forbidden`, the platform's
  HTML page, never reaches the app) — confirms the service is still not publicly reachable.
- With the identity token: the *application itself* responds — `/health` returns real `200`
  JSON; `/admin/organizations/<uuid>` returns `{"detail":"Not authenticated"}` (FastAPI's own
  401, not Google's), since no `Authorization: Basic` header was sent. This is the two-layer
  design working as intended: IAM gates *who can reach the app at all*, `require_admin` still
  gates the actual operation underneath, independently.

## Verification performed

- `gcloud run services get-iam-policy review-iq --region=asia-south1 --project=review-iq-prod`
  — confirmed `allUsers` → `roles/run.invoker`, service-wide (not path-scoped), 2026-07-31.
- Read every route in `app/api/admin.py` in full — confirmed all 5 are operator-only CRUD with
  no legitimate public caller.
- Read `app/auth/admin.py` in full — confirmed `require_admin` uses `hmac.compare_digest` for
  the username and argon2id `verify()` for the password (timing-safe, no plaintext comparison),
  reasonable as a defense-in-depth layer but not sufficient alone as the only gate on a publicly
  reachable service.
