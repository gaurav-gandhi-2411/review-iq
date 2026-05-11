# Runbook: Supabase Pause & Recovery

**Project:** review-iq  
**Supabase ref:** enqpluazgxewepchdeut  
**Region:** ap-south-1 (Mumbai)  
**Plan:** Free

---

## When does Supabase pause?

Free-plan projects pause automatically after **7 consecutive days of inactivity** (no API/DB traffic). Data is preserved; only compute is suspended.

---

## Symptoms of a paused project

- All API requests return `503 Service Unavailable` (PostgREST is down)
- `SUPABASE_URL/health` returns non-2xx or times out
- Direct psycopg2 connection to `db.enqpluazgxewepchdeut.supabase.co:5432` times out

---

## Recovery procedure

1. **Open the dashboard** — https://supabase.com/dashboard/project/enqpluazgxewepchdeut

2. **Click "Restore project"** — a banner appears at the top of any paused project page. Click it and wait.

3. **Wait for provisioning** — typically 30–60 seconds. Refresh until the dashboard shows "Healthy".

4. **Verify recovery:**
   ```powershell
   # Should return {"status":"ok"} or similar
   Invoke-RestMethod -Uri "https://enqpluazgxewepchdeut.supabase.co/rest/v1/" `
     -Headers @{ "apikey" = $env:SUPABASE_ANON_KEY }
   ```

5. **Verify DB connectivity:**
   ```powershell
   uv run python -c "
   import psycopg2, os
   from dotenv import load_dotenv; load_dotenv()
   conn = psycopg2.connect(
       host='db.enqpluazgxewepchdeut.supabase.co',
       port=5432, dbname='postgres', user='postgres',
       password=os.environ['SUPABASE_DB_PASSWORD'], sslmode='require'
   )
   print('OK — connected')
   conn.close()
   "
   ```

---

## Cold-start latency after restore

After manual restore, the first PostgREST request may take **5–10 seconds** as the VM warms up. Subsequent requests are fast. This is expected on the Free plan.

---

## Preventing pause

Keep at least one request hitting the project every 6 days. Options:
- A lightweight health-check cron (e.g., GitHub Actions scheduled workflow hitting `/health`)
- Regular use of the app in development
- Upgrade to Pro plan ($25/mo) — Pro projects are never auto-paused

---

## Data integrity

Data is **never lost** during a pause or restore cycle. Supabase snapshots the Postgres volume before pausing; restore simply re-provisions compute against the same volume.
