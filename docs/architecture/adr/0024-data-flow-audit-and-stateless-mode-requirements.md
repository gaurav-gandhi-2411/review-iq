# ADR 0024: Data-flow audit — where review text lands today, and what "stateless" requires

## Context

Session 12 D1 commits to a public claim: "we store nothing by default." A claim like this can
only be published once — if it's wrong, it's not a bug, it's a broken promise to every customer
who read it before signing up. This ADR is the audit that must exist before that claim goes live,
verified against the **running system**, not the code — this project has been wrong about
deployed behavior five times before (most recently H3b, Session 11: every real demo call silently
failed to record cost, found only by reading a row back from the database).

## Method

Two passes, in this order:

1. **Code trace**: every function that touches `review_text`/`request.text`, every `structlog`
   call site in the extraction/ingest/alerts paths, every table schema, the cassette mechanism,
   in-memory caches, and Sentry (absent — confirmed via `grep -ri sentry` across `app/`,
   `pyproject.toml`; no dependency, no integration, nothing to audit).
2. **Live verification**: three real calls against **production** (`api.samidhareviews.xyz`,
   revision `review-iq-00024-8r9`) with distinctive sentinel strings, one per endpoint under
   audit, at logged timestamps:
   - `POST /demo/extract`, sentinel `SENTINEL_5293D96A6753E847`, 2026-09-12T07:45:49Z–07:45:55Z.
   - `POST /v2/extract` (real API key, disposable test org, deleted after), sentinel
     `SENTINEL_V2_9AC41F`, 07:46:31Z–07:46:46Z.
   - `POST /v2/ingest/csv` (same test org), sentinel `SENTINEL_CSV_7B21E4`, submitted 07:47:00Z,
     confirmed `status: done` at 07:47:59Z.

   Then, independently of the code trace's predictions: **a Python script enumerated every
   `text`/`character varying`/`jsonb`/`json` column across every table in `public` (61 columns)
   and searched each one for all three sentinels** (`ILIKE '%sentinel%'`), and **`gcloud logging
   read`** searched both Cloud Run log streams (`run.googleapis.com%2Fstdout`, the app's own
   structured logs, and `run.googleapis.com%2Frequests`, GCP's platform HTTP-metadata log) for
   the literal string `SENTINEL` across the full call window. This order matters: the scan was
   written to find sentinels anywhere in the schema, not to check the specific columns the code
   trace already predicted — a scan scoped only to predicted columns would have missed exactly
   the kind of surprise this audit exists to catch (see Finding 2).

   Test org, API key, and all rows were deleted after verification (cascade-deleted via the org
   row; confirmed 0 rows remaining for that org in `extractions` and `batch_job_rows`).

## Findings (P1a) — every surface, one table

| Surface | Review text reaches it? | Retention today | How verified |
|---|---|---|---|
| `public.extractions.review_text` | **YES** | Forever (no purge) | Live: `SENTINEL_V2_9AC41F` and `SENTINEL_CSV_7B21E4` both found via direct column scan |
| `public.extractions.product` (LLM-derived) | **YES, when the model echoes input verbatim** | Forever (no purge) | Live: both sentinels appeared here too — the LLM used the sentinel string as the extracted "product name" in both calls |
| `public.batch_job_rows.text` | **YES** | Forever — status flips `pending→done`, `text` column is never cleared or deleted (confirmed by reading `20260709000001_batch_job_rows.sql`: no purge logic anywhere) | Live: `SENTINEL_CSV_7B21E4` found here at ingest time, still present after job completed |
| `public.batch_jobs.source_columns` | No — column names + input hashes only | n/a | Code: `20260511000006_batch_jobs.sql` schema; confirmed no hit in live scan |
| `public.extraction_costs` | No — provider/model/tier/tokens/cost only | n/a | Code (Session 11 audit) + confirmed no hit in this session's live scan |
| `public.usage_records` | No — counters only | n/a | Code trace; no hit in live scan |
| `public.alert_log.details` (JSONB) | **Partial — LLM-derived fields only** (`topics`, `cons`, `product_id`, `score`), not raw `review_text` | Append-only, no purge (`20260621000001_alerts.sql`: "no UPDATE/DELETE granted at all") | Code trace (`app/core/alerts/engine.py`); no live alert fired in this session's test (no alert-eligible content), not re-verified live this session |
| `public.authenticity_audits` | No — `review_hash`, score, label, flags only | n/a | Code: `20260611000001_authenticity_audits.sql` schema; no hit in live scan |
| `public.api_keys` / `organizations` / `organization_members` / `quota_requests` / `shopify_installations` / `google_business_installations` / `demo_daily_usage` | No | n/a | Live scan: zero hits across all remaining 61 audited columns |
| **Cloud Run app logs** (`run.googleapis.com%2Fstdout`, structlog JSON) | **YES — a real, previously undocumented leak** (Finding 2 below) | GCP default: `_Default` log bucket, 30-day retention, not configured otherwise (not verified whether this project has changed the default — flagged, not fixed, see P1d) | Live: both sentinels found verbatim in `extraction.completed`'s `product` field, both `/v2/extract` calls |
| **Cloud Run platform request logs** (`run.googleapis.com%2Frequests`) | No — method/path/status/latency/IP/trace only, no body | n/a | Live: fetched full JSON for the exact request window; confirmed no body field exists in the schema at all, not just absent from this sample |
| **Application-level error logs / stack traces** | **BELIEVED, not verified** — `llm.parse_error`/`router.unexpected_error`/`llm.unexpected_error` log `error=str(exc)`; a JSON-decode failure on a malformed LLM *response* can embed a snippet of that response (which is model output, not raw customer input, but is derived from it) | Same as app logs | Code trace only — did not reproduce a live parse failure this session; marked BELIEVED, not VERIFIED, per this engagement's evidentiary standard |
| **Sentry or equivalent** | N/A — not integrated | n/a | Verified absent: no `sentry` reference anywhere in `app/`, `pyproject.toml`, or `uv.lock` |
| **Demo endpoint in-process LRU cache** (`app/api/demo.py`, max 256 entries) | **Indirectly** — caches the `ReviewExtraction` result (not raw `review_text`, which isn't a field on that model), but `product`/`pros`/`cons`/`topics` can echo verbatim substrings (same mechanism as Finding 2) | Process lifetime only — lost on cold start, never written to disk | Code trace; not independently live-verified this session (would require inspecting live process memory, out of scope) |
| **CSV upload file itself** (`UploadFile`, FastAPI) | Transiently, during the request | None beyond the request lifecycle — FastAPI's `SpooledTemporaryFile` is not explicitly written to disk by this app's code and is garbage-collected after the request; no GCS bucket or explicit temp-file path exists anywhere in `app/api/v2/ingest.py`/`app/core/csv_ingest.py` | Code trace: grepped for `tempfile`/`NamedTemporaryFile`/`gcs`/`bucket`/explicit `open(...)` writes — none found |
| **Eval cassettes** (`eval/cassettes/*.json`) | Yes, but only eval-fixture text (synthetic or quarantined real reviews already disclosed as part of this project's public eval corpus) — never customer traffic | Committed to git, permanent, intentional | Code: `EVAL_CASSETTE_MODE` is unset in production (confirmed via `gcloud run services describe`'s env-var list, Session 11) — production always runs in `live` mode, cassette recording is never active outside CI/local eval runs |
| **Alert emails via Resend** | **Yes, LLM-derived summary content** (`_format_body` embeds `topics`/`cons`/etc. from `event.details`) | Held by Resend per its own retention policy (not this project's to set) — a real sub-processor data flow, not a bug; already the reason Resend belongs on the sub-processor list (P5c) | Code trace only, not fired live this session |

## Finding 2, in detail — the leak nobody had named

`app/api/v2/extract.py`'s `_run_extraction_v2`, on every successful (non-cached) extraction:

```python
log.info(
    "extraction.completed",
    product=extraction.product,
    model=model_name,
    latency_ms=latency_ms,
    org_id=ctx.org_id,
)
```

`extraction.product` is LLM output, not raw input — but LLM output is not guaranteed to be a
short, generic noun phrase. Both live test calls proved this directly: the sentinel string,
placed inside review text designed to look unusual, was extracted verbatim as the "product name"
and shipped into a structured log line, which Cloud Logging then retains under its default
policy. **This is real, mechanically confirmed, and was not caught by the earlier code-level
`grep` pass** (which searched for `text`/`review`/`body` in log calls, not `product` — the
literal lesson of this finding is that a keyword-scoped grep is exactly the kind of narrower-
than-advertised-surface sweep this engagement's own standing rules warn about; the sentinel scan
against every column, not just predicted ones, is what actually caught it).

`/demo/extract`'s own completion log (`app.api.demo`) does **not** include `product` — confirmed
clean by direct code read and by the sentinel scan finding zero hits for the demo sentinel in
Cloud Run logs. The leak is specific to `app/api/v2/extract.py`'s `extraction.completed` event.

## P1b — what persists TODAY, plainly stated

**`/v2/extract` and `/v2/extract/batch`** (both route through `_run_extraction_v2`): review text
persists unconditionally, forever, in `public.extractions.review_text`, with no retention
control, no purge path, and no opt-out. There is no stateless mode today — every org-scoped
extraction is retained by construction.

**`/v2/ingest/csv`**: review text persists in **two** places — `public.batch_job_rows.text`
(staged at upload time, never cleared) and, once the row is drained and processed,
`public.extractions.review_text` again (via the same `_run_extraction_v2` call the single-extract
path uses). This is P2e's "hardest case," confirmed live: the CSV sentinel appeared in both
tables.

**`/demo/extract`**: persists nothing to Postgres (no org to scope to, by design) — but
contributes to the process-local in-memory cache, and its own log line is clean (no product
field). This endpoint is already, structurally, closer to "stateless" than the org-scoped path,
though it has never been described that way.

## P1d — what must change for "we store nothing by default" to be literally true

Not implemented in this ADR — reported per the brief's explicit instruction. In order of what
blocks the claim hardest:

1. **`save_extraction_pg` must become conditional** on the org's `retention_mode` (P2a/P2b) —
   today it is called unconditionally from `_run_extraction_v2`, which is the single choke point
   for `/v2/extract`, `/v2/extract/batch`, and the CSV-ingest drain path alike (a genuine
   advantage: one fix point, not three).
2. **`extraction.completed`'s log call must drop the `product` field**, or hash/truncate it,
   before stateless mode can be called true — a customer on stateless mode whose review text gets
   summarized into a log line that GCP retains for 30 days is not "storing nothing," regardless
   of what the database says. This is the one item on this list that is a straightforward,
   reversible code change with no schema/product decision attached — worth fixing regardless of
   the rest of P2's timeline.
3. **`batch_job_rows.text` needs its own lifecycle decision**, independent of the `extractions`
   table fix — even a customer using stateless mode for `/v2/extract` would still have their CSV
   rows staged in this table today, since `enqueue_batch_job_rows_pg` has no retention-mode
   awareness at all. P2e must decide (and this ADR recommends, not decides): either purge
   `batch_job_rows.text` immediately after a row reaches `done`/`failed` regardless of retention
   mode (staging is not the same as retention), or declare CSV ingest retained-mode-only, as the
   brief's own P2e framing already anticipates as an acceptable honest fallback.
4. **The parse-error log paths (`llm.parse_error`, `router.unexpected_error`,
   `llm.unexpected_error`) are BELIEVED, not VERIFIED, to be clean** — worth a deliberate
   reproduction (force a malformed-JSON response through the pipeline in a test) before the
   stateless claim ships, rather than leaving this as an untested assumption in a claim this
   consequential.
5. **The demo endpoint's in-memory cache** is not a compliance problem today (process-local,
   never written to disk, already lost on cold start) but should be named explicitly in whatever
   trust-surface documentation ships alongside the stateless claim, so "stateless" is scoped
   honestly (no durable storage) rather than overclaimed (no storage of any kind, anywhere,
   ever).

## Consequences

- P2's implementation must treat `_run_extraction_v2` as the single enforcement point for
  `retention_mode` — both the DB write and the `extraction.completed` log field fix belong there.
- The stateless claim cannot honestly cover CSV ingest without either a `batch_job_rows` purge
  mechanism or an explicit "CSV ingest requires retained mode" carve-out (P2e's own framing).
- This audit is a snapshot at `review-iq-00024-8r9` (2026-09-12) — any future code path that adds
  a new `log.info`/`log.error` call touching an LLM-derived field must be checked against this
  same failure class before shipping, not assumed clean by analogy to this audit.

## Alternatives considered

- **Trust the code-trace pass alone, skip live sentinel verification.** Rejected outright — this
  is the exact failure mode named in the session brief ("wrong about deployed behaviour five
  times by reading code") and the one that found Finding 2, which no code-level grep for
  "text/review/body" would have caught.
- **Scope the live column scan to only the columns the code trace predicted.** Rejected — a
  scoped scan cannot find a surprise, by construction; the unscoped 61-column scan is what
  confirms the code trace's negative claims ("no hit anywhere else") rather than merely
  asserting them.
