# 30-minute new-user hand test (Samidha Reviews)

Owner: GG. Goal: act as a brand-new user once, end to end, and write down what broke.
Derived from code at the commit this file was added in (web/src/App.tsx, web/src/lib/api.ts,
app/api/bff/router.py, app/api/v2/extract.py, app/core/csv_ingest.py, app/auth/api_key.py,
docs/pricing.json). Anything not provable from the repo is marked UNVERIFIED.

URLs: web app https://app.samidhareviews.xyz, API https://api.samidhareviews.xyz, marketing
https://samidhareviews.xyz (all three from README.md; web/vercel.json only rewrites to
`/index.html`, so the app is a SPA). A read-only `GET /health` on the API returned
`{"status":"ok","db":"ok",...}` HTTP 200 when this was written; both web hosts returned 200.

## Hard limits for this test (shared Groq quota)

- Use a CSV of AT MOST 10 reviews. Make exactly ONE `/v2/extract` call with the API key.
- Do NOT try to provoke 429 / quota exceeded. Do NOT use the dashboard empty-state sample-data button or `/upload?sample=1`
  (it uploads public/sample-reviews.csv through the real pipeline; row count not checked).
- Do NOT use the public `/try` demo more than once (it has its own rate limit, 429 in api.ts).

## Pre-flight (5 min)

1. Email: use a fresh alias you control, e.g. `you+handtest-YYYYMMDD@gmail.com`. A plus alias
   gives a new Supabase user and a new org. Sign-in uses `signInWithOtp` (magic link, no
   password), redirect target is the app origin (Login.tsx).
2. Where the link arrives: your inbox. Sender and delivery time UNVERIFIED (Supabase auth
   email; Supabase SMTP config is not in the repo). Check spam. See
   docs/email-deliverability-runbook.md.
3. Browser: fresh Incognito window. Open DevTools (F12) -> Network tab, tick "Preserve log",
   filter `api.samidhareviews.xyz`. Console tab open too. Every signed-in call goes to
   `https://api.samidhareviews.xyz/bff/*` with `Authorization: Bearer <supabase JWT>`.
4. Write the CSV below to `handtest.csv` (UTF-8). Text column is auto-detected; fallback names
   are `review_text`, `review`, `comment`, `text`; optional date columns `review_date`, `date`,
   `created_at`, `review_created_at` (app/core/csv_ingest.py). Limits: 500 rows, 5 MB
   (Upload.tsx shows "max 500 rows - 5 MB"). Include one clearly urgent review.

```
review_text,product
"Battery died after 2 days and the charger got hot. Worried about safety. Want a refund.",Turbo-Vac 5000
"Great suction, easy to clean. Would buy again.",Turbo-Vac 5000
"Delivery was late but the product is fine.",Turbo-Vac 5000
"Bahut accha product hai, paisa vasool.",Turbo-Vac 5000
"Stopped working after a week, support never replied.",Turbo-Vac 5000
```

5. Terminal for the curl steps (key goes in an env var, never paste it into chat or commits):
   `export REVIEW_IQ_API_KEY=...` (bash) or `$env:REVIEW_IQ_API_KEY="..."` (PowerShell).

## Steps

### 1. Marketing site loads (1 min)
- URL: https://samidhareviews.xyz . Click the sign-up / app link.
- Expect: page renders, no console errors, link lands on https://app.samidhareviews.xyz/.
- Failure: blank page / 404 / wrong domain. Look: browser Network tab; Vercel dashboard
  deployments for the `site/` project (UNVERIFIED which Vercel project). Doc: site/ folder.

### 2. Sign up with magic link (3 min)
- URL: https://app.samidhareviews.xyz/ (route `/`, Login.tsx). Type the test email, click
  "Get access link". Expect: card "Check your inbox" showing your email.
- Open the email, click the link. Expect: you land on the app, then `/upload` (App.tsx sends
  new sign-ins on `/` to `/upload`). In Network tab: `POST /auth/provision` -> 200.
- Failure A: amber error text under the email field (Supabase error message, e.g. rate limit).
  Look: Supabase dashboard -> Auth -> Logs. Doc: docs/email-deliverability-runbook.md.
