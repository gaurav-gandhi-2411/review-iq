# IS 19000:2022 — Review Authenticity Compliance Posture

This document describes how review-iq supports IS 19000:2022 ("Online Consumer Reviews") moderation workflows. Read this before integrating the authenticity scoring feature into any compliance process.

> **Superseded (Session 15d, D5):** the public `POST /v2/authenticity`, `POST /v2/authenticity/batch` and `GET /v2/insights/authenticity` API routes described below were removed (the flag is unmeasurable; see the accuracy note at the end of this document). The `authenticity_audits` table, CSV-ingest `include_authenticity` scoring and the dashboard's own authenticity views are unchanged.

---

## What review-iq does

review-iq's authenticity scoring feature:

- **Scores** each review on a 0–1 scale (higher = more likely genuine) and assigns one of three labels: `genuine`, `suspicious`, or `likely_fake`.
- **Explains** the decision with human-readable reasons and a structured flags list (e.g. `incentivized_phrase`, `rating_text_mismatch`).
- **Records** an org-scoped audit trail (`authenticity_audits`) — review hash, score, label, flags, and timestamp — for every scored review. This audit trail supports the record-keeping requirements of a review-administrator moderation process.
- **Covers** English, Hinglish (en+hi code-mix), and Hindi reviews — Indian-market text patterns and Hinglish incentivized-disclosure phrases are explicitly modeled.

---

## What review-iq does NOT do

| Action | Status |
|---|---|
| Auto-reject or auto-hide flagged reviews | **Never** — review-iq flags for human decision only |
| Certify that your process is IS 19000:2022 compliant | **Not claimed** — see below |
| Guarantee detection of all fake or incentivized reviews | **Not claimed** — recall is unmeasured and will miss some |
| Replace a human review-administrator | **No** — human oversight is mandatory under IS 19000 |

---

## Approved language

When describing this feature in client-facing copy, marketing material, or contracts, use only the approved framing below:

| Approved | Prohibited |
|---|---|
| "supports IS 19000:2022 moderation" | "IS 19000:2022 certified" |
| "assists IS 19000:2022 compliance workflows" | "guarantees compliance" |
| "flags reviews for review-administrator decision" | "auto-rejects non-compliant reviews" |
| "audit trail for moderation records" | "compliance-certified audit trail" |

---

## How IS 19000:2022 maps to this feature

IS 19000:2022 requires platforms and sellers operating a review system to:

1. **Run a review-administrator process** — designated personnel must assess reviews before or after publication for authenticity.
2. **Flag incentivized and fraudulent reviews** — reviews exchanged for payment, free products, or discounts must be identified.
3. **Maintain records** — the moderation process must be documented and auditable.

review-iq contributes to requirements 1–3 as follows:

| Requirement | How review-iq contributes |
|---|---|
| Review-administrator process | Scoring output is input to the human administrator's decision, not a replacement for it |
| Flagging incentivized reviews | `INCENTIVIZED_PHRASE` flag covers English and Hinglish disclosed-incentive patterns |
| Flagging potentially fraudulent reviews | LLM signal scores promotional tone, generic/low-info content, rating-text mismatch |
| Batch fraud signals | Near-duplicate and burst detection surface coordinated review patterns |
| Audit records | `authenticity_audits` table stores score, label, flags per review per org |

---

## Human-in-the-loop requirement

IS 19000:2022 expects a **human review-administrator** to make the final moderation decision. review-iq's output must be treated as decision support, not an automated verdict. Integrations must:

- Present authenticity scores and flags to a human reviewer before taking any action on a review.
- Not auto-hide, auto-reject, or auto-publish reviews based solely on the authenticity score.
- Retain the audit trail for the period required by your compliance policy.

---

## Eval results and known limitations

**The authenticity scorer has no published accuracy figure.** No authenticity labels exist for the 106-review held-out corpus, so its precision and recall are not measured on real data. An earlier 40-item fixture set (21 positive) was in-sample and its result is not reproducible; it is kept only as a quarantined historical file (`eval/results/authenticity_latest.json`) and is not cited as a measurement.

**Known limitations:**

- Hindi (Devanagari-only) reviews: the scoring model handles Hindi via the same LLM prompt but the heuristic phrase list is primarily Latin-script (English + Hinglish). Hindi-only incentivized-disclosure detection is weaker.
- Novel fraud patterns not in the training distribution of the LLM may be missed.
- Real-world recall and precision are unknown and will vary by product category and fraud sophistication.
- Batch signals (near-duplicate, burst) require multiple reviews for the same product to fire.

---

_Last updated: v0.6.0 (June 2026). This document is version-controlled; check git history for changes._
