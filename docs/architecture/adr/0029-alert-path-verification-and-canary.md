# ADR 0029: Every alerting path, verified for real — and a canary so this can't quietly regress again

## Context

No alert reached GG since ~2026-08-14, through two consecutive systems: the Slack webhook
that was never configured, then the GitHub-issue alerts erroring on labels that never
existed (both found this session, by inspection — neither had ever fired to tell anyone it
was broken). Session 13 P7 asks to deliberately break each alerting path, confirm delivery
for real, add a standing canary, and audit every alerting path the same way.

## P7a — deliberately triggering each real alert path

**GitHub issues (`schedule-failure-alert` composite action)**: already exercised for real
earlier this session while investigating P3's capacity model. The `ci-alert`/`security`
labels the action requests had never been created in this repo — `gh issue create --label`
was erroring out (not silently skipping) on every scheduled-check failure since creation.
Fixed by creating the labels directly, then verified live by manually re-triggering
**Schema Drift Check** (a workflow that was — and, honestly, still is — genuinely red, from
the pre-existing grant drift finding 20 below): **issue #174 was created correctly on the
first re-trigger**, and confirmed again this session on a second re-trigger (the issue now
carries 3 comments — the original creation, and two "still failing" recurrence comments) —
both the first-creation and the recurring-failure paths are now real, live-verified, not
just believed fixed. Nothing was deliberately broken to produce this test; the underlying
drift is a real, separate finding (P8a below), and re-triggering it was the same action
that incidentally proved the alert path works.

