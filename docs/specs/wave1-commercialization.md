# Samidha Reviews — Commercialization Spec (Wave 1)

**Repo:** `C:\Users\gaura\ml-projects\review-iq` · `github.com/gaurav-gandhi-2411/review-iq`
**Product name:** Samidha Reviews · **Canonical domain:** `samidhareviews.xyz`
**Objective of this wave:** eliminate every credibility defect, collapse four surfaces into one owned domain, establish the pre-sale legal/security baseline, and start the long-lead asset that becomes the moat.

**Non-negotiable framing:** this is a product that a stranger will pay money for. Every claim on every surface must be true, current, and bounded by a confidence interval. "Known limitations" printed on a sales page are a defect to fix, not a disclosure to make.

---

## 0. Current state (verified 2026-07-30 from README + live demo page)

### Shipped and believed working
| Capability | Status |
|---|---|
| v2 multi-tenant API, Cloud Run `asia-south1` | live at `review-iq-ajjrytb3na-el.a.run.app` (unverified today) |
| argon2id per-org API keys, Postgres + RLS isolation | shipped Phase 2.0a |
| Extraction: single / batch(≤100) / CSV ingest(≤500 rows, async jobs) | shipped |
| `/v2/reviews` query, `/v2/insights` aggregate | shipped |
| Authenticity scoring + `authenticity_audits` table + IS 19000:2022 posture | shipped Phase 2.2 |
| Language detection (Devanagari regex + Hinglish heuristics + lingua-py) | shipped |
| Language-branched prompts en / hi-en / hi | shipped |
| Tiered router 8B/70B with escalation triggers | shipped but **default OFF** — misses the 85% gate |
| Provider abstraction + `trains_on_input` privacy enforcement (Gemini banned on org path) | shipped Phase 2.1 |
| Self-serve signup (Supabase magic-link → `riq_live_*`) | shipped |
| Eval harness: 46 fixtures, per-language CI gate, nightly Slack | shipped |
| Kill switch: Pub/Sub budget alert → Cloud Function → traffic 0 | shipped |
| CI: lint / format / mypy / tests, scoped eval workflow | shipped |

### Defects found today
| # | Defect | Evidence |
|---|---|---|
| D1 | Demo gallery broken in production — `could not load demo-data.json` | rendered on `review-iq-demo.pages.dev` |
| D2 | Dev instructions leaked to production copy: *"open via a local HTTP server (`python -m http.server`)"* | same page |
| D3 | Footer GitHub link is `href="#"` | same page |
| D4 | Live-analyze widget returned quota/rate-limit fallback | same page |
| D5 | Metric contradiction: README 84.4% / gate ≥85% vs demo page 83.8% / gate ≥83%; README text still asserts "CI breaks below 85%" | both surfaces |
| D6 | Prompt version contradiction: README says v2.1, demo page says v2.3 | both surfaces |
| D7 | "Known gaps" published on the sales page (sarcastic Hinglish, reviews <10 words) | demo page |
| D8 | Authenticity metrics reported as 1.000/1.000/1.000 with no confidence interval on n=40 | README |
| D9 | v1 HF Space live: single-tenant, SQLite, **no auth required**, publicly reachable | README |
| D10 | Four disjoint surfaces; none of them the domain that was purchased | — |

---

## 1. Severity-ranked gap register

### S0 — blocks any sale
1. **No billing / no plan enforcement.** No Stripe, no metered usage, no plan tiers, no dunning, no invoice. Free tier is asserted (100 extractions/mo) but there is no paid path at all.
2. **No customer-facing application.** Landing page + curl ≠ product. Needs dashboard: CSV upload, insight views, flagged-review queue with accept/reject, export, key management, usage/quota, billing portal.
3. **No legal baseline.** Missing Terms of Service, Privacy Policy, DPA template, sub-processor disclosure (Groq is a sub-processor processing customer end-user PII — this must be named), data-retention + deletion policy, DPDP Act 2023 (India) and GDPR posture, breach-notification commitment.
4. **PII leaves the perimeter unredacted.** Review text containing names, phone numbers, order IDs, emails is sent to a third-party LLM API with no redaction pass. This is the single hardest objection an enterprise buyer will raise.
5. **Metric integrity.** D5/D6/D8 must be reconciled to one machine-generated source of truth. A moving gate is worse than a low number.

