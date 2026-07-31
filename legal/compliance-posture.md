> **⚠️ REQUIRES LEGAL REVIEW.** This document was drafted by an AI coding assistant from the
> product's actual technical implementation and standard SaaS legal templates. It is a
> starting draft, not legal advice, and must be reviewed by qualified counsel before being
> relied upon or published to customers.

# Compliance Posture — DPDP Act 2023 (India) and GDPR

This is an honest, current-state statement of how Samidha Reviews' actual implementation maps to
India's Digital Personal Data Protection Act, 2023 ("DPDP Act") and the EU's General Data
Protection Regulation ("GDPR"), for customers evaluating whether the Service fits their own
compliance obligations. Per this repository's standing rule against unfalsifiable claims: this
document distinguishes **shipped and verified** from **planned/aspirational**, and it does not
claim certification the Service does not hold.

**We do not claim to be DPDP- or GDPR-"certified."** Neither framework offers a formal product
certification in that sense; what follows is a factual posture statement, not a compliance
guarantee.

---

## What is actually true today (verified against the codebase)

| Control | Status | Evidence |
|---|---|---|
| PII redaction before third-party LLM transmission | **Shipped** — destructive redaction of emails, phone numbers, credit card numbers | `app/core/sanitize.py`, `SECURITY.md` §1 |
| Multi-tenant database isolation (Row-Level Security) | **Shipped** — `USING`/`WITH CHECK` policies on every tenant table, anon-deny policy, dedicated non-superuser app role | `docs/data-ownership.md`, `SECURITY.md` §4 |
| API credential hashing | **Shipped** — argon2id, shown once, never logged | `SECURITY.md` §4 |
| Prompt-injection defenses | **Shipped** — two independent layers | `SECURITY.md` §2 |
| Secret management (no plaintext secrets in source/config) | **Shipped** — Google Cloud Secret Manager, per-secret least-privilege grants | `SECURITY.md` §8 |
| Self-service data export | **Shipped** — `GET /v2/dataset/export?format=jsonl` | `docs/data-ownership.md` |
| Self-service, verified, irreversible account/data deletion | **Shipped** — `DELETE /account`, type-to-confirm, synchronous, single-transaction cascade | [`data-retention-and-deletion.md`](./data-retention-and-deletion.md) |
| Structured application logging (`structlog`) | **Shipped** — used across the codebase for operational logging | repo-wide `structlog` usage |
| **Customer-facing structured audit log export** | **Not shipped** — listed as a Wave 1 work item (spec §4.E), not yet built. Do not represent this as available. | Wave 1 spec, gap register |
| Reversible/rehydratable PII tokenization | **Not shipped** — current redaction is one-way/destructive, not the reversible token map described as a future goal | [`privacy-policy.md`](./privacy-policy.md) §2 |
| Adversarial cross-tenant penetration test suite (the spec's 4 specific attack vectors: wrong-org key, forged JWT, mismatched `org_id`, direct app-role) | **Partially verified** — RLS isolation tests exist (`tests/integration/test_rls_isolation.py` + related), but a full audit against all 4 named vectors was not completed as part of this document's drafting (out of this task's scope — being handled separately per Wave 1 Section E) | `plan.md` E-kickoff notes, 2026-07-31 |
| Formal third-party certification (SOC 2, ISO 27001, IS 19000:2022 certification) | **None held.** The authenticity-scoring feature *supports* IS 19000:2022 moderation workflows — it does not certify compliance with that standard, and must not be described as doing so. | `docs/compliance.md` |

---

## DPDP Act 2023 (India) posture

The DPDP Act applies to processing of "digital personal data" within India, and to processing
outside India if it relates to offering goods/services to individuals in India. Relevant
obligations and current posture:

| DPDP requirement | Posture |
|---|---|
| Notice to data principals (§5) | Partial — this Privacy Policy and DPA template exist as drafts (this document set); they are not yet published/linked from customer-facing surfaces at the time of drafting (footer linking is part of this same work item — see the accompanying report). |
| Consent / legitimate use basis (§6–7) | Samidha Reviews acts as a data processor for review text Customers submit; Customers (data fiduciaries under DPDP) are responsible for their own lawful basis for collecting and forwarding end-customer review data — see [`dpa-template.md`](./dpa-template.md) §4. |
| Data principal rights — access, correction, erasure, grievance redressal (§11–13) | Access/export and erasure are shipped (see table above). A published grievance mechanism specific to DPDP §13 is **not yet in place** — see "Grievance / DPO contact" below. |
| **Consent Manager framework** | Not applicable in the current architecture — the Service does not operate as, or route consent through, a DPDP-registered Consent Manager. `[TODO: REQUIRES-LEGAL-REVIEW if this changes.]` |
| Significant Data Fiduciary obligations (§10) — includes appointing a Data Protection Officer | `[TODO: whether Samidha Reviews will be classified as a Significant Data Fiduciary depends on volume/sensitivity thresholds to be notified under DPDP rules — not yet determined. Even absent that classification, publishing a grievance/DPO-equivalent contact is good practice and is required below.]` |
| Cross-border transfer restrictions (§16) | The DPDP Act permits transfer except to countries the Central Government restricts by notification; no such restriction list is known to affect Groq (US) or GitHub backup storage (US) as of this drafting. `[TODO: REQUIRES-LEGAL-REVIEW — confirm no applicable restriction and reassess if DPDP rules/notifications change.]` |
| Breach notification (§8(6)) | See [`dpa-template.md`](./dpa-template.md) §9 — a 72-hour processor-to-controller notification commitment is drafted using GDPR's standard as the template default; DPDP's own breach-notice timing rules should be separately confirmed. |

### Grievance / Data Protection Officer contact (DPDP Act 2023 §10 requirement)

`[TODO: GG to provide grievance officer name/email per DPDP Act 2023 §10 requirements]`

This contact is left as an explicit placeholder rather than a fabricated name or address, per
standing instruction — do not publish this document to customers with this block unfilled.

---

## GDPR posture

GDPR applies where the Service processes personal data of individuals in the EU/EEA, or where an
EU-established Customer uses the Service.

| GDPR concept | Posture |
|---|---|
| Controller/Processor roles | Customer (business) = controller of end-customer review data; Samidha Reviews = processor. See [`dpa-template.md`](./dpa-template.md). |
| Lawful basis for processing | Customer's responsibility for the underlying review data; Samidha Reviews processes only per Customer's instructions (i.e. the API calls Customer makes). |
| Data subject rights (Art. 15–21) | Supported via the mechanisms in the table above (export, deletion); end-customer-initiated requests should be routed to the Customer (controller) per [`privacy-policy.md`](./privacy-policy.md) §5. |
| Data Protection Impact Assessment (Art. 35) | Not conducted as of this drafting — `[TODO: GG-DECISION — assess need once EU customer volume/risk profile is known.]` |
| International transfers (Art. 44+) | See [`privacy-policy.md`](./privacy-policy.md) §4 and [`sub-processors.md`](./sub-processors.md) for the current factual transfer footprint (Groq US, GitHub backups US, Supabase project data in AWS `ap-south-1`). Transfer mechanism (SCCs or equivalent) not yet formalized. `[TODO: REQUIRES-LEGAL-REVIEW.]` |
| EU representative (Art. 27) | Not appointed as of this drafting — required if the Service regularly offers goods/services to, or monitors, individuals in the EU at scale. `[TODO: GG-DECISION, informed by actual/expected EU customer volume.]` |
| Right to lodge a complaint with a supervisory authority | Not currently disclosed on any customer-facing surface. `[TODO: add once a relevant lead supervisory authority is determined, if applicable.]` |

---

## Summary: what a prospective customer's compliance/legal team should know

1. **Real, shipped controls exist today**: PII redaction, RLS tenant isolation, argon2id key
   hashing, self-service export and irreversible deletion. These are not aspirational.
2. **Two real, disclosed gaps** a careful reviewer will find: (a) PII redaction is currently
   one-way/destructive, not a reversible tokenization scheme; (b) encrypted database backups can
   retain deleted data for up to 90 days after a deletion request, even though the primary
   database deletion is immediate — see [`data-retention-and-deletion.md`](./data-retention-and-deletion.md).
3. **Legal/contractual scaffolding (this document set) is new as of this drafting** and requires
   counsel review, a filled-in grievance/DPO contact, and a decision on governing-law/jurisdiction
   language before being relied upon in a real sales conversation.

---

_This document is version-controlled; see git history for changes. Last drafted: 2026-07-31._
