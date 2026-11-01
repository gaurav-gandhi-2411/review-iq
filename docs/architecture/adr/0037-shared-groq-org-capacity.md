# ADR 0037: Groq capacity is shared across GG's products, so Samidha's real ceiling is unknown

Status: accepted 2026-10-08 (S19 R1). Corrects the capacity claims in ADR 0015's Session 13
correction and `docs/cost-model.md` section 6, which assumed Samidha owned the whole free-tier
budget.

## Context

GG has put all of his products on one Groq account (gg5678g@gmail.com; stated by GG in the S19 R
brief, **BELIEVED**: no Groq console or API call in this session shows the account/org of a key).
Groq enforces limits per organisation, and the free tier gives each model its own pool
(`openai/gpt-oss-20b` and `openai/gpt-oss-120b`: 30 RPM, 1K RPD, 8K TPM, 200K TPD, see
`docs/cost-model.md` section 6). Every key in the org draws from the same pools.

**VERIFIED by reading the other repos (local clones, 2026-10-08):** these products call Groq
with a `GROQ_API_KEY`:

| Repo | Evidence |
|---|---|
| agentic-shopping-assistant (StyleMaitri, T2 live) | `deploy/service.yaml` binds `asa-groq-api-key` to `GROQ_API_KEY`; `src/llm/client.py` `GroqClient` |
| triage-iq | `src/triage_iq/config.py` requires `groq_api_key`; many tests and eval code |
| agentic-travel-booking-system (DealHunter) | Groq adapter, nightly evals on Groq (`CURRENT_STATE.md`) |
| gg-portfolio | `lib/chatbot/llm-provider.ts` calls `api.groq.com` with `openai/gpt-oss-120b` (the same 120b pool) |
| agentgauge | `scripts/p2a_frontier_gate.py` |
| review-iq itself | production extraction **and** every eval/cassette recording use the one production key (`ops/runbooks/eval-cassette-rerecord.md`: no benchmark key exists) |

**Not verified:** that these keys belong to the same org. A key value does not reveal its org,
and reading other projects' secrets to compare hashes would only prove key equality, not org
equality. The GitHub code search used for the survey was rate limited partway, so the list above
comes from grepping local clones; repos not cloned locally were not read.

**VERIFIED observation (one data point):** on 2026-10-08 a Samidha reply-eval batch got a 429 on
`gpt-oss-120b` reading "Limit 200000, Used 199409". Production `extraction_costs` showed about
2K tokens on that model in the rolling window. So roughly 197K tokens of that day's 120b draw did
not come from Samidha production. The most likely source is another product on the same org
(**BELIEVED**; it could also be an earlier Samidha eval, which the ledger did not show).

## Decision

1. Samidha's capacity on the free tier is "the pool minus everything else in the org", not the
   pool. We cannot measure "everything else", so we publish **no free-tier reviews/month figure**
   as Samidha's own. The figure in `docs/cost-model.md` section 6 (about 140.6 extractions/day,
   about 4,217/month) is the ceiling **if Samidha were alone in the org**, and is now labelled so.
2. The production risk is stated plainly: a busy day in any other product silently starves
   Samidha's live extraction (429 -> durable queue backs off -> reviews wait or degrade). The
   existing limiter, queue and failover (OpenRouter ZDR) bound the damage but do not remove it.
3. The fix is a **dedicated Groq org for Samidha production** (free tier, $0). GG steps are in the
   procedure section below. Evals should use a separate key in a
   second org so a recording run cannot starve production either.
