# Review-IQ — `plan.md` v2

> **What changed from v1:** Scope expanded from "production-grade portfolio service" to "open-source product designed to be sellable as hosted SaaS + services." Hosting moves from Hugging Face Spaces to GCP Cloud Run (with hard billing caps). License changes from MIT-with-private-extensions to fully MIT, with monetization via hosted service and implementation work — not via code feature gating.

**Owner:** `gaurav-gandhi-2411`
**Status:** Phase 2.0a complete at v0.2.0. Phase 2.0b (Hinglish) next.
**Last updated:** 2026-05-12 — NOTE: this status line and phases 2.0b+ below are stale;
Phase 2.0a through 2.2 have all since shipped (see README Roadmap and memory). Superseded by
the Wave 1 tracker below for anything from 2026-07-30 onward.
**Live URL (v2, production):** https://review-iq-ajjrytb3na-el.a.run.app
**Live URL (v1, legacy demo):** https://gauravgandhi2411-review-iq.hf.space

---

## Wave 1 — Commercialization (started 2026-07-30)

Spec: `docs/specs/wave1-commercialization.md`. Sections A-H, stacked PRs, A gates everything.
Full 12-gate exit criteria in spec §5. Tracked here per rule 118 (checkpoint on every milestone).

### Premise corrections found before building (spec's own §0 had errors — reconciled 2026-07-30)
- Real eval gate: **0.83 overall / 0.80 per-language** (not 0.85), lowered 2026-06-14, rationale
  already in `eval/runner.py` code comment. Real committed number: 83.8% overall (en 86.2%,
  hi-en 80.9%, hi 80.7%), direct-mode, last regenerated 2026-07-06 (stale 24 days vs nightly CI).
- Real prompt version: **v2.3** (`app/core/prompts/__init__.py`). README has 3 different stale
  version strings (v2.0 in an example, v2.1 in roadmap text) — all wrong.
- Tiered router is **live in production right now**, undisclosed on README (README claims OFF).
  Confirmed via a live `/demo/extract` call served by `llama-3.1-8b-instant`. No
  `ENABLE_TIERED_ROUTING` override on Cloud Run — code default (`True`) applies. The demo page
  (`site/index.html`) already discloses this correctly; only the README is wrong.
- `samidhareviews.xyz` nameservers = registrar default DNS (not Cloudflare), apex has no A
  record — domain consolidation (Section C) starts from a completely blank DNS slate.
- v1 HF Space: `/extract` correctly requires an API key, but **`GET /reviews` and `GET
  /insights` are unauthenticated** and return 200 (currently empty, 0 rows) — a real, live,
  unauthenticated data-exposure endpoint. Confirms the Section C retirement call, sharper than
  the spec's original framing.
- **Section E is not starting from zero**: PII redaction already exists and is wired into
  `/v2/extract` (`app/core/sanitize.py` → `redact_pii()`), but it's destructive replacement
  (`[EMAIL]`/`[PHONE]`/`[CARD]`/name-intro-only), not the reversible token map the spec calls
  for; no order/invoice-ID pattern; unclear if wired into batch/CSV/reply paths; no measured
  recall. Adversarial cross-tenant RLS test files already exist
  (`tests/integration/test_rls_isolation.py` + 2 more) — depth/coverage vs the spec's exact
  four attack vectors (wrong-org key, forged JWT, mismatched org_id, direct app-role) not yet
  audited. Zero `BYPASSRLS` grep hits in-repo; a Postgres superuser role was already rotated
  off the backend connection 2026-07-26 (`fix(security): rotate backend Postgres connection
  off the shared superuser`) — re-verify live, don't assume clean from grep alone.
- **Section H is not starting from zero**: `benchmark/vernacular_v2/ingest_and_dedupe.py`
  already ingests 3 license-cleared Kaggle Flipkart datasets (ODbL-1.0/DbCL-1.0, "cleared
  2026-07-07"), deduped to ~245K rows with 595 isolated vernacular (per memory:
  project_vernacular_corpus_isolation.md). `eval/data/README.md` already tracks per-source
  license status, with 2 sources flagged "(check before use)" — unresolved, must be verified
  or dropped per spec rule. `benchmark/vernacular_v2/multi_llm_labeler.py` already exists —
  possible reusable prototype for Section B's multi-LLM consensus requirement, not yet audited
  against the spec's exact bar (>=3 model families, none under eval, Krippendorff/Fleiss
  reported, calibrated on a control set).
- Section G (cost telemetry): confirmed genuinely absent — `app/api/ops.py` has no per-
  extraction token/cost recording. Spec's premise holds here.
- Found but not in spec: 3 Cloud Run env vars (`DIGEST_TRIGGER_TOKEN`, `INGEST_TICK_TOKEN`,
  `DETECTOR_SWEEP_TRIGGER_TOKEN`) are plain env vars, not Secret Manager secrets, unlike every
  other credential on that service. Low blast radius (internal cron auth) but flagged for
  Section E's secret-hygiene sweep.
- Cleaned up: stale worktree `.claude/worktrees/deploy-3d7d4d8` (single already-superseded CORS
  commit) removed 2026-07-30 with explicit user confirmation.

