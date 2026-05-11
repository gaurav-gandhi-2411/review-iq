# Review-IQ — `plan.md` v2

> **What changed from v1:** Scope expanded from "production-grade portfolio service" to "open-source product designed to be sellable as hosted SaaS + services." Hosting moves from Hugging Face Spaces to GCP Cloud Run (with hard billing caps). License changes from MIT-with-private-extensions to fully MIT, with monetization via hosted service and implementation work — not via code feature gating.

**Owner:** `gaurav-gandhi-2411`
**Status:** Phase 2.0a complete at v0.2.0. Phase 2.0b (Hinglish) next.
**Last updated:** 2026-05-12
**Live URL (v2, production):** https://review-iq-ajjrytb3na-el.a.run.app
**Live URL (v1, legacy demo):** https://gauravgandhi2411-review-iq.hf.space

---

## 1. The product, restated

**One-line pitch:** An open-source review intelligence service that turns unstructured customer reviews — including Hinglish — into queryable, structured data, with the entire prompt, schema, and eval suite public.

**The wedge against incumbents (Yotpo, Birdeye, Trustpilot Insights):**
1. **Transparency** — every prompt, every fixture, every accuracy number public. Yotpo cannot match this without rewriting their product.
2. **Hinglish + Hindi + Tamil** — Indian language coverage incumbents don't have.
3. **Free to self-host** — anyone can run it. We sell the convenience of hosted + services around tuning/integration.
4. **Open eval as marketing** — the README's accuracy table is the sales pitch.

**Commercial model:** Fully MIT. Monetize via:
- **Hosted SaaS** — "we run it, you use the API" (most clients)
- **Implementation services** — connect to existing pipelines (Shopify, Zoho, etc.)
- **Vertical tuning** — fine-tune prompts/fixtures for a category (electronics, fashion, F&B)
- **Support contracts** — SLA, priority response
- **Training** — for in-house teams

No premium code branch. No feature gates. Same code self-hosters run as we do. This is the **Plausible / Cal.com / Supabase** pattern.

---

## 2. Scope honesty

This is a months-long build, not days, even with Claude Code carrying most of it. Phasing it explicitly:

| Phase | Scope | Outcome |
|---|---|---|
| **2.0a** | Multi-tenancy + Cloud Run migration | Working API on Cloud Run with org/user/key auth. Old HF Space stays as legacy demo. |
| **2.0b** | Hinglish + Hindi + real-data eval | The differentiator shipped. Eval tells the story. |
| **2.5** | SDKs + landing page + docs | A stranger can find it, sign up, integrate in 10 min. |
| **3.0** | Browser extension + embed widget | Viral marketing surface. |
| **3.5** | Premium-style features (Slack alerts, drift, weekly digest) | All free / OSS. Sold as services for setup. |
| **4.0** | Webhook ingestion, vector search, multi-region | Only if there's a real client demanding it. |

