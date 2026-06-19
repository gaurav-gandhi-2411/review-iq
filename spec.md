# Project Spec: review-iq — Data Asset + Correction Flywheel (the moat foundation)

## Goal
Turn review-iq from a stateless processor into a system that builds a durable, structured,
per-seller DATA ASSET out of reviews, and captures every user CORRECTION as labeled data that
compounds into the product's only real moat (better evals, better prompts, and a future fine-tuned
India-vernacular model nobody else can replicate). This is the foundation every other feature reads
from. The pitch to a non-technical seller: "We turn your unstructured review pile into a structured
intelligence asset YOU own — isolated, exportable, and yours — and it gets smarter every time you
correct it." Vertical-agnostic (apparel, electronics, grocery, supplements — any seller without an
ML team). Free tier, $0; this build is largely LLM-free (capture + DB + export), so quota-immune.

## Core principles (non-negotiable)
- **The seller's data is the seller's.** Every record and correction is org-isolated (RLS). A
  seller can export their full asset. This is both an integrity rule and a selling point.
- **Corrections are candidates, never auto-truth.** A user correction feeds a HUMAN-REVIEWED
  pipeline into eval fixtures / few-shot examples — it is never silently trusted or auto-applied
  to the model. (Same discipline that's kept this project's evals honest.)
- **No cross-tenant data use without explicit consent.** Aggregating sellers' data into a shared
  training corpus is a SEPARATE, consent-gated, opt-in decision — NOT built in this phase. Default
  is strict per-org isolation. Document the consent path; do not implement cross-tenant pooling.

## Current state (existing project — do not break)
- `main` at v0.6.3 (prod live: extraction, authenticity, insights, demo UI). Multi-tenant Postgres
  + RLS. Free tier.
- `extractions` table stores per-review output_json already (the asset partly exists).
  `authenticity_audits` stores authenticity results. **Known gap:** the two use different hash
  schemes and can't be joined today (per-SKU authenticity was deferred for this reason) — this
  phase is where review identity gets reconciled.
- FROZEN contracts: `/v1`, `/v2/extract`, `/v2/reviews`, `/v2/ingest`, `/v2/authenticity`,
  `/v2/insights/*`, `ReviewExtraction`, `riq_live_` format, RLS shape.
- Standing discipline: evals fail loud; no release without verified run; no stranded tags; no
  subagent DDL/prod writes; Groq-only on org path; $0.

## Scope

### In scope
- **Canonical review identity.** Introduce a stable `review_id` (or reconcile the hash scheme) so
  extraction + authenticity + (future) reply + corrections all link to the SAME review. This is the
  foundation of the asset and also unblocks the deferred per-SKU authenticity join. Migration ->
  ESCALATE (assess whether reconciling forward-only on new records is enough vs. backfilling).
- **Corrections capture.** New `corrections` table (org-scoped, RLS WITH CHECK + anon-deny,
  matching the established pattern). Fields: `id, org_id, review_id, source_type
  (extraction|authenticity|reply), field_path, original_value, corrected_value, correction_note,
  language, corrected_at`. Migration -> ESCALATE.
  - `POST /v2/corrections` — submit a correction; tenant-scoped; usage-recorded; field_path
    VALIDATED against an allowed set per source_type (no arbitrary writes).
  - `GET /v2/corrections` — list/review the org's corrections.
- **The data-asset read model.** `GET /v2/dataset` — returns the org's structured review records
  (raw text reference + extraction + authenticity + corrections, linked by review_id),
  paginated, tenant-scoped. This is "your reviews as structured data."
- **Dataset export.** `GET /v2/dataset/export?format=jsonl` — exports the org's structured records
  + corrections as a labeled JSONL dataset (the seller's owned asset; also the substrate for future
  training). Org-scoped only.
- **The flywheel pipeline (the moat mechanism).** `eval/flywheel/corrections_to_fixtures.py` —
  transforms accepted corrections into CANDIDATE eval fixtures / few-shot examples, output to a
  review queue for HUMAN sign-off before anything enters the gold eval set or prompt examples.
  Never auto-applied. (Fine-tuning corpus assembly is designed-for in the export format but
  fine-tuning itself is out of scope.)
- **Metrics:** corrections submitted by source_type, dataset records per org, candidate-fixtures
  generated.

### Out of scope (do not build)
- Actual model fine-tuning / training (future — needs corpus accumulation + compute; $).
- Cross-tenant data pooling / shared training corpus (consent-gated; design the consent path only).
- Natural-language "ask anything" query UI (insights already give structured analysis; NL-query
  is a later phase).
- The no-code seller UI (separate build).
- Managed ingestion connectors (the NEXT build after this).
- Auto-applying corrections to prompts/models without human review.
- Any change to frozen contracts, RLS shape, or key format. Any paid service.

## Tech stack
- Existing only. This build is mostly DB + validation + export — minimal/no LLM, so quota-immune.
  No pandas (stdlib json/csv). No new deps.