### S1 — blocks a *defensible* sale
6. **Zero moat.** MIT + public prompts + third-party inference. Nothing here requires anything only Samidha has.
7. **Adversarial robustness of authenticity is unmeasured.** Static fixtures, perfect scores, no attack surface tested (paraphrase, template shift, cross-model-family generation, incentivized-language laundering).
8. **Unknown unit economics.** No cost-per-extraction telemetry (listed as "Phase 2.x planned"). You cannot price without COGS.
9. **Free-tier dependency in the serving path.** Paying customers cannot sit behind a quota that visibly exhausts.
10. **Cross-tenant isolation is asserted, not adversarially tested.** RLS is enabled; there is no test suite that actively attempts cross-tenant reads with a valid-but-wrong org key. Prior work in this repo already surfaced a real `BYPASSRLS` grant issue — that area has a history.
11. **Hand-labeled fixtures.** Violates the standing directive: no workflow may block on GG labeling data.

### S2 — product completeness / applied-AI depth
12. No span-level attribution — a `con` is emitted with no pointer to the text that justifies it. Ungrounded and unauditable.
13. No emerging-issue detection. `/v2/insights` aggregates; it does not answer "what broke this week that wasn't broken last week." That temporal signal is what customers actually pay for.
14. No calibrated confidence / selective prediction, so no defensible human-review routing threshold.
15. No semantic search over an org's own review history ("has this complaint appeared before?").
16. Language coverage stops at 3; expansion currently means writing another prompt by hand.
17. No status page, no published SLA, no uptime measurement, no incident runbook exposed to customers.
18. No distinct logo or visual identity for "Samidha."

---

## 2. The USP — and why the current one doesn't exist

**The claim to be able to make, honestly, by end of Wave 3:**

> The only review-intelligence API running on a model trained specifically for Indian code-mixed reviews — measurably better than general frontier models on Hinglish/Hindi extraction, at a fraction of the per-call cost, and deployable inside the customer's own VPC so review PII never leaves their infrastructure.

Four components, each individually hard to copy:

| Asset | Why a competitor can't clone it quickly |
|---|---|
| Fine-tuned Indic review model (weights on HF) | requires the training corpus, not the code |
| Labeled Indic review corpus + public benchmark | data-collection time; becoming *the* benchmark is a distribution moat |
| Adversarial authenticity model | needs adversarial pairs nobody else has generated |
| Self-hostable small model → VPC deployment mode | Yotpo/Birdeye/Trustpilot are SaaS-only; they structurally cannot offer this |

**Direct evidence this is achievable:** the Hinglish embedding fine-tune already shipped — LoRA r=8 on `l3cube-pune/indic-sentence-bert-nli`, held-out Spearman 0.435 → 0.704, CI-significant, published at `gauravgandhi2411/hinglish-relatedness-sbert`. Same data domain, same technique family, already reusable as the retrieval/dedup component.

**License tension that must be resolved (the one genuinely irreversible decision).** MIT + public prompts is a strong credibility signal and a self-hoster funnel, but it is flatly incompatible with "a moat no one can copy." Recommended split:
- API code, eval harness, prompts → stay **MIT** (credibility, funnel, portfolio value)
- Fine-tuned weights on HF → **CC-BY-NC-4.0** or a custom source-available license (free for research and self-host below a volume threshold; commercial use licensed)
- Labeled corpus + benchmark → **CC-BY-NC-4.0**

Publishing to HF under NC satisfies the HF requirement without handing the moat to a competitor. Once published, a license cannot be tightened retroactively for that revision — so this is confirmed before the first HF push, not after.

**Pre-registered experiment, not a promise.** Whether a distilled 3B beats or matches the 70B teacher on Indic review extraction is unknown. Decision rule, registered before any training run:
- **SHIP-AS-QUALITY-MOAT** if the fine-tune's Indic-strata score ≥ (70B teacher − 2pp) with a bootstrap 95% CI excluding a −5pp regression, **and** ≥ +3pp over the 8B base with CI excluding zero.
- **SHIP-AS-COST-MOAT** if it lands within 2–5pp of the teacher at ≥10× lower cost/latency — the story becomes economics and VPC deployability, not accuracy.
- **HOLD** otherwise, and the moat pivots to corpus + benchmark + adversarial authenticity alone.

No marketing claim is written before the number exists. This mirrors the AgentGauge and TriageIQ discipline: an honest negative is an acceptable outcome; an unfalsifiable claim is not.