**Anti-goals (still):**
- ❌ Billing / payments code in the open-source repo (handle externally if/when there's a paid tier)
- ❌ Feature flags that hide capabilities from self-hosters
- ❌ Scrapers (legal minefield)
- ❌ Building a fine-tuned model in v2 (prompt + structured output is enough)
- ❌ Pretending we have an SLA we can't guarantee
- ❌ Marketing language that overpromises

---

## 3. Architecture (v2)

```
                    ┌─────────────────────────────────────────┐
                    │         review-iq.com (Phase 2.5)       │
                    │   Marketing · Docs · Pricing · Sign-up  │
                    │       Cloudflare Pages (free)           │
                    └────────────────┬────────────────────────┘
                                     │
                                     ▼
                    ┌─────────────────────────────────────────┐
                    │           app.review-iq.com             │
                    │  Dashboard · API keys · Usage · Insights│
                    │  (Phase 2.5 — for now embedded in API)  │
                    └────────────────┬────────────────────────┘
                                     │
                                     │  HTTPS (api key auth)
                    ┌────────────────▼────────────────────────┐
                    │           api.review-iq.com             │
                    │   /v1/extract  /v1/extract/batch        │
                    │   /v1/reviews  /v1/insights             │
                    │   Multi-tenant · per-key quotas         │
                    │       GCP Cloud Run (Always Free)       │
                    │       project: review-iq-prod           │
                    │       max-instances: 2, min: 0          │
                    └─────────┬─────────────┬─────────────────┘
                              │             │
                              ▼             ▼
            ┌──────────────────────┐  ┌──────────────────────┐
            │ Postgres (Supabase)  │  │   LLM (Groq prod,    │
            │ orgs · users · keys  │  │   Gemini dev only)   │
            │ reviews · usage      │  │                      │
            └──────────────────────┘  └──────────────────────┘

  Distribution surfaces (all hit api.review-iq.com):
  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐
  │ Direct API  │  │ Python SDK  │  │  JS SDK     │  │  Browser Ext │
  │   (curl)    │  │  Phase 2.5  │  │  Phase 2.5  │  │   Phase 3.0  │
  └─────────────┘  └─────────────┘  └─────────────┘  └──────────────┘
```

### Why Cloud Run (and how we keep it free)

- Always Free tier: 2M requests/mo, 360k vCPU-seconds, 180k GiB-seconds
- Scales to zero — no traffic = no spend
- `max-instances=2` — can't accidentally scale to a $1000 bill
- Hard kill switch via Pub/Sub + Cloud Function on budget breach (see §11)
- We use the $300/90-day credit as buffer, never depend on it

### Why Supabase (and how we keep it free)

- Free tier: 500 MB Postgres, 50k MAU, 5 GB egress/mo, no card required
- Auth bundled — saves us writing user management
- pg_cron for scheduled jobs (drift eval, retention pruning)
- Pause-after-7-days on free tier is fine — the API wakes it on first request

### Why Groq stays primary

- Free tier: ~14k req/day on Llama 3.3 70B, no card
- **Does not train on inputs** — safe for client data even on free tier
- Native JSON mode + Pydantic = clean structured output

### Why Gemini becomes dev-only

Confirmed: Gemini's free-tier terms allow Google to train on inputs. That's a blocker for any client who cares about privacy. Gemini stays as a fallback for **internal eval and demo runs only**. When we onboard the first paying client, we either upgrade Gemini to paid (where training is opted out) or remove it entirely. The LLM client is structured so this swap is one-line.

---

## 4. Data model — multi-tenant migration

Current schema (v0.1.3):
- `extractions(id, input_hash, review_text_redacted, output_json, model, prompt_version, schema_version, extracted_at, latency_ms)`

Implemented schema (v2.0a) — authoritative as of Step 6:
```sql
-- Tenancy
organizations (id uuid PK, name text, slug text UNIQUE, plan text, created_at timestamptz)
organization_members (org_id, user_id, role)  -- stub, Phase 2.0b

-- Auth: argon2id-hashed keys, prefix-indexed for O(1) lookup
api_keys (
  id uuid PK, org_id uuid FK,
  key_hash text UNIQUE, key_prefix text,  -- key_prefix = first 17 chars of raw key
  name text, quota integer,               -- quota = monthly call limit
  created_at timestamptz, last_used_at timestamptz, revoked_at timestamptz
)

-- Data: flat columns for direct querying + RLS on org_id
extractions (
  id uuid PK, org_id uuid FK, api_key_id uuid FK,
  input_hash text,                        -- SHA-256 of sanitised text
  review_text text,
  -- flat LLM output fields (added migration 20260511000004):
  product text, stars int, stars_inferred int, buy_again boolean,
  sentiment text, urgency text, language text,
  review_length_chars int, confidence real,
  topics jsonb, competitor_mentions jsonb, pros jsonb, cons jsonb, feature_requests jsonb,
  -- provenance:
  model text, prompt_version text, schema_version text,
  latency_ms int, extracted_at timestamptz, is_suspicious boolean,
  created_at timestamptz
  UNIQUE (org_id, input_hash)             -- idempotency: cache per org
)
  INDEX (org_id, sentiment), (org_id, urgency), (org_id, product)
  INDEX (org_id, created_at DESC)

-- Metering: one row per API call; tokens_used updated post-LLM (Phase 2.1)
usage_records (
  id uuid PK, org_id uuid FK, api_key_id uuid FK,
  tokens_used integer DEFAULT 0,          -- updated after extraction; 0 on failure
  created_at timestamptz
)
  INDEX (api_key_id, created_at)          -- monthly COUNT for quota enforcement
```

**Schema deviations from original plan:**
- `extractions.output_json` → flat columns (queryable without JSON extraction operators)
- `extractions.tokens_in/tokens_out` → deferred to Phase 2.1 (LLM client not yet returning token counts)
- `usage_records` → row-per-call model (not daily aggregates); monthly quota enforced via COUNT WHERE date_trunc('month')
- `rate_limit_rpm/rpd` on api_keys → not implemented (quota is monthly only for now)

**Migration from v0.1.3:** Existing SQLite data is dev-only. Drop it. Production starts clean on Postgres. No backfill needed.

**RLS (Row-Level Security):** Postgres RLS policies on every tenant table. Even if API code has a bug, the database refuses cross-tenant reads.

---

## 5. API design — backwards compatible v1, new v2

The current API is `/extract`, `/extract/batch`, `/reviews`, `/insights`. We version it:

- **`/v1/*`** — current endpoints, unchanged behavior. Existing demo continues to work.
- **`/v2/*`** — multi-tenant endpoints. Require API key. Add `org_id` scoping in responses.
- **`/health`, `/metrics`** — unchanged.
- **`/admin/*`** — internal (HTTP Basic auth, owner-only) for org/key management until dashboard ships.

**Auth:** `Authorization: Bearer riq_live_<32-char-hex>` for `/v2/*`. Public on `/v1/*` initially (rate-limited per IP).

**Rate limits:**
- `/v1/*`: 30 req/min/IP (existing)
- `/v2/*`: per-key as configured in `api_keys` row, default 60 rpm / 1000 rpd / 30k/mo

---

## 6. Hinglish + Hindi + Tamil — the moat

This is the most important Phase 2 work. It's also the easiest to do badly.

### Approach

1. **Language detection** before LLM call — `lingua-py` (open source, fast, accurate on Hinglish)
2. **Branched prompts** — separate prompt template per detected language; same schema, different examples
3. **Translation NOT required** — Llama 3.3 70B handles Hinglish natively. Tested on a small set during planning; output is sensible.
4. **Real eval data** — Flipkart Kaggle dataset has genuine Hinglish reviews in the wild. We surface candidates, hand-label, add to fixtures.

### Eval expansion

Current: 25 English fixtures. Target end-of-2.0b:
- 25 English (existing, untouched)
- 18 Hinglish (real, hand-labeled from Flipkart Kaggle)
- 6 Hindi (Devanagari script, real)
- 6 Tamil (real, optional — only if data is easily available)
- = ~55 fixtures total

User commits: ~2 hours hand-labeling Hinglish candidates that CC pre-filters from the dataset.

### Stretch (Phase 2.0b late or 3.0)

Bengali, Marathi, Telugu, Kannada, Gujarati. Each adds ~1 day of work + fixtures. Easy to do incrementally if there's signal.

---

## 7. Real data sourcing for eval

Plan.md v1 used CC-generated synthetic fixtures. v2 expands to real data:

| Source | Type | License | Use |
|---|---|---|---|
| Flipkart Reviews (Kaggle) | E-commerce, Hinglish-rich | CC0 / public | Primary Hinglish source |
| Amazon Reviews 2023 (HuggingFace, McAuley Lab) | E-commerce, English | Research / open | English breadth + edge cases |
| Google Local Reviews (McAuley Lab) | Business reviews | Research / open | Diversity beyond e-commerce |
| Synthetic (CC-generated) | Edge cases | n/a | Adversarial: PII, prompt injection, sarcasm, very short, very long |

CC scripts a `eval/data/sample.py` that pulls candidates from each source, deduplicates, surfaces ~100 candidates for review, of which ~30-50 become labeled fixtures.

---

## 8. Distribution

### 2.5 — SDKs

**Python:** `pip install review-iq`
```python
from review_iq import Client
client = Client(api_key="riq_live_...")
result = client.extract("So I bought the Turbo-Vac 5000...")
print(result.cons)  # ["short battery life", ...]
```

**JS/TS:** `npm install review-iq`
```typescript
import { ReviewIQ } from "review-iq";
const client = new ReviewIQ({ apiKey: "riq_live_..." });
const result = await client.extract("So I bought...");
```

Both: typed (Pydantic → JSON Schema → TypeScript types via auto-gen), async, retry with exponential backoff, structured errors.

### 3.0 — Browser extension

Right-click any review on Amazon / Flipkart / Google → "Analyze with Review-IQ" → popup shows structured breakdown (pros, cons, sentiment, urgency, competitor mentions). Calls public `/v1/extract` (rate-limited, no key required, attribution shown).

Distribution: Firefox AMO (free), self-hosted .crx for Chrome/Edge (defer Web Store $5 fee).

### 3.0 — Embed widget

`<script src="https://review-iq.com/widget.js" data-api-key="..."></script>` → embedded review summarizer for product pages. Phase 3.0 stretch.

### Landing page (Phase 2.5)

Cloudflare Pages. Sections:
- Hero: "Open-source review intelligence. Hinglish included."
- Live demo: paste a review, see structured output
- Eval table (the actual sales pitch)
- Quick start (curl + Python + JS examples)
- Self-host or use hosted (link to GitHub + sign up)
- "How we make money" (services, hosted, support — fully transparent)
- Open repo, eval suite, prompts (links)

---

## 9. Repo evolution

```
review-iq/                           # Same repo, evolved
├── README.md                        # Rewritten as product README
├── plan.md                          # This file
├── ARCHITECTURE.md
├── PROMPTS.md
├── SECURITY.md                      # NEW: PI defense, PII handling, RLS
├── CONTRIBUTING.md                  # NEW: how to add language, fixtures
├── LICENSE                          # MIT (unchanged)
├── pyproject.toml
├── Dockerfile                       # Updated for Cloud Run
├── cloudbuild.yaml                  # NEW: Cloud Run deploy
├── .github/workflows/
│   ├── ci.yml
│   ├── deploy-cloudrun.yml          # NEW
│   ├── deploy-hf.yml                # Existing, kept for legacy demo
│   └── publish-sdks.yml             # NEW: pypi + npm release on tag
├── app/
│   ├── core/                        # Mostly unchanged
│   ├── api/
│   │   ├── v1/                      # Existing endpoints, public
│   │   └── v2/                      # NEW: tenant-scoped
│   ├── auth/                        # NEW: API key middleware
│   ├── tenancy/                     # NEW: org/user/key services
│   ├── billing/                     # NEW: usage metering (no payments code)
│   └── lang/                        # NEW: lingua-py wrapper, prompt routing
├── eval/
│   ├── fixtures/
│   │   ├── en/                      # 25 existing
│   │   ├── hi-en/                   # NEW: Hinglish
│   │   ├── hi/                      # NEW: Hindi
│   │   └── ta/                      # NEW: Tamil (optional)
│   ├── data/                        # NEW: real-data sourcing scripts
│   └── runner.py
├── sdks/
│   ├── python/                      # NEW: Phase 2.5
│   └── javascript/                  # NEW: Phase 2.5
├── extension/                       # NEW: Phase 3.0
│   ├── chrome/
│   └── firefox/
├── docs/                            # NEW: docs site source (mkdocs?)
├── landing/                         # NEW: Cloudflare Pages site
└── ops/
    ├── budget-killswitch/           # NEW: Pub/Sub + Cloud Function
    └── runbooks/                    # NEW: how to verify $0 spend, incident response
```

---

## 10. Tech choices (where they differ from v1)

| Concern | v1 | v2 | Why changed |
|---|---|---|---|
| Hosting | HF Spaces | GCP Cloud Run | Production signal, autoscale, scales to zero |
| DB | SQLite | Supabase Postgres | Multi-tenancy, RLS, managed |
| Auth | None | Supabase Auth + custom API keys | Multi-tenant required |
| LLM fallback | Gemini | Gemini (dev only) | Privacy concern for client data |
| License | MIT | MIT (unchanged) | Aligns with fully-OSS direction |
| Frontend | Server-rendered | Server-rendered v2.0, separate Next.js v2.5 | Phased |
| Lang detection | None | `lingua-py` | Hinglish requirement |
| Real eval data | Synthetic only | Hybrid (Flipkart + Amazon Reviews + synthetic) | Credibility |

---

## 11. Cloud Run cost-control regime (the not-negotiable part)

These are non-optional. Every one ships before any production traffic.

1. **Separate GCP project**: `review-iq-prod`. Isolated billing, isolated IAM. Triage-iq stays in its own project.
2. **Budget alerts**: $0.50, $1, $5, $10 — email + SMS to the user.
3. **Hard kill switch** (`ops/budget-killswitch/`):
   - Pub/Sub topic on budget threshold
   - Cloud Function that calls `cloudbilling.projects.updateBillingInfo` with empty billing → disables billing → all paid services stop
   - Deployed via Terraform in the repo, reproducible
4. **Cloud Run config**:
   - `--max-instances=2`
   - `--min-instances=0`
   - `--concurrency=80`
   - `--timeout=60s`
   - `--cpu=1 --memory=512Mi` (smallest viable)
5. **Cloud Run egress**: VPC connector NOT enabled (egress through default = free up to 1GB/mo, fine)
6. **Container Registry**: Use Artifact Registry free tier (500MB storage = ~5 image versions, GC older)
7. **Logs**: Cloud Logging free tier = 50 GiB/mo. Set log retention to 7 days.
8. **Monthly verification**: First of each month, runbook step that confirms billing dashboard shows $0. Documented in `ops/runbooks/monthly-cost-check.md`.

If we hit Always Free limits: API returns 503 with retry-after header. We do not auto-upgrade. We wait, or we reduce traffic.

---

## 12. Phase 2.0a — execution plan (next CC kickoff)

This is the immediate next phase. Detail level matches Phase 1's §13.

**Branch strategy:** Major change (multi-tenancy) on a long-lived `feat/2.0a-multi-tenant` branch with sub-PRs into it. v0.1.3 / `main` stays untouched until 2.0a is fully green and merged.

**Steps:**

1. **GCP project bootstrap** — `review-iq-prod` project, billing account, Always Free verification, budget alerts, kill-switch deployed FIRST (before any service)
2. **Supabase project bootstrap** — `review-iq` project, schema migration files, RLS policies, connection string in HF Space + Cloud Run env
3. **Schema migration** — alembic or Supabase migrations: orgs, users, members, api_keys, usage_records; add `org_id` to extractions
4. **Auth middleware** — API key parsing, hashing, lookup, quota check, usage recording
5. **Tenancy services** — CRUD for orgs, users, keys; admin endpoints
6. **API v2 endpoints** — copy of v1 with `org_id` scoping; v1 stays untouched
7. **Cloud Run deploy** — Dockerfile updates, cloudbuild.yaml, GH Actions workflow, secrets in Secret Manager
8. **Migration tests** — old v1 calls still work; v2 calls require auth; cross-tenant isolation enforced
9. **Eval re-run on Cloud Run** — verify accuracy unchanged in new environment
10. **README updates** — Cloud Run URL, v2 API docs, hosted vs self-host section
11. **Cutover plan documented** — when to deprecate HF Space (probably never; it remains the legacy v1 demo)
12. **v0.2.0 tag**

**Definition of done for 2.0a:**
- [x] `review-iq-prod` GCP project created, $0 spend confirmed
- [x] Kill switch deployed and tested (manual budget breach simulated) — 2026-05-10
- [x] Supabase project live, schema applied, RLS policies enforced
- [x] `https://review-iq-ajjrytb3na-el.a.run.app` responds — revision `review-iq-00002-gxv`, warm latency ~87ms
- [x] `/v2/extract` requires API key, scopes by org_id
- [x] Eval ≥ 85% on Cloud Run — **87.9%** (25 fixtures, HTTP mode, 2026-05-11)
- [x] Cross-tenant isolation tested — Gauntlet 1: Beta org sees 0 reviews after Alpha extraction
- [ ] Quota enforcement tested on Cloud Run (key with quota=10 returns 429 on 11th request)
- [ ] Monthly cost runbook executed once successfully ($0 confirmed)
- [ ] v0.2.0 tagged

---

## 13. Phase 2.0b — Hinglish + real data (preview)

Will detail in next plan iteration once 2.0a is green. Headlines:

- `lingua-py` integration, branched prompts, language field in extractions
- Flipkart Kaggle data ingestion script
- Hand-labeling session with user for ~30 candidates → 18 keepers
- Hindi (Devanagari) fixtures via similar process
- Eval expanded to ~55 fixtures, accuracy table updated in README
- `v0.2.1` tag

---

## 14. Open questions (deferred decisions, not blocking 2.0a)

- Domain name: stay on `*.run.app` Cloud Run URL initially. Acquire `review-iq.dev` (~$15/yr) at first paying customer or 2.5 phase, whichever first.
- SDK auto-generation tooling: TBD in 2.5. Likely `openapi-python-client` + `openapi-typescript`.
- Landing page framework: TBD in 2.5. Astro vs plain HTML — likely plain HTML for simplicity.
- Browser extension framework: TBD in 3.0. Plain JS or WXT.
- Whether to add a "Powered by Review-IQ" backlink requirement on extension free tier — TBD.

---

## 15. Definition of done for the overall product (Phase 2 + 2.5 + 3.0)

When all of these are true, Review-IQ is a real product:

- [x] Cloud Run production deployment — `review-iq-ajjrytb3na-el.a.run.app`, live since 2026-05-11
- [x] Multi-tenant API with API keys, quotas, isolation — argon2id keys, RLS, per-org scoping
- [ ] ≥ 50 eval fixtures across 3+ languages, ≥ 85% accuracy each
- [ ] Python SDK on PyPI
- [ ] JS SDK on npmjs
- [ ] Landing page live, eval table public, quick-start examples
- [ ] Self-serve sign-up flow (email → API key in 30 sec)
- [ ] Browser extension on Firefox AMO (Chrome optional)
- [ ] README rewritten as product-facing
- [ ] CONTRIBUTING.md so external contributors can add languages/fixtures
- [ ] SECURITY.md documenting PI defense, PII, RLS
- [ ] Runbook for monthly cost verification ($0 confirmed)
- [ ] At least one demo conversation with a real DTC brand (this is on the user, not CC)

---

## 16. The honest commercial framing

When someone visits `review-iq.com` (or the README), this is what they see:

**Free, forever, fully open source.**
- All code MIT licensed
- All prompts public
- All eval fixtures and accuracy numbers public
- Self-host instructions in repo

**Need it hosted? We run it for you.**
- Free tier on hosted: 100 extractions/mo
- Pay tiers (when offered): hosted infra + support, not feature gates
- Same code we open-source

**Need help integrating?**
- Implementation services (paid, scoped engagements)
- Vertical fine-tuning for your domain
- Custom Slack alerts / dashboards / pipelines
- Email: [user-provided]

This framing is honest, doesn't overpromise, and gives every visitor a free path. It also signals clearly that money exchanges hands for **service**, not **software**.

---

## 17. What's NOT in v2 of this plan, intentionally

- Pricing page with specific dollar amounts (premature; figure out after first conversations)
- Stripe integration (premature; first paying clients can be invoiced manually)
- Customer support tooling (premature; email is fine)
- Marketing strategy / content calendar / SEO plan (out of engineering scope)
- Sales process / CRM (out of scope; user's domain)
- Legal: ToS, Privacy Policy, DPA (need real templates when first client is real)

These are real product needs but they aren't *this plan's* job. Flag them when relevant.

---

## 18. Living document

This plan is at v2. It will reach v3 when 2.0a is green and we plan 2.0b in detail. v4 when SDKs are designed. v5 when the extension is scoped. The plan evolves with what we learn from building.
