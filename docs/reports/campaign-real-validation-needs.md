# Suspicious-pattern (campaign) detection: what a real-campaign validation needs

Date: 2026-10-11 (S22). Companion to `docs/specs/campaign-detection.md` (pre-registered) and the sealed synthetic run
(recall 0.880 [0.855, 0.903] against 0.782 for the best baseline, 0.060 false alerts per product-month, SYNTHETIC).
The detector ships default-off. No real-world claim is made, and none can be until the following exist.

## Why synthetic is not enough

The sealed run injects campaigns we modelled (volume bursts, rating shifts, reviewer clustering) into real streams
we assumed organic. It can show the detector finds what we thought of, with an upper bound on false alerts under that
assumption. It cannot show that real campaigns look like our injections, that real organic streams contain no
unlabelled campaigns, or that an adaptive seller does not avoid the signals.

## What would turn it into a real validation

| Need | Why | How much (computed) | Who can supply it |
|---|---|---|---|
| Confirmed campaigns with timestamps (platform takedowns, a seller's own removed reviews, a documented incentivised-review event) | Ground truth for recall | Recall interval half-width at 0.88: n=20 -> about 0.14; 30 -> 0.12; 60 -> 0.083; 100 -> 0.064 (Wilson, computed 2026-10-11). Plan for at least 60 confirmed campaigns across at least 20 products | A connected store (Judge.me), a platform partner, or published takedown datasets with licences cleared |
| Hour-resolution timestamps and a stable reviewer identifier | The detector's burst and clustering features | Production extraction rows carry neither ratings nor a verified-purchase flag today (STATUS.md); the ingestion plumbing must carry them | Judge.me connector (real store); a schema addition |
| A prospective false-alert study with a human adjudicating every alert | The synthetic false-alert rate is an upper bound under the "assumed organic" assumption | At 0.06 alerts per product-month, 200 product-months give about 12 alerts to adjudicate; 500 give about 30. Report the share a person judges to be a genuine pattern (alert precision) with a CI | Pilot sellers who accept opt-in alerts |
| Adversarial probing | A seller who knows the rules can stay under them | Re-run the sealed evaluation with injections that spread reviews over days, vary ratings, and use many reviewers; report recall for each | Us, once real campaigns show how they behave |
| Wording review by a person outside the project | The product says "suspicious pattern", never "fake review"; the claim is about timing and similarity, not authenticity | A written sign-off that the UI copy test (already automated) matches what a seller will infer | GG, legal read |

## Pre-registered stop rule for the real study (to be written before it runs)

A real-world claim needs: recall above the best baseline with a CI excluding zero on confirmed campaigns, at most one
false alert per product-month with adjudicated precision reported, and the wording test passing. A result that fails any
of the three is reported as not shipped, with the numbers. Until then the feature remains opt-in and default-off.
