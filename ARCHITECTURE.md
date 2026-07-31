# Architecture

## Storage split: v1 (SQLite) vs v2 (Postgres)

| Layer | v1 | v2 |
|---|---|---|
| Storage | `app/core/storage.py` (aiosqlite) | `app/core/storage_pg.py` (psycopg2) |
| Auth | `app/core/auth.py` (static API key from `.env`) | `app/auth/api_key.py` (argon2id-hashed keys in Postgres) |
| Endpoints | `/extract`, `/reviews`, `/insights` | `/v2/extract`, `/v2/extract/batch`, `/v2/reviews`, `/v2/insights` |
| Deployment | HF Spaces (SQLite on persistent volume) | Cloud Run (ephemeral filesystem — Postgres required) |

v1 endpoints remain untouched to avoid regressions. v2 is the production multi-tenant path.

## Production ingress topology

Three public hostnames all ultimately reach the same `review-iq` Cloud Run service
(`asia-south1`), whose own IAM policy is `allUsers: roles/run.invoker` (fully public) — none of
the three adds or removes authorization; a route-level gate (e.g. `/admin/*`) must be verified
through all three to be considered actually enforced. See ADR 0009 for the full trace.

| Hostname | Mechanism | Terminates TLS? | Notes |
|---|---|---|---|
| `api.samidhareviews.xyz` | **Firebase Hosting** rewrite (`glob: "**"` → Cloud Run `review-iq`) | Yes, Firebase-managed cert | **Not** a native `gcloud run domain-mappings` mapping — a different, correctly-working mechanism this repo previously assumed didn't exist. Live since 2026-07-07. Never caches (dynamic `run` rewrites are excluded from Firebase's CDN cache). |
| `review-iq-<hash>.a.run.app` | Cloud Run's own default URL | Yes, Google-managed | The raw backend host; always reachable regardless of any DNS/CDN layer above it. |
| `app.samidhareviews.xyz` | Vercel (separate project, `web/` frontend) | Yes, Vercel-managed | Reaches the SPA in `web/`, which calls `api.samidhareviews.xyz` — not the same backend process as the two rows above. |

`samidhareviews.xyz` (the bare apex, marketing/demo) and `app.samidhareviews.xyz` are on
**Vercel**, a separate account (`gg5678g@gmail.com`) from the one running Cloud Run/Firebase
(`gaurav.gandhi2411@gmail.com`) — see ADR 0009's operational-risk note.

## Connection modes

Two connection strings serve different purposes:

- **`SUPABASE_DATABASE_URL`** (port 6543, PgBouncer transaction mode): all application traffic — auth middleware, v2 extract, admin endpoints. `SET LOCAL` (transaction-scoped GUCs) works correctly in transaction mode.
- **`SUPABASE_DIRECT_URL`** (port 5432, direct Postgres): migrations (`supabase db push`) and integration tests that require session-level GUCs or DDL.

Never use the direct URL in application code.

## Tenant isolation

Defense-in-depth: two independent layers guard tenant data.

1. **App-level scoping**: every query in `storage_pg.py` includes `WHERE org_id = %s` explicitly.
2. **Postgres RLS**: each transaction opens with:
   ```sql
   SET LOCAL ROLE authenticated;
   SET LOCAL "app.current_org_id" = '<org_id>';
   ```
   RLS policies on `extractions` check `current_setting('app.current_org_id')`. A bug that drops the WHERE clause cannot read another tenant's rows.

## argon2id under lock — scaling ceiling

`_lookup_and_record` holds a `SELECT FOR UPDATE` row lock on the `api_keys` row for the full duration of argon2id verification (~100 ms CPU). This serializes concurrent requests that share the same API key.

**Consequence**: throughput ceiling per key ≈ 10 req/s at 100 ms/verify. Acceptable for Phase 2 (B2B SaaS, low per-key concurrency). At Phase 4 scale:

- Option A: move argon2 verify before the lock (prefix lookup → verify → re-lock for quota; accept 2-query overhead).
- Option B: cache verified keys with short TTL (Redis/in-memory) — trades security margin for throughput.
- Option C: switch to a cheaper HMAC-based scheme for high-throughput tenants.

Revisit when any single key exceeds ~5 concurrent extractions.

## asyncio thread pool — Cloud Run concurrency ceiling

argon2 verification is CPU-bound (~50–100 ms per call). On Cloud Run with `--cpu=1`, the default asyncio thread pool has 5 workers (`min(32, os.cpu_count() + 4)`). Concurrent `/v2` requests above 5 will queue at the auth step waiting for a thread-pool slot.

This is acceptable at free-tier traffic levels. If throughput needs to scale beyond this, options:

- **(a)** Move argon2 verify outside the row lock via re-fetch pattern (prefix lookup → verify → re-lock for quota check) — reduces lock hold time and allows thread pool to overlap verification across requests.
- **(b)** Raise Cloud Run CPU to 2 — doubles thread pool capacity and reduces per-call latency under load.
- **(c)** Lower `--concurrency` to match thread pool size — reduces queuing but limits per-instance throughput.

This pairs with the "argon2id under lock" ceiling above: both limits apply concurrently; whichever is hit first determines observed throughput.

## Admin authentication

`/admin/*` endpoints use HTTP Basic auth. The password is stored as an argon2id hash in `ADMIN_PASSWORD_HASH`. Username comparison uses `secrets.compare_digest` (timing-safe). argon2 verify is always called regardless of username match to prevent username enumeration via timing side-channel.

## API key lifecycle

```
generate_api_key()
  → (raw_key, key_prefix, key_hash)
  → INSERT api_keys (key_prefix, key_hash, quota)
  → raw_key returned to caller once (never stored)

_lookup_and_record(raw_key)
  → SELECT FOR UPDATE WHERE key_prefix = ? AND revoked_at IS NULL
  → _PH.verify(key_hash, raw_key)
  → COUNT usage_records this month
  → if count >= quota → 429
  → INSERT usage_records (tokens_in=0, tokens_out=0) RETURNING id
  → COMMIT → return ApiKeyContext(usage_record_id=...)

_run_extraction_v2(request, ctx)
  → extract_with_llm() → (output, model, latency_ms, tokens_in, tokens_out)
  → save_extraction_pg()
  → update_usage_tokens(ctx.usage_record_id, tokens_in, tokens_out)
     writes tokens_in + tokens_out; tokens_used is a generated column (sum)

rotate: SELECT FOR UPDATE old + UPDATE revoked_at + INSERT new — single transaction
revoke: UPDATE SET revoked_at = now() WHERE revoked_at IS NULL — idempotent 404 on second call
```

### LLM failure — quota slot consumed

`_lookup_and_record` commits the `usage_records` INSERT before the LLM is called. If the LLM then fails:

- The usage_record row exists with `tokens_in=0, tokens_out=0, tokens_used=0`
- The monthly COUNT includes this row, so the quota slot is consumed
- `update_usage_tokens` is never called (it's only reached on success in `_run_extraction_v2`)
- The client receives a 503 and no extraction result

**Rationale:** The client consumed API infrastructure (auth, DB write, quota check). Refunding the slot on LLM failure would require a compensating transaction and complicates the quota model. At Phase 2 scale, LLM failures are rare; revisit if error rates climb.
