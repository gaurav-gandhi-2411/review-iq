# DRAFT - not approved - do not publish

Task S17 F3c. Proposed corrections to legal, data-ownership and API copy that still describe
authenticity (fake-review) scoring after the surface was removed (PR #217 removed `/v2`
authenticity routes; PR #251, DRAFT, removes `/bff/authenticity*`, the web pages,
`include_authenticity` on CSV ingest and the 0.30 health-score term). Reason for removal: the flag
is unmeasurable (ADR 0030 context, S15d D5; `docs/compliance.md` accuracy note). The
`authenticity_audits` table and its historical rows remain; no DDL planned.

Baseline read: `origin/main` @ cf3b425. Line numbers are from that commit. Nothing here is
published or applied; GG approves final text. All wording is a suggestion only.

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
| 5 | legal/privacy-policy.md:36 | S | Keep row, reword as historical; retention is an OPEN QUESTION |
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
| 17 | legal/sub-processors.md:76 | S? | Resend alert types (likely-fake, fake-cluster, fake-campaign): VERIFY |
| 18 | legal/data-retention-and-deletion.md:77 | none | KEEP (cascade list still true) |
| 19 | legal/data-retention-and-deletion.md:91 | S | Retention row: keep, mark historical, OPEN QUESTION |
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
Reason: the Service no longer offers authenticity scoring. Removing a feature may engage a ToS
clause on material changes; Q1.

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
Proposed: "Historical authenticity audit records | `org_id`, a SHA-256 hash of the review text (not
the plaintext), score, label, flags, timestamp. | Generated by an authenticity-scoring feature that
has been withdrawn; no new records are created. Existing records remain in the
`authenticity_audits` table and are deleted when the organization is deleted. [OPEN QUESTION FOR GG:
whether to state a purge date, Q3.]"
Reason: the table and rows still exist, so the category must stay disclosed; "Generated when ...
runs" becomes false. "No new records are created" is UNVERIFIED: re-check every writer of
`authenticity_audits` on the post-#251 tree (`grep -rn authenticity_audits app/`) before approving.

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
instructed processing. Narrowing is normally customer-favorable, but the template may exist as
executed copies. Q2.

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
"likely-fake", "fake-cluster" and "fake-campaign" are fake-review-adjacent alert types. UNVERIFIED
whether they were removed by #217/#251 (I did not trace `app/core/alerts/`). Do not edit until GG
confirms which alert kinds still send; if removed, delete those names. The sub-processor identity
(Resend) and its role are unchanged either way. Q6.

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
OPEN QUESTION FOR GG (Q3): the existing schedule says "retained indefinitely" and states no period I
could rely on, so I invented none. A table with no live writer and indefinite retention is a
data-minimisation smell; a bounded period or one-time purge would be a new commitment and a
deletion (irreversible, out of scope for this draft).

## legal/compliance-posture.md

**CP-1, line 37. Class W.**
Current: "None held. The authenticity-scoring feature *supports* IS 19000:2022 moderation workflows -
it does not certify compliance with that standard, and must not be described as doing so."
Proposed: "None held. Samidha Reviews does not certify, and must not be described as certifying,
compliance with IS 19000:2022 or any other standard." Update the Evidence cell
(`docs/compliance.md`) once that file is superseded (Q7).
Reason: the feature that "supported" IS 19000 moderation is gone; the no-certification statement is
the load-bearing part and is kept.

## docs/data-ownership.md

- **DO-1, line 18.** Keep: RLS still covers the table.
- **DO-2, lines 55 and 59. Class W.** Relabel row 55 "Authenticity audit (historical, no longer
  generated)". Line 59 stays factually true (generated column on both tables).
- **DO-3, line 63. Class W,** follows the dataset API decision. Option (a): "`GET /v2/dataset` -
  paginated per-review records linking extraction + corrections (an `authenticity` key is retained
  for pre-withdrawal reviews and is null otherwise; deprecated)". Option (b): "...linking
  extraction + corrections".

## API surfaces (contract, not legal text)

External customers: UNVERIFIED. DB-free evidence only: `docs/payments-readiness.md:7` says "Today
first customers are invoiced manually" (suggests few or no external customers, does not say none);
`PLAN.md` lines 326 and 863 refer to "the first paying client" as a future event. I found no
evidence of external consumers of `GET /v2/dataset` or of `source_type=authenticity` corrections,
and no evidence against. Q5.

