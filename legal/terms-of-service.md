> **⚠️ REQUIRES LEGAL REVIEW.** This document was drafted by an AI coding assistant from the
> product's actual technical implementation and standard SaaS legal templates. It is a
> starting draft, not legal advice, and must be reviewed by qualified counsel before being
> relied upon or published to customers.

# Terms of Service

**Product:** Samidha Reviews ("the Service") — a review-intelligence API operated by
Gaurav Gandhi ("we", "us", "our"), doing business from India.

**Effective date:** `[TODO: GG to set effective date on publication]`
**Legal entity operating the Service:** `[TODO: GG to confirm — sole proprietor / registered
entity name and registration number, if any]`

By creating an account, obtaining an API key, or otherwise using the Service, you ("Customer",
"you") agree to these Terms.

---

## 1. Service description

Samidha Reviews is a hosted API that accepts customer review text and returns structured
extraction (sentiment, topics, pros/cons, competitor mentions, urgency signals), authenticity
scoring, and aggregate insights. The Service is provided via:

- A REST API (`/v2/*` endpoints) authenticated with an `riq_live_*` API key.
- A self-serve account (Supabase magic-link signup) for key management and usage tracking.
- A free tier (currently 100 extractions/month, ≤500 rows / ≤5 MB per CSV upload) and, in the
  future, paid tiers — see `[TODO: link to pricing page once Wave 2 billing ships; no paid plan
  exists as of this document's drafting]`.

The underlying code is published under the MIT License (see §4). Running the Service yourself
("self-hosting") is not governed by these Terms — self-hosters operate their own instance under
the MIT License only. These Terms govern use of **the hosted Service we operate** at
`api.samidhareviews.xyz` / `review-iq-ajjrytb3na-el.a.run.app` and any successor domain.

---

## 2. Acceptable use

You agree not to use the Service to:

1. Submit review text you do not have the right to process, or in violation of a data subject's
   rights under applicable law (see the [Privacy Policy](./privacy-policy.md) and
   [DPA template](./dpa-template.md) for the shared responsibility model).
2. Attempt to exceed, circumvent, or abuse rate limits, quotas, or authentication controls.
3. Attempt to access another organization's data, probe for cross-tenant isolation weaknesses
   outside a coordinated responsible-disclosure process (see `SECURITY.md`), or reverse-engineer
   the Service to bypass metering.
4. Submit content that is unlawful, infringing, or that you know to be malicious (e.g. crafted
   prompt-injection payloads intended to manipulate the underlying LLM beyond normal review
   analysis).
5. Resell or sublicense API access to third parties as a competing hosted service without our
   prior written consent. (Self-hosting the open-source code is explicitly permitted under the
   MIT License and is not restricted by this clause.)

We reserve the right to suspend or terminate access for violations of this section.

---

## 3. Account and API key responsibilities

- You are responsible for safeguarding your `riq_live_*` API key and your Supabase-authenticated
  account session. Keys are shown once at creation/regeneration and are never displayed or
  logged again in plaintext (see `SECURITY.md` §4).
- You are responsible for all activity that occurs under your API key, whether or not you
  authorized it, except to the extent caused by our failure to meet our security obligations.
- If you believe a key has been compromised, regenerate it immediately via
  `POST /account/regenerate-key` (this revokes the old key and issues a new one) or contact us
  at `[TODO: support email — see also SECURITY.md's disclosure contact]`.
- You are responsible for the accuracy of any data (e.g. `org_id`, review text) you submit, and
  for obtaining any consents required from your own end-customers before submitting review text
  that contains their personal data (see the DPA template, §"Controller obligations").

---

## 4. Intellectual property

**Two distinct things are true at once, and this section exists to keep them from being
confused:**

1. **The code is MIT-licensed and open source.** The API implementation, prompts, eval harness,
   and schema are published at `github.com/gaurav-gandhi-2411/review-iq` under the MIT License
   (see `LICENSE` in that repository). Anyone may copy, modify, and self-host it, commercially or
   otherwise, subject only to the MIT License's terms (attribution + warranty disclaimer).
2. **The hosted Service is a separate commercial offering.** When you use the hosted Service we
   operate (as opposed to self-hosting the code), you are a customer of a running service — API
   uptime, managed infrastructure, quota enforcement, support, and (in the future) paid plans.
   Using the hosted Service does not grant you rights beyond the MIT License to our trademarks,
   the "Samidha Reviews" name/mark, or any non-code assets (logo, marketing copy) we may publish
   separately from the MIT-licensed code.

**Your review data.** You (or your organization) retain all ownership rights to the review text
and any resulting structured extraction, authenticity score, or insight generated from it. We
claim no ownership over your data. See `docs/data-ownership.md` in the repository and the
[Privacy Policy](./privacy-policy.md) for how your data is processed, stored, and exported.