4. Until then, the headroom guard (`eval/quota_guard.py`, #303/#309) cannot see cross-product
   draw: its success-header counters are per-minute tokens and a daily *request* count, and the
   only authoritative daily-token signal is a 429 body, which arrives after the fact. It is a
   best-effort proxy, and a green guard does not mean headroom exists.

## Dedicated-org procedure (GG; CC cannot do these)

1. Create a new Groq account under an email that is not shared with the other products (a
   second org under the same login does not help: limits are per org, and the console login
   decides the org). Free tier, no card.
2. Create an API key in that org (console). Do not paste it in chat.
3. Store it: `gcloud secrets versions add groq-api-key --project=reviewiq-prod-260813
   --account=gaurav.gandhi1129@gmail.com --data-file=-` (pipe the key on stdin).
4. Cloud Run already reads `GROQ_API_KEY` from Secret Manager `groq-api-key` at `latest`
   (`ops/runbooks/cloud-run-deploy.md`), but a running revision reads the value only at start,
   so start a new revision ( `gcloud run services update review-iq --region=asia-south1`) to
   pick up the new version.
5. Verify with one real extraction and read the `x-ratelimit-*` response headers; confirm in the
   Groq console that usage appears under the new org only.
6. Disable the old secret version after the check.
7. For evals: a second key in a second org, kept out of production (new Secret Manager secret
   and a loader like the one the eval scripts already use); until then evals keep competing with
   production and every other product.

## Consequences

- Positive: the free-tier capacity claim stops over-promising; the sizing of the cause is honest.
- Negative: until step 3 is done, Samidha cannot state its own capacity and a Groq-paid plan
  decision (cost-model section 6) is not comparable to anything measured.
- Existing numbers that used the pool as Samidha's own (U4d/U5c quota plans) are budgeted
  against a ceiling that may already be consumed elsewhere; the quota-guard persisted 429 floor
  (#309) is what protects those runs.

## Alternatives

- Pay for the Groq Developer plan on the shared org: raises the shared pool but keeps the
  starvation coupling and costs money; deferred, see `docs/cost-model.md` section 6.
- Route Samidha production to OpenRouter by default: costs money per call and changes the
  provider posture; the failover already exists for outages, not for routine capacity.
- Measure the other products' draw by reading their code: it shows who *can* draw, not how much,
  so it cannot replace a dedicated org.

## Amendment 2026-10-10 (S21 G3): the dedicated org exists and is live

GG created a dedicated Groq organisation and stored its key as `groq-api-key-samidha`. Executed and
verified this session:

- **Primary:** Cloud Run `review-iq` revision `review-iq-00152-bad` binds `GROQ_API_KEY` to
  `groq-api-key-samidha` (VERIFIED by `gcloud run services describe`, names only). A real
  `/demo/extract` on the tagged no-traffic revision returned 200 before it took traffic, and again on
  live traffic (VERIFIED). The key is valid and Groq reports the same per-model limits as the shared
  org: `x-ratelimit-limit-requests` 1000/day and `x-ratelimit-limit-tokens` 8000/min on
  `openai/gpt-oss-20b`, `openai/gpt-oss-120b` and `qwen/qwen3.8-27b` (VERIFIED, response headers,
  both orgs).
- **Failover:** `SECONDARY_PROVIDER_KIND=groq`, `SECONDARY_PROVIDER_MODEL=openai/gpt-oss-120b`,
  `SECONDARY_PROVIDER_API_KEY` bound to the shared `groq-api-key` (ADR 0038). Proved offline with the
  real `extract_with_llm`: with the primary key deliberately invalid, the call was served by
  `provider=secondary` in 4.75 s (VERIFIED, `llm.extracted` log line). The live primary was not
  broken to test it.
- **Not measurable from headers:** the per-day token pool (TPD). Groq's success headers do not carry
  it; the 200K figure above is Groq's published free-tier number (**BELIEVED** to apply to the new
  org) and only a 429 body reveals the real remaining budget. The dedicated org removes the
  cross-product starvation described in the Decision, but it does not enlarge the pool: Samidha's
  free ceiling is still about 140 extractions/day on the 200K assumption (`docs/cost-model.md` s6).
- Measuring real capacity therefore needs a deliberate burn to the first 429, which is not done
  here: it would consume the day's production budget.
