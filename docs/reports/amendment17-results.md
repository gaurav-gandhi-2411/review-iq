# Amendment 17 results: the recall floor fails F2 (recall on 4+ star reviews, lower bound 0.831 < 0.85)

Registered in `docs/specs/review-intent.md` Amendment 17 at `ff0119afe6a0e1130aafe3521f18e54a096e142b` before any item was drawn; tau
(0.07) fixed by `engine/intent/a17_tau.py` from validation data only. Evaluated once, S31 (2026-10-24 UTC). Labels are SILVER (four-judge
panel). Artifact: `reports/intent/fresh17/result.json` (ids and numbers only). Nothing is enabled or deployed; the router stays OFF.

## What was run

| Step | Evidence |
|---|---|
| Draw | fresh17 = Block A natural 1,800 + Block B stars >= 4, 2,400 (seed 49), against 16,240 prior texts and every earlier manifest + ledger; ledger 11,583 sealed / 10 excluded, leakage check ok (#618) |
| Labels | four-judge panel, one Kaggle session (35,749 s), Ollama 0.40.6 pinned, judge digests identical to Amendment 16; size rule met without top-up: gold-yes A 260, S4 133 |
| Model | run 2 (served static-int8, x64 thresholds), fresh17 scored once on a Kaggle CPU with the pinned numerics (991 s; model sha256 `33158169…`) |
| LLM | gpt-oss-20b zero-shot on Block A, Ollama 0.40.6 pinned, effort low, 1,500-token cap: 1,800/1,800 rows, 0 empty |

## Criteria (R1+ = R1 OR p(needs_action = yes) >= 0.07; R1 alone shown for reference)

| | Measure | R1+ | R1 (report only) | Required | Met |
|---|---|---|---|---|---|
| F1 | recall, Block A gold-yes | 250/260 = 0.962 [0.931, 0.979] | 247/260 = 0.950 | lower bound >= 0.90 | yes |
| **F2** | recall, S4 gold-yes | 119/133 = 0.895 **[0.831**, 0.936] | 115/133 = 0.865 [0.796, 0.913] | lower bound >= 0.85 | **no** |
| F3 | routed share, Block A | 534/1,800 = 0.297 | 0.267 | <= 0.30 | yes (by 0.003) |
| F4 | recall(R1+) minus recall(LLM), Block A | +0.058 [+0.019, +0.100]; LLM 235/260 = 0.904 | — | lower bound > -0.05 | yes |

Wilson 95 percent for proportions; paired bootstrap (2,000 resamples, seed 42) for F4.

## Reading

1. **Complaints hidden in four- and five-star reviews remain the weak stratum.** The floor recovered 4 of the 18 S4 positives R1 misses
   (0.865 -> 0.895), as validation predicted in direction, but a 0.85 Wilson lower bound at n = 133 needs 122 of 133 (0.917). The 14
   remaining misses are confident "no" calls (p(yes) < 0.07 and no abstention). On validation, the next lower floor that caught more
   (0.055) already routed 0.311 of reviews, over the 0.30 cap; fresh17 is not used to look for one (it is spent).
2. **F3 passed by 0.003**, as the pre-registration warned (about even odds).
3. **F4 is better than non-inferior**: the encoder router with the floor catches more Block A complaints than gpt-oss-20b zero-shot (lower
   bound +0.019), at 0.297 of the extraction volume (capacity multiplier 2.73 at open rate 0.1).
4. In Amendments 16 and 17 the router met every overall-recall and LLM-comparison criterion and failed on the positive-star stratum
   both times (A16 E2 with run 4, A17 F2 with run 2 + floor; Amendment 14's C3, a different sealed-set criterion, is also still unmet).
   On this evidence the remaining problem is what a "4 or 5 stars but needs action" review looks like, not where the threshold sits
   (BELIEVED: an interpretation, not a tested claim).

## What happens next

As registered, no further amendment in this session. Nothing changes in production. A next step for the owner, not started: collect
human-checked examples of positive-star reviews that need action (the panel's silver labels on this stratum are also the least reliable:
sentiment alpha 0.64), then a training change targeted at them, evaluated on yet another fresh set.
