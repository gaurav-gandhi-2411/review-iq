> **⚠️ REQUIRES LEGAL REVIEW.** This document was drafted by an AI coding assistant from the
> product's actual technical implementation and standard SaaS legal templates. It is a
> starting draft, not legal advice, and must be reviewed by qualified counsel before being
> relied upon or published to customers.

# Data Retention and Deletion

This document states how long Samidha Reviews keeps data, and describes — accurately, against
the real shipped code — how account deletion actually works. It is referenced from the
[Privacy Policy](./privacy-policy.md) §3 and the [DPA template](./dpa-template.md) §8, and must
stay consistent with both.

---

## A note on the endpoint name

The Wave 1 commercialization spec (`docs/specs/wave1-commercialization.md`) refers to this
capability as `DELETE /v2/data`. **That exact path does not exist in the codebase.** What exists
is a real, already-shipped, functionally equivalent endpoint at a different path:
**`DELETE /account`** (commit `c6ded24`, `app/api/account.py`). This document describes the real
endpoint, not the spec's placeholder name — the spec should be reconciled to reference the
actual path.

---

## The real deletion mechanism (`DELETE /account`)

| | |
|---|---|
| **Path** | `DELETE /account` |
| **Auth** | `Authorization: Bearer <supabase_jwt>` — the same Supabase-session auth used by `GET /account` and `POST /account/regenerate-key`, **not** the `riq_live_*` API key. The JWT is verified via `supabase.auth.get_user(jwt)`; a 401 is returned on any verification failure. |
| **Confirmation mechanism** | Type-to-confirm. The request body must include `confirm_slug`, which must **exactly match** the caller's own organization slug (as returned by `GET /account`). A mismatch returns `400 Bad Request` and deletes nothing. |
| **Scope** | The caller's **own** organization only. `org_id` is resolved server-side from the verified JWT's `user_id` via `organization_members` — there is no code path where a request parameter can target a different organization (verified by `test_cannot_delete_another_orgs_account`). |
| **Synchronous or async?** | **Synchronous.** The FastAPI handler awaits the delete inline (`await asyncio.to_thread(_do_delete_org, ...)`) and returns `204 No Content` once the database transaction has committed. There is no background job, queue, or "deletion pending" state — by the time the caller receives a response, the delete has already happened. |
| **What actually gets deleted** | One `DELETE FROM public.organizations WHERE id = %s` statement, in a single transaction. Every dependent row across the schema is removed via `ON DELETE CASCADE` foreign keys (defined in `supabase/migrations/20260510000001_create_tables.sql` and subsequent migrations) — this covers `extractions`, `usage_records`, `api_keys`, `batch_jobs`, `batch_job_rows`, `corrections`, `authenticity_audits`, alert preferences/log, Shopify/Google installation records, and quota requests. One statement, one transaction: **no partial-delete state is possible** — either the whole organization's data is gone, or (on error) none of it is. |
| **Is it reversible?** | **No**, not through the product. The code comment in `app/api/account.py` explicitly calls this "Permanently delete... Irreversible." There is no soft-delete, no undo window, and no "restore my account" self-service path. (See "Residual copies in backups" below for the one place a copy can still exist, outside the product, for a bounded time.) |
| **Race handling** | If the organization has already been deleted (e.g. a duplicate/racing request), the DELETE is a no-op (`rowcount == 0`), logged as `account.delete_no_op_already_gone` — not surfaced as an error to the caller. |
| **Audit logging** | The deletion request and completion are both logged at `WARNING` level (`account.delete_requested`, `account.deleted`) with `org_id`, `slug`, and `user_id` — these structured log lines themselves persist per the "Usage/API logs" retention below. |

---

## Retention schedule

| Data category | While account is active | After `DELETE /account` |
|---|---|---|
| Review text, extraction output, usage records, corrections, account/key metadata | Retained indefinitely — no automatic expiry today. | Deleted immediately and permanently from the primary database (see mechanism above). |
| Authenticity audit records (`authenticity_audits`) | Retained indefinitely; stores a SHA-256 hash of review text, not the plaintext (`SECURITY.md` §10). | Deleted immediately, same cascade. |
| **Encrypted nightly database backups** (GitHub Actions `db-backup` workflow — `ops/runbooks/db-restore.md`) | N/A — full-database dumps, taken nightly. | **Not** purged retroactively. A backup taken before the deletion request will still contain the deleted organization's data until that specific backup artifact ages out. GitHub Actions artifact retention is a **hard 90-day limit** on the current (free-tier) plan — this is the real ceiling, not a policy choice, and it is a genuine disclosure: deleted data can persist in an encrypted backup for **up to 90 days** after deletion. |
| Usage/API structured logs (Cloud Run / Google Cloud Logging) | Retained per Google Cloud Logging's platform default. `[TODO: no custom log-sink or retention override was found in this codebase's ops docs — confirm whether the project relies on Cloud Logging's default `_Default` bucket retention (commonly 30 days) or whether this should be explicitly configured and documented. Not verified against live GCP project configuration in this session.]` | Not explicitly purged by the `DELETE /account` flow — log lines referencing the deleted `org_id`/`user_id` follow the same platform-default log retention as any other log line, independent of the database deletion. |

`[TODO: GG-DECISION / REQUIRES-LEGAL-REVIEW — the numbers above are current infrastructure
defaults, not numbers deliberately chosen as a retention *policy*. In particular: (1) whether
90-day backup residency after a deletion request is acceptable under DPDP Act 2023 / GDPR's
"without undue delay" deletion expectations is a legal call informed by GG's risk tolerance —
options include shortening backup retention, or documenting backup residency as an accepted,
disclosed exception (the common industry pattern); (2) the exact Cloud Logging retention should
be confirmed against the live GCP project rather than assumed from platform defaults.]`

---

## Data retrieval before deletion

A customer who wants their data before deleting their account can retrieve the full structured
dataset via `GET /v2/dataset/export?format=jsonl` (see `docs/data-ownership.md`) — this is not
automatically offered as a "download before you delete" step in the current `DELETE /account`
flow, and there is no reminder/confirmation UI step forcing an export first.
`[TODO: GG-DECISION — consider whether the account-deletion UX (once a dashboard exists in Wave
2) should prompt an export first; this is a product decision, not documented as a commitment
here.]`

---

_This document is version-controlled; see git history for changes. Last drafted: 2026-07-31,
against `app/api/account.py` as of commit `c6ded24` and its current state on this branch._