**Dataset: `app/api/v2/dataset.py:42,49` and `app/core/dataset/builder.py` (1, 111, 116, 129, 201,
262).** The summary reads "(extraction + authenticity + corrections)", the example response carries
`"authenticity": {"score": 0.88, "label": "genuine", "flags": []}`, and the builder joins
`authenticity_audits` onto each record (`auth_by_review_id.get(rid)`).
- (a) Deprecate: keep the `authenticity` key, document it as deprecated and "present only for
  reviews scored before the feature was withdrawn, otherwise null", unvalidated, with no accuracy
  figure. Summary becomes "extraction + corrections (+ deprecated authenticity)". Example shows
  `"authenticity": null`. No contract break; keeps the historical join live.
  Cost: keeps an unmeasurable score in exports that a reader may take as a quality signal.
- (b) Remove: drop the key and the `authenticity_audits` query. Breaking for any client parsing the
  key; `docs/data-ownership.md` calls the JSONL export format "stable and self-describing", which
  argues for deprecate-first.
- **Recommendation: (a)** now, with removal in a later versioned change (`/v2` is the only version
  present, so removal means a new version or a dated deprecation window). Risk: low to moderate,
  UNVERIFIED.

**Corrections enum: `app/core/corrections/schema.py:11,32` and `app/api/v2/corrections.py:92`.**
`SourceType.authenticity` accepts writes for `score|label|flags`, and the docstring advertises it.
- **Recommendation: deprecate, do not delete.** Keep the enum member so stored `corrections` rows
  with `source_type='authenticity'` still deserialize and still appear in dataset/export; reject
  NEW writes with `422` and a message naming the withdrawal; remove "authenticity (score, label,
  flags)" from the docstring at `corrections.py:92`; mark the enum value deprecated in OpenAPI and
  add a changelog entry. Removing the member would break reads of any existing authenticity
  corrections (count UNVERIFIED, no DB access) and any client sending the value.
- Breaking-change risk: writes of this value become `422`; reads unaffected. Likely zero external
  callers (UNVERIFIED). Versioning: this narrows accepted input within `/v2`; per rule 110 either
  (i) announce deprecation and keep accepting writes for a stated window, or (ii) accept it because
  the producing surface is already gone and no external consumer is known. GG decides (Q5).

## Stragglers (README.md, SECURITY.md, docs/compliance.md)

Sweep scope, stated so it can be falsified: patterns `authenticit`, `fake[- ]review`,
`fake-campaign` (case-insensitive) over `README.md`, `SECURITY.md`, `docs/compliance.md`, `legal/`,
`docs/data-ownership.md` and the four named API files. NOT swept: other files under `docs/`, `web/`,
`site/`, `app/` beyond the four files, `eval/`, `ops/`. Expect more hits there; this draft does not
claim completeness.

- README.md:260 (`include_authenticity=true` form field), :372-377 (features list; :374 says
  `include_authenticity` and the dashboard views are "unchanged"; :376 "IS 19000:2022 support
  posture"; :377 "Not measured").
- SECURITY.md:119-129, section 10 "Authenticity Audit Trail" (line 129: scorer "supports / assists"
  moderation workflows). The :121-127 table/RLS/isolation statements stay true for the historical
  table.
- docs/compliance.md: title "Review Authenticity Compliance Posture", lines 3, 5 (supersede banner
  says CSV ingest and dashboard are "unchanged"), 11, 15, 48, 60, 68-69, 76.

Per its commit titles #251 touches all three; reconcile against that diff, do not double-edit.

## OPEN QUESTIONS FOR GG

1. Is removing "authenticity scoring" from the ToS service description (TOS-1) a material change to
   the Service under the ToS change clause, requiring notice? (I did not read that clause for this
   draft; not decided.)
2. DPA: does any customer hold an executed DPA built from this template? Do the narrowed purposes
   (DPA-1..7) and the sub-processor "what it processes" rows (SP-1, SP-2) require notice? The
   sub-processor LIST itself (Groq, Supabase, OpenRouter, Resend) does not change in this draft.
3. Historical `authenticity_audits` rows: keep "indefinite" (DR-2 unchanged), set a bounded period,
   or purge? A purge is a deletion and a new retention commitment; I proposed none and invented no
   period.
4. Are the 33 historical rows truly all internal orgs with none in the last 30 days? (From the
   task brief; I have no DB access.) If any belong to an external org, PP-2/DR-2 disclosure and
   possibly a customer notice become required rather than optional.
5. Dataset `authenticity` key: (a) deprecate-null (recommended) or (b) remove? Corrections: reject
   new writes now (recommended) or announce a deprecation window first? Are there any external API
   consumers (UNVERIFIED)?
6. Resend alert types (SP-3): were likely-fake / fake-cluster / fake-campaign alerts removed?
7. Retire `docs/compliance.md` or keep it as a historical record with a banner? Affects CP-1's
   evidence cell and the PP-2 cross-reference.
8. Sequencing: apply only after #251 merges; statements such as "no new records are created" depend
   on its code state.
