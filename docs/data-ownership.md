# Data Ownership, Isolation, and the Consent Path

## Per-org isolation

Every piece of data in review-iq is owned by the org that produced it. Isolation is enforced
at two independent layers:

1. **Application layer** — every query includes `WHERE org_id = %s` with the authenticated
   org's UUID.
2. **Database layer (Row-Level Security)** — Postgres RLS policies on all tenant tables enforce
   `USING (org_id = current_org_id())` for SELECT and `WITH CHECK (org_id = current_org_id())`
   for INSERT/UPDATE. The `WITH CHECK` clause is mandatory on every table: without it, a tenant
   could INSERT a row tagged with another org's `org_id` while the `USING` clause still blocks
   their reads — closing the INSERT bypass gap explicitly on each table.
3. **Anon-deny** — a separate policy `FOR ALL TO anon USING (false)` blocks all unauthenticated
   access to every tenant table, regardless of any other policy.

Tables covered: `extractions`, `authenticity_audits`, `corrections`, `usage_records`,
`api_keys`, `organizations`, `organization_members`, `batch_jobs`.

The application connects via `service_role` (which bypasses RLS) but sets
`SET LOCAL ROLE authenticated` and `SET LOCAL "app.current_org_id" = <org_id>` inside every
transaction before issuing data queries. This means RLS acts as a defence-in-depth even if an
application bug drops a `WHERE org_id` clause.

Cross-tenant isolation is proven via a rollback-wrapped test in
`tests/integration/test_rls_isolation.py` and by the corrections-specific proof run during
migration verification:
- Org A's authenticated context sees only Org A's rows (SELECT).
- Org A's context with `WHERE org_id = org_B` returns 0 rows (RLS `USING` filters).
- Org A's context INSERTing a row tagged `org_id = org_B` raises `InsufficientPrivilege`
  (RLS `WITH CHECK` blocks).
- Anon context returns 0 rows on all tables.

## The seller's data asset

Each seller's data is composed of:

| Component | Table | Link key |
|---|---|---|
| Raw review text | `extractions.review_text` | `review_id` |
| Structured extraction | `extractions` (flat columns) | `review_id` |
| Authenticity audit | `authenticity_audits` | `review_id` |
| User corrections | `corrections` | `review_id` |

`review_id` is the canonical stable identity for a review — the SHA-256 hex of the review text,
no prefix. It is a generated column on both `extractions` and `authenticity_audits`, computed
from the existing hash columns so all historical records are covered without a backfill sweep.

A seller can retrieve their full structured asset at any time:
- `GET /v2/dataset` — paginated per-review records linking extraction + authenticity + corrections.
- `GET /v2/dataset/export?format=jsonl` — full JSONL export, one record per line, scoped to the
  authenticated org. Format is stable and self-describing; designed as the substrate for future
  model training should the seller opt in.

## Corrections discipline

User corrections feed a human-reviewed pipeline — they are never silently trusted or auto-applied
to the model, prompts, or the gold eval set:

1. A correction is submitted via `POST /v2/corrections` and stored in the `corrections` table,
   scoped to the submitting org.
2. `field_path` is validated against an explicit allow-list (`ALLOWED_FIELD_PATHS` in
   `app/core/corrections/schema.py`) — no arbitrary field writes are accepted.
3. `eval/flywheel/corrections_to_fixtures.py` transforms accepted corrections into CANDIDATE
   fixtures and writes them to `eval/flywheel/candidates/`. The script enforces a hard guard:
   it raises `ValueError` if the output directory resolves to `eval/fixtures/` (the gold set).
4. A human reviewer must inspect, validate, and manually copy an approved candidate into
   `eval/fixtures/` before it enters the gold eval set or is used as a few-shot example. This
   step has no automated path.

## Cross-tenant data use — consent path (not implemented; design only)

**Default: strict per-org isolation.** No seller's data is used in any aggregate corpus,
shared model, or cross-tenant analysis without explicit opt-in.

**If cross-tenant pooling is ever introduced**, the following consent steps are required:

1. **Explicit opt-in per org** — a seller must affirmatively consent (not opt-out). Consent is
   stored as a timestamped record (`org_id`, `consent_version`, `consented_at`, `scope`).
2. **Scope declaration** — consent must name the specific use: e.g., "aggregate training corpus
   for vernacular model fine-tuning", not a blanket permission.
3. **Revocability** — a seller can revoke consent at any time; revocation must be honoured before
   the next training run. Their data must be excluded from any corpus built after revocation.
4. **Audit trail** — a log of which orgs' data entered which corpus run, so revocations can be
   back-applied if technically feasible.
5. **Separate schema path** — cross-tenant pooling must be built as a distinct, explicitly-gated
   code path, not by relaxing existing RLS policies. The default `USING (org_id = current_org_id())`
   policies must remain unchanged.

**Nothing in the current codebase implements cross-tenant pooling.** This section documents the
required consent design for future reference; any implementation requires explicit sign-off.
