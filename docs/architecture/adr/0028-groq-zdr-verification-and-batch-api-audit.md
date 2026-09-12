# ADR 0028: Groq ZDR verification — what's actually checkable, and the Batch API audit

## Context

GG enabled "Inference APIs" ZDR in the Groq console this session but left Global ZDR off (Global
ZDR also disables Batch and fine-tuning storage, per Groq's own docs). Session 13 P2 asks to (a)
verify from a live API response that inference ZDR is in effect for this key, (b) determine
whether any code path calls Groq's actual Batch API, (c) report whether Global ZDR is safe to
enable given (b), and (d) update the legal/security surface to the real retention position.

## P2a — live-response verification: not possible for this provider, and here's the evidence

The brief asks to verify via "headers or docs endpoint, not the console screenshot." Investigated
both, in order:

1. **Response headers.** Made a real, live `POST /openai/v1/chat/completions` call against
   production's Groq API key and captured every response header verbatim:
   ```
   HTTP/1.1 200 OK
   x-groq-region: bom
   x-ratelimit-limit-requests: 1000
   x-ratelimit-limit-tokens: 8000
   x-ratelimit-remaining-requests: 999
   x-ratelimit-remaining-tokens: 7922
   x-ratelimit-reset-requests: 1m26.4s
   x-ratelimit-reset-tokens: 585ms
   x-request-id: req_01m2aq3tkbej9v7bp9mwdv3c1d
   ... (cache-control, cloudflare/CF-RAY, set-cookie, via, HSTS — standard infra headers)
   ```
   **No header anywhere in this response indicates ZDR status.** A first-pass web search
   suggested Groq responses carry an `x-zero-data-retention` header — this is FALSE for Groq: that
   header belongs to xAI's ("Grok," a different company, easily confused by name) API, and the
   search result had conflated the two. Caught before reporting it as fact by fetching Groq's own
   docs page directly rather than trusting the search summary (rule: evidence over recall — a
   plausible-sounding claim from a blended search result is not verified until checked against the
   provider's own primary source).

2. **Docs endpoint.** Fetched `console.groq.com/docs/api-reference` directly: the only documented
   endpoints are chat completions, responses (beta), audio, models, batches, and files/fine-tuning
   — no organization/account/admin/data-controls endpoint exists. Fetched
   `console.groq.com/docs/your-data` directly: it describes ZDR's effect (disables Batch/fine-
   tuning, stops the 30-day troubleshooting log) but explicitly gives no way to read the setting
   back via API — the only documented path to check or change it is the Console UI's Data Controls
   page.

**Conclusion, stated plainly rather than stretched: Groq does not expose ZDR status through any
live API response or documented endpoint. The verification method this session's brief asked for
does not exist for this provider.** This is not a search failure on this session's part — it is a
real gap in Groq's own API surface, confirmed against Groq's own current documentation. The
inference-ZDR-enabled state is **BELIEVED** (GG reported enabling it in Console), not VERIFIED by
this session's own independent means, and this ADR states that distinction explicitly rather than
blurring it (rule 65a). The only way to raise this from BELIEVED to VERIFIED would be a written
confirmation from Groq support, or GG re-checking Console Data Controls and reporting the visible
toggle state directly (not a chat description of a screenshot — rule 101b).

## P2b — Batch API call-site audit

Searched every LLM-provider call site in the codebase for use of the Groq SDK's `.batches.*`,
`.files.*`, or `.fine_tuning.*` methods (the actual Groq Batch API), as distinct from this
project's own batching *loop* over individual chat-completion calls:

| File | Groq SDK method(s) called | Batch API? |
|---|---|---|
| `app/core/providers/groq.py` (`GroqProvider.complete`) | `client.chat.completions.create` | No |
| `app/api/ops.py` (`_provider_status_deep`) | `client.models.list` | No |
| `eval/consensus/panel.py` | `client.chat.completions.create` | No |

**No call site anywhere in this repository uses Groq's Batch API.** `POST /v2/extract/batch` and
`POST /v2/ingest/csv` are this project's own async job machinery (`batch_job_rows` Postgres table,
`app/core/ingest_worker.py`'s drain loop) issuing individual `chat.completions.create` calls one
row at a time — architecturally unrelated to Groq's `/v1/batches` endpoint despite the similar
name. Verified by reading `app/api/v2/extract.py::extract_batch` directly: it enqueues rows and
returns a `job_id` immediately; processing happens via `_drain_until_batch_complete`, which is
this project's own background task, not a submission to Groq.

## P2c — Global ZDR is safe to enable

Given P2b found zero Batch/fine-tuning usage anywhere in this codebase, enabling Global ZDR has no
functional impact on this product — there is nothing running today that Global ZDR's Batch/fine-
tuning shutoff would break.

**Exact console steps for GG** (not executed by this session — GG confirmation only):
1. Log into `console.groq.com`.
2. Navigate to **Settings → Data Controls**.
3. Under **Zero Data Retention**, toggle from "Inference APIs only" to **Global**.
4. Confirm the dialog (it will note Batch and fine-tuning storage become unavailable — both
   already unused by this codebase per P2b, so this is a no-op functionally).

## P2d — legal/security surface updated to the real position

- `SECURITY.md`: states the current real position — inference ZDR enabled (BELIEVED, GG-reported,
  not independently API-verifiable per P2a), Global ZDR not yet enabled, and that this codebase
  has zero Batch/fine-tuning API usage (so enabling Global ZDR is a pending, safe, zero-blast-
  radius step).
- `legal/sub-processors.md`: the "Zero Data Retention eligibility for this account" TODO row is
  resolved — replaced with the real, honestly-qualified state (BELIEVED not VERIFIED, and why).
- `legal/privacy-policy.md`: retention language updated to describe the two-mode retention model
  (`#167`, merged) explicitly: stateless orgs (default) — Samidha Reviews itself persists nothing
  beyond usage/cost counters; retained orgs — a customer-chosen 30/90-day window in Supabase, with
  on-demand purge (`POST /v2/purge`). Groq's position stated as inference-ZDR-believed-enabled,
  same honesty qualifier as above.

## Consequences

- Same honesty standard as the rate-limit correction (Session 12/13): a claim this session could
  not independently verify is stated as BELIEVED, with the exact reason it couldn't be raised to
  VERIFIED, rather than rounded up to sound more confident than the evidence supports.
- If GG enables Global ZDR per P2c's steps, this ADR's BELIEVED/VERIFIED distinction for inference
  ZDR remains accurate — Global ZDR is a superset, so enabling it doesn't change what could or
  couldn't be checked via API; the same console-only limitation applies.

## Alternatives considered

- **Report the `x-zero-data-retention` header as if it applied to Groq, since the search result
  said so.** Rejected immediately upon checking Groq's own docs and a real live response — this
  would have been exactly the kind of unverified claim rule 101/65a exists to prevent, and the
  header genuinely does not exist in Groq's real response.
- **Treat GG's console-enablement statement as VERIFIED since GG is a trusted source.** Rejected:
  rule 101b applies even to the user's own description of a screen this session never saw directly
  — the distinction is not about trust, it's about what channel produced the claim. Stated as
  BELIEVED, which is the honest and sufficient classification here, not a slight against GG's
  report.