**GitHub issues, second mechanism (`model-availability-check.yml`'s own inline
`github-script` alerting, independent of `schedule-failure-alert`)**: already verified real
this session (via PR #170) — issue #127 was genuinely created, then auto-closed by the
workflow's own recovery logic once the underlying regex bug was fixed. This is a second,
separate GitHub-issue alerting implementation in this repo (not sharing code with
`schedule-failure-alert`), confirmed independently working.

**Email (Resend)**: sent a real, deliberate test message via `ResendChannel.send()` directly
(same code path `app/core/alerts/engine.py` uses for a real flagged-review alert), to
`RESEND_TEST_RECIPIENT` — the one Resend-account-verified address the sandbox sender can
reach (see `docs/email-deliverability-runbook.md`). **The send call succeeded**: Resend's
API returned a real message ID (`d52576b3-c1e6-46df-80dd-b3d4c5264063`), no `ChannelError`
raised. Attempted to independently verify delivery status via Resend's own
`GET /emails/{id}` endpoint (deliberately not trusting our own code's success log line) —
**this API key is scoped send-only and cannot query message status** (`ResendError: This
API key is restricted to only send emails`). This is a real, informative finding in its own
right: the production Resend key follows least privilege (rule 96) tightly enough that even
this session's own verification attempt was refused by it. **Net result: VERIFIED that a
real send succeeded (real ID, no error); NOT independently verified that it reached an
inbox** — that last step needs GG to check the `RESEND_TEST_RECIPIENT` inbox (including
spam) for a message titled "[TEST] Samidha Reviews alert-path verification (Session 13
P7a)," sent today.

**Slack webhook**: not re-tested. Confirmed (again) that `SLACK_WEBHOOK_URL` has never been
configured as a repo secret — every caller (`eval.yml`, `failover-probe.yml`,
`web-surface-probe.yml`) already guards this and falls back to the GitHub-issue path when
the secret is absent, so there is no live webhook URL to send a test payload to. A webhook
that has never existed cannot be "deliberately triggered" — the honest finding is that this
channel has never sent anything, ever, and every caller already treats that as the expected
state rather than an error.

**Nothing was left in a "broken" state to revert.** The labels created are a permanent fix,
not a test scaffold. The two GitHub-issue triggers were real re-runs of already-scheduled
workflows, not artificially injected failures requiring cleanup. The one real email sent is
a one-time, already-completed action with no lasting state to undo.

## P7b — a standing canary, so this can't quietly regress again

New `.github/workflows/alert-path-canary.yml`, daily cron. Deliberately fails a step every
run (that's the intended, permanent-red control condition, not a bug), sends the failure
through the real `schedule-failure-alert` path, then **independently verifies** that the
resulting tracking issue actually exists and was updated within the last 15 minutes. If the
alert mechanism silently breaks again — a label disappears, a token scope changes, GitHub's
API changes — this verification step fails too, and that failure is a *different*, newly
red step in the Actions UI: a signal from GitHub's own native workflow-status system, a
channel independent of the custom composite action being tested. The tracking issue this
canary maintains (title: "Alert path canary (expected to be open — this is the control)")
is *supposed* to stay open and get a fresh comment every day — closing would itself be the
anomaly worth investigating.

## P7c — every alerting path in this repo, audited

| Path | Mechanism | Last real, confirmed delivery | Verified how |
|---|---|---|---|
| GitHub issue (`schedule-failure-alert`) | `eval.yml`, `schema-drift-check.yml`, `web-surface-probe.yml`, `demo-quota-probe.yml`, `alert-path-canary.yml` (new) | 2026-09-12, issue #174 (created + 2 recurrence comments) | This session: fixed the missing `ci-alert`/`security` labels, manually re-triggered twice, read the resulting issue directly |
| GitHub issue (`model-availability-check.yml`'s own inline mechanism) | that workflow only | 2026-09-12, issue #127 (created, then auto-closed on recovery) | This session (PR #170): manually re-triggered, read the resulting issue and its auto-close |
| Email (Resend) | `app/core/alerts/engine.py` → `digest.py` / immediate alerts → `resend_channel.py` | 2026-09-12, message ID `d52576b3-...`, real API success | This session: sent a real deliberate test message; verified the send succeeded (real ID, no error). Could NOT independently verify inbox arrival — the production API key is send-only scoped. GG's inbox check would close this fully. |
| Slack webhook | `eval.yml`, `failover-probe.yml`, `web-surface-probe.yml` (all conditional) | **Never** | Confirmed (repeatedly, across sessions) that `SLACK_WEBHOOK_URL` has never been a configured repo secret — every caller already falls back to the GitHub-issue path when absent |

## Consequences

- The alerting layer is no longer "trusted on faith" for its two most-used mechanisms
  (GitHub issues, email) — both have a real, dated, evidenced delivery on record, not just
  code that looks like it should work.
- The canary makes the NEXT silent regression visible within 24 hours instead of weeks,
  closing the exact gap this ADR's Context section describes.
- Slack remains a documented dead channel, not a false promise — every caller already
  degrades gracefully in its absence; removing the Slack code path entirely is a reasonable
  future cleanup but out of this ADR's scope (it costs nothing to leave, and every branch
  guards it correctly today).

## Alternatives considered

- **Treat the successful Resend API call alone as full proof of delivery.** Rejected — a
  successful send-API response proves the message left Resend's servers, not that it
  reached an inbox (spam filtering, bounces, and provider-side drops all happen after that
  point). Stated the boundary of what was actually verified rather than rounding up.
- **Give the canary workflow write access to query its own alert issue via the composite
  action's internal state instead of a fresh `gh issue list` search.** Rejected: re-deriving
  the issue's real, current state independently (a fresh search, not a value handed off
  in-process) is exactly what makes the verification step trustworthy — trusting the alert
  step's own reported success would just be testing the mechanism against itself.

## Amendment (Session 15c)

**A claim in this ADR was wrong.** The Slack row in the table above and the Slack paragraph in the
verification write-up state that every caller of the Slack path (`eval.yml`,
`failover-probe.yml`, `web-surface-probe.yml`) "falls back to the GitHub-issue path when
the secret is absent." That was true of `eval.yml` and `web-surface-probe.yml` only.
`failover-probe.yml` never called the alert action: with `SLACK_WEBHOOK_URL` unset it
simply ran the probe without `--slack-webhook` and, on failure, alerted nobody. Evidence:
`gh run list --workflow=failover-probe.yml` shows 7 of 7 scheduled runs failed
(2026-09-13 through 2026-09-19), and `gh issue list --state all --search failover` returns
no issues. The probe was failing for a real reason (`GEMINI_API_KEY` and
`SECONDARY_PROVIDER_*` are unset in repo secrets), so a real outage sat unreported for a
week.

**Why the canary did not catch it.** The canary's surface is the composite action
`.github/actions/schedule-failure-alert` and nothing else: it proves the action can open,
comment on and close an issue. A scheduled workflow that never calls the action is outside
that surface entirely, so it was invisible to the canary by construction. The same gap
applied to `bypassrls-container-check.yml`, `db-backup.yml`, `deploy-cloud-run.yml`
(scheduled drift job), `migration-drift-check.yml`, `secret-scan.yml` (weekly full-history
scan) and `security-bypassrls-check.yml`: none had a failure -> issue path.

**What this change does.** Each of those seven workflows now ends with a step (for
`deploy-cloud-run.yml`, a separate final job, so `issues: write` is never granted to the job
holding the Workload Identity Federation token) that calls the composite action, gated by
`if: always() && !cancelled() && github.event_name == 'schedule'`. Push and pull-request
runs do not open ops issues. `issues: write` is scoped to the job that needs it. Because
`failover-probe.yml` is genuinely failing, it will open a `ci-alert` issue on its next
scheduled run; that is the intended effect, not a regression.

**Enforcement is now static, not just runtime.** `scripts/check_scheduled_workflows_alert.py`
runs in CI (`lint-and-test`) and fails, naming the file, if any workflow with a `schedule`
trigger lacks a recognised alert path (the composite action, or inline `gh issue create` /
`issues.create`). Delegation to a reusable workflow, Slack-only notification, and an
unparseable workflow file all fail closed. Its known looseness (per-file, not per-step; it
does not evaluate `if:` conditions) is stated in the script's docstring; the guard removes
the "forgot to add any alert" failure, not every possible misconfiguration of one.
