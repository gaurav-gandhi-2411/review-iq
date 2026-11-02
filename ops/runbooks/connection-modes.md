# Runbook: Supabase Connection Modes

**Project:** review-iq  
**Supabase ref:** enqpluazgxewepchdeut

---

## Two connection modes — when to use each

| Mode | Port | Env var | Use for |
|---|---|---|---|
| **Pooler — transaction** | 6543 | `SUPABASE_DATABASE_URL` | App traffic (auth middleware, all v2 endpoints) — **not yet active** |
| **Direct** | 5432 | `SUPABASE_DIRECT_URL` | Migrations, integration tests with session GUCs; also app traffic until pooler is enabled |

**Pooler status (as of 2026-05-11):** Not yet provisioned. Port 6543 returns "tenant/user not found".  
**Action required before Cloud Run deploy:** Enable via Supabase Dashboard → Database → Connection Pooling, then flip `_db_connect()` in `app/auth/api_key.py` to use `supabase_database_url`.

With `max-instances=2` Cloud Run, direct connections are safe (≤ 4 concurrent, within 60-connection free-tier limit). Pooler becomes critical only when scaling past `max-instances=5`.

Never swap them incorrectly: direct in multi-instance app traffic exhausts connections; pooler for session-scoped GUCs silently loses state mid-connection.

---

## Pooler — transaction mode (port 6543)

**Host:** `aws-0-ap-south-1.pooler.supabase.com`  
**User:** `postgres.<project_ref>` (i.e. `postgres.enqpluazgxewepchdeut`)  
**Connection string:** `SUPABASE_DATABASE_URL` in `.env`

**Properties:**
- Each transaction borrows a backend connection from the pool; released at COMMIT/ROLLBACK
- Free tier limit: up to 60 pooled client connections sharing ~15 Postgres backends
- `SET LOCAL` (transaction-scoped GUCs) works correctly — committed/rolled-back with the transaction
- `PREPARE`/`DEALLOCATE` not supported — do not use prepared statements over the pooler
- Session-level `SET` (without LOCAL) is unsafe — state can leak to the next tenant's transaction

**Used by:**
- `app/auth/api_key.py` — key lookup, quota check, usage record insert
- All future v2 endpoint DB calls

**Client-side pool (S23):** app code reuses connections via `app/core/db_pool.py` instead of
opening one per call. Per instance: `DB_POOL_MAXCONN` (default 5), `DB_POOL_WAIT_SECONDS`
(default 5; then HTTP 503 + `Retry-After`). Cloud Run `maxScale=3` x 5 = 15 pooled-client
connections worst case (limit above: ~60), plus a second revision's instances during a deploy.
Watch for `db_pool.exhausted` / `db_pool.leaked_connection` warnings. There is no off switch
(`DB_POOL_MAXCONN=1` still reuses the connection); rollback = revert the PR.

---

## Direct — session mode (port 5432)

**Host:** `db.enqpluazgxewepchdeut.supabase.co`  
**User:** `postgres`  
**Connection string:** `SUPABASE_DIRECT_URL` in `.env`

**Properties:**
- Dedicated Postgres backend per connection — full session state available
- Supports `SET LOCAL ROLE authenticated` + `SET LOCAL "app.current_org_id"` for RLS simulation
- Supports prepared statements, `LISTEN/NOTIFY`, `COPY`
- Hard limit: 60 total direct connections on free tier (including Supabase dashboard + pooler backends)
- **Never use from app code** — Cloud Run can spawn 10 instances × 80 concurrency = 800 potential connections

**Used by:**
- `supabase/push.py` — migration runner (`ALTER TABLE`, `CREATE POLICY`, etc.)
- `tests/integration/test_rls_isolation.py` — requires `SET LOCAL ROLE` per transaction
- `tests/integration/test_auth_concurrency.py` — fixture setup/teardown only; actual test uses pooler

---

## Session mode pooler (port 6543 with session affinity)

Not currently configured. Would be needed for:
- Long-lived background workers that use `LISTEN/NOTIFY`
- Connections that require prepared statements

If needed: set `pool_mode=session` in the Supabase dashboard under Database → Connection Pooling.

---

## Verifying connection type

```powershell
# Check what port a psycopg2 connection is using
uv run python -c "
import psycopg2, os
from dotenv import load_dotenv; load_dotenv()

conn = psycopg2.connect(os.environ['SUPABASE_DATABASE_URL'])
cur = conn.cursor()
cur.execute('SELECT inet_server_port()')
print('Pooler port:', cur.fetchone()[0])
conn.close()

conn2 = psycopg2.connect(os.environ['SUPABASE_DIRECT_URL'])
cur2 = conn2.cursor()
cur2.execute('SELECT inet_server_port()')
print('Direct port:', cur2.fetchone()[0])
conn2.close()
"
```

Expected output:
```
Pooler port: 6543
Direct port: 5432
```
