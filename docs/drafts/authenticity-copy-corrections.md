# DRAFT - not approved - do not publish

Task S17 F3c, revision 2 (2026-10-07, after GG decisions relayed by the orchestrator). Proposed
corrections to legal, data-ownership and API copy that still describe authenticity (fake-review)
scoring after the surface was removed (PR #217 removed `/v2` authenticity routes; PR #251, now
merged, removed `/bff/authenticity*`, the web pages, `include_authenticity` on CSV ingest and the
0.30 health-score term). Reason for removal: the flag is unmeasurable (ADR 0030 context, S15d D5;
`docs/compliance.md` accuracy note). The `authenticity_audits` table and its historical rows
remain; no DDL planned.

Baseline: line numbers in the per-document sections are from `origin/main` @ cf3b425; this branch
has since merged `origin/main` (merge commit 274f7b6) and the legal/ and data-ownership files were
unchanged by that merge (verified by `git diff cf3b425 HEAD --stat`), so those line numbers still
hold. Nothing here is published or applied; GG approves final text. Wording is a proposal.

Status of decisions (GG, relayed by the orchestrator): Q1-Q8 are DECIDED and the decided wording
replaces the earlier open-question form (see "Decisions" near the end). Q5 (API) is implemented on
main. Sections A (Gemini retirement) and B (per-user `last_seen_at`) are NEW and are for GG to
approve; I do not decide them.

Classification key: **W** = wording fix (text made true to current behavior, no change to what we do
with data); **S** = substantive (a processing purpose, output or data category is described as
removed, or a disclosed list changes; may raise a customer-notice question for GG); **S?** = likely
substantive, depends on a fact I could not verify.

## Summary table

| # | File:line | Class | Action |
|---|---|---|---|
| 1 | legal/terms-of-service.md:23 | S? | Remove "authenticity scoring" from service description |
| 2 | legal/terms-of-service.md:94 | W | Keep ownership of historical scores, drop forward-looking wording |
| 3 | legal/terms-of-service.md:135,137 | W | Remove authenticity outputs from warranty disclaimer |
| 4 | legal/privacy-policy.md:33 | S? | Drop "authenticity score/label/flags" from output category |
| 5 | legal/privacy-policy.md:36 | S | Keep row, reword as historical (decided Q3/Q4) |
| 6 | legal/privacy-policy.md:57 | W | Drop "and authenticity signal" |
| 7 | legal/privacy-policy.md:68 | W | Reword stored-data list (historical records only) |
| 8 | legal/dpa-template.md:22 | S | Remove purpose "authenticity scoring" |
| 9 | legal/dpa-template.md:35 | S | Remove from nature of processing |
| 10 | legal/dpa-template.md:36 | S | Remove "authenticity signals" from purpose |
| 11 | legal/dpa-template.md:37 | S | Reword storage item (3) |
| 12 | legal/dpa-template.md:110 | S | Groq data row |
| 13 | legal/dpa-template.md:112 | S | Supabase data row |
| 14 | legal/dpa-template.md:152-153 | S | Audit-trail control no longer a current control |
| 15 | legal/sub-processors.md:30 | S | Groq "what it processes" |
| 16 | legal/sub-processors.md:55 | S | Supabase "what it processes" |
| 17 | legal/sub-processors.md:76 | S? | Resend alert types: say "not active", not "removed" (decided Q6) |
| 18 | legal/data-retention-and-deletion.md:77 | none | KEEP (cascade list still true) |
| 19 | legal/data-retention-and-deletion.md:91 | S | Retention row: keep, mark historical, retained until purged (decided Q3) |
| 20 | legal/compliance-posture.md:37 | W | Remove "supports IS 19000" claim, keep no-certification statement |
| 21 | docs/data-ownership.md:18 | none | KEEP (RLS still covers the table) |
| 22 | docs/data-ownership.md:55,59 | W | Mark table historical |
| 23 | docs/data-ownership.md:63 | W | Follows dataset API decision |
| 24 | app/api/v2/dataset.py:42,49 | API | See API section |
| 25 | app/core/dataset/builder.py:1,111,116,129,201,262 | API | See API section |
| 26 | app/core/corrections/schema.py:11,32 | API | See API section |
| 27 | app/api/v2/corrections.py:92 | API | See API section |
| 28-30 | README.md, SECURITY.md, docs/compliance.md | covered by #251? | Reconcile against #251 diff |