---

## 3. Wave plan

| Wave | Scope | Gate to exit |
|---|---|---|
| **1 (this spec)** | Truth reconciliation · domain consolidation · surface retirement · logo/identity · legal + security baseline · PII redaction · adversarial RLS suite · cost telemetry · corpus-mining pipeline starts | one canonical domain live, zero contradictory claims, every metric machine-sourced with CIs, corpus accumulating |
| **2** | Customer dashboard · Stripe billing + metered plans · quota enforcement · status page + SLA · onboarding flow | a stranger can sign up, pay, upload a CSV, and get value without talking to GG |
| **3** | The moat: corpus → distillation fine-tune → HF publish → benchmark publish · VPC/self-host deployment mode | pre-registered decision rule evaluated and recorded as an ADR |
| **4** | Adversarial authenticity rebuild · span attribution · emerging-issue detection · calibrated selective prediction · language expansion via fine-tune | authenticity claims carry adversarial numbers and CIs |

---

## 4. Wave 1 work items

### A. Truth reconciliation (do this first — everything else inherits it)
- Single source of truth: `eval/results/latest.json`, written only by the eval runner. README, landing page, docs, and the dashboard all render from it at build time. No number is ever hand-typed into a surface again. Add a CI check that fails if a `%`-formatted accuracy figure appears in Markdown or HTML that isn't generated from that file.
- Resolve the gate question honestly: state the real gate value, and if it was lowered from 85% to 83%, record an ADR explaining why with the measurement that justified it. Do **not** silently keep the lower gate while advertising the higher one.
- Reconcile prompt version across all surfaces to the actual shipped version.
- Recompute every reported metric with a bootstrap or Wilson 95% CI. Replace `1.000` with `1.000 [0.84, 1.00], n=40` everywhere, including the README. Small-n perfection is reported as small-n perfection.
- Re-render the tiered-router decision: it is shipped but disabled and the disabled state is not disclosed on customer surfaces. Either enable it with an honest routed number, or disclose that routing is off. Do not advertise the cost benefit of a feature that is switched off.

### B. Fixture labeling — remove GG from the loop
- Replace hand-labeling with blind multi-LLM consensus across ≥3 **different model families**, no family being the one under evaluation.
- Measure inter-judge agreement (Krippendorff's α for ordinal fields, Fleiss' κ for categorical). Report it.
- Calibrate judges on a small unambiguous control set first; drop any judge that fails the control.
- Label the corpus honestly as **LLM-consensus ground truth**, never as human ground truth, in the README and in any paper.
- Expand the eval set well past 46 — n=46 across 3 languages cannot resolve a 2pp difference. Target ≥300 with per-language strata sized for the effect you intend to claim; compute and state the MDE.

### C. Domain consolidation onto `samidhareviews.xyz`
Target topology:
- `samidhareviews.xyz` — marketing + docs (static, Cloudflare Pages, custom domain)
- `app.samidhareviews.xyz` — dashboard (Wave 2 fills it; Wave 1 stands up the shell + auth)
- `api.samidhareviews.xyz` — Cloud Run domain mapping to the v2 service
- `docs.samidhareviews.xyz` or `/docs` — API reference
Retire: `review-iq-demo.pages.dev` (301 → apex), the raw `*.run.app` hostname (keep reachable, stop publishing it), and **the v1 HF Space** — an unauthenticated single-tenant SQLite service on the public internet is a security finding, not a demo. Either delete it or replace it with a model-card demo once Wave 3 publishes weights.
Every internal reference, README link, OG tag, and canonical URL updated in the same PR. Add a link-health CI job that fails on any 404/`href="#"`.

### D. Logo and visual identity
- "Samidha" (समिधा) — the wood offered to a sacred fire. The product takes raw scattered material and turns it into signal/heat. That's the concept to design into the mark: many small inputs converging into one bright, structured form. Do not ship a generic speech-bubble-with-star review icon, and do not reuse any other project's mark.
- Deliver: SVG primary mark, monochrome variant, favicon set, 1200×630 OG image, and a token file (colors, type scale, spacing) applied consistently across marketing, dashboard, docs, and the HF model card.
- Modern, high-contrast, WCAG AA verified with a contrast test in CI.

