# Email deliverability runbook — Resend sender switch

review-iq sends alert emails (immediate + daily digest) via Resend
(`app/core/alerts/channels/resend_channel.py`). The sending identity is
entirely config-driven — switching sandbox → a free `eu.org` domain → a paid
custom domain is an environment-variable change plus a Resend dashboard step,
never a code change.

## Current state (as of this doc)

Live default: **Resend sandbox** (`RESEND_FROM_EMAIL=onboarding@resend.dev`).
Sandbox only delivers to the one Resend-account-verified email
(`RESEND_TEST_RECIPIENT`) — it cannot reach real sellers' inboxes. This is
intentional: do not point `RESEND_FROM_EMAIL` at anything unverified in prod.

## Switching to a verified sending domain (eu.org, or any real domain later)

1. **Add the domain in Resend** — dashboard → Domains → Add Domain. Resend
   gives you a set of DNS records (SPF, DKIM, and a DMARC recommendation).
2. **Add those DNS records** at your domain's DNS provider (eu.org's control
   panel, or your registrar for a paid domain):
   - **SPF**: a `TXT` record on the root (or subdomain) authorizing Resend's
     sending servers — Resend gives you the exact value.
   - **DKIM**: one or more `CNAME`/`TXT` records Resend generates per-domain;
     paste them exactly as shown.
   - **DMARC**: a `TXT` record at `_dmarc.<domain>`, e.g.
     `v=DMARC1; p=none; rua=mailto:you@yourdomain` to start in monitor-only
     mode. Tighten to `p=quarantine` once you've confirmed alignment.
3. **Verify** in the Resend dashboard — propagation is usually minutes, can
   take up to 48h for some registrars.
4. **Flip the sender, nothing else**:
   ```
   RESEND_FROM_EMAIL=alerts@yourdomain.eu.org
   RESEND_FROM_NAME=Review-IQ Alerts
   RESEND_REPLY_TO=support@yourdomain.eu.org   # optional
   ```
   Restart the service (Cloud Run redeploy or local reload) — no code change.
5. Send a real test alert (`POST /internal/digest/run` with a seeded pending
   event, or trigger one through the normal ingestion path) and confirm the
   Resend dashboard shows the send succeeded from the new domain.

## Honest success bar

- **eu.org path**: SPF + DKIM passing and Resend reporting a verified,
  successful send is the actual success criterion. eu.org subdomains carry
  weak/no sender reputation, so landing in spam even with a fully verified
  domain is expected, not a bug — it proves the mechanism (auth passes) works.
  Real inbox placement needs a domain with established reputation, which is
  out of scope until a paying seller justifies buying one.
- **Testing the subject emoji**: set `ALERT_SUBJECT_EMOJI_ENABLED=False` and
  send the same alert both ways through a spam-score checker (e.g.
  mail-tester.com) or your own inbox, then compare. This is a manual step —
  automated tests can't judge spam-folder placement.

## Unsubscribe / List-Unsubscribe

Every alert email includes a one-click unsubscribe link
(`app/core/alerts/unsubscribe.py`, `GET`/`POST /unsubscribe`) once
`UNSUBSCRIBE_SIGNING_KEY` and `API_PUBLIC_BASE_URL` are set. Unset either and
alerts still send — just without the link/header, which hurts deliverability
but never blocks sending. The link clears `organizations.notification_email`
for that org (the same field that gates all sends), so unsubscribing stops
every alert type at once. Sellers re-enable via the existing authenticated
`PUT /bff/alerts/notification-email`.