Hit counts per file (distinct locations): terms-of-service 3; privacy-policy 4; dpa-template 7
(lines 152-153 are one sentence); sub-processors 3; data-retention-and-deletion 2 (1 keep, 1
change); compliance-posture 1; data-ownership 4 line groups (1 keep); dataset.py 2; builder.py 6;
schema.py 2; corrections.py 1; README.md 5 lines (260, 372-377); SECURITY.md 4 lines
(119-129); docs/compliance.md about 9 lines (1, 3, 5, 11, 15, 48, 60, 68-69, 76).

Overlap with #251: its commit titles show it already edits README, SECURITY.md and
docs/compliance.md ("record the removal of the authenticity surface"). On `origin/main` those say
`include_authenticity` and the dashboard views are "unchanged", which #251 makes false. Items 28-30
are not re-drafted; the #251 diff itself was not read (only commit titles).

---

## legal/terms-of-service.md

**TOS-1, lines 22-24 (cited :23). Class S?**
Current: "returns structured extraction (sentiment, topics, pros/cons, competitor mentions, urgency
signals), authenticity scoring, and aggregate insights."
Proposed: "returns structured extraction (sentiment, topics, pros/cons, competitor mentions, urgency
signals) and aggregate insights."
Reason: the Service no longer offers authenticity scoring. DECIDED (Q1): ToS section 10 ("Changes
to these Terms", lines 180-185) says material changes are reflected by an updated "Effective date"
and, where practicable, notice to registered accounts. All registered accounts are internal/test
(14 orgs on or before 2026-07-10, all internal/test by name; 19 orgs now, per GG), so: add a dated
changelog line and update the "Effective date" at publication; no notice period; revisit at the
first external customer. Proposed changelog line: "2026-10-07 - Removed 'authenticity scoring'
from the service description (section 1) and the warranty disclaimer (section 9); the feature was
withdrawn." (section numbers to be confirmed against the ToS before publishing). Fact limit:
the 19-org figure, and that the 5 orgs created after 2026-07-10 are internal, are from the
orchestrator's instruction and were NOT checked by me (no DB access).

**TOS-2, line 94. Class W.**
Current: "any resulting structured extraction, authenticity score, or insight generated from it."
Proposed: "any resulting structured extraction or insight generated from it, including any
historical authenticity score generated before that feature was withdrawn."
Reason: ownership language must still cover the historical rows; simply deleting the words would
narrow the ownership grant. Shorter alternative: delete "authenticity score," only.

**TOS-3, lines 135 and 137. Class W.**
Current: "...EXTRACTION, SENTIMENT, TOPIC, OR AUTHENTICITY OUTPUTS ARE ACCURATE... (see
`docs/compliance.md` - authenticity scoring output is decision support, never an automated
verdict)."
Proposed: "...EXTRACTION, SENTIMENT, OR TOPIC OUTPUTS ARE ACCURATE... WITHOUT HUMAN REVIEW." and
delete the parenthetical.
Reason: no authenticity output is produced any more, and the parenthetical points at a document
that is being superseded. Do not weaken the disclaimer: a catch-all ("OR OTHER OUTPUTS") is safer
than naming outputs, if GG prefers.

## legal/privacy-policy.md

**PP-1, line 33. Class S?**
Current: "Sentiment, topics, pros/cons, competitor mentions, urgency signal, authenticity
score/label/flags."
Proposed: "Sentiment, topics, pros/cons, competitor mentions, urgency signal."
Reason: no longer generated; historical rows are disclosed under PP-2.

**PP-2, line 36. Class S (a data category changes status).**
Current: "Authenticity audit records | `org_id`, a SHA-256 hash of the review text (not the
plaintext), score, label, flags, timestamp. | Generated when authenticity scoring runs
(`authenticity_audits` table) - see `SECURITY.md` section 10, `docs/compliance.md`"
Proposed (decided Q3/Q4): "Historical authenticity audit records | `org_id`, a SHA-256 hash of the
review text (not the plaintext), score, label, flags, timestamp. | Historical records only: the
authenticity-scoring feature was withdrawn and no new rows are created (no new authenticity records
since 2026-07-26). Existing rows remain in the `authenticity_audits` table until purged and are
deleted when the organization is deleted."
Reason: the table and rows still exist, so the category must stay disclosed; "Generated when ...
runs" is false now. Writer re-check on current origin/main (this revision, `git grep` over `app/`):
the only INSERT into `authenticity_audits` is `save_authenticity_audit_pg` (`app/core/storage_pg.py`
line 847, INSERT near 862); it has NO caller in `app/` (callers exist only in tests: an integration
isolation test, and `tests/unit/test_ingest_worker.py` which asserts it is NOT called). So no code
path writes new rows. Reader paths remain in `app/core/alerts/` (digest) and `storage_pg.py`
(count/summary/list helpers). Limit: static grep of the merged tree, not a runtime check.

