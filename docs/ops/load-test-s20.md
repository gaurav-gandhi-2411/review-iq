# Load test S20 (F4b): infrastructure capacity with the LLM provider mocked

Labels: **VERIFIED** = measured by the command shown; **BELIEVED** = derived or sourced from
other docs, not measured here. Raw data: `reports/load/s20-run.json` (generated; produced by
`scripts/load/harness.py` at the commit that introduced it, seed 42, 20 s per step).

## Bottom line

- One local uvicorn worker (same command as the Dockerfile CMD, single worker) serves about
  **326 req/s of mocked `POST /v2/extract`** with the fake provider at 0 ms (VERIFIED, c=1;
  flat 311-345 req/s from c=1 to c=128). That is CPU-bound: about 3 ms of Python per request.
- With a simulated 800 ms provider the server is not the limit anywhere in the ramp: throughput
  scales linearly with concurrency (136.5 req/s at c=128, p95 1.05 s, 0 errors, VERIFIED).
- Plain reading (BELIEVED, Little's law): **one Cloud Run instance at the configured
  concurrency of 80 handles about 80 / 0.9 s = ~89 req/s of mocked 800 ms extraction**, and
  would hit the ~330 req/s CPU ceiling only if concurrency were raised past ~300. On a 1 vCPU
  Cloud Run core, which is slower than this laptop core, expect a lower CPU ceiling (not
  measured; run the Cloud Run steps below to get it).
- The real limit is the provider, not the API: Groq free tier is 30 RPM per model (0.5 req/s)
  and `app/core/capacity.py` records a whole-business ceiling of 5.62 extractions/min
  (0.094 req/s, BELIEVED, repo constant derived from the 8K TPM cap). Ratio of the mocked
  knee to that: 326 / 0.5 = **~650x** the RPM cap and 326 / 0.0937 = **~3,500x** the token-bound
  ceiling; at the concurrency-80 estimate, 89 / 0.5 = ~180x and 89 / 0.0937 = ~950x. The API
  tier is not the bottleneck for years of growth; Groq quota is.
- The real authenticated path is not in these numbers (see "Not measured"). One argon2id
  verify (`PasswordHasher()` defaults, as in `app/auth/api_key.py`) costs **59.9 ms** on this
  machine (VERIFIED, command below), so per-vCPU authenticated throughput is bounded near
  1/0.06 = ~17 req/s (BELIEVED, assumes verifies fully occupy a core). That, not extraction
  CPU, is likely the true per-instance ceiling for `/v2/*` API-key calls. BFF routes use
  Supabase JWT sessions (no argon2) and are not subject to it.

## Deployed configuration (Cloud Run, source: `ops/runbooks/cloud-run-deploy.md`, BELIEVED)

Runbook table "current as of v0.13.0 / revision review-iq-00026-ldd": memory 1Gi, cpu 1,
timeout 120s, **concurrency 80**, min-instances 0, **max-instances 3**. `docs/cost-model.md`
confirms maxScale=3 live. The deploy workflow (`.github/workflows/deploy-cloud-run.yml`) sets no
CPU/memory/concurrency flags on `gcloud run deploy`, so those are service-level settings; the
runbook says the live revision wins (`gcloud run revisions describe`, not run here).
Container CMD (Dockerfile): `uvicorn app.main:app --workers 1`.

## Method

- `scripts/load/mock_server.py`: the real FastAPI app (`create_app()`, `DEPLOY_TARGET=cloud-run`
  router set, all middleware incl. SlowAPI, Prometheus, CORS) on `127.0.0.1`, single worker.
  Dummy env set before import; nothing can reach Groq/OpenRouter/Supabase.
- Provider seam: `app.core.llm.GroqProvider` is replaced by a fake with the
  `app.core.providers.base.Provider` shape; it sleeps `--llm-ms` then returns a canned valid
  JSON. The real `extract_with_llm` parse/validate path, prompt build, sanitizer, language
  detection, grounding and cost pricing all run.
- Faked, so excluded from the numbers: auth (`require_api_key` / `require_session_read`
  dependency overrides), the Groq injection classifier (a second live call, stubbed to
  `False`), alert wiring, and Postgres calls (`list_extractions_pg` returns 50 canned rows,
  cost recording is a no-op, `/health` DB ping is an instant fake). `RATE_LIMIT_PER_MINUTE` is
  raised to 1,000,000 (prod is 30/min per IP, which would 429 any load test; limiter code still
  runs). Org is in the default stateless mode (no cache lookup, no persistence).
- `scripts/load/harness.py`: closed loop (each worker sends its next request when the last
  returns), concurrency 1,2,4,8,16,32,64,128 for 20 s each, seed 42 for request bodies, 1,800 s
  hard cap, spawns/terminates its own server child by handle. Uses raw asyncio streams, not
  httpx: a control run against a no-op ASGI app (`control_bare` below) showed httpx collapsing
  from ~900 to ~57 req/s above 8 workers on this machine, i.e. the client would have been the
  bottleneck. With the raw client the control reaches 6,259 req/s peak (VERIFIED), ~7x above the
  slowest scenario, so server results below are not client-bound.
- Machine: AMD Ryzen 7 6800H, 8 cores / 16 threads, Windows 11, Python 3.11.9 (VERIFIED).
  Other processes were running (unquantified).

Reproduce: `.venv\Scripts\python.exe scripts/load/harness.py --out reports/load/s20-run.json`
(about 20 minutes; `--step-seconds 3 --only health` for a smoke run).

## Results (VERIFIED, all 0.00% errors in all 56 steps)

Columns: req/s, latency ms p50 / p95 / p99.

| c | health | root `/` (404 on Cloud Run) | GET /v2/reviews | GET /bff/reviews |
|---|---|---|---|---|
| 1 | 819 / 1.1 / 1.8 / 2.1 | 815 / 1.1 / 1.7 / 1.9 | 369 / 2.4 / 3.8 / 4.1 | 544 / 1.7 / 2.5 / 2.7 |
| 4 | 923 / 3.8 / 6.3 / 7.4 | 851 / 4.2 / 6.7 / 7.3 | 356 / 10.9 / 15.1 / 17.5 | 578 / 6.6 / 9.2 / 10.4 |
| 16 | 855 / 16.5 / 26.5 / 72 | 902 / 15.4 / 24.8 / 65 | 348 / 44 / 60 / 101 | 579 / 26 / 35 / 83 |
| 64 | 819 / 70 / 139 / 154 | 848 / 68 / 130 / 144 | 338 / 183 / 250 / 268 | 553 / 107 / 176 / 196 |
| 128 | 780 / 150 / 232 / 249 | 831 / 142 / 221 / 233 | 336 / 372 / 457 / 467 | 518 / 240 / 325 / 351 |

Full 8-level tables for every scenario are in `reports/load/s20-run.json`. Extraction:

| c | extract, LLM 0 ms | extract, LLM 800 ms |
|---|---|---|
| 1 | 326 / 2.9 / 4.2 / 4.8 | 1.2 / 818 / 821 / 835 |
| 2 | 311 / 6.3 / 8.5 / 10.5 | 2.4 / 820 / 823 / 824 |
| 4 | 324 / 11.9 / 16.1 / 19.8 | 4.9 / 820 / 827 / 833 |
| 8 | 323 / 23.8 / 32.0 / 41.2 | 9.7 / 825 / 838 / 838 |
| 16 | 334 / 45.9 / 63.2 / 106 | 18.9 / 838 / 891 / 915 |
| 32 | 339 / 90 / 145 / 164 | 36.3 / 878 / 929 / 954 |
| 64 | 340 / 180 / 255 / 269 | 71.3 / 882 / 974 / 994 |
| 128 | 345 / 364 / 437 / 462 | 136.5 / 907 / 1050 / 1117 |

Control (no-op ASGI app, harness ceiling): 3,157 req/s at c=1, peak 6,259 at c=64.

## Knee / degradation

There is no error knee: 0 errors up to c=128 in every scenario. The server is a single
Python event loop, so for every CPU-bound endpoint it saturates at **c=1 to c=2** and extra
clients only queue (latency = c / throughput):

| scenario | saturation throughput | p95 doubles vs c=1 at |
|---|---|---|
| health | ~920 req/s (c=4); 780 at c=128 | c=4 |
| GET / (404) | ~900 req/s | c=2 |
| GET /v2/reviews | ~370 req/s | c=4 |
| GET /bff/reviews | ~590 req/s | c=4 |
| extract, LLM 0 ms | ~326-345 req/s | c=2 |
| extract, LLM 800 ms | not reached by c=128 (136.5 req/s, still linear) | never |

Practical latency budget: p95 stays under 100 ms to c=16 for extraction at 0 ms; with 800 ms
provider latency p95 stays under 1.05 s through c=128. Throughput erodes slightly at c=128 for
`/health` and BFF (about -15% and -12% from peak): scheduling overhead, not errors.

## Not measured (and why)

- **DB latency / DB path: NOT measured.** No local Postgres (`which psql pg_ctl postgres`: not
  found) and the Docker daemon is not running (`docker info` fails to connect to the engine
  pipe). Nothing was installed. The API ran against in-process fakes, so a real request adds
  one or more Supabase pooler round trips (auth does ~5 queries incl. `SELECT ... FOR UPDATE`;
  `/health` opens a fresh psycopg2 connection per call) plus the threadpool they run in. The
  harness can add a fixed simulated DB delay (`mock_server.py --db-ms`), but that models
  nothing real; do not quote it. To measure the DB path, run against a throwaway Postgres with
  `supabase/migrations` applied and a seeded key.
- **Real authentication cost.** Replaced by dependency overrides. Argon2 verify alone was
  measured: `.venv\Scripts\python.exe -c "import time;from argon2 import PasswordHasher as P;
  p=P();h=p.hash('x');t=time.perf_counter();[p.verify(h,'x') for _ in range(20)];
  print((time.perf_counter()-t)/20*1000)"` printed 59.9 (ms/verify, VERIFIED).
- Real network, TLS, Cloud Run's front end, cold starts, autoscaling to 3 instances, the
  1 vCPU CPU quota, the real injection classifier call, the rate limiter at its real 30/min.
- `/` returns 404 on Cloud Run (dashboard router mounts only for non-cloud-run targets); the
  `root_404` column measures the unmatched-route path through the middleware stack.

## Caveats

Local machine, loopback only, no real network; the load generator shares the CPU with the
server (the control run bounds that effect: it can push ~6k req/s, the server is at most ~900).
A Ryzen 6800H core is probably faster than a Cloud Run 1 vCPU, so absolute req/s here are an
upper bound (BELIEVED). Closed-loop load with zero think time is a stress test, not a traffic
model. 20 s steps give 25 samples at the slowest cell (800 ms, c=1): its p99 is noise.

## Running the same harness against Cloud Run (text only; nothing here was run)

The mock server fakes auth, so it must never sit behind a public URL. Use a throwaway,
IAM-protected service with no secrets, not the production service or its revisions.

1. Build an image with the harness: take the production image and add a layer
   (`COPY scripts/load/ ./scripts/load/`). `mock_server.py --host 0.0.0.0` is the only
   non-loopback bind, needed because Cloud Run requires binding 0.0.0.0:$PORT.
2. Deploy as a separate service with the production sizing, no traffic exposure to the public:

```text
gcloud run deploy review-iq-loadtest --image=<IMAGE> --region=asia-south1 --project=reviewiq-prod-260813 --account=gaurav.gandhi1129@gmail.com --no-allow-unauthenticated --cpu=1 --memory=1Gi --concurrency=80 --min-instances=1 --max-instances=1 --timeout=120 --command=python --args=scripts/load/mock_server.py,--host,0.0.0.0,--port,8080,--llm-ms,800
```

3. Run the harness from a machine with a decent network path, once per `--llm-ms` value
   (redeploy with `--args` changed), authenticating with an identity token:

```text
gcloud auth print-identity-token --account=gaurav.gandhi1129@gmail.com
python scripts/load/harness.py --base-url https://<SERVICE_URL> --header "Authorization: Bearer <TOKEN>" --only extract_llm800ms --out reports/load/cloudrun.json
```

   Tokens expire in about 1 hour; the full ramp for one scenario takes about 3 minutes.
   `--max-instances=1` isolates single-instance capacity; raise it to 3 to see autoscaling.
4. Delete the service afterwards (`gcloud run services delete review-iq-loadtest ...`).

If a `--no-traffic` tagged revision of the production service is preferred instead, the app
needs a mock-provider mode that does not exist today; do not point this harness at production.
