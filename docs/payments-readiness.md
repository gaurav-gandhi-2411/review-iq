# Payments readiness (Samidha Reviews)

Status: research note, no code. Facts read 2026-09-20 ("verified 2026-09-20" = page fetched that day);
items tagged [Un] are UNVERIFIED (list at the end). Not legal or tax advice: confirm entity, GST and
FEMA points with a Chartered Accountant before acting. Prices on `site/index.html`: Starter
$29/INR 1,499, Growth $79/INR 4,999, Scale $199/INR 12,999 per month; annual = 2 months free
(GST-inclusive or exclusive is not stated [U7]). Today first customers are invoiced manually.

## 1. Gateway options (all verified 2026-09-20 unless tagged)
<!-- METRICS:HISTORICAL -->

| Option | Onboarding (proprietor?) | Recurring / USD route | Fee headline |
|---|---|---|---|
| Razorpay [1][2][3] | PAN + bank proof + KYC; proprietor may use personal bank a/c; GSTIN only if registered; Udyam optional | Subscriptions: cards, UPI AutoPay, eMandate. International cards need separate activation (video KYC, purpose codes); settles INR. Individuals are steered to PayPal/bank transfer [U2] | 2% domestic; up to 3% intl; recurring cards +0.9%; 18% GST on fees |
| Stripe India [4][5] | Invite-only since 2024; no self-serve signup; contact sales | Strong Billing + e-mandate support, INR/USD presentment | 2% dom, 3-4.3% intl, +2% FX, Billing 0.7% |
| PayU [6][7] | Proprietorship: bank proof + 2 govt proofs (GST/MSME/IEC/ITR...) + signer PAN | Recurring rate not published [U8] | 2% dom, 3% intl, +18% GST |
| Cashfree [8][9] | Proprietor: 2 business proofs (Udyam etc.) + PAN/Aadhaar + current a/c; LLP/Pvt Ltd via incorporation docs | Subscriptions: cards/NACH/UPI AutoPay; intl cards 140+ currencies | 1.95% dom (0% promo to Mar 2027), 2.99% intl; mandates INR 7.5 + 5-15 each |
| Paddle (MoR) [10][11] | Sellers "anywhere except sanctioned list"; business verification "not required for individuals or sole traders"; India-specific terms [U6] | Full subscription billing, global tax; payouts to seller | 5% + 50c (custom below $10) |
| Dodo Payments (MoR) [12][13] | India-built. Bank type decides class: personal a/c = Individual (even with Udyam/GST), company a/c = Organization; GST optional below threshold | India-domestic checkout (UPI/cards) supported. INR is not a payout wallet: payouts in USD/GBP/EUR | 4% + 40c US; India 4% + 15c; +1.5% intl, +0.5% subs; $5 if payout < $1,000 |
| Lemon Squeezy (MoR) [14][15] | India listed, but bank payout needs a Stripe-approved account; else PayPal only. Folding into Stripe Managed Payments (invite, 35+ countries; India not addressed) [U10] | Subscriptions yes | Not fetched [U6] |
<!-- /METRICS:HISTORICAL -->

RBI e-mandate rules (apply to every INR recurring option): Digital Payments E-mandate Framework, 2026
(21 Apr 2026): AFA-free recurring only up to INR 15,000 per debit after one-time AFA mandate
registration; pre-debit notification at least 24h before each debit; customer can opt out [16].
Consequence for our prices (my arithmetic, not a source): monthly plans (max INR 12,999) fit under the
limit; annual plans at 10x monthly are INR 14,990 (Starter, just under), 49,990 (Growth), 129,990
(Scale) and would need AFA on every renewal, and Stripe's docs say UPI recurring does not exceed
INR 15,000 [5]. Sell Growth/Scale annual as one-off invoices/payment links, not auto-debit.

## 2. Entity route (cheapest legitimate path)

- Sole proprietorship + Udyam: no MCA filing; Udyam is free and only a recognition, no limited liability
  [17]. Razorpay/PayU/Cashfree/Dodo/Paddle all have a proprietor path per the table. Needs a current
  account in the business name for Cashfree/PayU-style proofs. Cheapest, fastest, personal liability.
- LLP (roughly INR 9-23k all-in) [18]: needs 2+ partners [U1], so not a solo route.
- Pvt Ltd (roughly INR 7-25k) [18] or OPC (single member): limited liability, cleaner for Stripe/
  investors/enterprise contracts, higher annual compliance. Directors/members minimum [U1].