**PP-3, line 57. Class W.**
Current: "...to generate the structured extraction and authenticity signal."
Proposed: "...to generate the structured extraction."
Reason: Groq should no longer receive text for authenticity scoring (verify on post-#251 tree).

**PP-4, line 68. Class W.**
Current: "Extraction output, authenticity audit records (hashed review text only, not plaintext - see
section 1 table above), account data, and usage records are stored in Supabase Postgres..."
Proposed: "Extraction output, account data, and usage records, together with any historical
authenticity audit records (hashed review text only, not plaintext - see section 1 table above), are
stored in Supabase Postgres..."
Reason: keeps the disclosure true without implying the data is still generated.

## legal/dpa-template.md

All DPA items are class S: subject matter, nature and purpose are the Article 28(3) description of
instructed processing. Narrowing is normally customer-favorable. DECIDED (Q2): no executed DPA
found and none believed to exist (`docs/payments-readiness.md` line 7: first customers are invoiced
manually; no external orgs in the DB, per GG), so narrow the purposes, nature and data description
now (DPA-1 to DPA-7 below, apply as written). Add at the top of the DPA template's internal notes
(not customer-facing text): "Re-check before the first customer signs: confirm this template still
matches the processing actually performed." I did not search for executed DPAs outside the repo.

**DPA-1, line 22.** Current: "review text extraction, authenticity scoring, and aggregate insights"
Proposed: "review text extraction and aggregate insights"

**DPA-2, line 35.** Current: "Automated extraction (LLM-based structured data extraction),
authenticity scoring, storage, and aggregate insight generation."
Proposed: "Automated extraction (LLM-based structured data extraction), storage, and aggregate insight
generation."

**DPA-3, line 36.** Current: "...into structured, queryable data and authenticity signals."
Proposed: "...into structured, queryable data."

**DPA-4, line 37.** Current: "(3) storage of extraction output, hashed authenticity audit records,
and usage records in the database sub-processor (Supabase Postgres)"
Proposed: "(3) storage of extraction output and usage records, and continued storage of previously
generated hashed authenticity audit records until deletion, in the database sub-processor (Supabase
Postgres)"
Reason: historical rows are still processed (stored) data.

**DPA-5, line 110 (Groq).** Current: "for the sole purpose of generating structured extraction and
authenticity signal output."
Proposed: "for the sole purpose of generating structured extraction output."

**DPA-6, line 112 (Supabase).** Current: "extraction output (retained-mode organizations only - see
section 8), hashed authenticity audit records, account data, usage records"
Proposed: "extraction output (retained-mode organizations only - see section 8), historical hashed
authenticity audit records (no longer generated), account data, usage records"

**DPA-7, lines 152-153 (security measures, "Audit trail").**
Current: "Audit trail - authenticity scoring decisions are recorded in an org-scoped
`authenticity_audits` table (hashed review text, not plaintext), itself subject to the same RLS
isolation."
Proposed: delete the bullet, or reword: "Historical audit records - previously generated
authenticity audit records remain in an org-scoped table (hashed review text, not plaintext) under
the same RLS isolation, until deleted." Recommend the reword only if we want the RLS statement to
keep covering the historical table; a control described as current with no live writer is the
decorative-control shape (rule 85a).

## legal/sub-processors.md

**SP-1, line 30 (Groq). Class S.**
Current: "Groq receives this redacted text to generate structured extraction and authenticity signal
output."
Proposed: "Groq receives this redacted text to generate structured extraction output."
Note: Groq itself is unchanged; only what it receives is narrowed. Not a sub-processor list change.

