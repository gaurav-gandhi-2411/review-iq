# ADR 0005: Brand Gazetteer Vetoes spaCy PERSON-NER Before PII Redaction

**Status:** Accepted
**Date:** 2026-07-31
**Scope:** Wave 1 Section E (PII redaction) follow-up fix. Closes a specific bug found in
`app.core.sanitize._redact_names_ner` after the section's original gate work landed (PR #27):
`en_core_web_sm` misclassifies common brand/product names as `PERSON` on short, informal
review text, and the redactor had no mechanism to distinguish "this is a real person's
name" from "this is a brand spaCy got wrong." Does not touch any other Wave 1 section.

## Context

`_redact_names_ner()` already correctly filters spaCy's NER output to `label_ == "PERSON"`
only — `ORG`/`PRODUCT` entity types were never redacted, and that part of the design was
never the bug. The actual bug is that `en_core_web_sm`, an English small model tuned on
general-domain text, frequently tags a brand name as `PERSON` when it appears in the kind
of short, comparative sentence a review is made of ("go with a Dyson instead," "my old
Shark vacuum," "better than my old Bose ones"). Reproduced directly, pre-fix:

```
"...if I had to choose again, I would go with a Dyson instead." -> "...a [NAME_1] instead."
"Compared to my old Shark vacuum..."                            -> "Compared to my old [NAME_1] vacuum..."
"...better than my old Bose ones."                               -> "...better than my old [NAME_1] ones."
```

A fourth case, from the existing eval fixture set (`eval/fixtures/013_multi_product.json`,
`eval/fixtures/017_very_long.json`), is a fictional earbuds brand: `en_core_web_sm` tags
"NovaPod" inconsistently — `ORG` in one sentence, `PERSON` in another, in the same review
text — confirmed live against the installed model:

```python
>>> nlp("Bought the NovaPod X earbuds along with the NovaPod charging case.").ents
[('NovaPod', 'ORG'), ('NovaPod', 'PERSON')]
```

This is not a cosmetic accuracy nuance. `competitor_mentions` is a shipped, contracted
schema field (`app.core.schemas.ReviewExtraction` / `ReviewExtractionV2`) — it is part of
the product's advertised output, the exact thing a customer pays to have extracted from
their reviews ("what competitors do my customers mention?"). When redaction fires on a
brand name before the LLM ever sees the review text, the LLM cannot extract that brand into
`competitor_mentions` no matter how good the prompt is — the information is gone from the
input. Redacting a brand is therefore a **functional regression** in a contracted API
output, not merely a small accuracy hit to be tolerated. The privacy control (PERSON
redaction) and the extraction contract (`competitor_mentions`) were operating on
overlapping, undisambiguated entity classes, and the underlying NER model's own
classification is not reliable enough at that boundary to be trusted un-checked.

## Decision

1. **A brand/product-name gazetteer vetoes any PERSON-tagged NER span before redaction.**
   In `_redact_names_ner()`, a span is redacted only if `label_ == "PERSON"` **and** its
   text (case-insensitively) is not in the combined gazetteer. A gazetteer hit means the
   span is treated exactly as if NER had never flagged it — left in the original text,
   never added to the `RedactionMap`. This is the mechanism that keeps the two entity
   classes (privacy-redacted PERSON vs. contract-extracted ORG/PRODUCT/competitor names)
   structurally disjoint at the point where the unreliable classifier's output is consumed,
   rather than relying on the classifier to have drawn that line correctly itself.

2. **Two real sources, no fabricated catalog.** There is no per-org product-catalog
   feature in this codebase today, so none was invented for this fix:
   - `_STATIC_BRAND_GAZETTEER` (`app/core/sanitize.py`) — a curated, ~130-entry Python
     `frozenset` of common consumer brands relevant to Indian e-commerce reviews
     (appliances, audio, mobile/electronics, marketplaces, personal care, footwear).
     Explicitly documented in-code as a starting list, not exhaustive.
   - `list_known_brand_names_pg()` (`app/core/storage_pg.py`) — `SELECT DISTINCT
     jsonb_array_elements_text(competitor_mentions) FROM public.extractions WHERE
     competitor_mentions IS NOT NULL`. Confirmed live against production: 10 distinct
     values today (`Amazon, Boat, Bose, Dyson, JBL, Philips, Roomba, Shark, iRobot`, plus
     one noise value `"same model"`). This is a **cross-org query** — brand names are not
     tenant-sensitive data (not PII, not a competitive secret; by definition a name the
     product already extracted correctly once), so it deliberately does not call
     `_set_tenant()`, matching the existing `list_orgs_with_dated_extractions_pg` pattern
     and its documented justification style. Every future org's successfully-extracted
     competitor mention organically grows this list with zero manual curation — this is
     the intended long-term coverage mechanism, not the static list.

3. **Combined, cached once per process.** `_get_brand_gazetteer()` merges both sources
   into one case-insensitive `frozenset`, behind `@lru_cache(maxsize=1)` — the exact same
   pattern `_get_ner_pipeline()` already uses for the (also slow-to-init) spaCy model. The
   DB half is loaded once per process lifetime, not live-updated; a brand mentioned for the
   first time after process start is not vetoed until the next deploy/restart. Accepted
   tradeoff: brand names don't change fast enough for this to matter in practice, and this
   avoids adding a live DB round-trip to every single redaction call.

4. **Graceful degradation, matching `_get_ner_pipeline`'s own contract.** If the DB call
   fails (no DB configured — the default in unit-test contexts — or a live connectivity
   issue), `_get_brand_gazetteer()` catches the exception, logs
   `sanitize.brand_gazetteer_db_load_failed`, and falls back to the static list alone.
   Redaction must never crash extraction over a gazetteer refresh failure.

## Evidence — measured accuracy-delta re-confirmation

`scripts/measure_redaction_accuracy_delta.py` (paired ON-vs-OFF fixture scoring, bootstrap
95% CI on the mean paired difference) was re-run after this fix, with 12 live Groq calls
made to close cassette gaps created specifically by the fixed redactor's now-different
output (see script docstring for the full paired-design rationale):

- 12 live calls, all served by `llama-3.1-8b-instant` (no escalation, no degradation):
  17,900 input + 1,460 output tokens total, ≈$0.001 at Groq's published per-token rate for
  that model (no `pricing.py` exists on this branch to cite instead — flagged as a gap,
  not fabricated).
- 44/49 fixtures paired (up from 30/49 pre-fix). The remaining 5
  (`001_turbo_vac`, `007_buy_again_ambiguous`, `008_pii_heavy`, `010_urgent_angry`,
  `015_medium_urgency`) are blocked in **both** arms — a pre-existing cassette gap that
  predates this fix and is unrelated to it (their raw, un-redacted text has never had a
  cassette recorded), left untouched as out of scope here.
- **Paired mean delta (ON − OFF): −0.78pp, 95% CI [−2.16pp, +0.35pp]** — under the 1pp
  gate from the original Wave 1 Section E measurement plan. This confirms the fix did not
  merely feel better anecdotally; it holds up against the eval set.

## Consequences

**Positive:**
- The three reproduced bug-report sentences (Dyson/Shark/Bose) and the `009_competitor_heavy`
  eval fixture (Dyson, Shark, iRobot, Roomba all covered by the DB-sourced gazetteer) no
  longer lose their competitor mentions to redaction — closes the functional regression
  in `competitor_mentions` described above.
- The DB-sourced half means coverage grows automatically as the product is used, with zero
  ongoing manual maintenance of the static list.
- Both new code paths (`_get_brand_gazetteer`, `list_known_brand_names_pg`) are unit
  tested, including the graceful-degradation-on-DB-failure path and the static-audit
  cross-tenant test (`tests/integration/test_adversarial_cross_tenant.py`) that would
  otherwise flag `list_known_brand_names_pg` as an undocumented cross-org gap.

**Negative / residual risk:**
- **NovaPod is not fixed and cannot be, by this mechanism.** It is a fictional brand
  invented for the eval fixture set — it has no real-world presence, so it can never
  legitimately appear in the static list (which is curated from real consumer brands) or
  in `competitor_mentions` history (nothing has ever genuinely extracted it, since it does
  not exist as a real product). `013_multi_product` and `017_very_long` therefore still
  redact "NovaPod" post-fix — this is documented in code
  (`tests/unit/test_sanitize.py::TestBrandGazetteerVetoesPersonNer::test_novapod_residual_gap_not_covered_by_gazetteer`,
  written to fail loudly the day this gap is closed) rather than silently claimed as
  solved. Closing it would require either a per-org product catalog (the clean extension
  point `_get_brand_gazetteer()` is designed for, not built here) or replacing/fine-tuning
  the NER model — both out of scope for this fix.
- The static gazetteer is a curation exercise and will always miss some real brands not
  yet seen in production `competitor_mentions` data and not anticipated by the curator.
  This is an accepted, disclosed limitation, not a claim of completeness.
- The process-lifetime cache means a brand's first-ever mention in production still
  redacts on that occurrence (the DB doesn't have it yet) and only stops redacting after
  the next deploy/restart re-loads the gazetteer. This is the same tradeoff
  `_get_ner_pipeline()` already accepts for model loading, extended to this data source.
- 5 fixtures remain permanently excluded from the accuracy-delta measurement due to the
  pre-existing both-arms cassette gap noted above — the 44/49-fixture delta is not a
  full-49-fixture measurement, and this ADR does not claim it is.

## Alternatives considered

1. **Swap `en_core_web_sm` for a larger/better-tuned NER model.** Rejected for this fix —
   larger scope (model evaluation, latency/cost impact, redeployment), no guarantee a
   bigger general-purpose model reliably distinguishes brand names from person names on
   short informal review text either (the underlying ambiguity — "Dyson" the vacuum vs.
   "Dyson" a surname — is a genuinely hard NER problem, not just a model-size problem).
   The gazetteer is a targeted, cheap, auditable fix for the specific failure mode observed
   in production-realistic text; a model swap is a larger, separate investment that could
   still be pursued later without conflicting with this change.
2. **Fabricate a placeholder product-catalog table** so the fix looks more "complete."
   Rejected — this codebase has no product-catalog feature; inventing one purely to backfill
   this fix would be dishonest scaffolding (a table with no real write path, immediately
   stale) and was explicitly out of scope for this change. The two real sources used here
   (static list + `competitor_mentions` history) are both genuinely populated today.
3. **Disable PERSON redaction entirely for short reviews below some length threshold**
   (the theory being brand mentions cluster in short comparative sentences). Rejected —
   this would also disable genuine short-review name redaction (`"Rajesh was rude,"` a
   short sentence), trading one false-negative class for another with no net improvement
   and no gazetteer-style auditability of what specifically got vetoed and why.
4. **Manually hardcode NovaPod (and other fixture-only fictional names) into the static
   gazetteer** to make the "known reproduction cases" test suite fully green. Rejected —
   this is not a real brand; adding it would misrepresent the static list as containing
   only real, curated consumer brands (its own in-code documentation states exactly that)
   purely to pass a test, which is the same class of dishonesty CLAUDE.md rule 53 and this
   repo's existing eval-gate ADR (0001) both explicitly reject. The residual gap is
   disclosed and tested instead.
