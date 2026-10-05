# Multi-brand scope: one agency, ~12 brands

**Target user:** An agency owner or account manager who manages reviews for roughly 12 client brands from one login.
**Pain point:** The product is single-organization. One login sees one flat pool of reviews, with no way to separate, switch between or report on brands, so an agency either mixes brands in one dashboard or runs 12 separate accounts.
**Success metric:** An agency user can switch to any of their brands in one click and see only that brand's urgent queue, health score and concerns; a rollup view shows which brands need attention first. (Target to be set with the first agency customer; none is measured today.)
**Who pays:** The agency, on a plan priced per brand or per workspace (pricing decision is out of scope here).

Status: scoping only. Nothing in this document is built. This is the G3d deliverable of the S17 dashboard work. Statements marked BELIEVED are inferences from reading code and migrations, not verified against the production database or a running deployment.

## 1. What exists today (read, not assumed)

- **Tenant = organization.** `public.organizations` (id, name, slug UNIQUE, plan in free/pro/enterprise) is the only tenant level. `supabase/migrations/20260510000001_create_tables.sql`.
- **Membership is one-to-one per user.** `organization_members (org_id, user_id, role)` has PRIMARY KEY `(org_id, user_id)` in the create-tables migration, but migration `20260801000001_role_separation_bypassrls_remediation.sql` (statement at line 219) adds `CONSTRAINT organization_members_user_id_key UNIQUE (user_id)`. VERIFIED by reading the migration. Its own comment (lines 213-216) says: if multi-org membership is ever a product feature, this constraint must be dropped and every `WHERE user_id = %s` call site re-audited. The constraint is also asserted as a postcondition in that file, so a migration that drops it must update the postcondition.
- **User to org resolution assumes at most one row.** `public.resolve_org_for_user(p_user_id)` (`20260801000002_tenant_resolvers_auth_signup.sql`, lines 36-41) does `SELECT org_id ... WHERE user_id = p_user_id LIMIT 1`; its comment says the unique constraint guarantees at most one row. It is called from `app/auth/session.py` at lines 58 and 152 (the write path and the read path of every session-authenticated BFF call). With two memberships, `LIMIT 1` would pick an arbitrary org silently. VERIFIED by reading; the silent-wrong-answer consequence is BELIEVED (Postgres returns no guaranteed order without ORDER BY).
- **First-login provisioning creates one org per user.** `public.create_org_and_membership(p_user_id, p_name, p_slug)` inserts an organization and an `owner` membership; `app/auth/signup.py` relies on the unique-violation to handle a concurrent-signup race.
- **RLS is keyed on a single org id per transaction.** `public.current_org_id()` (`20260510000002_rls_policies.sql`, line 17) resolves from the JWT or from `app.current_org_id`; policies are `org_id = public.current_org_id()`. The application sets it with `SET LOCAL "app.current_org_id"` in `_set_tenant()` (`app/core/storage_pg.py`, line 72). Every table with an `org_id` column uses this shape.
- **Tables carrying `org_id` (from `supabase/migrations/`, grep of `org_id`):** `api_keys`, `extractions`, `usage_records`, `organization_members`, `authenticity_audits`, `corrections`, `alert_preferences` and `alert_log` (`20260621000001_alerts.sql`), `shopify_installations`, `google_business_installations`, `batch_jobs`, `batch_job_rows`, `extraction_costs`, `quota_requests`. Uniqueness constraints that include `org_id`: `extractions (org_id, input_hash)` (`20260511000004`), `alert_preferences (org_id, event_type)`, `authenticity_audits (org_id, review_hash)`.
- **API keys belong to an org, and quota is per key.** `api_keys.org_id` plus `quota` and `usage`; the dashboard usage line comes from `_get_quota_and_usage(api_key_id, org_id)` in `app/api/bff/router.py` (line 238), which counts `usage_records` for that one key in the current calendar month. The key shown on the dashboard is the one the session resolved to. The per-plan key quota ceiling is `PLAN_QUOTA_LIMITS` keyed on `organizations.plan` (`router.py` near lines 148-170 and 985-991).
- **Plan and billing are on the organization.** `organizations.plan`; retention mode is also per org (`20260912000001_organizations_retention_mode.sql`).
- **The UI has no org concept.** `web/src/components/Layout.tsx` has no switcher; every BFF call resolves the org from the JWT user (`require_session` / `require_session_read`), so the browser never names an org.
- **There is already a free-text `product` on every review** (`list_extractions_pg` in `app/core/storage_pg.py` selects `product`, filtered with `ILIKE`). It is not a brand and is not controlled vocabulary. BELIEVED to be too noisy to use as a brand key without a mapping step.

## 2. Options for the data model

**A. Brands as separate organizations, user belongs to many (reuse the existing tenant level).**
Each brand is an `organizations` row; an agency user is a member of ~12 orgs. Needs: drop `organization_members_user_id_key`; replace `resolve_org_for_user` with something that takes an explicit org id and checks membership; the BFF needs an active-org input (header or path). RLS, quotas, keys, alerts, batch jobs, plan and retention all work unchanged per brand because the tenant boundary is untouched. Costs: the agency has no single owner object (billing is per org, so 12 plans; a rollup needs a cross-org read that RLS is built to prevent); `extractions (org_id, input_hash)` dedupes within a brand only, which is correct.

**B. New level above orgs: account (agency) with brands underneath.**
Add `accounts` (billing owner, plan) and make `organizations` children via `account_id`. Brand = organization. Membership attaches at account level with optional per-brand restriction. Cleanest commercial model (one invoice, one plan, N brands) and enables rollup views by reading across a known list of org ids. Costs: new table, a new RLS function (`current_account_id`), plan/quota/retention move or get an inheritance rule, signup flow changes, every `plan` read site changes.