**SP-2, line 55 (Supabase). Class S.**
Current: "...extraction output, authenticity audit records (hashed review text, not plaintext - see
`SECURITY.md` section 10), account/organization data..."
Proposed: "...extraction output, historical authenticity audit records (hashed review text, not
plaintext - see `SECURITY.md` section 10; no longer generated), account/organization data..."
Note: `SECURITY.md` section 10 is being rewritten by #251; re-point the cross-reference after it
lands.

**SP-3, line 76 (Resend role). Class S?**
Current: "urgency, likely-fake, fake-cluster, topic-spike, batch-defect, and fake-campaign
notifications".
"likely-fake", "fake-cluster" and "fake-campaign" are fake-review-adjacent alert types. DECIDED
(Q6), facts per orchestrator verification: `ENABLE_FAKE_CAMPAIGN_DETECTOR=false` and
`ENABLE_BATCH_DEFECT_DETECTOR=false` in production, `alert_log` has 0 fake-type rows, but the code
for LIKELY_FAKE / FAKE_CAMPAIGN alerts still exists. So the alert types are "not active", NOT
"removed". Proposed (batch-defect is also behind a disabled flag in production, so it is listed as
not active too): "Alert types: urgency and topic-spike notifications are the active types sent to
an organization's registered recipient; likely-fake, fake-cluster, fake-campaign and batch-defect
alert types exist in code but are not currently active." Removing the dead alert code is a separate later decision
for GG. The sub-processor identity (Resend) and its role are unchanged. I did not verify the
flag values or alert_log myself.

## legal/data-retention-and-deletion.md

**DR-1, line 77 (cascade list).** Keep unchanged: `authenticity_audits` still exists and still
cascades on organization deletion; removing it would make the statement false.

**DR-2, line 91 (retention schedule). Class S.**
Current: "Authenticity audit records (`authenticity_audits`) | Retained indefinitely; stores a SHA-256
hash of review text, not the plaintext (`SECURITY.md` section 10). | Deleted immediately, same
cascade."
Proposed (minimal, keeps the existing schedule true): "Historical authenticity audit records
(`authenticity_audits`; no longer generated) | Retained indefinitely while the account is active
(existing schedule, unchanged); stores a SHA-256 hash of review text, not the plaintext
(`SECURITY.md` section 10). | Deleted immediately, same cascade."
Revised per decision Q3 (replaces the proposal above): "Historical authenticity audit records
(`authenticity_audits`) | Historical records only; no new rows are created. Retained until purged;
stores a SHA-256 hash of review text, not the plaintext (`SECURITY.md` section 10). | Deleted
immediately, same cascade." No retention period is stated or invented. The purge itself is a later
data-deletion step that GG approves separately (not part of this draft; no DDL, no deletion done).

## legal/compliance-posture.md

**CP-1, line 37. Class W.**
Current: "None held. The authenticity-scoring feature *supports* IS 19000:2022 moderation workflows -
it does not certify compliance with that standard, and must not be described as doing so."
Proposed: "None held. Samidha Reviews does not certify, and must not be described as certifying,
compliance with IS 19000:2022 or any other standard." The Evidence cell may keep pointing at
`docs/compliance.md`, which is KEPT as a historical record (decided Q7; banner text under
"Decisions" below).
Reason: the feature that "supported" IS 19000 moderation is gone; the no-certification statement is
the load-bearing part and is kept.

## docs/data-ownership.md

- **DO-1, line 18.** Keep: RLS still covers the table.
- **DO-2, lines 55 and 59. Class W.** Relabel row 55 "Authenticity audit (historical, no longer
  generated)". Line 59 stays factually true (generated column on both tables).
- **DO-3, line 63. Class W,** matches the implemented dataset API (decision Q5): current "paginated
  per-review records linking extraction + authenticity + corrections." Proposed: "`GET /v2/dataset`
  - paginated per-review records linking extraction + corrections. The `authenticity` key is
  deprecated and always null (removal not before 2027-01-01)."

## API surfaces (IMPLEMENTED on main; decision Q5 accepted)

Checked against the merged tree (this branch after merging origin/main), not against a proposal.

**Dataset (`GET /v2/dataset`, `app/api/v2/dataset.py`, `app/core/dataset/builder.py`).** The route
keeps the `authenticity` key, always `null`, documented as deprecated. The code carries the text:
"DEPRECATED. Authenticity scoring was removed; this key is always null. It is kept only so existing
clients keep parsing the response. It will be removed in a future versioned change; not before
2027-01-01." The example response shows `"authenticity": None`. The builder no longer joins
`authenticity_audits` (builder.py shrank by about 60 lines in the merge).
Copy consequence: DO-3 above and the `docs/data-ownership.md` line 63 text follow this; any public
API reference should say "deprecated, always null, removal not before 2027-01-01".

