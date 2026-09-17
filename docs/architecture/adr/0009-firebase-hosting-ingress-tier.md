# ADR 0009: Firebase Hosting as the `api.samidhareviews.xyz` Ingress Tier

**Status:** Accepted — documents an already-live, previously-undocumented production dependency.
**Date:** 2026-07-31
**Scope:** Undocumented ingress tier + DNS enumeration + P3 unblock, P1. `api.samidhareviews.xyz`
is fronted by Firebase Hosting, not the native Cloud Run domain mapping (`gcloud run
domain-mappings create`) every other document in this repo — `ops/runbooks/cloud-run-deploy.md`,
PR #19's escalation steps, `plan.md`'s Section C entries — assumes. This ADR exists because that
assumption was live-verified wrong, not because a new architecture decision was made today; the
architecture described here has been running in production, undocumented, since 2026-07-07.

## Context

Investigating why `api.samidhareviews.xyz` was serving correctly despite `samidhareviews.xyz`'s
nameservers still being NameCheap's default (`dns1/dns2.registrar-servers.com` — the Cloudflare
migration PR #19 recommends was never executed) surfaced that `api.samidhareviews.xyz` resolves
via a **CNAME to `review-iq-prod.web.app`** (confirmed via direct DNS query), not a CNAME/A record
to any Cloud Run-issued target. `review-iq-prod.web.app` is a Firebase Hosting default site URL —
Firebase project ID `review-iq-prod`, the same GCP project the Cloud Run service lives in.

Queried Firebase Hosting's own API directly (`firebasehosting.googleapis.com`, not assumed):