**C. New level below orgs: brand/workspace inside one org (add `brand_id` to review data).**
Keep one org per agency, add `brands` and a `brand_id` on `extractions`, `batch_jobs`, alert preferences, etc. No membership change, one plan, rollup is a plain GROUP BY. Costs: every table that has per-brand meaning gets a column, every RLS policy and every query needs a second scoping predicate (the system's isolation guarantee is `org_id`-only today; adding a second axis means a brand-level leak is not stopped by RLS unless new policies are written), and `extractions (org_id, input_hash)` uniqueness decides whether the same review text in two brands collides.

BELIEVED recommendation: A is the smallest path that keeps the tested tenant isolation intact; B is the right end state commercially; C is the cheapest in schema count but the weakest on isolation. Choose A now and design for B later.

## 3. Auth

- Drop `organization_members_user_id_key` (migration; update its postcondition in the same file's lineage; add the undo case the repo now requires for every migration, see `tests` for the migration-undo coverage added in S16).
- Replace `resolve_org_for_user(user_id)` with a membership check: `resolve_org_for_user(user_id, requested_org_id)` returning the org only if a membership row exists, and a separate "list my orgs" function for the switcher. The `LIMIT 1` behaviour must go; otherwise a multi-org user gets an arbitrary org (silent wrong answer).
- Both session code paths in `app/auth/session.py` (write path and read path) and `app/auth/signup.py` need the same change. `require_session` / `require_session_read` must read the requested org from the request and fail closed (403) if it is absent for a multi-org user, never fall back to "first row".
- Roles exist (`owner`, `admin`, `member`) but only the membership table carries them; BELIEVED no per-endpoint role checks exist beyond ownership of the resolved org, so per-brand permissions (an account manager sees only 4 brands, a client sees one) would need to be introduced, not just surfaced.
- Supabase Auth user ids are unaffected; the JWT does not carry an org today (BELIEVED from `current_org_id()` resolution order, which prefers the JWT claim or the session setting).

## 4. API

- Scope each BFF call by an explicit org: a path prefix (`/bff/orgs/{org_id}/reviews`) is explicit and cacheable and makes the guard obvious in review; a header (`X-Org-Id`) is a smaller diff to the 16 existing BFF routes (`@router` decorators in `app/api/bff/router.py`). BELIEVED better: header first (additive, old clients keep working for single-org users), path later.
- Public API keys (`riq_live_...`) are already per org, so per-brand keys come free under option A. Under option C a key would need a brand scope or a brand header.
- Quota is per key and per calendar month today (`_get_quota_and_usage`); an agency with 12 brands means 12 quotas. Whether quota is pooled per account is a pricing decision that needs option B.
- Rollup endpoint (new): a read that takes a list of org ids the user is a member of and returns per-brand summary rows (urgent count, health score, trend, last arrival). Under option A this must run as 12 tenant-scoped queries (or a new SECURITY DEFINER function with a membership check) because RLS blocks a single cross-org query by design.

## 5. UI

- Brand switcher in `Layout` (header), persisted per user (localStorage is fine for the first slice, the same per-browser caveat as "last visit" applies); the current brand must be visible on every page, because a wrong-brand reply to a customer is the costly error.
- Per-brand dashboard is the existing page, scoped.
- Rollup view ("what needs me across all brands"): one row per brand with urgent count, health band with direction (reusing `dashboardModel.ts` per brand), last review arrival; sorted by urgency. This is the Monday-morning view for an agency and is likely the most valuable screen.
- "New since last visit" is keyed per user id today (`samidha:lastVisit:<userId>`); it must become per user and per brand.
- Permissions UI (invite a teammate to specific brands) is a separate, later slice.

## 6. Effort and risk, smallest shippable slice first

1. **Slice 1, smallest shippable (option A, read-only switch for owners): 1 migration + auth change + switcher.** Drop the unique constraint; membership-checked resolver with explicit org id; header-based org selection on the BFF; header switcher; an operator-run script (or admin endpoint) to attach an existing agency user to additional orgs. No new tenancy table, no RLS change. Risk: HIGH on the auth path, because this is the code that decides which tenant's data a user sees. Needs the cross-org isolation test pattern already used for RLS (two users, two orgs, assert no leakage, and assert the multi-org user without a header is refused). BELIEVED about 1 to 2 weeks including the isolation tests.
2. **Slice 2: rollup view** (per-brand summary endpoint plus page). Risk: MEDIUM (performance of 12 queries; correctness of per-brand scoping). Pure read.
3. **Slice 3: self-serve brand creation and invitations** (create org under an account, invite members, roles). Risk: MEDIUM-HIGH (provisioning race handling in `signup.py` changes shape).
4. **Slice 4: option B account level** (single invoice, pooled quota, plan inheritance). Risk: HIGH (billing and plan semantics), and the largest schema change. Do only when a paying agency needs a single plan.

Not on the list because it is not needed to ship slice 1: moving `product` to a brand taxonomy, cross-brand analytics, white-labelling.

## 7. Decisions needed before building

- Is the agency the billing customer (option B) or does each brand pay (option A)? This decides slice order.
- Do agency staff need per-brand permissions in the first release, or is "member of an org sees all of it" enough?
- Is quota pooled or per brand?
- Which existing user is the first agency pilot, and are their 12 brands already separate orgs, or one org with mixed data? If mixed, splitting existing `extractions` rows by brand is a data migration with no key to split on today (BELIEVED: `product` is the only candidate, and it is free text).