- Failure B: link opens but you bounce to the login page. Check Supabase Auth -> URL
  Configuration (redirect URLs must include https://app.samidhareviews.xyz). Memory note says
  this was a manual fix once; current state UNVERIFIED.
- Failure C: `/auth/provision` non-200 (non-fatal in UI, silently swallowed in App.tsx, so
  you only see it in DevTools). Later steps will 4xx. Look: Cloud Run logs (see bottom).

### 3. Upload the CSV (5 min)
- URL: https://app.samidhareviews.xyz/upload . Drop `handtest.csv` (or click the drop zone).
- Expect: "Uploading your reviews..." then "Processing your reviews" with "N of 5 reviews
  done", then "Done!" and automatic redirect to the dashboard. Network:
  `POST /bff/ingest/csv` -> 202 `{job_id,total:5,status:"pending",...}`, then repeated
  `GET /bff/ingest/<job_id>` until status `done`.
- Failure: red ErrorBox with "Try a different file".
  - 413: file over 5 MB or over 500 rows. 422: no usable text column or empty text column.
  - Spinner stuck at 0 of 5 for minutes: durable queue not draining. Look: Cloud Run logs for
    `bff.ingest.job_created`, then ingest-tick Scheduler job. Doc: ops/runbooks/ and
    memory note on the 2-minute `ingest-tick` Scheduler (UNVERIFIED current state).
  - "Service is busy - try again in N seconds": 502/503 with Retry-After (cold start or LLM
    upstream down; api.ts ServiceWarmingError). Wait, retry once. Doc:
    ops/runbooks/cold-start-tuning.md.
  - "Monthly review limit reached.": a 429. You should not see this with 5 rows on Free.

### 4. Dashboard (3 min)
- URL: https://app.samidhareviews.xyz/dashboard ("What customers are saying").
- Expect: health score card, sentiment/urgency summary, top themes (trend data), stat cards
  including "Urgent issues: N reviews" (should be at least 1 for the safety review, but LLM
  output varies, so treat 0 as a note, not a hard fail), and the usage bar (step 5).
  Network: `GET /bff/insights/health-score`, `GET /bff/insights/trends?limit=5&bucket=month`,
  `GET /bff/account`, `GET /bff/reviews`.
- With 5 reviews trends may be sparse/empty: UNVERIFIED what the empty trend state looks like.
- Failure: "Your dashboard is ready" empty-state card with Upload button after a successful
  upload (data not visible: RLS/org mismatch, a P1 bug, send me this); red ErrorBox; spinner
  forever. Look: Network response body + Cloud Run logs for the request.

### 5. Quota meter (1 min)
- On the dashboard, find the usage bar: text `used / quota reviews`
  (Dashboard.tsx UsageBar). Expect `5 / 1,000 reviews` (Free tier quota is 1000 per
  docs/pricing.json `tiers.free.quota`; the bar turns amber at 80%). Network: `GET /bff/account`
  returns `{org_id, quota, usage_this_month}`.
- Failure: quota other than 1,000 (plan/keys mismatch, see PLAN_QUOTA_LIMITS in
  app/api/bff/router.py), `usage_this_month` of 0 after a processed upload (UNVERIFIED
  whether bulk CSV rows count toward usage identically to API calls; record what you see),
  or the bar missing. Do not try to fill the quota.

### 6. Urgent queue (3 min)
- URL: https://app.samidhareviews.xyz/reviews ("Your reviews"). Click the "Urgent" quick-filter
  chip (or the "Urgent issues" card on the dashboard, which applies `urgency=high`).
- Expect: only high-urgency rows, each tagged "Urgent". Network: `GET /bff/reviews?urgency=high`.
  The Export menu offers CSV/JSON (`GET /bff/export/reviews?format=csv`); optional, one click.
- Failure: empty list although the dashboard counted urgent reviews; filter chip does nothing;
  "No reviews match the active filters." with filters cleared (wrong). Look: Network response.
  Note: "urgent queue" here is the urgency=high filter; a separate human-review routing queue is
  only a design doc (docs/human-review-routing-queue-design.md), not a UI route (no such route
  in App.tsx).

### 7. Single review extraction view (3 min)
- From /reviews click one review. URL pattern: `/reviews/<sha256-hex>` (hash without the
  `sha256:` prefix). Expect "What we found": sentiment, urgency, stars inferred, pros, cons,
  topics, language. Do NOT click "Draft a reply" (it calls the LLM; not needed for this test).
- Failure: 404/blank detail page, fields all empty, language obviously wrong (known weak:
  language label agrees with humans only ~48% per app/api/v2/extract.py docstring; note, not a
  fail). Look: Network `GET /bff/reviews?...` response for that review.

### 8. Create an API key (2 min)
- URL: https://app.samidhareviews.xyz/keys ("API keys"). Click "Create key", name it
  `handtest`, leave Monthly quota at 1000, submit.
- Expect: amber banner "Copy this key now ... we won't show it again" with a `riq_live_...`
  key and a Copy button; row appears in the table (prefix only). Network: `POST /bff/keys`
  -> 201. Copy the key into the env var from pre-flight.
- Failure: 400 "Requested quota (N) exceeds the free plan's limit (1000)" (you raised the
  quota above 1000; expected, not a bug); 401 Not signed in; 500. Reload the page: the full key
  must NOT be shown again (if it is, that is a security bug, send me this).

### 9. API call with that key (2 min)
Exactly one call. Header is `X-API-Key` or `Authorization: Bearer` (app/auth/api_key.py;
Bearer wins if both are sent).

```bash
curl -s -i -X POST https://api.samidhareviews.xyz/v2/extract \
  -H "X-API-Key: $REVIEW_IQ_API_KEY" -H "Content-Type: application/json" \
  -d '{"text": "Handtest: great sound quality but the battery dies after 3 hours."}'
```

- Expect: HTTP 200 JSON with `product`, `pros`, `cons`, `sentiment`, `urgency`, `language`,
  `input_hash`, `org_id`, `degraded` and a metadata block (field names per the example in
  app/api/v2/extract.py). Then refresh /dashboard: usage should be 6 / 1,000 (an identical text
  in the same org is served from cache with no quota spent; this text is new, so it counts).
  Optional: `curl -s -H "X-API-Key: $REVIEW_IQ_API_KEY" "https://api.samidhareviews.xyz/v2/reviews?limit=5"`
  (read only, no LLM).
- Failure: 401 (missing/invalid key: check you pasted the full key, header spelling);
  429 (quota; stop, do not retry); 503 `upstream LLM unavailable` with `Retry-After: 30`
  (provider down or Groq limit; stop, do not loop); 5xx otherwise. Look: Cloud Run logs for
  the request; GET /health?deep=1 shows provider reachability (the deep probe makes a live
  provider call, use once at most).

### 10. Revoke the key and sign out (2 min)
- On /keys click "Revoke" on the `handtest` row, then "Confirm". Network:
  `DELETE /bff/keys/<id>` -> 204. Re-run the curl from step 9 is NOT needed (it would
  spend quota); a 401 is expected but UNVERIFIED, skip unless debugging.
- Click "Sign out" in the left nav. Expect: redirect to `/` login page; visiting `/dashboard`
  redirects back to `/`.
- Failure: stays on the dashboard; revoke returns non-204; back button shows stale data.

## Results

| # | Step | Pass/Fail | Notes (time, status codes, screenshot name) |
|---|------|-----------|----------------------------------------------|
| 1 | Marketing site | | |
| 2 | Magic-link sign-up | | |
| 3 | CSV upload (5 rows) | | |
| 4 | Dashboard | | |
| 5 | Quota meter (5 / 1,000) | | |
| 6 | Urgent filter | | |
| 7 | Single review detail | | |
| 8 | Create API key | | |
| 9 | curl /v2/extract (1 call) | | |
| 10 | Revoke key + sign out | | |

## Where to look when something fails

- Browser: DevTools Network (status, response body, `Retry-After`) and Console.
- API logs: Cloud Run service `review-iq`, region `asia-south1`
  (ops/runbooks/cloud-run-deploy.md). Its GCP project id there is `review-iq-prod`, but a
  memory note says the estate moved to `reviewiq-prod-260813`; the runbook may be stale, so
  confirm the project (UNVERIFIED) and pass `--account=gaurav.gandhi1129@gmail.com` to gcloud.
  Structured log events to grep: `bff.ingest.job_created`, `bff.keys.created`.
- Auth/email: Supabase dashboard -> Auth -> Logs; docs/email-deliverability-runbook.md.
- Web deploy: Vercel project for `web/`.
- Other runbooks: ops/runbooks/ (cold-start-tuning, supabase-pause-recovery, secret-rotation).

## If X fails, send me this

Send: step number; UTC time of the attempt; the test email alias (not the magic link, not any
token); the failing request line from DevTools (method, path, status) and the JSON response
body; the `Retry-After` header if present; a screenshot of the visible error; for step 9 the
status line and body from `curl -i`.

NEVER send: the magic-link URL, the Supabase JWT / `Authorization` header, the `riq_live_...`
key, or the Supabase anon/service keys. Redact them from screenshots and HAR files first.
Revoke the `handtest` key afterwards even if a step failed.