- Suggested: proprietorship + Udyam + current account now; convert to Pvt Ltd/OPC when B2B contracts
  or liability demand it (gateway accounts do not transfer; re-onboard). Confirm with a CA.

## 3. GST and export of services

- Registration: inter-state services suppliers are exempt from registration up to INR 20 lakh
  aggregate turnover (INR 10 lakh special-category states) per Notification 10/2017-IT, original
  2017 text read [19]; later amendments not checked [U5]. Practitioner sources conflict on whether
  exporters must register regardless of turnover [20]; LUT (RFD-11) is filed while logged in with a
  GSTIN [21], so in practice register before the first export. Domestic SaaS to Indian customers:
  <!-- METRICS:HISTORICAL --> 18% (SAC 9983xx) [22], charged once registered/over threshold. <!-- /METRICS:HISTORICAL -->
- Export of services is zero-rated under s.2(6) IGST: supplier in India, recipient and place of
  supply outside India, payment in convertible foreign exchange (or INR where RBI permits),
  separate entities [23]. Supply under LUT, filed before first export each FY, valid one FY
  (Form RFD-11, ineligible if prosecuted for tax evasion of INR 2.5 cr or more) [21].
- Proof of realisation: Rule 96A: proceeds within one year of invoice else IGST + 18% interest
  [20]; keep FIRC/e-FIRA per receipt [20][23]. Invoices must cite the LUT number [20]; other Rule 46
  fields (GSTIN, sequential number, SAC, place of supply) not fetched [U11].
- MoR: the MoR is the seller to the end customer [24]; GG then supplies the MoR, not the end buyer.
  Whether that is a clean export of services, and whether the MoR route yields FIRC/e-FIRA for GG,
  rests on vendor-authored claims only [24][13] and my inference [U4]. It removes foreign VAT/sales-tax
  duty, not GG's own Indian GST/LUT/FEMA duty.

## 4. What of PR #43 (orphaned Stripe work) is salvageable

Finding: PR #43 shows MERGED (2026-07-31T18:45:36Z, merge commit `e07d13d`) but its base was the
stacked branch `fix/wave1-s0-bypassrls-remediation`, not `main`; that stack ended in closed PR #27, so
nothing reached main (`git merge-base --is-ancestor` on 5f2e674 and e07d13d vs origin/main: not
ancestors; main has no billing/stripe files). Still lives at: branch `origin/feat/wave2-p4-minimal-billing`,
commit `5f2e67461a32cc17b552b66724ae429dad07ba3c` (1 commit, 11 files, +1069/-4), and merge commit `e07d13d`
on `origin/fix/wave1-s0-bypassrls-remediation` and `origin/feat/wave1-e-security-legal`. Sibling: PR #42
(ADR 0007 pricing from COGS, `docs/architecture/adr/0007-pricing-tiers-from-real-cogs.md`) is orphaned
the same way, branch head `de27404`. A trial 3-way merge onto current main conflicts in `app/api/account.py`,
`pyproject.toml`, `uv.lock`.

| File(s) | Verdict |
|---|---|
| `app/api/webhooks/stripe.py` (+189), `app/core/billing.py` (+175) | Stripe-only. Reuse the shape: verify signature before parse, 4-event lifecycle (checkout done / sub updated / sub deleted / payment failed), server-side org_id round-tripped in metadata. Provider calls rewritten for a new gateway. |
| `app/core/billing_storage.py` (+128) | Best asset: updates `organizations.plan` AND `api_keys.quota` together (quota is per-key, a real wiring bug it caught). Must change: `get_org_id_for_stripe_customer_pg` uses raw `psycopg2.connect`, which predates the S0 role separation; use a tenant resolver like `20260801000002`. |
| `supabase/migrations/20260731000003_...` | Idea reusable (status enum, unique customer-id index) but `stripe_*` columns become gateway-neutral and the timestamp/`plan` CHECK must be rebased on main's ledger and grants. |
| `tests/unit/test_billing.py` (222 lines, 13 tests) | HMAC-vector signature tests reusable as a pattern; checkout/portal tests are Stripe-mocked, discard. |
| `PLAN_QUOTAS` 2,000/10,000; ADR 0007/0008 $19 Starter | STALE: site says 5,000/25,000 reviews and $29. `bff/router.py` on main carries a placeholder `PLAN_QUOTA_LIMITS` awaiting real tiers. |
| Stripe Checkout/Portal/Smart Retries, `stripe` dep, `uv.lock` | Assumes Stripe direct availability for an unregistered seller; invalid [4]. Drop; regenerate lockfile. |
| ADR 0008 escalation steps | Structure (test mode first, webhook secret in Secret Manager) reusable; content Stripe-specific. |