**Corrections (`app/api/v2/corrections.py`, `app/core/corrections/schema.py`).** The enum member
`SourceType.authenticity` is kept (schema.py lines 11 and 32), so reads and stored rows keep
working. A request validator (corrections.py lines 30-36) rejects new writes with
`source_type='authenticity'` with 422: "source_type 'authenticity' is deprecated and no longer
accepted; authenticity scoring was removed". The docstring (line 105) states this.

Breaking-change risk: writes of that value now return 422 (breaking for any caller still sending
it); reads unaffected. External customers: none known (UNVERIFIED beyond the evidence in
decisions Q1/Q2: first customers invoiced manually, no external orgs in the DB per GG; I have no DB
access). Versioning: removal of the dataset key is a later versioned change, not before 2027-01-01.

## Stragglers (README.md, SECURITY.md, docs/compliance.md)

Sweep scope, stated so it can be falsified: patterns `authenticit`, `fake[- ]review`,
`fake-campaign` (case-insensitive) over `README.md`, `SECURITY.md`, `docs/compliance.md`, `legal/`,
`docs/data-ownership.md` and the four named API files. NOT swept: other files under `docs/`, `web/`,
`site/`, `app/` beyond the four files, `eval/`, `ops/`. This draft does not claim completeness.

Update after merging origin/main: PR #251 has merged and already edited all three. README.md
changed (about 94 lines), SECURITY.md section 10 now carries a Session 17 (W6) note that no API
route or ingest path writes or reads the table, and `docs/compliance.md` line 5's superseded banner
now says the dashboard routes and CSV-ingest option were removed too. SECURITY.md line 131 still
says the scorer "supports / assists" moderation workflows (stale, same issue as CP-1) and
`docs/compliance.md` still opens "Read this before integrating the authenticity scoring feature".
Both are inside the decided Q7 treatment below; do not double-edit.

---

## Decisions (GG, relayed by the orchestrator; replaces the former OPEN QUESTIONS list)

### Q1. ToS service-description change - DECIDED
Facts: ToS section 10 (lines 180-185) requires "an updated Effective date" and, where practicable,
notice to registered accounts. All registered accounts are internal/test (14 orgs on or before
2026-07-10, internal/test by name; 19 orgs now).
Decision: dated changelog line plus updated Effective date; no notice period; revisit at the first
external customer. Before/after quote: see TOS-1 (before: "...urgency signals), authenticity
scoring, and aggregate insights." after: "...urgency signals) and aggregate insights."). Not
verified by me: the org counts and names.

### Q2. Executed DPAs - DECIDED
None found, none believed to exist (`docs/payments-readiness.md` line 7; no external orgs in the
DB, per GG). Narrow the DPA purposes, nature and data description now (DPA-1 to DPA-7). Add the
internal line: "Re-check before the first customer signs." Before/after quotes: see DPA-1 to DPA-7.

### Q3. Historical `authenticity_audits` rows - DECIDED
Keep the table (no DDL). Retention wording: "historical records only; no new rows are created;
retained until purged". No retention period invented. The purge is a later data-deletion step that
GG approves separately. Before/after quotes: PP-2 and DR-2 above. DPA-6, SP-2 and DO-2 carry the
same "historical" wording.

### Q4. Verified facts about the rows - DECIDED
The 33 audit rows span 10 orgs; last created 2026-07-26 (71 days before 2026-10-05). Use the
sentence "no new authenticity records since 2026-07-26" (used in PP-2). The earlier phrase "all
internal" is DROPPED: 5 of the 10 orgs were created after 2026-07-10 and were not checked. Counts
are from the orchestrator; I did not query the DB.

### Q5. API choices - DECIDED AND IMPLEMENTED
Dataset: key kept, always null, deprecated, removal not before 2027-01-01. Corrections: reads keep
working, new `authenticity` writes return 422. See "API surfaces" above.

### Q6. Fake alerts - DECIDED
Wording says these alert types are "not active", not "removed". See SP-3. Removing the dead alert
code (LIKELY_FAKE / FAKE_CAMPAIGN) is a separate later decision. Facts (orchestrator-verified, not
rechecked by me): `ENABLE_FAKE_CAMPAIGN_DETECTOR=false` and `ENABLE_BATCH_DEFECT_DETECTOR=false` in
production; `alert_log` has 0 fake-type rows.

