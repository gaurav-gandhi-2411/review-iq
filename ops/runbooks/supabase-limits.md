# Runbook: Supabase Free Plan Limits

**Project:** review-iq  
**Supabase ref:** enqpluazgxewepchdeut  
**Region:** ap-south-1 (Mumbai)  
**Plan:** Free

---

## Hard limits (Free tier)

| Resource | Limit | What happens at limit |
|---|---|---|
| Database size | 500 MB | New writes rejected (INSERT fails with `53100 disk_full`) |
| Egress bandwidth | 5 GB / month | Additional egress blocked; API returns 403 |
| Monthly Active Users (PostgREST auth) | 50 000 MAU | New sign-ins blocked |
| Edge Function invocations | 500 000 / month | Functions return 429 |
| Realtime concurrent connections | 200 | New connections rejected |
| Storage | 1 GB | Uploads rejected |
| Auto-pause | 7 days inactivity | Project suspended (see pause-recovery runbook) |

> Phase 1 of review-iq uses **direct psycopg2 + service_role** (no PostgREST auth, no MAU). MAU limit is not currently relevant.

---

## Current usage check

### Database size
```sql
-- Run in Supabase SQL editor or via psycopg2
SELECT
  pg_size_pretty(pg_database_size('postgres')) AS db_size,
  pg_size_pretty(pg_total_relation_size('public.extractions')) AS extractions_size;
```

### Row counts
```sql
SELECT
  (SELECT COUNT(*) FROM public.organizations)    AS orgs,
  (SELECT COUNT(*) FROM public.extractions)      AS extractions,
  (SELECT COUNT(*) FROM public.usage_records)    AS usage_records,
  (SELECT COUNT(*) FROM public.api_keys)         AS api_keys;
```

### Via Supabase dashboard
- **Database → Database size:** https://supabase.com/dashboard/project/enqpluazgxewepchdeut/database/usage
- **Monthly egress:** https://supabase.com/dashboard/project/enqpluazgxewepchdeut/settings/billing

---

## Egress estimation

Each extraction response is ~500 B–2 KB JSON. At 5 GB/month limit:
- **5 GB / 1 KB** ≈ 5 million extraction reads per month before egress cap
- Realistically not a concern until traffic is large; re-evaluate at Pro upgrade

---

## Upgrade path

Upgrade to **Pro ($25/mo)** when any of the following apply:
- DB approaching 400 MB (80% of free limit)
- Monthly egress > 4 GB
- Need to eliminate auto-pause behavior
- Need point-in-time recovery (PITR)

Upgrade via: https://supabase.com/dashboard/project/enqpluazgxewepchdeut/settings/billing

---

## Alert strategy (Phase 2)

Phase 2 cost dashboard (plan.md §2) will surface Supabase usage trends. Until then, check the dashboard manually when running load tests or before demos.