- **Site**: `projects/review-iq-prod/sites/review-iq-prod` (the project's `DEFAULT_SITE`).
- **Custom domain**: `api.samidhareviews.xyz`, `status: DOMAIN_ACTIVE`, `certStatus: CERT_ACTIVE`,
  `dnsStatus: DNS_MATCH`, `updateTime: 2026-07-07T12:14:15Z`.
- **Latest release** (`releases/1783423643919000`): `rewrites: [{glob: "**", run: {serviceId:
  "review-iq", region: "asia-south1"}}]` — created and finalized by **`gaurav.gandhi2411@gmail.com`**
  on **2026-07-07T11:27:17Z**, `deployment-tool: cli-firebase`.

## Which Google account owns it

**`gaurav.gandhi2411@gmail.com`** — the same account that owns the `review-iq-prod` GCP project
and deploys the Cloud Run service. Firebase Hosting for this project was set up under this
account, via the `firebase` CLI, on 2026-07-07 (same day the Vercel domain object for
`app.samidhareviews.xyz` was created under a **different** account, `gg5678g@gmail.com` — see
"Two-account operational risk" below).

## Rewrite target and forwarding behavior

The rewrite (`glob: "**"` → `run: {serviceId: "review-iq", region: "asia-south1"}`) matches
**every path**, unconditionally, to the same public Cloud Run service (`review-iq`) this repo's
other docs already describe. This is Firebase Hosting's native "Cloud Run rewrite" feature, not a
custom proxy or a separately-deployed artifact:

- **TLS**: Firebase Hosting terminates TLS for `api.samidhareviews.xyz` itself (Google-managed
  cert, confirmed `CERT_ACTIVE`) and forwards the request to Cloud Run over HTTPS. There are two
  TLS terminations in the chain (client → Firebase edge → Cloud Run), the same double-termination
  pattern any CDN-fronted Cloud Run service has.
- **Caching**: Firebase Hosting's documented behavior is that `run` rewrites are always treated as
  dynamic content and are never served from Firebase's static CDN cache — this matches every
  observation made against this service this session (every `/v2/extract` call through this
  domain returned a fresh, request-specific result; no stale/cached response was ever observed).
  Documented behavior, not something separately load-tested here.
- **Does it bypass any control Cloud Run enforces?** No — and this matters more than it looks.
  The `review-iq` Cloud Run service's own IAM policy is `allUsers: roles/run.invoker` (fully
  public — confirmed via `gcloud run services get-iam-policy`). Firebase Hosting doesn't need to
  bypass anything: it's simply **another public hostname pointing at an already-fully-public
  backend**, not a distinct security boundary. Concretely, this means whatever protects a route
  at the application layer (e.g. `/admin/*`'s `SERVICE_ROLE` gating, landed 2026-07-31) protects it
  identically through every ingress — Firebase-fronted domain, Firebase's own default
  `*.web.app` host, and the raw `*.run.app` host — because all three ultimately invoke the exact
  same deployed code with no intermediate authorization layer of Firebase's own. Verified directly
  (same-day P0 pass): `/admin/*` returns 404 through all three; `/health` returns 200 through all
  three.

  **Scope note**: "the exact same deployed code" above means the three ingresses in this
  ADR's title all front the same public `review-iq` Cloud Run service — it does NOT mean
  every admin route lives there too. `app/api/admin.py`'s org/key CRUD is deliberately
  mounted on a *separate* Cloud Run service, `review-iq-admin` (`SERVICE_ROLE=admin`,
  `--no-allow-unauthenticated`, its own IAM-gated invoker binding — see ADR 0006), which
  none of these three ingresses expose at all. The public service returning 404 for
  `/admin/*` above is exactly what that split intends: it's absence of the route, not a
  same-service authorization check.

## Two-account operational risk

This project's production ingress now spans **two separate Google/personal accounts with no
shared recovery path**:

- **`gaurav.gandhi2411@gmail.com`** — owns the `review-iq-prod` GCP project (Cloud Run, Secret
  Manager, Firebase Hosting, the `api.` ingress).
- **`gg5678g@gmail.com`** — owns the Cloudflare account (`review-iq-demo` Pages project) and the
  Vercel account (the domain object for `samidhareviews.xyz`, the `app.` ingress, the new
  `samidha-reviews-web` project from this pass).

**Risk**: loss of either account (credential loss, suspension, 2FA lockout) takes down the
surfaces it owns, and **the other account has no ability to recover them** — there is no shared
organization, team, or backup credential bridging the two. A `gg5678g@gmail.com` lockout loses the
dashboard and marketing site with no path to recreate them from the `gaurav.gandhi2411@gmail.com`
side (a new Vercel/Cloudflare project could be stood up, but the domain objects/DNS records
themselves live under the locked account). A `gaurav.gandhi2411@gmail.com` lockout loses the API
entirely (Cloud Run, the DB connection secrets, Firebase Hosting) with no path to recover from the
`gg5678g@gmail.com` side.

**Not fixed here** — this is a risk finding, not a remediation. The two candidate fixes (consolidate
onto one account, or add a shared/secondary owner to both) are both real changes to account-level
access control outside what this pass should decide unilaterally; flagging for a GG decision.

## Addendum (2026-08-01): mail-routing topology, and a retracted finding

An earlier pass claimed outbound email from `alerts@mail.samidhareviews.xyz` (Resend, the alert
notification channel — see `app/core/alerts/channels/resend_channel.py`) was "very likely failing
SPF/DKIM checks," based on `nslookup`/PowerShell queries against `mail.samidhareviews.xyz` finding
no TXT or MX records. **That finding is retracted as stated.** Direct DNS verification (Google and
Cloudflare DoH, cross-resolver) found Resend's DKIM record live and correctly resolving at
`resend._domainkey.mail.samidhareviews.xyz` — a real RSA key, not absent. The root cause of the
original miss: DKIM records live at a selector-prefixed name (`<selector>._domainkey.<domain>`)
that a guessed/sampled public sweep won't find without either the exact selector name or a full
zone export — this session's original check queried only the bare `mail.samidhareviews.xyz` name
directly, which was never where DKIM would be. Confirming DKIM alone (correctly aligned, since the
selector's own name scopes it to `mail.samidhareviews.xyz`, matching the `From:` header domain)
is sufficient for a message to pass DMARC — RFC 7489 only requires SPF-alignment *or*
DKIM-alignment, not both — so the practical claim ("mail is likely failing authentication") does
not hold regardless of SPF's status.

**SPF's own status remains genuinely unresolved, not confirmed either way**, and this ADR does not
claim otherwise: repeated cross-resolver DoH queries against `mail.samidhareviews.xyz`, the bare
apex, and a plausible bounce subdomain (`send.samidhareviews.xyz`) found no SPF TXT record
anywhere. This may mean Resend's setup for this account doesn't require one, or it may mean it
exists at a name not yet checked — the authoritative source is the full Namecheap zone export
(pending as part of the DNS cutover), not another round of guessed public queries.

**`mail.samidhareviews.xyz` carries its own, independent DMARC policy**, distinct from the apex:
`_dmarc.mail.samidhareviews.xyz` → `v=DMARC1; p=none;` (confirmed via DoH, a genuinely separate
record from `_dmarc.samidhareviews.xyz`, not inherited). This means DMARC alignment/policy for
the sending subdomain is self-contained — relaxed-alignment inheritance from the apex is not a
live concern here, since the subdomain publishes its own explicit policy regardless. Like the
apex record, it currently has no `rua=` tag and so collects zero aggregate reports; both are
being updated in the same DNS cutover pass to add `rua=mailto:dmarc@samidhareviews.xyz`.

**Standing lesson, not specific to this domain**: a negative result from a guessed or sampled
public-DNS sweep only rules out the specific names tried — it is not evidence that no record
exists anywhere in the zone. Selector-prefixed records (DKIM), non-obvious subdomains, and
anything requiring the actual zone file are exactly what a partial sweep will systematically miss.
Treat "found nothing" as "checked these specific names, found nothing there" — never as "confirmed
absent" — until a full zone export (or equivalent authoritative source) is in hand.

## Decision

Document the ingress tier as it actually exists rather than migrate it to match the repo's prior
(incorrect) assumption. Firebase Hosting → Cloud Run works, is already TLS-verified and
`DOMAIN_ACTIVE`, and has been running since 2026-07-07 with no incident. Replacing it with a
native `gcloud run domain-mappings create` mapping would be a working-system migration with no
stated benefit — the two mechanisms are functionally equivalent for this service's needs (both
proxy to the same Cloud Run backend, both terminate TLS, neither caches dynamic responses). If a
concrete reason to migrate emerges later (e.g. consolidating tooling, Firebase Hosting quota
limits), it gets its own ADR then.

## Consequences

- `ops/runbooks/cloud-run-deploy.md` and any future onboarding doc must describe `api.`'s ingress
  correctly — corrected in `ARCHITECTURE.md` (this pass); the runbook itself is a separate,
  smaller follow-up edit.
- Any future security review of `/admin/*` (or any other route-level gate) must verify through
  all three live hostnames, not just the raw `*.run.app` host — this ADR's own verification
  section is the template for that check going forward.
- The two-account risk is now a named, documented finding (not silently discovered again by a
  future session) — see "Two-account operational risk" above.

## Alternatives considered

- **Migrate `api.` to a native Cloud Run domain mapping, matching PR #19's original assumption.**
  Rejected for now: no functional gap identified: TLS, dynamic-content correctness, and IAM
  exposure are all equivalent to what a native mapping would provide. Migrating a working ingress
  with no stated benefit is exactly the kind of unjustified-complexity move this repo's own
  standing rules reject.
- **Leave it undocumented.** Rejected — this is the exact failure mode the wider "surface
  recovery" pass exists to close: undocumented infrastructure that silently decays or gets
  mis-assumed by the next person (or the next Claude Code session) reading this repo.