### Q7. docs/compliance.md - DECIDED: keep with a historical-record banner
Current banner (line 5, merged): "> **Superseded (Session 15d D5 + Session 17 W6):** the public
`POST /v2/authenticity`, ... were removed ...". Proposed replacement banner, placed directly under
the title and the opening sentence at line 3 reworded:

> **HISTORICAL RECORD - not a description of the current product.** The authenticity (fake-review)
> scoring feature described below has been withdrawn: its API routes, dashboard pages, CSV-ingest
> option and health-score term no longer exist, and no new authenticity records are created (none
> since 2026-07-26). It was withdrawn because the flag could not be measured (see the accuracy note
> at the end of this document). This file is kept only to document what the product previously did
> and for the historical `authenticity_audits` records that remain until purged. It must not be
> relied on, cited, or presented as a compliance claim: Samidha Reviews does not certify compliance
> with IS 19000:2022 or any other standard.

Line 3 before: "This document describes how review-iq supports IS 19000:2022 ('Online Consumer
Reviews') moderation workflows. Read this before integrating the authenticity scoring feature into
any compliance process." After: "This document records how review-iq previously supported IS
19000:2022 ('Online Consumer Reviews') moderation workflows, for the historical record only." The
same file-level treatment applies to SECURITY.md line 131 ("supports / assists ... moderation
workflows"): suggest "Historical: the authenticity scorer ... (withdrawn); see docs/compliance.md".

### Q8. Sequencing / writer re-check - DONE
Apply text only after the code state is on main: for #251 it is. Writer re-grep on current
origin/main (recorded under PP-2): no caller of `save_authenticity_audit_pg` in `app/`. For
sections A and B below the gating is stated per section.

---

## SECTION A (for GG to approve; NOT decided): Gemini retirement - apply only after #262 is deployed

PR #262 (pending merge) removes the Gemini fallback. I did not read #262. Every sentence below still
describes Gemini as excluded or gated behind `ENABLE_GEMINI_FALLBACK`. Proposed replacement text
requested by the coordinator: "no Gemini or Google model is used or retained as a sub-processor".

Caution on that exact wording: `legal/sub-processors.md` (line 61 onward) and
`legal/privacy-policy.md` (line 145) list Google Cloud Run (and Secret Manager) as Google
infrastructure, "listed for transparency". A flat "no Google ... sub-processor" sentence would
contradict that. Suggested safe variant used below: "no Gemini or other Google generative model
is used or retained". GG to choose between the two.

| # | File:line | Current text | Proposed replacement |
|---|---|---|---|
| A1 | legal/privacy-policy.md:66 | "Google Gemini is excluded from the customer-data (`/v2`) path entirely." | "No Gemini or other Google generative model is used for any Customer data." (delete if GG prefers silence) |
| A2 | legal/sub-processors.md:86-90 ("Not currently in use" list) | "**Google Gemini** - explicitly excluded from the customer-data (`/v2`) path. `SECURITY.md` section 3: `allow_gemini_fallback=False` is hardcoded on every `/v2/extract` call; Gemini is reachable only on the legacy `/v1` demo path, gated behind `ENABLE_GEMINI_FALLBACK` (default `false`), because the Gemini free tier uses inputs for training ... Listed here explicitly so it is clear this was a deliberate exclusion, not an oversight." | "**Google Gemini** - not used. No Gemini or Google model is used or retained as a sub-processor; the fallback was removed (date to be filled after #262 deploys)." Keep the Google Cloud Run entry (line 61) unchanged. |
| A3 | SECURITY.md:67 | "**Gemini (Google Gemini 2.0 Flash):** Removed from the v2 (client-data) path entirely. `allow_gemini_fallback=False` is hardcoded ... reachable only on the legacy `/v1` demo path, and only when `ENABLE_GEMINI_FALLBACK=true` ..." | "**Gemini:** removed. No Gemini or Google model is used or retained as a sub-processor." |
| A4 | SECURITY.md:113 | secrets list includes `GEMINI_API_KEY` | drop `GEMINI_API_KEY` from the list once the secret is deleted (secret deletion is a separate GG-approved step) |
| A5 | README.md:315 | "LLM (fallback) \| Google Gemini 2.0 Flash" | remove the row (or name the actual fallback) |
| A6 | README.md:333 | "Store secrets in Secret Manager: `groq-api-key`, `gemini-api-key`, ..." | drop `gemini-api-key` |

Judged historical, left alone: `docs/decorative-control-sweep.md` lines 63, 112, 114 (records of a
past sweep, including the failover probe's `GEMINI_API_KEY`; note the failover probe and
`model-availability-check.yml` reference Gemini and will need their own change in #262),
`docs/specs/wave1-commercialization.md:24` ("Gemini banned on org path", shipped record),
`docs/specs/s15c-language-routing.md:234`. Eval-only use is a different question:
`docs/eval-contamination-and-the-honest-number.md:138`, `docs/specs/s15d-gold-label-review.md`
lines 58 and 96 say a Gemini model (`gemini-3.5-flash-lite`) was a judge in an eval panel. If any
eval still calls a Google model, the sentence "no Gemini or Google model is used" would be false
for eval, though eval does not process Customer data. GG to confirm the scope of the sentence
(product path vs everything).

Scope: `git grep -niE "gemini|ENABLE_GEMINI_FALLBACK"` over `legal/`, `SECURITY.md`, `README.md`,
`docs/` (excluding `docs/drafts` and `docs/architecture/adr`). NOT swept: ADRs (historical),
`ops/`, `.github/`, `app/`, `web/`, `site/`. `legal/dpa-template.md` has no Gemini mention.
Classification: this is a substantive-adjacent change (a sub-processor is described as never used,
not removed from an active list, so it is not a sub-processor list change since Gemini was never
listed as active). Whether a notice is needed: GG (no external customers known).

## SECTION B (for GG to approve; NOT decided): per-user `last_seen_at` - apply only if the migration is applied

A new per-user activity timestamp on `organization_members` is personal data (it tracks when an
identifiable user was last active). The PR is in progress; `git grep last_seen_at` on current
origin/main returns nothing, so no code or migration exists to verify against. All text below is
therefore conditional and UNVERIFIED against the real column name, trigger events, or update
frequency; revise after the migration lands.

**B1, legal/privacy-policy.md (add a row to the data table near line 35, "Usage/API logs" row):**
"| **Last-activity timestamp** | The time a user was last active in the Service (`last_seen_at`, stored per user per organization on `organization_members`). | Generated by the Service when you use it; used to show organization administrators when a member was last active. [CONFIRM the actual purpose and who can see it] |"
Single-sentence form if GG prefers prose: "We record, for each user, the time they were last active
in the Service, so that organization administrators can see recent activity; this timestamp is
deleted with the user or organization."

**B2, legal/data-retention-and-deletion.md (add a row to the retention schedule after line 90):**
"| Per-user last-activity timestamp (`organization_members.last_seen_at`) | Retained while the user is a member of the organization; overwritten on each new activity (no history kept) [CONFIRM overwrite-only]. | Deleted with the user or organization via the existing `ON DELETE CASCADE` on `organization_members` (the account-deletion statement removes the organization and its dependent rows). |"
Check needed before publishing: the existing line 77 cascade list does not name
`organization_members`; confirm the foreign key to `organizations` is `ON DELETE CASCADE`
and add `organization_members` to that list. I did not verify this FK.

**B3, docs/data-ownership.md:** add `organization_members.last_seen_at` to the table list at line
18-19 (it already lists `organization_members` under RLS) and one line in the ownership section:
"Per-user activity timestamps (`last_seen_at`) are stored per user and organization, are subject to
the same RLS, and are deleted with the user or organization."

---

## STILL OPEN / NOT VERIFIED BY ME

- All org counts, org names, and the 33-row / 10-org / 2026-07-26 figures (orchestrator-supplied; no
  DB access).
- Flag values in production and `alert_log` contents (Q6).
- The exact ToS section numbers for the changelog line (section 1 and the disclaimer's section
  number were not re-read; TOS-1 says "to be confirmed").
- Contents of PRs #262 and the `last_seen_at` PR (not read); `last_seen_at` has no code on main.
- Whether `organization_members` FK is `ON DELETE CASCADE`.
- GG decisions still needed: Section A wording scope (Google vs Gemini only; eval judges), Section
  B purpose and visibility, and the later data-deletion step for historical rows and the dead alert
  code.
