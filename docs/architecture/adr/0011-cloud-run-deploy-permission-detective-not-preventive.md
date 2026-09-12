# ADR 0011: Cloud Run Deploy Permission — Detective Control, Not Preventive (For Now)

**Status:** Accepted.
**Date:** 2026-08-01
**Scope:** Whether to restrict `gaurav.gandhi2411@gmail.com`'s `roles/owner` grant on
`review-iq-prod` so that Workload Identity Federation (PR #60's CI pipeline) becomes the
*only* principal able to deploy Cloud Run, or to rely on the drift check (detective) alone.

## Context

PR #60 gives GitHub Actions (via WIF) a real production deploy path. That's only a sound
architecture if every identity that *could* deploy outside it is trusted and accounted for.
An audit (session-collision investigation, 2026-08-01) found:

- `gaurav.gandhi2411@gmail.com` holds `roles/owner` on the project — not a narrow
  `roles/run.developer` grant, the full primitive bundle (billing, IAM administration, every
  resource type). This is the same identity that performed the untracked `v0-19-0` and
  `bypassrls-cutover` manual deploys this project's own incidents are named after.
- The only mechanism that can restrict *just* the deploy-capable permissions out of an
  Owner-holding principal, without also stripping billing/IAM management, is an IAM Deny
  Policy (a separate GCP construct, layered on top of role grants, that overrides them for
  the specific permissions it names — `run.services.update` / `run.services.create`).

## Decision

**Do not apply a deny policy right now.** Rely on the detective control already built
(`scripts/check_cloud_run_deploy_is_from_main.py`, wired into
`.github/workflows/deploy-cloud-run.yml`) instead of a preventive one.

The two are asymmetric in cost at this project's current stage:

- **Preventive (deny policy) cost:** removes the *only* manual escape hatch during a CI
  outage — no `gcloud run deploy`, no `gcloud run services update-traffic` for the owner
  account at all, for either service. It would also block the exact traffic-split commands
  PR #60's own staged rollout uses internally (`update-traffic --set-tags` /
  `--to-latest`) if those commands are ever run by hand outside CI (e.g., debugging a stuck
  rollout). A deny policy is an all-or-nothing gate on *every* deploy path, CI included in
  spirit even where not in mechanism, because a future CI credential problem forces the same
  manual path the policy just removed.
- **Preventive benefit right now:** this project has zero paying customers. A bad or
  untracked deploy today is a solo-developer inconvenience (traced and fixed the same day,
  as this session's own incidents were), not a customer-facing incident with contractual or
  reputational weight.
- **Detective (drift check) cost:** near zero — it's already built, already wired into both
  a post-deploy step and a nightly schedule.
- **Detective benefit:** catches exactly the failure mode that actually occurred (an
  untracked manual deploy) within, at most, one nightly cycle — proven live against the real
  incident twice this session (correctly failed against both `v0-19-0` and
  `bypassrls-cutover`, the two real untracked tags that existed in production).

Given the preventive control's cost is real and immediate (loses the only break-glass path)
while its risk-reduction is marginal at zero-customer volume, and the detective control
already does the job of *finding out*, the proportionate choice today is detective-only.

### Making the detective control loud, not just present

`deploy-cloud-run.yml` runs `check_cloud_run_deploy_is_from_main.py`:
- As the last step of `build-and-deploy`, immediately after every deploy — so an untracked
  or failed-to-resolve deploy is caught in the same CI run that created it.
- On an independent `schedule` trigger (`0 3 * * *`, daily) — so a manual deploy that never
  goes through this pipeline at all (exactly what happened twice this session) is still
  caught, within 24 hours, with no dependency on anyone remembering to run anything.

Either failure mode is a **hard CI failure** (red run, GitHub's own default failed-workflow
notification to the account) — not a warning, not an informational log line. This is the
same "won't prevent, will detect same-day" pattern already established for the frontend's
`check_prod_deploy_is_from_main.py` (rule 31a's own precedent).

## Revisit trigger

**Reopen this decision once review-iq has paying customers.** At that point a bad deploy
stops being a same-day solo-developer fix and starts being a customer-facing incident with
real cost — the preventive control's cost/benefit calculus flips, and the deny policy
(exact permissions and break-glass procedure already specified in this session's audit
report) should be applied at that point, not before.

## Alternatives considered

- **Deny policy now, with a break-glass exception principal.** Rejected for now per the
  cost/benefit above — revisit at the trigger point.
- **Downgrade `roles/owner` to a narrower predefined/custom role bundle.** Rejected: GCP's
  primitive roles are monolithic; there's no supported way to "subtract one permission" from
  Owner short of removing it entirely and re-granting a custom role that excludes deploy
  permissions but includes everything else Owner currently provides (billing, IAM policy
  administration, Secret Manager, etc.) — a much larger and more disruptive change than the
  targeted deny-policy alternative, for the same outcome.
- **Do nothing (no detective control either).** Rejected outright — this is what was already
  in place before this session's work, and it's exactly how two untracked deploys happened
  without anyone noticing until an unrelated investigation surfaced them.

## Consequences

- The owner account retains full manual deploy capability. Nothing technical prevents a
  third untracked deploy from happening the same way the first two did.
- The drift check is the sole enforcement mechanism until the revisit trigger. If it is ever
  weakened, disabled, or its schedule removed without an equivalent replacement, this
  decision's entire premise (that detection happens reliably and promptly) no longer holds
  and should be treated as a live gap, not a formality.
- `ops/runbooks/bypassrls-cutover-status.md` (this same pass) documents a related, narrower
  point: a live database read immediately before any write is the actual binding mitigation
  for concurrent-session migration risk, in the same spirit as this ADR's preference for
  verified detection over unverified prevention.