## Architecture
```
app/core/corrections/
  schema.py        # NEW: Correction model, source_type + allowed field_path sets, validation (pure)
  service.py       # NEW: submit/list, validation, review_id linking
app/core/dataset/
  builder.py       # NEW: assemble per-review structured records (extraction+authenticity+corrections)
app/api/v2/
  corrections.py   # NEW: POST/GET /v2/corrections
  dataset.py       # NEW: GET /v2/dataset, GET /v2/dataset/export?format=jsonl
eval/flywheel/
  corrections_to_fixtures.py  # NEW: corrections -> CANDIDATE fixtures (human-gated, never auto)
supabase/migrations/
  <new>_review_id.sql         # NEW (ESCALATE): canonical review identity / hash reconciliation
  <new>_corrections.sql       # NEW (ESCALATE): corrections table + RLS (WITH CHECK + anon-deny)
docs/
  data-ownership.md           # NEW: per-org isolation, export, consent path for any future shared use
```

## Data model (migrations — escalation-gated)
```sql
-- canonical identity so all per-review artifacts join
review_id  -- stable id on extractions (+ backfill or forward-only — assess), reused by
            -- authenticity_audits, corrections, future replies

corrections (
  id, org_id, review_id, source_type, field_path,
  original_value TEXT, corrected_value TEXT, correction_note TEXT,
  language TEXT, corrected_at TIMESTAMPTZ
)
INDEX (org_id, corrected_at DESC), INDEX (org_id, review_id)
RLS: ENABLE; authenticated USING/WITH CHECK (org_id = current_org_id()); anon USING(false).
```

## Verification commands
```yaml
- name: tests
  cmd: uv run pytest -v
  required: true
- name: integration
  cmd: uv run pytest -v -m integration
  required: true
- name: lint
  cmd: uv run ruff check .
  required: true
- name: format
  cmd: uv run ruff format --check .
  required: true
- name: types
  cmd: uv run mypy app
  required: true
```

## Decision authority (autonomous mode per CHARTER.md)
Orchestrator builds and reports autonomously. ESCALATE only:
- The two migrations (review_id reconciliation, corrections table) — show DDL before applying;
  no subagent applies it (standing rule).
- The release/tag and any prod deploy.
- The eval/ground-truth sign-off: folding any correction-derived candidate into the gold eval set
  or prompt few-shots is HUMAN-approved, never automatic.
- The cross-tenant consent design (document only; do not implement pooling without explicit sign-off).
- Any frozen-contract change.

## Hard rules
- Per-org isolation absolute: a cross-tenant test must prove org A cannot read org B's corrections
  OR dataset OR export. WITH CHECK + anon-deny on the new table.
- Corrections are candidates only — never auto-applied to model/prompt/gold-set.
- No cross-tenant data pooling in this phase. Default = strict isolation.
- field_path on corrections validated against an allowed set — no arbitrary writes.
- Frozen contracts untouched. $0 / free tier. Full suite after each pass; escalate on any
  existing-test failure. No subagent DDL/prod writes.

## Budget
- Soft target: 2 CC sessions. Hard cap: escalate after 20 executor invocations. `/cost` at midpoint.

## Success criteria (verify ALL before declaring done)
- [ ] Canonical `review_id` links extraction + authenticity (+ corrections); the previously-deferred
      extraction<->authenticity join now works (or the remaining gap is documented honestly).
- [ ] `corrections` table created (post-escalation) with RLS; cross-tenant isolation PROVEN (read +
      insert blocked across orgs, anon denied) with rollback-wrapped proof.
- [ ] `POST /v2/corrections` validates field_path against the allowed set, is tenant-scoped, records
      usage; `GET /v2/corrections` returns only the org's corrections.
- [ ] `GET /v2/dataset` returns the org's structured review records (linked artifacts), tenant-scoped.
- [ ] `GET /v2/dataset/export?format=jsonl` exports the org's labeled dataset; org-scoped; valid JSONL
      on REAL data.
- [ ] `corrections_to_fixtures.py` produces CANDIDATE fixtures to a review queue; nothing auto-enters
      the gold set; a test asserts no auto-application.
- [ ] `docs/data-ownership.md`: per-org isolation, seller export, and the consent-gated path for ANY
      future shared/aggregate use. No cross-tenant pooling implemented.
- [ ] Frozen contracts intact; full suite green; $0. Next clean version tagged (escalated; after the
      stale v0.7.0 tag is removed — reserve v0.7.0 for completed P2 reply-drafting; pick the next
      honest linear number).

## Build order
1. `corrections/schema.py` (Correction model + allowed field_path sets + validation) as pure
   functions with boundary unit tests.
2. Review-identity: assess current hashes; design canonical `review_id`; ESCALATE the migration
   (with DDL shown). On approval, apply + verify extraction<->authenticity linkage.
3. `corrections` table migration (ESCALATE, DDL shown) + RLS + rollback-wrapped isolation proof.
4. `corrections/service.py` + `POST`/`GET /v2/corrections` (validated, tenant-scoped, usage-recorded).
5. `dataset/builder.py` + `GET /v2/dataset` + `GET /v2/dataset/export` (org-scoped, JSONL).
6. `eval/flywheel/corrections_to_fixtures.py` — corrections -> candidate fixtures, human-gated queue.
7. `docs/data-ownership.md` (isolation + export + consent path). Metrics. Full verify. Escalate tag.
