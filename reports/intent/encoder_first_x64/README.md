# Encoder-first routing: capacity multiplier and urgent-review recall (spec Amendment 14, S27)

Registered in `docs/specs/review-intent.md` Amendment 14 (commit ed74662) before any router output or safety-stratum LLM row existed. Served-model decisions only (static-int8 ONNX, committed thresholds, sha256 `4cf745db3af84846...`). Labels are SILVER (four-judge panel). The safety stratum is ENRICHED and keyword-mined: it is never a base rate and is never pooled with the Amendment 5 sealed set. Feature flag `ENABLE_ENCODER_FIRST` stays OFF.

## Current capacity against the pricing quotas (`eval/results/capacity_model.json`, committed)

<!-- METRICS:START:ef_capacity_now -->
binding pool: large (gpt-oss-120b); tokens per extraction {'small': 2524.8, 'large': 2393.9}; traffic mix {'small': 0.4057, 'large': 0.5943}

| | per day | per month (30 d) |
|---|---|---|
| hard limit (200K tokens/day/model) | 140.6 | 4217 |
| 100K working budget | 70.3 | 2109 |
| Starter needs (5,000/month) | 166.7 | 5000 |
| Growth needs (25,000/month) | 833.3 | 25000 |

multiplier needed: Starter 1.19 (hard limit) / 2.37 (100K budget); Growth 5.93 / 11.86
<!-- METRICS:END -->

## Routing share and the capacity multiplier (Amendment 5 sealed set, 757 ids)

<!-- METRICS:START:ef_routing_share -->
R1 routes 214 of 757 = 0.2827 (Wilson 95 [0.2518, 0.3158]); R2 (sensitivity only) routes 206 = 0.2721.
reasons (overlapping): {'needs_action_abstained': 124, 'urgency_abstained': 169, 'needs_action_yes': 82, 'urgency_high_candidate': 4}

| open rate o | multiplier | extractions/day hard | /day 100K | /month hard | /month 100K | Starter (hard/100K) | Growth (hard/100K) |
|---|---|---|---|---|---|---|---|
| 0.0 | 3.537 | 497.3 | 248.6 | 14918 | 7459 | True/True | False/False |
| 0.1 | 2.821 | 396.6 | 198.3 | 11899 | 5949 | True/True | False/False |
| 0.3 | 2.008 | 282.3 | 141.2 | 8470 | 4235 | True/False | False/False |
<!-- METRICS:END -->

## Urgent-review and needs-action recall (criteria C1 to C4)

<!-- METRICS:START:ef_criteria -->
| criterion | n | recall | Wilson 95 | passes (point >= 0.95 and lower >= 0.90; C4 point >= 0.97) |
|---|---|---|---|---|
| C1 safety stratum test, gold urgency=high, R1 routing recall (groups) | 130 groups | 1.0 | [0.9713, 1.0] | True |
| C2 Amendment 5 set, gold urgency=high (reported only) | 4 items | 1.0 | [0.5101, 1.0] | n/a (insufficient data) |
| C3 Amendment 5 set, gold needs_action=yes, R1 routing recall | 111 items | 0.9369 | [0.8755, 0.9691] | False |
| C4 safety test, urgent items the LLM path caught, R1 routing recall (groups) | 44 groups (50 items) | 1.0 | [0.9197, 1.0] | True |
<!-- METRICS:END -->

## Matched-precision reading on the same safety test items (groups)

<!-- METRICS:START:ef_matched_precision -->
LLM rows 720; empty or unparseable or missing 0; ended at the token cap 0

| flag | recall | precision |
|---|---|---|
| encoder_urgency_high_decision | 0.3808 ([0.3054, 0.4704]) | 0.8182 ([0.7197, 0.9069]) |
| llm_urgency_high_answer | 0.3385 ([0.2628, 0.4234]) | 0.7719 ([0.6479, 0.8616]) |
| R1_as_routing_flag | 1.0 ([0.9713, 1.0]) | 0.3412 ([0.2954, 0.3902]) |
<!-- METRICS:END -->

## Verdict under the registered rule

<!-- METRICS:START:ef_verdict -->
encoder-first R1 must NOT be enabled (not met: C3)
<!-- METRICS:END -->