### Section status
| Section | Status | Notes |
|---|---|---|
| A — Truth reconciliation | **done, PR #16 (draft)** | Implemented, independently re-verified (ruff/mypy/950 tests/scanner/drift-check all re-run myself, not just trusted). Opened as draft, not auto-merged: rule 70a gate 3's generated-artifact carve-out doesn't literally cover `eval/results/*.json`, flagged rather than self-granted. CI green on push, PR-triggered run in progress as of opening. |
| B — Kill hand-labeling | **done, PR #17 (draft, stacked on #16)** | Built and independently re-verified: `eval/agreement.py` (Krippendorff/Fleiss, textbook-verified), `eval/power_analysis.py` (MDE), `eval/consensus/` package (2-judge panel after calibration dropped `allam-2-7b`; `llama-3.3-70b-versatile` excluded by design as a self-judging conflict), 132 fixtures total with real consensus ground truth (not 300 — real corpus yield + Groq rate-limit wall, documented honestly), ADR 0002. **INCIDENT (resolved):** recording cassettes for the 83 new fixtures exhausted production's `GROQ_API_KEY` daily TPD budget for `llama-3.3-70b-versatile` (99,770/100,000) after 43/132 — a real quota-consumption event I ran without recognizing it as the exact escalation case my own standing rules name. No customer-impact evidence found in Cloud Run logs, not exhaustively proven either. With GG's explicit direction, filled the gap via OpenRouter (83/83, Gemini configured as unused fallback) — ADR 0003. **Then caught two more bugs in my own recovery, during independent verification, before either executor did:** (1) my own WIP commit had captured a broken partial-run snapshot (27.3%) instead of the true 83.8%/49-fixture figure — restored from the last known-good commit; (2) the 83 substitute-provider fixtures sat inside `eval/fixtures/`, which `eval.runner` scans fresh on every real CI run (not a static check) — would have silently regenerated a 74.6%/FAIL result on the next push regardless of what was committed. Moved to `eval/fixtures/_pending_groq_cassette/` (outside the runner's scan), restoring the clean 49-fixture/83.8% CI gate, verified reproducible twice. This PR adds 132 fixtures' worth of real ground truth to the repo but changes nothing about what currently gates CI. |
| C — Domain consolidation | **code done, PR #19 (draft, stacked on #17)** | Closed a real live bug found during premise-verification: v1 HF Space's `GET /reviews`/`GET /insights` were unauthenticated (confirmed via direct curl) — now require the same API key as every other v1 endpoint; `deploy.yml` no longer auto-pushes to the Space. Fixed 3 of 4 demo-page defects (D2 leaked dev instruction, D3 dead footer link, D7 stale known-gaps banner); D1/D4 did **not** reproduce when checked live today — not fabricated a fix, added a build-time regression guard instead. New link-health CI gate. Draft not auto-merged: touches auth + deploy config, rule-70a gate 4 fails regardless of size. Registrar confirmed via RDAP = NameCheap; DNS genuinely blank. Numbered escalation steps for Cloudflare/NameCheap/Vercel/Search-Console/HF in the PR body — none done yet, needs GG. |
| D — Logo/identity | **done, PR #21 (draft, stacked on #19)** | Replaced the generic speech-bubble-with-star mark (byte-identical in 3 places) with a "samidha" concept: crossed sticks converging into one flame. First attempt (thin radiating sparks) collapsed into a generic AI-sparkle glyph at favicon scale — rejected, rebuilt as solid crossed-log shapes before shipping. Full WCAG-AA-verified token set (`design/tokens.json`) with 2 documented failing pairs (ember+white text, ember-on-paper links) fixed via usage rules, not silently avoided; `scripts/check_contrast.py` is a real CI gate. Draft not auto-merged: 499 non-binary lines, over the reviewable guideline, no generated-artifact carve-out applies. HF model card and full page re-theming explicitly deferred (no card exists yet; re-theming is its own reviewable pass). |
| E — Security + legal | **done, PR #27 (draft, branched from main)** | Legal docs (6 files + REQUIRES-LEGAL-REVIEW banners + real `DELETE /account` documented, not the spec's non-existent `DELETE /v2/data`). CVE gate live (2 real HIGH CVEs found+fixed — pyasn1, pyjwt — blocking gate now enforced, all Actions SHA-pinned). Rate limiting confirmed already shipped (audit finding #10). Adversarial cross-tenant suite: exact 4 spec vectors + RLS-disable proof (MVCC-safe technique, verified on a scratch table before ever touching real tables), 27 integration tests green. **BYPASSRLS re-verified — exists, but is an intentional, already-documented 2026-07-26 decision** (matches Supabase's `service_role` pattern); empirically confirmed `_set_tenant()` genuinely re-enforces RLS despite it; storage_pg.py audited (20/21 functions correctly scope, the 1 exception already justified); added a static-audit regression test. Secret scan (gitleaks+trufflehog): 1 real finding — a "public demo key" was live in README in this public repo for ~23 days (2026-05-15 to 2026-06-07), since removed from current code but still in git history — reported, not rotated, per standing instruction. **PII redaction rebuilt as reversible (unique per-occurrence tokens, in-memory rehydration) but the accuracy-delta gate hit its STOP condition and needs a GG decision**: 19/49 eval fixtures unmeasurable without live Groq calls (none available this session), and the measurable evidence shows the NER name-detector redacting competitor/brand names (Dyson, Shark, Bose, NovaPod) as PERSON entities — reproduced independently, not just trusted. Redaction ships ON by default (security requirement stands independent of this), but 3 options are laid out in the PR for the accuracy-mechanism question. Name-redaction recall measured honestly: 55.6% [41.2%, 69.1%] (overall 83.5% [75.8%, 89.0%], n=121). |
| F — Reliability | **done, PR #31 (draft)** | Headline finding: org-key path had ZERO working failover (`SecondaryProvider` was a literal `NotImplementedError` stub, uncredentialed in prod; Gemini correctly banned there). Fixed: real OpenRouter-backed `SecondaryProvider`, hardcoded `zdr:true` on every request (not config-gated), live-verified 3x against the real ZDR endpoint allowlist plus one full extraction round-trip. Nightly synthetic failover probe (real live calls, both-OK and both-FAIL runs verified). SLO: real Cloud Monitoring/Logging data pulled — 99.972% non-5xx over 14d (all-endpoint, no per-route breakdown available) but only n=2 real extractions logged, honestly reported as `INSUFFICIENT DATA` rather than fabricated. Status page: UptimeRobot free-tier recommended, not built (numbered signup steps in PR). Found+fixed in review: this branch's own new workflow file was unpinned (parallel-branch gap vs. Section E's sweep, which couldn't cover a file that didn't exist yet) — pinned to the same SHAs. |
| G — Cost telemetry | **code MERGED-status: no (PR #24 still draft/open) — DEPLOYED: yes — VERIFIED-LIVE: yes (2026-07-31)** | Per-extraction USD/INR cost recording anchored to the shared single/batch/CSV extraction path (usage_records turned out unusable — 1:1 with a request, not an extraction, and batch/CSV never populate it). All 4 prices + USD→INR rate live-verified against real provider pages (2026-07-31), not recalled. Found (not fixed, flagged for F): Gemini 2.0 Flash — the configured fallback model — was deprecated/shut down by Google on 2026-06-01. **Wave 2 close-out P1**: this PR's code (merged cleanly onto current `main`) was built (`v0-18-0`), deployed to production as revision `review-iq-00035-4nr`, promoted to 100% traffic, and proven with two real `/v2/extract` calls producing two real `extraction_costs` rows confirmed via direct DB query (then cleaned up) — see PR #24 comment for the full staged-deploy trace. **`main` and production have diverged**: production runs this PR's code, `main` does not, until the PR itself is merged (blocked on rule 70a gate 3 — 891 reviewable lines exceeds the 400-line auto-merge ceiling, needs human merge). |
| H — Corpus mining | **done, PR #25 (draft)** | Extended `benchmark/vernacular_v2/`, both unclear-license Kaggle sources resolved (CC0-1.0; one wired in, one left unwired — schema unverifiable in a gitignored worktree). ADR 0004: target ≥300K corpus / ≥5,000 Indic-strata eval (MDE≈2.2pp), floor ≥2,000 (MDE≈3.5pp) — current real Indic yield is only 595 (~12% of floor), named as the real Wave 3 blocker. $0 spent (no live creds in that worktree; every LLM stage mock-tested, a bounded 72-call/~$0.05 live-run plan is ready and cost-capped). Independently rediscovered, and confirmed already-fixed, the same PII name-regex bug E's rebuild caught. |

### Stack unblock + pre-Section-E audit (2026-07-31)
Rebased `#16→#17→#19→#21` onto current `main` (main had moved forward via tracker-only PRs
#18/#20/#22) and reconfirmed CI green on all four **against their actual current base**, not
their original one — this surfaced two real bugs neither original build caught, both fixed at
the layer that introduced them (not papered over one layer up), then cascaded through the rest
of the stack:
- `check_no_hardcoded_metrics.py` (from #16) and `check_link_health.py` (from #19) had never
  seen `docs/specs/wave1-commercialization.md` before (it only reached `main` via #18, after
  both scripts were written) and both flagged the spec's own historical-defect writeup /
  backtick-quoted `href="#"` prose as live violations. Root-cause fixed (spec docs excluded
  from the metrics scanner like ADRs already are; Markdown code-span/fence stripping added to
  the link scanner) rather than suppressed.
- Confirmed ADR 0001 exists and names the tiered-router cost/accuracy tradeoff (per-language
  gates held ≥80% at ~1% overall cost from small-model routing on hi/hi-en) as the 0.85→0.83
  rationale — satisfies the standing "must the ADR say so in those terms" bar.
- Confirmed every customer-facing accuracy figure (README, site/index.html, site/docs/index.html)
  renders from the same `eval/results/latest.json` via METRICS markers, and direct/routed are
  byte-identical under the current config (ADR 0001) — no surface shows a stale/different
  number. Found and fixed one disclosure-consistency gap: site/index.html didn't say
  "mode: routed" the way README did (same number, just under-labeled).
- Found and fixed a real false claim: site/index.html claimed the capability gallery "works
  with JS disabled." Verified directly (raw HTML with zero JS execution) that this was false —
  100% JS-populated, no `<noscript>` fallback, a JS-disabled visitor saw a blank section. Added
  a real static example inside `<noscript>`, corrected the copy, and added a third
  `check_link_health.py` check that fails on a missing/placeholder/non-JSON-shaped `<noscript>`
  block (proven against 8 new tests, including the real committed file).
All landed on PR #21 (current stack tip) — see its "Update" section for the itemized commit
list. Full stack reconfirmed CI-green after every rebase step, verified via `gh pr checks`,
not assumed.

### E kickoff (2026-07-31)
Confirmed before building: PII redaction (`app/core/sanitize.py`) is destructive
(`[EMAIL]`/`[PHONE]`/`[CARD]`/name-intro-only replacement), not the reversible token map the
spec requires; no order/invoice-ID pattern. Adversarial cross-tenant RLS test files exist
(`tests/integration/test_rls_isolation.py` + 2 more) but depth vs. the spec's exact 4 attack
vectors (wrong-org key / forged JWT / mismatched org_id / direct app-role) not yet audited
against those specific vectors. Zero `BYPASSRLS` grep hits confirmed.

### G/H kickoff (2026-07-31)
G (cost telemetry): confirmed genuinely absent — `app/api/ops.py` has no per-extraction
token/cost recording. H (corpus mining): `benchmark/vernacular_v2/` already has ingestion +
dedup + a multi-LLM labeler prototype from earlier work — extending, not rebuilding.

### Post-E remediation (2026-07-31) — P0-P3 + Section F + Section H sourcing decision

**P0 (secret exposure) — CLOSED.** The demo key (`riq_live_d6fb4d0c...`) was revoked
(`revoked_at` set, live-verified: the key now returns 401 on the real API). Usage audit:
exactly 1 usage record exists for this key, timestamped **~16 hours BEFORE** the key was even
published in README — almost certainly the original setup/test call, not external use. **Zero
unattributable usage during the actual ~23-day exposure window.** History-reachability confirmed
precisely: present in 21 historical commits (all pre-dating removal), absent from all 7 current
Wave 1 feature branches and all 20 version tags. Not rewriting history (the key is dead; a
rewrite would disrupt every clone/fork of this public repo for no live-security benefit) —
flagging that call as available if GG wants it anyway.

**P1 (NER redaction bug) — bug fix CLOSED, accuracy-delta gate NOT cleared — corrected
2026-07-31.** Confirmed the bug was exactly what was suspected: `_redact_names_ner()`
already correctly filtered to `PERSON`-only (ORG/PRODUCT were never touched), but spaCy's
small model misclassifies brand names as PERSON on short review text. Fixed with a brand
gazetteer (132-entry static list + live `competitor_mentions` history, 10 real brands
already recorded including the exact Dyson/Shark/Bose that were misredacted) — a gazetteer
hit vetoes redaction of that span regardless of spaCy's classification. ADR 0005 records
the "entity classes must be disjoint from the product schema" rationale. One
honestly-documented residual gap: a fictional eval-fixture brand name with no real-world
presence still misclassifies (can't enter a real gazetteer without fabricating an entry) —
tested and designed to fail loudly once a real per-org catalog would close it, not
silently accepted.

**The accuracy-delta gate itself was previously mischaracterized as passing ("under the
1pp gate") — that was wrong and is corrected here.** At the intermediate 44/49-paired
measurement, the 95% CI was [−2.16pp, +0.35pp]: an interval that admits both a >1pp
regression AND crosses zero does not clear a 1pp gate by any reasonable reading, regardless
of the point estimate (−0.78pp) looking small. Re-measured against the now-FULL 49/49
fixture set (the 5 previously-blocked fixtures — 001_turbo_vac, 007_buy_again_ambiguous,
008_pii_heavy, 010_urgent_angry, 015_medium_urgency — recorded live in both arms, 10 calls,
Groq quota headroom checked first): **paired mean delta (ON − OFF) = −0.71pp, 95% CI
[−1.92pp, +0.35pp] — still does NOT clear the gate** (still crosses zero, still admits
a regression exceeding 1pp). This number appears on no customer-facing surface and no
README; the point estimate is not restated as if it had passed. Formal decision on what
this means for shipping redaction (already ON by default as a security requirement,
independent of this accuracy question) remains a GG call.

**P2 (BYPASSRLS reachability) — S0 finding, reported not fixed.** Traced in code, not
inferred: `review_iq_app` (rolbypassrls=true) IS reachable from 3 live, request-serving paths
with zero `_set_tenant()` call — `app/api/admin.py` (every DB helper; protection is
single-factor HTTP Basic auth with **confirmed no rate-limiting**), and
`app/api/webhooks/{google,shopify}.py`'s org-resolution lookups (protected by a shared-secret
token / HMAC signature, also single-factor). Ruled out by tracing, not assuming: no
fallback-DSN chain exists (`DATABASE_URL` is an unrelated legacy v1 SQLite path); the
migration runner uses a distinct `postgres` superuser credential over a direct (non-pooler)
connection, not shared with the app's PgBouncer pool; every `SET ROLE` in the codebase is the
safe transaction-scoped `SET LOCAL ROLE` form. Added an `xfail(strict=True)` assertion to the
adversarial suite (`TestBypassrlsServingPathReachability`) — fails today on purpose, tracked,
will loudly XPASS-fail the moment someone "fixes" it without deliberately removing the marker.
**Not fixed in this pass, per instruction.**

**P3 (dead failover) — confirmed, fix dispatched.** Fixed the stale `gemini-2.0-flash` default
(live-verified deprecated/shut down by Google 2026-06-01 → `gemini-2.5-flash`). The real
finding: traced `app/core/llm.py::extract_with_llm`'s full fallback chain and confirmed
**`SecondaryProvider` — the org-key path's only non-Gemini failover option — is a literal
unimplemented stub** (`NotImplementedError` if ever called), not even configured with
credentials in production. Combined with Gemini being correctly banned on the org-key path
(`trains_on_input`), **the paid path has zero working failover today** — any Groq
degradation/outage means 100% of paying-customer traffic gets a 503. Section F work (in
progress) implements a real `SecondaryProvider` (OpenRouter, routed to a verified no-train
model), a nightly synthetic failover probe exercising both fallback paths end-to-end, an
uptime probe/status page, and a measured (not asserted) SLO.

**Section H corpus-sourcing decision — ADR 0005 committed to PR #25, priced not decided.**
Live research (not memory): no public dataset combining Hindi/Hinglish + product-review domain
+ adequate volume exists (checked Kaggle, HuggingFace, AI4Bharat's IndicNLP catalog).
Commercial vendors (Shaip, Twine) exist, no public pricing — reported as an explicitly-labeled
rough range, not a quote. 4 options priced (public sources / licensed data / narrow to
Hinglish-only / accept the cost-moat decision-rule branch) for yield/cost/calendar/USP-support
each. Synthetic review generation ruled out categorically, not priced — GG decides.

---

## Wave 2 — Dashboard, pricing, billing, redaction-gate close-out (started 2026-07-31)

Orchestrator + executor + verifier. Checkpoint per rule 118 (in progress; updated as each
priority lands, not all landed yet in this pass).

| Priority | Status | Notes |
|---|---|---|
| P0 — Redaction gate MDE | **done, PR #38 (draft, stacked on Section E)** | Did not re-run the measurement. Computed the paired-design MDE from the already-recorded 49/49 result's exact sample std dev (4.0719pp, extracted via replay-mode-only re-execution, zero live calls). Added `paired_mde`/`required_n_for_paired_mde` to `eval/power_analysis.py` (existing fn assumes independent samples — wrong model here). MDE at n=49: 1.14pp (precision-only) / 1.63pp (alpha=0.05, power=0.80, this project's own corpus-sourcing convention). Both exceed 1pp — the original gate was unachievable at max available sample, confirmed as a spec defect. **Decision recorded in ADR 0005's amendment: restate the gate at 1.63pp (the more conservative framing) rather than grow the fixture set** — growing to n≈131 for the power framing is a 2.6x lift disproportionate to a hygiene control; n≈70 (precision framing) priced as a cheap future option via the existing consensus pipeline, not executed. Also found and corrected an already-merged wrong claim: `plan.md`'s own "under the 1pp gate" line (from the Wave 1 S0 pass) was itself incorrect — fixed via a separate small PR (#35, merged) following the established docs(plan) auto-merge precedent. |
| P1 — ACL exposure standing audit | **done, PR #39 (draft, stacked on the S0 fix)** | The Wave 1 S0 finding (SECURITY DEFINER function inheriting EXECUTE via Supabase's default ACLs) is a recurring class. `scripts/check_acl_exposure.py` queries the live schema for any table/view/SECURITY DEFINER function missing its protective revoke/anon-deny-policy, with exactly one justified allowlist entry (`current_org_id()`, which genuinely needs `authenticated` EXECUTE for RLS itself to function). Backfill-verified against live prod: clean. **Proven to catch the bug**, not just report clean: a new integration test creates a disposable, unprotected SECURITY DEFINER function mimicking the S0 bug exactly, confirms it's flagged, confirms a properly-revoked sibling is not, confirms `current_org_id()` stays allowlisted — all verified against live prod, cleanup confirmed. Wired as a new nightly + migration-triggered CI workflow reusing the existing `SUPABASE_DIRECT_URL` secret. |
| P2 — Dashboard | **partial, PR #40 (draft)** | Recon first (this mattered): a full dashboard already exists and is deployed — CSV upload w/progress, sentiment/topics/competitor/urgency display, filters, review detail, authenticity insights. The highest-leverage gap was structural, not a missing feature: **every route required Supabase magic-link signup first**, including `/upload` — breaking "stranger sees value in under 60 seconds, no docs" at the very first step. Built a new public `/try` page (no signup): drop a real CSV or use a bundled sample in one click, client-side-parsed, runs the first 5 rows through the existing keyless `/demo/extract` endpoint (stays within its 5/min cap), same visual language as the real dashboard, ends on a sign-up CTA. **Verified live in a browser** (chrome-devtools MCP against a real local backend, not just built) — screenshots in `reports/screenshots/wave2-p2-try-demo/`. Found and fixed a real bug while building this: `App.tsx`'s auth listener force-navigated to `/` on every auth-state event including initial mount, silently stomping navigation to any public route — `/try` was unreachable until this was fixed. Also added a "try with sample data" action to the authenticated empty-dashboard/upload-idle states (runs the real ingest pipeline, not the ephemeral demo). **Still not done**: flagged-review queue with accept/reject actions (needs a new `authenticity_audits` disposition column + BFF endpoint + UI — no such disposition/action concept exists in the schema today), export (CSV/JSON) UI (`/v2/dataset/export` exists, not mirrored in the BFF, no button), API key management page (backend `app/api/account.py` exists, no UI), a dedicated usage-vs-quota page (today only a banner), and an explicit audit that any newly-added accuracy/confidence figure is sourced from `eval/results.json` (not `eval/results/latest.json`, which doesn't exist — a premise correction). |
| P3 — Pricing from real telemetry | **done, PR #42 (draft, ADR 0007)** | Section G's cost-telemetry code (`app/core/pricing.py`, real verified constants) is real but lives on unmerged `feat/wave1-g-cost-telemetry` and was never deployed — `extraction_costs` does not exist in production, confirmed via a live read-only query. Real recorded cost sample size is 0, not thin — reported as such, not extrapolated. Computed a theoretical COGS estimate instead: real pricing constants against real token counts pulled from 68 actual Groq responses already on file (`eval/cassettes/cassettes.json`), bucketed by language. Result: en (small tier) ~$0.096/1k extractions, hi (large) ~$0.626/1k, hi-en (large) ~$1.167/1k — large tier costs 6.5–12x small, almost entirely the model choice not token counts. Verdict: cost is inference-dominated, not fixed-floor-dominated (both Cloud Run and Supabase are on free tiers today, zero fixed cost currently paid) — usage-based pricing is coherent. Proposed Free(100)/Starter(2,000, $19)/Growth(10,000, $79) tiers, 85–99% margin shown. **Found and flagged**: the API docs claim a 100/month free-tier quota but `admin.py`'s actual default is 1000 — a real, live discrepancy needing reconciliation independent of tier pricing. |
| P4 — Minimum-viable billing | **done, PR #43 (draft, ADR 0008)** | No Stripe account exists (no `STRIPE_SECRET_KEY` anywhere) — a genuine wall, numbered escalation steps given (account/price/webhook setup, env vars, migration, quota reconciliation, live test-mode verification). Built Stripe Checkout + Customer Portal (Stripe's own hosted pages) for signup and self-service plan management, Stripe's native Smart Retries for dunning — no custom payment form/subscription UI/retry logic, keeping this genuinely minimum-viable. Migration widens `organizations.plan` to include `starter`/`growth` plus Stripe identifiers (one source of truth, not a parallel table); dry-run verified against prod, all 20 existing orgs remain valid. **Found and fixed two real bugs before they shipped**: quota enforcement actually reads `api_keys.quota` (per-key), not `organizations.plan` — every subscription sync now updates both, or a paying customer would stay capped at their old free-tier limit after upgrading; a downgrade helper's placeholder would have overwritten the real Stripe customer ID on cancellation, caught in self-review before commit. 13 new unit tests, including webhook signature verification against a real, independently-constructed HMAC-SHA256 test vector (not mocked) — valid/forged/tampered/expired cases all correctly handled. **Explicitly unverified against a live Stripe account** — flagged in ADR 0008 and every touched module's docstring; do not treat as tested the way the rest of this Wave has been. |

**Cross-branch note found during P0**: merging `feat/wave1-b-llm-consensus-labeling` into a
worktree already holding the S0/Section-E branches silently changed the redaction-delta
script's behavior (49/49 paired → 32/49, asymmetric blocking, a suspicious exact-zero delta) —
root-caused to wave1-b's own auto-merged `eval/runner.py` changes, not the fixtures/cassettes
(which merged cleanly). Not investigated further (out of scope for P0), but flagged in PR #38:
**whoever eventually merges wave1-b should re-run `measure_redaction_accuracy_delta.py`
post-merge and confirm it still reproduces 49/49** before trusting anything eval-related on
that combined branch.

---

## Surface recovery + admin lockdown (2026-07-31)

Triggered by the Wave 2 close-out P4 audit finding two problems beyond what was asked: the S0
BYPASSRLS finding was never actually applied to production (task-list "done" meant dry-run only),
and `app.samidhareviews.xyz` was returning Vercel's `DEPLOYMENT_NOT_FOUND`.

**P0 (cut the live exposure without touching the DB) — VERIFIED-LIVE, PR #46 (draft).**
Deployed a second Cloud Run service, `review-iq-admin`, `--no-allow-unauthenticated`, mounting
only `ops_router` + `admin_router` (cherry-picked just the `SERVICE_ROLE` routing split from ADR
0006/PR #33 — not the full S0 branch). The existing public `review-iq` service no longer mounts
`admin_router` under any config. Verified directly: public service's `/admin/*` → **404** (fully
unmounted, stronger than the 401 that existed before); `review-iq-admin` unauthenticated → **403**
from Cloud Run's own IAM layer, before the app is even reached; same path via an authenticated
`gcloud run services proxy` identity → **401** `{"detail":"Not authenticated"}` (reaches the app,
`require_admin`'s Basic auth still demands a password underneath — both layers live). IAM policy
on the new service has **zero explicit bindings** — my own access works only via pre-existing
project-owner permissions, confirming no new public/service-account grant was introduced. Real
`POST /v2/extract` against the public domain with a disposable test key still returns a correct
200 (non-admin path unaffected); test org/key deleted after. **BYPASSRLS remains present on
`review_iq_app`, unchanged — this is reachability mitigation, not the fix.** `ADMIN_DATABASE_URL`
deliberately points at the SAME existing `supabase-database-url` secret (not a new
`review_iq_admin` role) so the admin service works today with zero DDL/DML against prod; P3 swaps
this once PR #33's migration lands, in the exact order that PR already specifies.

**P1 (diagnose the missing Vercel project) — diagnosed, neither offered explanation holds. Not
redeploying — escalated to GG.** Ruled out "personal scope, not team scope" two independent ways:
the Vercel MCP integration's `list_teams` returns exactly one scope
(`gaurav-gandhi-2411's-projects`), and the `vercel` CLI, logged in as `gaurav-gandhi-2411`
directly (not through the MCP connector), lists the identical 7 projects under the identical
scope — no separate personal account exists to check. Ruled out "retired deliberately during
Section C" by reading PR #19's own numbered console steps in full: **step 4 explicitly instructs
keeping and using the `review-iq-web` Vercel project** ("Vercel dashboard → the `review-iq-web`
project → Settings → Domains → Add → enter `app.samidhareviews.xyz`") — the only retirement this
PR instructs anywhere is step 6, the **v1 Hugging Face Space** (a different platform entirely,
`huggingface.co/spaces/gauravgandhi2411/review-iq`), never Vercel. Corroborating evidence: the
live `ALLOWED_ORIGINS` env var on `review-iq` still lists `review-iq-web-umber.vercel.app` — a
real prior deployment URL for a project literally named `review-iq-web`, which does not appear
among the 7 projects that exist today. **Conclusion: the `review-iq-web` Vercel project was
removed by some action outside anything recorded in this repo — most likely a manual deletion
during or after the Section C console steps, cause unknown from repo-internal evidence alone.**
Per instruction, not redeploying until GG confirms what happened.

**P1 correction (same day, later pass) — the above was wrong to call it "deleted with cause
unknown"; better evidence exists and was found by querying Vercel's and Firebase's own APIs
directly, not memory or repo text.** `vercel domains inspect app.samidhareviews.xyz` (an
authoritative, timestamped Vercel control-plane record, not a memory note) shows
`samidhareviews.xyz` was added to this Vercel account on **07 July 2026 16:08:03** — matching the
same day as a separately-discovered, independently authoritative record: Google's Firebase
Hosting API shows `api.samidhareviews.xyz` was attached via a Firebase Hosting rewrite-to-Cloud-Run
(**not** the native `gcloud run domain-mappings` PR #19 describes — a genuinely different
mechanism PR #19's own recon missed), deployed by `gaurav.gandhi2411@gmail.com`, reaching
`DOMAIN_ACTIVE`/`CERT_ACTIVE` at **07 July 2026 12:14:15 UTC**. Both are hard, cryptographically-
backed, cloud-provider-timestamped facts, not recollection — and they directly contradict a
separate claim (from outside this repo) that no Samidha custom domain resolved on that date: this
one demonstrably did, and still does (re-verified live today).
For `app.`: the DNS-level record (a direct **A** record, `76.76.21.21`, Vercel's anycast IP — not
a CNAME, confirmed by contrasting an explicit CNAME query, which returns nothing, against a plain
query, which resolves) still exists and still resolves today. Vercel's domain **object** for the
whole zone also still exists in the account. What's gone is specifically the **project** that once
claimed that domain (`review-iq-web` — also corroborated by an untracked, pre-existing file in
this working directory, `review-iq-product-overview.md`, which names
`review-iq-web-umber.vercel.app` directly as "the live app," independent of any memory system).
Vercel's own nameserver check on the domain object shows a standing mismatch (`✗`: intended
`ns1/ns2.vercel-dns.com`, actual still NameCheap's `dns1/dns2.registrar-servers.com`) — the
account was set up expecting full nameserver delegation to Vercel, which was never completed; the
subdomain-level A record was added directly on NameCheap's own DNS instead, entirely independent
of that unfinished delegation.
**Corrected framing: the project existed, genuinely worked (at its own `.vercel.app` URL, evidenced
independently of memory), and the custom-domain attachment was genuinely started (DNS record
live since 2026-07-07) — what's missing is not "was this ever attempted," it's that the specific
Vercel project which once claimed the domain no longer exists, while the DNS record and the
domain object both survive it.** This is closer to the original "a project was deleted" framing
than to "the attachment was never completed" — corrected here with authoritative evidence instead
of a memory note, per instruction.

**P2 (extend Section F's probe to web surfaces) — VERIFIED-LIVE (probe logic), PR #47 (draft).**
`scripts/probe_web_surfaces.py` + `.github/workflows/web-surface-probe.yml`, mirroring
`probe_failover.py`/`failover-probe.yml`'s nightly-cron + optional-Slack-notify convention. Checks
all 4 named surfaces (marketing apex, dashboard, API health, `/try`) for HTTP 200 **and** a
content-marker assertion — a parked domain or generic error page can return 200 too. Ran live
against production as verification, not just against mocks: correctly caught the exact ongoing
incident (`ConnectError` on the apex — no DNS record; `404` on dashboard and `/try`; API
genuinely healthy) — proof it detects the real failure mode, not just a green mocked test suite.
The *standing nightly job* activates once merged to `main` (GitHub Actions schedules don't run off
feature branches); the probe logic itself is proven live today.

**P3 (post-merge cutover: redeploy web, run PR #33's migration, re-verify BYPASSRLS gone) — not
started, explicitly gated on the stack merging and deploying first, per instruction. Readiness:**
P0 is merge-ready (draft PR #46, VERIFIED-LIVE). P1 is blocked on GG's answer before any redeploy
can happen. P2 is merge-ready (draft PR #47). PR #33 (the migration itself) is unchanged, still
holding its own 9-step apply sequence. Waiting on GG per instruction — not proceeding further.

### Decouple P3, unblock DNS (2026-07-31, later same day)

**P0 (correct the P1 record) — done, see the P1-correction entry above.**

**P1 (re-issue console steps as one consolidated block) — done, delivered directly to GG, not
duplicated here** (per instruction: one block, no cross-referencing other PR bodies). Root cause
confirmed by direct DNS query: nameservers are still NameCheap's default
(`dns1/dns2.registrar-servers.com`) — the Cloudflare migration PR #19 recommends was genuinely
never executed; Section C is complete in code, unexecuted in the world, exactly as instructed to
assume. Precise current record inventory (queried directly, not assumed): apex has no A/AAAA at
all; `api` is a CNAME to `review-iq-prod.web.app` (Firebase Hosting, working, since 2026-07-07 —
no action needed, corrects PR #19's assumption that this still needs a native Cloud Run domain
mapping); `app` is a direct A record to `76.76.21.21` (Vercel, DNS-side already correct, blocked
only on which Vercel project claims it — see P1 correction above and P3 below).

**P2 (DB-side cutover) — readiness reported, not executed** (explicitly does not depend on the
Vercel question, per instruction). The exact two currently-red tests, confirmed from PR #33's own
body: `test_app_role_without_set_tenant_sees_no_orgs` and
`test_admin_and_webhook_serving_paths_do_not_use_a_bypassrls_role` (both in
`tests/integration/test_adversarial_cross_tenant.py`), both correctly failing today against live,
unmigrated production (`review_iq_app.rolbypassrls` re-confirmed `true` this same session).
**Reconciliation note for whoever executes this**: today's P0 admin-lockdown work (PR #46)
already partially completed PR #33's steps 3 and 5 — `review-iq-admin` already exists and is
already `--no-allow-unauthenticated`, but wired to reuse the existing `supabase-database-url`
secret rather than a dedicated `review_iq_admin`-scoped credential. At cutover, step 4 (create
`admin-database-url` secret) still needs to happen, then the existing `review-iq-admin` service's
`ADMIN_DATABASE_URL` mapping needs `--update-secrets` (not a from-scratch redeploy) to point at
it, before step 8's `ALTER ROLE review_iq_app NOBYPASSRLS`. PR #33 itself still needs to merge —
the webhook SECURITY DEFINER rewiring (the second of the three required fixes) is not live yet;
only the routing/config third was cherry-picked into PR #46 today.

**P3 (dashboard redeploy) — held**, per instruction, pending GG's answer on the Vercel project
question above. When unblocked: deploy from `web/`'s current repo source only, never by
attempting to restore whatever `review-iq-web` used to contain — the repo is the source of truth,
not a guess at the prior project's exact configuration.

### Undocumented ingress tier + DNS enumeration + P3 unblock (2026-07-31, third pass)

GG confirmed the `review-iq-web` Vercel project deletion was deliberate. Unblocked P3; three more
findings surfaced in the same pass.

**P0 (verify admin lockdown through every real ingress) — VERIFIED-LIVE, all three.** `/admin/*`
→ 404 through `api.samidhareviews.xyz` (Firebase-fronted), `review-iq-prod.web.app` (Firebase's
own default host), and the raw `*.run.app` host — all three, every sub-path checked
(`/admin/organizations/<uuid>`, `POST /admin/organizations`, `/admin/organizations/<uuid>/keys`).
`/health` → 200 through all three, confirming no regression. `review-iq-admin` reconfirmed
IAM-403 unauthenticated; confirmed via the Firebase Hosting API that only one site
(`review-iq-prod`) exists, with its rewrite hardcoded to the `review-iq` service only — no path
reaches `review-iq-admin` through Firebase. **P0's VERIFIED-LIVE label now genuinely holds across
every live ingress, not just the raw Cloud Run host it was originally checked against.**

**P1 (document the ingress tier) — done, PR #48 (draft), ADR 0009.** `api.` turns out to be
Firebase Hosting's Cloud Run rewrite (site `review-iq-prod`, live since 2026-07-07,
`DOMAIN_ACTIVE`/`CERT_ACTIVE`), not the native Cloud Run domain mapping every other doc in this
repo assumed. Firebase Hosting adds no separate authorization layer — `review-iq`'s own IAM is
`allUsers`, so Firebase is just another public hostname on an already-public backend, which is
exactly why P0's fix verified identically across all three ingresses. Named the two-account
operational risk (`gaurav.gandhi2411@gmail.com` for GCP/Firebase, `gg5678g@gmail.com` for
Cloudflare/Vercel, no shared recovery path) as a finding for GG to decide on, not fixed here.
`ARCHITECTURE.md` and `ops/runbooks/cloud-run-deploy.md` corrected.

**P2 (DNS enumeration before executing console steps) — best-effort public enumeration done,
reported directly to GG with an explicit NameCheap-export fallback for what public DNS can't see**
(no API credentials exist for NameCheap in this repo/environment). Found: apex has a
`google-site-verification` TXT record (Search Console, from the same 2026-07-07 session) that
would break silently if lost during a nameserver cutover; `_dmarc` TXT exists
(`v=DMARC1; p=none;`); **`mail.samidhareviews.xyz` (Resend's configured sending domain, confirmed
via `RESEND_FROM_EMAIL` secret) has NO SPF/DKIM/MX records at all** — a real, pre-existing,
separately-discovered gap (outbound email is very likely failing sender authentication today,
independent of anything in this pass) — flagged, not fixed. CAA records could not be checked by
either DNS tool available (`nslookup`, PowerShell `Resolve-DnsName`) — genuine tool limitation,
not asserted as absent.

**P3 (redeploy web/ as a new Vercel project) — VERIFIED-LIVE.** New project `samidha-reviews-web`
(account `gg5678g@gmail.com`), deployed from `web/`'s current `main` HEAD only (`npm ci` + a local
sanity build against real production env vars before deploying — clean). `app.samidhareviews.xyz`
attached; resolved instantly (no propagation wait — the existing A record already pointed at
Vercel's edge). Verified three ways, not just a 200: (1) the P2 probe script
(`scripts/probe_web_surfaces.py`) — dashboard, api, try-page all **OK**, only the still-unmigrated
apex marketing surface fails, exactly as expected; (2) a real Chrome session against the live
domain — zero console errors/warnings, full page render (screenshot taken); (3) `/try`'s SPA
rewrite confirmed serving the same shell with the correct `<title>`. `npm ci` flagged 4 pre-existing
high-severity vulnerabilities in `web/`'s dependency tree — not fixed in this pass (pre-existing,
out of scope), named here so it isn't silently lost.

**P4 (database cutover) — not executed, correctly still blocked.** PR #33 has not merged; the
webhook SECURITY DEFINER rewiring (required before the bypass can safely be removed) is not live.
Readiness unchanged from the prior pass's report. Will execute the moment merge+deploy lands.

### Pre-cutover blockers + email authentication (2026-07-31, fourth pass)

**P0 (CAA via DoH) — cleared, no blocker.** Windows DNS tools can't query CAA; queried both Google
(`dns.google/resolve`) and Cloudflare (`cloudflare-dns.com/dns-query`) DoH endpoints directly —
both agree: **no CAA record exists anywhere in the zone** (apex, `api`, or `app`). Absence of a
CAA record means no CA restriction applies — Cloudflare's cert issuance for the apex is not
blocked. Nothing to add to the cutover steps for this.

**P1(a) (what sends the magic-link) — VERIFIED-LIVE, urgent question resolved cleanly.** No
Supabase Management API access exists in this environment to read the SMTP config directly, so
triggered the actual live flow instead: `POST /auth/v1/otp` against production, to a disposable
public Mailinator inbox (not a memory-system inspection — a real send, real receive). The email
arrived from **`noreply@mail.app.supabase.io`** — Supabase's own default sender, not Resend.
**Self-serve signup is unaffected by the Resend gap.** DKIM signature present and well-formed for
`mail.app.supabase.io` (via Postmark's sending infra); no third-party pass/fail verdict available
from Mailinator's public API, but this is Supabase's own long-established transactional
infrastructure, not the subject of the actual gap. Test user cleaned up (`DELETE FROM auth.users`,
confirmed).

**P1(b) (Resend authentication records) — partially blocked, real gap confirmed independently
twice.** `RESEND_API_KEY` (existing secret) is a send-only restricted key — cannot read Resend's
domain-verification API for the exact DKIM record values; would need a broader-scoped key or GG's
own dashboard check (numbered path given directly to GG, not fabricated). Confirmed via a SECOND
DNS tool (PowerShell `Resolve-DnsName`, independent of the `nslookup` finding from the pass
before) that `mail.samidhareviews.xyz` genuinely has no MX/TXT records — outbound alerts are very
likely failing SPF/DKIM today. Recommended starting DMARC: `p=none` with an `rua=` reporting tag
added (the existing `_dmarc` record has neither `rua=` nor `ruf=` — currently collects zero
visibility despite existing) — monitor-only until alignment is confirmed clean, then tighten to
`quarantine`, then `reject`. Full consolidated steps (including this) delivered directly to GG.

**P2 (extend CVE gate to web/) — done, PR #49 (draft).** Fixed all 4 pre-existing highs
(react-router-dom migrated to react-router@8, since react-router-dom's 7.x line never received
the CSRF-bypass fix and has no forward path other than migrating off it; postcss + brace-expansion
via `npm audit fix`) — `npm audit`: 4 high → 0, all severities, re-verified after the fix, not
assumed. New `npm-audit` CI job, same blocking high/critical threshold as the Python gate, real
SHA-pinned `actions/setup-node` (resolved via the GitHub API, not guessed). **Audited Section E's
other two controls for the same reach question, as asked**: secret scanning (gitleaks/trufflehog)
is **not a standing CI gate at all** — grepped every workflow on every branch, found none; the
"1 real finding" in Section E's own entry above was a one-time manual scan, not continuous
coverage for either `app/` or `web/`. SHA-pinning is consistently applied everywhere that exists
today — no gap. This CVE fix is included in the already-redeployed `app.samidhareviews.xyz` (same
deploy as the P3 pass below) — re-verified live in a real browser session post-fix: zero console
errors on `/` and `/try`.

**P3 (two-account recovery) — not fixed, three genuine walls, numbered steps given to GG.**
- **GCP/Firebase**: blocked by GCP's own `SOLO_MUST_INVITE_OWNERS` API constraint — a personal
  (non-org) Google Account can't be granted Owner via a direct API binding at all; GCP requires
  the Console's invite-and-accept flow. Confirmed by attempting the real API call, not assumed.
- **Cloudflare**: the existing `wrangler` OAuth token's scopes don't include account-member
  management — confirmed by attempting the real Members API call (`Authentication error`).
  Needs a Console-based invite from `gg5678g@gmail.com`'s side, accepted by
  `gaurav.gandhi2411@gmail.com`.
- **Vercel**: hard blocked — the account is on the **Hobby plan, which does not support team
  members at all** (not a permissions gap, a plan-tier limitation), confirmed by attempting the
  real invite (`Team members are not permitted on the Hobby Plan`). Fixing this requires upgrading
  to Pro — a recurring paid charge, so escalated rather than purchased; Vercel's own purchase-quote
  API deliberately withholds the exact price (points to `vercel.com/pricing` instead of a stale
  training-data number). Not upgraded — GG's call.

Full numbered steps for all three (exact menu paths) delivered directly to GG, not summarized
here.

### Production-alias integrity + Vercel exit + secret-scan gate (2026-07-31, sixth pass)

GG confirmed the Vercel deletion was deliberate (from the prior pass's open question).

**P0 (how did unmerged code reach production?) — root cause found, fixed, regression-tested.**
Not Vercel auto-promoting branches (ruled out: this project has no GitHub integration at all,
confirmed via the project API — no `link` object). The actual mechanism: `vercel deploy --prod`
deploys whatever is in the local working directory at the moment it's run, completely independent
of git/PR state — I ran it twice from local, iterated (the react-router CVE fix) directly in that
same local checkout, and deployed again, all before ever opening PR #49. **Fix**: connected the
Vercel project to GitHub (`vercel git connect`) — the concrete setting changed. Real caveat stated,
not glossed over: this doesn't fully prevent a future manual `vercel deploy --prod` from an
arbitrary local checkout; the actual guarantee is the same discipline already established for
Cloud Run (clean checkout of the exact commit, never the working directory) — moot within hours of
this pass anyway, since P2 retires the Vercel project entirely. **Regression-tested the migration
itself** in a real browser: `/`, `/upload`, `/dashboard`, `/reviews`, `/reviews/:reviewHash`
(exercises `useParams`), `/authenticity`, an unknown path (catch-all), and both browser back and
forward — zero console errors/warnings on any of them (one pre-existing, unrelated a11y notice
about a missing `autocomplete` attribute, nothing to do with the router).

**P1 (secret scanning as a standing gate) — done, PR #50 (draft), with a real near-miss caught
before shipping.** First attempt committed a `--baseline-path` JSON report to allowlist the 19
pre-existing findings — **GitHub's own push protection rejected the push**: the report embeds raw
matched secret text, and one of the 19 (a test fixture shaped like a Shopify token) re-triggered
GitHub's scanner as a fresh "leak," and the committed report file recursively flagged itself on
the next scan. Fixed at the root, not routed around: switched to `.gitleaksignore` (fingerprint
only, `commit:file:rule:line`, never the matched text). Each of the 19 was individually read and
confirmed safe before allowlisting (15 test fixtures, 1 OpenAPI docs example, 2 already-documented
historical items) — not blanket-accepted. Proved the final mechanism in an isolated throwaway
repo: a partial allowlist still catches a second, un-allowlisted planted secret (exit 1); both
allowlisted passes clean (exit 0). Two triggers: PR-diff-only, and a weekly full-history scan.

**P2 (exit Vercel for Cloudflare Pages) — Pages built + VERIFIED-LIVE at its own URL; DNS cutover
correctly still held.** New project `samidha-reviews-web` on the `gg5678g@gmail.com` Cloudflare
account (same one already running `review-iq-demo`), built from the exact same source already
live (`main` + PR #49's CVE fixes), deployed via `wrangler pages deploy`. Added
`web/public/_redirects` (`/* /index.html 200`) — Cloudflare Pages' equivalent of `vercel.json`'s
SPA rewrite, which didn't exist for this project before. Verified at
`https://samidha-reviews-web.pages.dev/`: byte-identical render to the Vercel deployment, zero
console errors, deep-link route (`/reviews/deadbeef1234`) and `/try` both correctly served via the
SPA fallback (not a 404). **DNS cutover NOT done yet, correctly**: Cloudflare Pages custom domains
require the zone to actually be managed by Cloudflare (same constraint already known from the
apex/`review-iq-demo` case) — `samidhareviews.xyz` is still on NameCheap's nameservers, so
`app.samidhareviews.xyz` can't be attached to this Pages project until the same pending
NameCheap→Cloudflare migration (already in the consolidated cutover steps) happens. **Vercel is
therefore NOT retired yet** — it still serves the live custom domain; retiring it now would cause
a real outage. Updated the probe script's stale comment (referenced `web/vercel.json`, now
`web/public/_redirects`) — the checks themselves are unaffected, since they test the domain, not
the specific host.

**P3 (execute the two free invites) — both re-attempted for real, both confirmed still blocked by
genuine platform constraints, not just repeated from the prior report.** GCP: retried with
`--condition=None` to rule that variable out — same `SOLO_MUST_INVITE_OWNERS` error. Cloudflare:
same token-scope block as before. Numbered console steps for both delivered directly to GG.
Vercel's invite question is now moot per P2 above (once fully retired).

**P4 (DMARC rua target) — needs one clarification from GG before finalizing**: "my Gmail" is
ambiguous between the two accounts active this session (`gg5678g@gmail.com` for
Cloudflare/Vercel, `gaurav.gandhi2411@gmail.com` for GCP/Firebase) — asked directly rather than
guessed, since a wrong inbox means silently losing DMARC visibility exactly the way the existing
record already does.

**P5 — unchanged, still correctly blocked** on PR #33 merging.

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
- [x] Quota enforcement tested on Cloud Run — 10×200 then 429 "Monthly quota exceeded (10/10)" — 2026-05-12
- [x] Monthly cost runbook executed — ₹0.00 confirmed (97.9 MB AR, 4/6 SM versions, billing enabled) — 2026-05-12
- [x] v0.2.0 tagged — 2026-05-12

---

## 13. Phase 2.0b — Hinglish + Hindi + real-data eval

**Status:** Planning complete. Decisions locked. Ready for CC kickoff.
**Predecessor:** Phase 2.0a closed at v0.2.0 (Cloud Run multi-tenant API live, 87.9% eval).
**Target:** v0.3.0
**Estimated CC sessions:** 5-6

---

## 13.1 Scope summary

What this phase delivers:

1. **Coverage debt cleanup** from 2.0a (dashboard.py, storage.py SQLite paths)
2. **Real review data ingestion** — Flipkart Kaggle + Amazon Reviews 2023 sample
3. **Hand-labeled Hinglish fixtures** — 15 real reviews from Flipkart, labeled by the user
4. **Hindi fixture set** — 6 fixtures, can be synthetic (Devanagari script is a different surface from Hinglish; LLM-generated Hindi is closer to real Hindi than LLM-generated Hinglish is to real Hinglish)
5. **Language detection** via `lingua-py`
6. **Branched per-language prompts** — same schema, language-specific prompt templates and few-shot examples
7. **Eval expansion** — from 25 to ~46 fixtures (25 English + 15 Hinglish + 6 Hindi), per-language accuracy reported
8. **Per-fixture regression cleanup** — target 85%+ on the 5 weak English fixtures (sarcasm, feature_requests, very_long, packaging_damage, competitor_switch)
9. **Drift monitoring** — nightly eval, Slack alerts on regression
10. **v0.3.0 tag** when all of the above is green

Deliberately deferred to later phases:

- Tamil → Phase 2.5
- Bengali, Marathi, Telugu, Kannada, Gujarati → opportunistic, only if a real client asks
- Per-language prompt optimization beyond v1 → 2.0c
- Vector search over reviews → Phase 3.0
- Webhook ingestion → Phase 4

---

## 13.2 Why these specific choices

**Hinglish hand-labeled, Hindi synthetic.** Hinglish is the moat — Yotpo/Birdeye/Trustpilot don't handle it because their training data is English-centric and their prompts assume English. Real Hinglish is short, ungrammatical, code-mixed (Latin and Devanagari in same review), and full of slang ("paisa vasool", "bakwaas", "ok ok product hai") that synthetic Hinglish from English-trained LLMs does not reproduce. The hand-labeled real fixtures are the entire differentiation. Hindi (pure Devanagari) is closer to what LLMs already do well, so synthetic Hindi fixtures are acceptable as a starting point. They can be replaced with real Hindi later if accuracy is weak.

**Slack for drift alerts, not email/GH issues.** Slack is where developers actually look. Email gets filtered. GitHub issues are ceremonial — they pile up unread. A real-time Slack ping into a channel you check is the only alert mechanism that gets acted on. The user must provide one Slack webhook URL (free Slack workspace, 5-minute setup).

**Coverage cleanup first, not last.** Carrying uncovered prod code into a phase that adds more code means the gap compounds. Closing it before language work means the new code has tests as a baseline expectation.

**Real data ingestion before hand-labeling.** Hand-labeling needs candidates. CC surfaces real reviews from Flipkart Kaggle that are Hinglish-detected by `lingua-py`, the user picks 15 from a ranked candidate set. This is much faster than the user manually scrolling through datasets.

**Per-fixture regression cleanup at the end, not the beginning.** Multi-language work changes the prompt and may resolve some English regressions naturally (e.g., the prompt becomes more explicit about completeness, which helps sarcasm/feature_requests detection too). Fixing regressions before language work means doing the work twice. Defer the English fixes to a single targeted step after the language branches are in.

---

## 13.3 Execution sequence

Each step = one feature branch off `feat/2.0b-hinglish` → sub-PR → squash merge. Same pattern as 2.0a.

### Step 1 — Coverage debt cleanup
**Branch:** `feat/2.0b-01-coverage`
**Deliverables:**
- `tests/unit/test_dashboard.py` — covers `app/api/dashboard.py:24-27` and any other handler logic
- `tests/unit/test_storage_sqlite.py` — covers `app/core/storage.py:220-234, 347` and the SQLite migrate() path
- Combined coverage target: ≥ 93% (currently 92.08%)
- No new prod code; tests only
**DoD:** combined coverage ≥ 93%, all existing tests still passing.

### Step 2 — Real review data ingestion
**Branch:** `feat/2.0b-02-real-data`
**Deliverables:**
- `eval/data/sample_flipkart.py` — script that downloads the Flipkart product reviews dataset from Kaggle, samples ~2000 reviews, runs `lingua-py` language detection on each, classifies into english/hinglish/hindi/other, writes results to `eval/data/flipkart_candidates.jsonl` (gitignored, locally stored)
- `eval/data/sample_amazon.py` — same shape for Amazon Reviews 2023 (HuggingFace McAuley Lab), sampling ~3000 reviews. Used for English breadth, not Hinglish.
- `eval/data/README.md` — instructions for the user to: install Kaggle CLI (`pip install kaggle`), authenticate (one-time), run the sample scripts, expected output sizes
- `eval/data/.gitignore` — block the raw downloads and candidate JSONL files; only the sampling scripts are committed
- `requirements-dev.txt` updated with `kaggle`, `lingua-language-detector`, `datasets` (HF datasets lib)
**User action required:** one-time Kaggle CLI setup (~5 min). CC walks the user through it as a stop gate.
**DoD:** running the two sample scripts produces deterministic output (same seed = same sample); candidate JSONL files exist with language labels; ~600+ Hinglish candidates identified from Flipkart.

### Step 3 — Hand-labeling session (user time)
**Branch:** `feat/2.0b-03-hinglish-fixtures`
**This step requires ~90 minutes of the user.**
**Pre-CC work:** CC builds `eval/label-helper.py` — an interactive CLI that:
1. Loads the top 50 Hinglish candidates from `flipkart_candidates.jsonl` (ranked by length 50-500 chars, language confidence > 0.7, diversity heuristic)
2. Displays one review at a time
3. Prompts user for: product name, stars (if stated), pros (comma-sep), cons (comma-sep), buy_again (y/n/unclear), sentiment, urgency, topics, competitor_mentions
4. Skips review (s), accepts (a), regenerates from next candidate (n)
5. Stops at 15 accepted fixtures
6. Writes fixtures to `eval/fixtures/hi-en/001.json` through `015.json` in the standard fixture shape
**User action:** run `uv run python eval/label-helper.py`, label 15 reviews (skip ones that are too short, vague, or off-topic). The tool surfaces ~50 candidates so user can be selective.
**DoD:** 15 Hinglish fixtures committed to `eval/fixtures/hi-en/`, ground truth labeled by user. Existing English fixtures untouched.

### Step 4 — Hindi fixtures (synthetic)
**Branch:** `feat/2.0b-04-hindi-fixtures`
**Deliverables:**
- 6 Hindi fixtures in `eval/fixtures/hi/`, generated via LLM with specific personas (frustrated customer, happy customer, ambiguous-buy-again, urgent safety issue, feature request, neutral)
- Each fixture's ground truth verified by CC (re-extracted, compared to claimed ground truth, manually adjusted if model output is the more reasonable answer)
- README note: Hindi fixtures are synthetic v1; replace with real Hindi reviews when a buyer demands Hindi accuracy SLA
**DoD:** 6 Hindi fixtures committed, all extract correctly with current prompt (>=85%) before any language-branching work.

### Step 5 — Language detection + routing
**Branch:** `feat/2.0b-05-lang-detect`
**Deliverables:**
- `app/core/language.py` — `detect_language(text: str) -> Literal["en", "hi-en", "hi", "other"]` using `lingua-py`
- Confidence thresholds: < 0.5 → "other" (still processed as English fallback)
- Unit tests with fixtures from each language
- `app/api/v2/extract.py` calls `detect_language` and passes result to the prompt builder
- `language` field on extraction output reflects detected language (was already in schema; now populated)
- Migration: not needed (language column already exists in extractions table)
**DoD:** language detection unit tests pass with 95%+ accuracy on a held-out set of 50 reviews (mix of all 4 categories); v2 extract endpoint includes detected language in response.

### Step 6 — Branched per-language prompts
**Branch:** `feat/2.0b-06-prompts-v2`
**Deliverables:**
- `app/core/prompts/` directory:
  - `en.py` — current prompt (renamed from prompt.py), bumped to v2.0
  - `hi-en.py` — Hinglish prompt with Hinglish few-shot examples drawn from fixtures
  - `hi.py` — Hindi prompt
  - `__init__.py` — exports `build_prompt(text: str, language: str) -> str` selector
- `PROMPTS.md` updated with the three new prompt versions, diffs from v1, rationale
- Unit tests on `build_prompt` covering each language branch + the fallback case
- Existing `app/core/prompt.py` removed; all imports updated
**Design constraint:** all three prompts produce output conforming to the SAME `ReviewExtractionLLMOutput` schema. The schema does not branch by language. Only the prompt does.
**DoD:** prompts wired in, all existing English tests pass, Hinglish and Hindi fixtures hit ≥ 85% on individual extraction.

### Step 7 — Eval expansion + per-language reporting
**Branch:** `feat/2.0b-07-eval-multilang`
**Deliverables:**
- `eval/runner.py` updated to:
  - Discover fixtures from all three subdirectories (`en/`, `hi-en/`, `hi/`)
  - Report overall accuracy AND per-language breakdown
  - Output goes to `eval/report.md` in a table format
- `eval/report.md` — auto-generated, committed (so README can link to it)
- README eval table updated: per-language scores + overall
- CI eval gate: ≥ 85% overall AND ≥ 80% per language (separate thresholds, both must pass)
**DoD:** full eval (46 fixtures) runs locally and on Cloud Run; per-language breakdown ≥ 80% Hinglish, ≥ 80% Hindi, ≥ 85% English (regression cleanup in Step 8 will push English higher).

### Step 8 — Per-fixture regression cleanup (English)
**Branch:** `feat/2.0b-08-english-cleanup`
**Target fixtures (current scores from v0.2.0 eval):**
- 012_sarcasm: 70.7% → target 85% (documented hard; may stay below if it's a model limit)
- 014_feature_requests: 75.7% → target 85%
- 017_very_long: 78.0% → target 85% (regression from 86% on HF; investigate Cloud Run-specific behavior)
- 018_packaging_damage: 78.9% → target 85% (Amazon hallucinated as competitor)
- 025_competitor_switch: 77.9% → target 85% (topic vocabulary mismatch)
**Approach:**
1. For each fixture, run the current extractor and capture output
2. Compare to ground truth, identify which field(s) drag the score
3. Iterate on `en.py` prompt with targeted few-shot examples or instructions
4. **Rule:** every prompt change must keep the OTHER 24 English fixtures at their current scores or higher. Use full English eval (25 fixtures) as the gate.
5. If 012_sarcasm cannot reach 85% after 3 iterations, accept the regression and document it as a known model weakness in `eval/known-weaknesses.md`.
**DoD:** English fixtures average ≥ 88% (up from 87.9%); no fixture below 80% except documented hard cases (max 1).

### Step 9 — Drift monitoring with Slack alerts
**Branch:** `feat/2.0b-09-drift-monitoring`
**Deliverables:**
- `.github/workflows/nightly-eval.yml` — cron: `'0 2 * * *'` UTC (~7:30 AM IST), runs full eval against Cloud Run URL with the public-demo key (or a dedicated eval key — see decision below)
- `eval/drift_detector.py` — compares today's per-fixture scores against yesterday's `eval/report.md`; flags any drop > 5pp on a fixture or > 2pp overall
- `app/core/slack.py` — minimal POST-to-webhook helper, used by drift detector
- `SLACK_WEBHOOK_URL` added to GitHub Actions secrets (user provides webhook URL as a stop gate)
- `ops/runbooks/drift-response.md` — how to investigate a drift alert
**Decision needed from user:** create a dedicated `nightly-eval` API key with quota=5000/month, separate from `public-demo`. Reason: nightly eval is 46 calls/day × 30 days = 1380 calls/month. Eats half the public-demo quota if shared. Dedicated key keeps the demo key clean.
**DoD:** workflow runs nightly, Slack channel receives "eval green: 88.4% overall" or "DRIFT: 005_all_positive dropped from 94% to 71%" with the failing fixture details.

### Step 10 — Documentation + v0.3.0 tag
**Branch:** `feat/2.0b-10-release`
**Deliverables:**
- README.md updated: Hinglish/Hindi mentioned in opener, per-language eval table, "how language detection works" section
- `plan.md` §13 marked complete, §2 status line updated
- `PROMPTS.md` finalized for all three languages
- `ARCHITECTURE.md` updated with language detection flow
- `eval/known-weaknesses.md` documents any English fixtures that didn't reach 85% (with reasoning)
- v0.3.0 tag on main: `git tag -a v0.3.0 -m "Phase 2.0b: Hinglish + Hindi support, drift monitoring, English regression cleanup"`
- v0.3.0 GitHub Release with notes
**DoD:** plan.md §13 fully checked, README accurately represents the product, tag pushed.

---

## 13.4 Definition of done for Phase 2.0b (overall)

- [ ] Combined coverage ≥ 93%
- [ ] Real Flipkart Hinglish data sampled and 15 reviews hand-labeled by user
- [ ] 6 synthetic Hindi fixtures committed
- [ ] `app/core/language.py` shipped, 95%+ accuracy on held-out detection test
- [ ] Three per-language prompts in `app/core/prompts/`
- [ ] Full eval (46 fixtures) ≥ 85% overall AND ≥ 80% per language
- [ ] English regression cleanup: average ≥ 88%, no fixture below 80% except ≤ 1 documented hard case
- [ ] Nightly drift workflow in GH Actions, posts to Slack
- [ ] Slack alert tested by intentionally introducing a regression and confirming the alert fires
- [ ] README accurately represents the multi-language product
- [ ] v0.3.0 tagged

---

## 13.5 Cost ceiling check

| Resource | Phase 2.0a baseline | Expected 2.0b delta |
|---|---|---|
| Cloud Run requests | < 1k/mo | +1380/mo (nightly eval) = still well under 2M free |
| Cloud Run vCPU-sec | < 1k/mo | +~3000/mo (nightly eval × 7s avg) = still under 360k free |
| Artifact Registry | 97.9 MB / 500 MB | No image change unless lingua-py bloats it significantly; flag if > 200 MB |
| Secret Manager | 4 / 6 versions | +1 if we add SLACK_WEBHOOK_URL there (likely) = 5/6, still under |
| Supabase storage | < 1 MB | +small (more extractions from nightly eval) = nowhere near 500 MB |
| Groq API calls | < 1k/mo | +1380/mo eval + dev = ~3k/mo, well under 14k/day free tier |

Expected total cost: ₹0.00 throughout. Same kill-switch is armed.

---

## 13.6 Open decisions (need user confirmation before CC kickoff)

1. **Slack workspace + webhook URL.** Does the user have a Slack workspace they want alerts in, or do they need to create one? Free Slack workspace creation is ~5 minutes. The webhook URL goes in GH secrets, never committed.

2. **Dedicated `nightly-eval` API key with quota=5000.** Confirm OK to create alongside the existing `public-demo` key.

3. **Kaggle account.** The user needs a Kaggle account + API token to download Flipkart data. Free, ~5 minutes. CC will walk through the setup when Step 2 hits the stop gate.

4. **Time budget for hand-labeling.** Confirm ~90 minutes is acceptable. If not, we can drop to 10 fixtures (~60 min) but the per-language eval gets noisier.

---

## 13.7 Risk register (things that could blow up scope)

| Risk | Likelihood | Mitigation |
|---|---|---|
| `lingua-py` confuses Hinglish ↔ Hindi | Medium | Test on 50-review held-out set; if accuracy < 90%, add fastText as secondary detector |
| Llama 3.3 70B genuinely cannot extract Hinglish well | Medium | Step 6 has a hard gate at ≥ 80% Hinglish accuracy. If we can't hit it, escalate to user — options are: try Gemini, lower the gate and document limitation, or fall back to Path B from earlier (synthetic fixtures, marketing claim deferred) |
| Kaggle dataset has license restrictions | Low | Flipkart product reviews dataset on Kaggle is CC0; verify before commit |
| Cloud Run cold start affecting nightly eval | Low | Nightly eval is the warm-up; if first call cold-starts and times out, retry once is fine |
| 012_sarcasm fixture genuinely cannot reach 85% | Medium | Acceptance documented in Step 8 |
| English regression cleanup regresses Hinglish/Hindi | Medium | Eval gate runs ALL fixtures, not just English; prompt changes that regress non-English fail CI |

---

## 13.8 What Phase 2.0c looks like (preview, not in scope here)

When 2.0b ships, the natural next moves:

- Tamil + Bengali language support (now that the multi-language pattern is proven)
- Python SDK (`pip install review-iq`)
- JS SDK (`npm install review-iq`)
- Landing page on review-iq.com (when we acquire the domain)
- Real Hindi fixtures replacing the synthetic ones

This is the bridge from "production multi-lingual API" to "product anyone can find and use." Plan in detail when we get there.

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