**Our IP.** Aside from the MIT-licensed code itself, any Service branding, the hosted
infrastructure, and non-code assets (e.g. the visual identity/logo) remain our property.

---

## 5. Fees and payment

As of this document's drafting, the Service offers a free tier only; no paid plan, billing
integration, or payment processor is live (see `plan.md`'s "Commercial model" section — Wave 2
scope). This section will be completed before any paid tier launches, and will cover: pricing,
billing cycle, refund policy, and consequences of non-payment (quota suspension, not data
deletion, pending the retention policy in
[`data-retention-and-deletion.md`](./data-retention-and-deletion.md)).
`[TODO: GG to complete this section before Wave 2 billing launch — REQUIRES-LEGAL-REVIEW]`

---

## 6. Service availability and limitations

- The Service is provided on a best-effort basis. No uptime SLA is published or guaranteed as of
  this document's drafting (see Wave 1 spec §4.F — a status page and measured SLO are planned
  before any SLA is published).
- We may suspend or rate-limit access for maintenance, abuse mitigation, or upstream provider
  (Groq/Supabase) outages beyond our control.
- The free-tier demo endpoint (`/demo/extract`) is globally rate-limited and provided without
  any availability guarantee.

---

## 7. Disclaimers and limitation of liability

`[TODO: REQUIRES-LEGAL-REVIEW — the following is a standard SaaS template; the specific caps,
carve-outs, and enforceability under Indian contract law must be reviewed by counsel before
publication.]`

THE SERVICE IS PROVIDED "AS IS" AND "AS AVAILABLE," WITHOUT WARRANTIES OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, OR
NON-INFRINGEMENT. WE DO NOT WARRANT THAT EXTRACTION, SENTIMENT, TOPIC, OR AUTHENTICITY OUTPUTS
ARE ACCURATE, COMPLETE, OR SUITABLE FOR ANY PARTICULAR REGULATORY, COMPLIANCE, OR MODERATION
DECISION WITHOUT HUMAN REVIEW (see `docs/compliance.md` — authenticity scoring output is decision
support, never an automated verdict).

TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, OUR AGGREGATE LIABILITY ARISING OUT OF OR
RELATED TO THE SERVICE SHALL NOT EXCEED THE GREATER OF (A) THE FEES YOU PAID US IN THE 12 MONTHS
PRECEDING THE CLAIM, OR (B) `[TODO: GG/counsel to set a floor amount, e.g. INR amount, for
free-tier users who have paid nothing]`. WE ARE NOT LIABLE FOR INDIRECT, INCIDENTAL, SPECIAL, OR
CONSEQUENTIAL DAMAGES.

Nothing in this section limits liability that cannot be limited under applicable law (including,
where applicable, liability for gross negligence, willful misconduct, or breach of statutory data
protection obligations).

---

## 8. Termination

- **By you:** you may stop using the Service at any time. To permanently delete your account and
  all associated data, use the self-service deletion flow (`DELETE /account`, type-to-confirm —
  see [`data-retention-and-deletion.md`](./data-retention-and-deletion.md) for exactly what this
  does).
- **By us:** we may suspend or terminate your access for violation of §2 (Acceptable use),
  non-payment (once paid plans exist), or if required by law. Where practicable, we will provide
  notice before termination for reasons other than security/legal necessity.
- Sections that by their nature should survive termination (IP ownership, liability limitations,
  governing law) survive.

---

## 9. Governing law and dispute resolution

`[TODO: REQUIRES-LEGAL-REVIEW — this clause carries real legal weight and must not be relied
upon without counsel sign-off. Drafted assumption below, not a final decision.]`

These Terms are governed by the laws of India, without regard to conflict-of-law principles.
Courts located in `[TODO: GG/counsel to specify city/jurisdiction — e.g. the courts of the city
where the operating entity is registered]` shall have exclusive jurisdiction over any dispute
arising out of or relating to these Terms or the Service, subject to any mandatory consumer- or
data-protection-law venue rights that cannot be contractually waived (e.g. an EU-based customer's
statutory rights under GDPR are not overridden by this clause).

---

## 10. Changes to these Terms

We may update these Terms as the Service evolves (e.g. when Wave 2 billing or new features ship).
Material changes will be reflected by an updated "Effective date" above and, where practicable,
notice to registered accounts. Continued use of the Service after a change takes effect
constitutes acceptance.

---

## 11. Contact

- **General/legal inquiries:** `[TODO: GG to provide a support/legal contact email]`
- **Security disclosures:** see `SECURITY.md` (`gaurav.gandhi2411@gmail.com`, 72-hour
  acknowledgment target).
- **Data protection / DPDP grievance officer:** see
  [`compliance-posture.md`](./compliance-posture.md).

---

_This document is version-controlled; see git history for changes. Last drafted: 2026-07-31._