### E. Security and legal pre-sale baseline
- **PII redaction before egress:** detect and mask emails, phone numbers, order/invoice IDs, and person names in review text before it is sent to any third-party LLM. Reversible token map held server-side so output can be rehydrated. Measure redaction recall on a labeled set and report it — this becomes a sales asset, so it needs a number.
- **Adversarial cross-tenant test suite:** for every org-scoped endpoint, attempt access to another org's rows with a valid key from the wrong org, a forged JWT, a mismatched `org_id` in the body, and a direct connection using the app role. Assert deny. Re-verify no `BYPASSRLS` grant exists on the app role. This suite runs in CI on every push.
- Legal pages generated and linked: Terms, Privacy, DPA template, sub-processor list (naming Groq and Supabase), retention schedule, `DELETE /v2/data` hard-delete endpoint with verification, DPDP Act 2023 + GDPR posture statement.
- Secret hygiene sweep: full history scan (gitleaks/trufflehog), rotate anything found, confirm no key material in the repo or in any built artifact.
- Dependency and container CVE gate: target 0 high/critical, SHA-pin all GitHub Actions, least-privilege workflow tokens.
- Per-plan rate limiting and abuse controls on `/demo/extract` and the signup path.
- Structured audit log with customer-facing export.

### F. Reliability
- Get off free-tier-only inference for the paid path: paid Groq tier or a self-hosted fallback, with the router failing over rather than surfacing "quota exhausted" to a customer.
- Fix the demo gallery to be genuinely static-first (D1/D2) so it renders with zero network and zero quota.
- Uptime probe + public status page; SLO defined and measured before an SLA is published.

### G. Cost telemetry (gates Wave 2 pricing)
- Per-extraction cost record: tokens in/out per tier, provider, model, ₹ and $ cost. Aggregate to cost-per-1k-extractions per language per tier.
- Dashboard panel and a weekly report. Pricing in Wave 2 is derived from this, not guessed.

### H. Corpus mining starts now (long-lead critical path for Wave 3)
- Build the ingestion pipeline for publicly available Indian review data with license provenance recorded per record. No scraping of sources whose terms forbid it — record the license for every source and drop anything unclear.
- Language stratification (en / hi-en / hi, plus ta / mr / bn for later), deduplication (reuse the Hinglish SBERT embedding for near-dup), PII scrubbing at ingest.
- Teacher-labeling pipeline: 70B teacher produces extraction targets; multi-family consensus validates a sample; agreement reported.
- Adversarial authenticity pair generation: fake reviews synthesized by model families *different* from both the detector and the teacher, plus paraphrase and template-shift attacks. Held out entirely from any training.
- Target volume and the MDE it supports stated in the ADR before training begins.

---

## 5. Verification gates for Wave 1
1. `samidhareviews.xyz`, `api.`, `app.` all resolve, serve valid TLS, and pass a link-health sweep with zero broken links.
2. `review-iq-demo.pages.dev` 301s to the apex; v1 HF Space removed or replaced; no surface publishes the raw `*.run.app` host.
3. No accuracy figure exists on any surface that is not generated from `eval/results/latest.json`; CI enforces it.
4. Every reported metric carries n and a 95% CI. No bare `1.000`.
5. Zero "known limitation" text on customer-facing surfaces — each item either fixed or moved to an engineering ADR.
6. Adversarial cross-tenant suite green, and demonstrated to fail when RLS is deliberately disabled (prove the test can catch the bug).
7. PII redaction recall measured and reported on a labeled set.
8. 0 high/critical CVEs; clean secret-history scan; all Actions SHA-pinned.
9. Legal pages live and linked from footer and signup.
10. Logo applied consistently across marketing, docs, dashboard shell, OG preview, and favicon.
11. Cost-per-extraction telemetry emitting real numbers for ≥7 days.
12. Corpus pipeline running with license provenance per record and a volume/MDE ADR committed.

---

## 6. Honesty rules for this wave
- Distinguish **verified** from **believed** in every report. "CI green" means a run was observed, not that it should be green.
- Never widen a tolerance, lower a gate, or shrink an eval set to make a check pass. If a gate fails, report the failure and the diagnosis.
- "Document as a known limitation" is a last resort after solution paths are exhausted, and even then it is framed as "here is what would break it" — and it does not appear on a customer-facing surface.
- If a premise in this spec does not check out against the repo, reconcile to reality and say so rather than building on the wrong premise.