## 5. Recommended path and checklist

Path: proprietorship + Udyam -> Razorpay for INR (domestic Subscriptions, UPI AutoPay, monthly plans);
USD customers by Razorpay international if approved, else a MoR (Dodo is India-built and takes an
individual account; Paddle is the mature alternative). Stripe only if an invite arrives. Engineer
behind a gateway `Protocol`, not a Stripe assumption.

GG checklist, in order:
1. Confirm entity route and GST/FEMA points with a CA.
2. Udyam registration; open a current account in the business name.
3. GST registration (before first export, see s3); file LUT (RFD-11) each FY.
4. Apply: Razorpay (INR); enable international or apply to a MoR; keep FIRC/e-FIRA for each receipt.
5. Give engineering: test-mode keys, webhook secret, plan IDs, decided prices and GST-inclusive rule.

Engineering (list only, not started):
- Gateway-neutral billing tables/columns; provider adapter `Protocol`; test-mode-first config.
- Plan/entitlement table as single source; retire `PLAN_QUOTA_LIMITS`; sync `api_keys.quota` atomically.
- Webhook idempotency: store provider event id (unique), ack duplicates, tenant-scoped org resolution.
- Dunning [U3]: states past_due/grace/suspend, retry emails, e-mandate pre-debit surfaced to the customer.
- GST invoices: sequential numbers, GSTIN, SAC, place of supply, LUT reference for exports, credit notes.
- Reconciliation job (provider vs DB), FIRC/e-FIRA store, metrics/alerts on failed webhooks.

## Sources (fetched 2026-09-20)
[1] https://razorpay.com/docs/payments/account-activation-support/?preferred-country=IN
[2] https://razorpay.com/pricing/  [3] https://razorpay.com/docs/payments/payments/international-payments/
[4] https://support.stripe.com/questions/stripe-accounts-are-invite-only-in-india
[5] https://docs.stripe.com/india-recurring-payments (also https://stripe.com/en-in/pricing)
[6] https://payu.in/pricing/  [7] https://docs.payu.in/docs/documents-checklist-for-account-activation
[8] https://www.cashfree.com/payment-gateway-charges/  [9] https://www.cashfree.com/docs/help/account/account-activation
[10] https://www.paddle.com/pricing  [11] https://www.paddle.com/help/start/account-verification/what-is-business-verification (also .../which-countries-are-supported-by-paddle)
[12] https://dodopayments.com/pricing  [13] https://docs.dodopayments.com/miscellaneous/faq
[14] https://docs.lemonsqueezy.com/help/getting-started/supported-countries  [15] https://www.lemonsqueezy.com/blog/2026-update
[16] https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=13374
[17] https://www.incorpx.io/blog/sole-proprietorship-registration-process-documents (search snippet only)  [18] https://www.taxaj.com/learn/company-registration-fees-india/
[19] https://cbic-gst.gov.in/hindi/pdf/integrated-tax/10_2017_IT.pdf
[20] https://taxguru.in/goods-and-service-tax/guide-lut-filing-zero-rated-export-supply-compliance.html (Rule 96A also via search summary of xflowpay/payglocal blogs)
[21] https://tutorial.gst.gov.in/userguide/refund/Furnishing_of_Letter_of_Undertaking_for_Export_of_Goods_or_Services.htm
[22] https://www.xflowpay.com/blog/gst-on-software-services (search summary only)
[23] https://razorpay.com/blog/export-services-gst-conditions-guide/
[24] https://razorpay.com/blog/merchant-of-record-vs-international-payment-gateway-decision-guide/ (competitor-authored)

## UNVERIFIED
U1 LLP/Pvt Ltd/OPC minimum partners/directors (statute, not fetched). U2 Razorpay treatment of a Udyam
proprietor for international payments. U3 Razorpay dunning/retry, webhook signature scheme, hosted portal.
U4 MoR-to-GG export/GST/FIRC treatment. U5 amendments to 10/2017-IT; exporter registration conflict.
U6 Paddle India specifics, INR checkout on MoRs, Lemon Squeezy fees. U7 site price GST basis.
U8 PayU recurring fee; Cashfree/PayU proprietor docs partly from vendor pages. U10 Stripe Managed
Payments in India. U11 full Rule 46 invoice fields. Also [17], [22] were search summaries only.
