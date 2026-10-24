# Amendment 16 results: run 4 does not meet the registered criteria (E2 and E5 fail)

Registered in `docs/specs/review-intent.md` Amendment 16 at `03084fc37c3c756b52f0d1ccbfc4eab151af0836`, before any draw.
Evaluated once, S31 (2026-10-24 UTC). Labels are SILVER (four-judge panel consensus, not human gold). Artifact:
`reports/intent/fresh16/result.json` (ids and numbers only). Nothing is enabled or deployed; the router stays OFF and
production keeps serving run 2.

## What was run

| Step | Evidence |
|---|---|
| Draw | `fresh16` sealed 3,900 (Block A natural 1,800; B stars >= 4, 1,700; C > 128 tokens, 400), `train16` 2,400 (T1 stars >= 4 + problem lexicon, T2 long), seeds 46/47, ledger-checked (#592/#593) |
| Labels | four-judge panel, Ollama 0.40.6 sha256 `c164ab8b…c0e5e0`, three Kaggle sessions, `panel_pin` over all three: recorded and correct (#615). Size rule met without a top-up: gold-yes A 268, S4 126, SL 135 |
| Capacity probe | Cloud Run, registered budget p95 < 1 s: 128 tokens pass (355 ms), 256 fail (1,107 ms), 512 fail (2,137 ms) -> arms H128 and HT128 only (`reports/serving/classify_redeploy_s31.md`) |
| Training | Kaggle `review-intent-run4` v1, one T4, 2,549 s: H128 best validation macro-F1 0.745 (epoch 10), HT128 0.718 (epoch 6) |
| Selection (validation only, code) | H128 chosen: HT128 had the higher R1 score (0.959 vs 0.932) but its mean F1 0.678 is not within 0.01 of the best (0.728). Independently recomputed by a verifier from validation outputs: 0 differences |
| Quantisation | Addendum 1 automatic rule: accepted, validation agreement with fp32 0.9859 (>= 0.90), ffn_out on layers [0, 1, 9, 10, 11]; model sha256 `ede02592…8176d` |
| Scoring | run 4 on fresh16 inside the same kernel after selection (Kaggle CPU, pinned ORT); run 2 on fresh16 in a separate Kaggle CPU kernel (pinned); gpt-oss-20b zero-shot on Block A, Ollama 0.40.6 pinned, effort low, 1,500-token cap, 1,800/1,800 rows |

## Criteria (run 4; run 2 shown for reference, not a criterion)

| | Measure | Run 4 | Run 2 (report only) | Required | Met |
|---|---|---|---|---|---|
| E1 | R1 recall, Block A gold-yes | 253/268 = 0.944 [0.910, 0.966] | 257/268 = 0.959 [0.928, 0.977] | lower bound >= 0.90 | yes |
| **E2** | R1 recall, S4 (stars >= 4) gold-yes | 114/126 = 0.905 **[0.841**, 0.945] | 116/126 = 0.921 [0.860, 0.956] | lower bound >= 0.85 | **no** |
| E3 | R1 recall, SL (> 128 tokens) gold-yes | 123/135 = 0.911 [0.851, 0.948] | 127/135 = 0.941 [0.887, 0.970] | lower bound >= 0.85 | yes |
| E4 | routed share, Block A | 0.223 [0.204, 0.243] | 0.264 | <= 0.30 | yes |
| **E5** | macro-F1 run 4 minus run 2, Block A | needs_action +0.001, broad_intent +0.067, **sentiment -0.021** [-0.081, +0.040], urgency +0.085 | — | no task drops more than 0.02 | **no** |
| E6 | run 4 recall minus LLM recall, Block A gold-yes | +0.037 [-0.004, +0.078]; LLM recall 243/268 = 0.907; 0 missing LLM rows | — | non-inferior, margin 0.05 | yes |

Intervals: Wilson 95 percent for proportions; paired bootstrap (1,000 resamples, seed 42) for E5 and E6.

## Reading

1. **The targeted training additions did not help the weak strata.** Run 4's recall is at or below run 2's on every subset
   (Block A 0.944 vs 0.959, S4 0.905 vs 0.921, SL 0.911 vs 0.941) and it routes less (0.223 vs 0.264). Diagnosis on
   validation data only: run 4 abstains less, and abstention is exactly how R1 catches uncertain complaints; on the 4+ star
   validation reviews it routes 7.0 percent against run 2's 10.0 percent. The lexicon-selected additions were easy for
   both models (run 2 already routes 62 of 63 targeted validation positives), so they taught confidence, not recall.
2. **E5 fails by the registered point rule on sentiment (-0.021 against a 0.02 limit)** with an interval that spans zero
   widely; sentiment is also the one task whose fresh16 labels FAIL the label-quality gate (alpha 0.660, AC1 0.868,
   prevalence skew from Block B). Recorded as a failure, with that caveat, not explained away.
3. **E6 holds**: the encoder router is non-inferior to the all-LLM path on Block A recall at a quarter of the extractions
   (capacity multiplier at open rate 0.1: 3.33 for run 4).
4. Run 2's numbers above are report-only; they are not a pass for run 2 under this amendment.

## What happens next

Nothing changes in production. The registered follow-up (at most one cycle, S31 spec P8) is Amendment 17: keep the served
model (run 2) and add a recall-calibrated needs_action floor to R1, chosen on validation data only, then test it once on a
fresh sealed set. Its pre-registration states the fix, the selection rule, the sample and the criteria before any draw.
