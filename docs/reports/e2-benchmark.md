# Classification engine: E2 benchmark report (S21, rewritten S22)

Spec and pre-registered protocol: `docs/specs/classification-engine.md`. Every number below is rendered from the
result JSONs in `reports/engine/e2/` by `engine/experiments/report.py` (regenerate with
`python -m engine.experiments.report reports/engine/e2`) or read from the same files by the prose generator. Models
were trained on free Kaggle Tesla T4s; the LLM baseline ran on Groq (dedicated org, free tier, 100K tokens per model
per UTC day); the serving bench ran on a Kaggle CPU kernel (Intel Xeon 2.2 GHz, 1 thread).

## What changed since the first version of this report

1. **Like-for-like metrics.** The first version compared macro-F1 with accuracy in places (a fine-tuned macro-F1 against a
   published accuracy; a fine-tuned macro-F1 against an LLM's accuracy). Every arm and every published reference now appears in
   BOTH metrics in the first table below, and the prose compares accuracy to accuracy and macro-F1 to macro-F1 only. A metric
   a source did not report is shown as "not reported", never filled in.
2. **Precision at coverage is the headline claim** (section "Precision at coverage"), with CIs, a threshold chosen on other
   data, and the cost of making the claim safe.
3. **The LLM comparison is paired.** Same items, both arms, with a verbalised confidence from the LLM. The earlier
   unpaired rows (different subsamples) are kept only where labelled.
4. **Training is reproducible.** All 21 Track A jobs were re-run on a second Kaggle session at a later commit: macro-F1 and
   accuracy are bit-identical in every run (max absolute difference 0.0).

## Headline: calibration, not accuracy

At a 95 percent precision requirement on BANKING77 (77 intents, 168 paired test items), the fine-tuned e5-base model answers
**96.4 percent** of inputs (coverage [0.869, 1.000]); gpt-oss-120b, prompted zero-shot and ranked by its own
verbalised confidence, answers **13.1 percent** (coverage [0.000, 0.488]). The intervals do not overlap. That is the measured edge:
the fine-tuned model knows which answers to trust, so most traffic can be automated at a stated precision, while the prompted model's
confidence barely ranks its answers.

Read this with its limits: (1) it is a public benchmark, not review text; no review-intent metric exists yet (see the review-intent reports).
(2) Both coverages are oracle points, with the threshold chosen on the test curve; the honest, transferable operating point is the
conservative-threshold row in the Verdict (a few points lower coverage). (3) n = 168, so the intervals are wide, and the
LLM's is wide because few of its answers clear the bar. (4) One LLM, one prompt, zero-shot; a stronger prompt or a retrieval-augmented
few-shot arm is untested here. (5) The confidence signal is a verbalised number, not token log-probabilities, which the provider did not expose.

## Verdict

1. **Known-intent quality matches the published numbers it can be compared with.** Main model: multilingual-e5-base, 3 seeds.
   BANKING77 accuracy 0.936 and macro-F1 0.936; published RoBERTa-base accuracy 0.941 (macro-F1 not reported).
   MASSIVE en-US accuracy 0.900 and macro-F1 0.880; the published XLM-R accuracy is 0.883 and our own XLM-R reproduction gives
   0.886. CLINC150 accuracy 0.967 and macro-F1 0.966; aggregators list about 0.97 to 0.98 (model and split unstated).
2. **Precision at coverage, BANKING77, e5.** On the test curve precision stays at or above 0.95 up to coverage 0.974
   [0.961, 0.989] (an oracle point). Precision at coverage 0.9 / 0.8 / 0.7 is 0.977 / 0.988 / 0.991.
   A threshold chosen to hit exactly 0.95 on half of the items reached it on the other half only 0.44 of the time;
   requiring the Wilson lower bound to clear 0.95 on the tuning half gives precision 0.961 at coverage
   0.952 and hits the target in 0.96 of splits. CLINC150 stays above 0.95 at full coverage (accuracy
   0.968); MASSIVE en-US reaches 0.876 oracle and 0.825 with the conservative rule (hit rate 0.92).
   The sentence a product may say is the conservative one, and only for this kind of text.
3. **Against a prompted LLM on the same items.** BANKING77, gpt-oss-120b zero-shot, 168 items: fine-tuned accuracy
   0.917 against 0.786; difference 0.131 [0.071, 0.196], exact McNemar p = 0.00011
   (27 items only the fine-tuned model got right, 5 only the LLM). The LLM's verbalised confidence barely ranks its answers: it
   reaches precision 0.95 at coverage 0.131 [0.000, 0.488] against
   0.964 for the fine-tuned model on the same items, and its precision at coverage 0.9 is
   0.837 against 0.974. 
4. **CLINC150 LLM comparison.** On CLINC150 the paired run covers n = 109 items (the token ceiling and qwen's repeated 503 over-capacity responses stopped it there): fine-tuned accuracy 0.936 against 0.881 for the LLM, difference 0.055 [0.000, 0.119], exact McNemar p = 0.10938 (8 items only the fine-tuned model got right, 2 only the LLM). The interval includes zero: this comparison is UNDERPOWERED and is not evidence of equality. Separating a gap of this size needs a few hundred paired items, about three UTC days of the qwen budget for a 150-label prompt; the harness resumes where it stopped.
5. **Open-set rejection.** Mahalanobis on the fine-tuned features has the best AUROC on all four unknown sets; the dedicated
   unknown head is the worst scorer everywhere. Energy matches or beats Mahalanobis on rejection recall on two sets and needs no
   statistics or int8 recalibration (the safe fallback). Every scorer clears the 23 to 34 percent range reported by the earlier
   private project; that is context on a different task, not a head-to-head.
6. **Serving meets 100 ms p95 on one thread** for both encoders after the threshold fix (below); nothing is deployed.

## Deviations from the pre-registered protocol (disclosed)

- Active learning ran an initial 100 random rows plus 4 uncertainty rounds of 100, not 5 rounds.
- Hierarchy compares flat, joint parent loss, and domain-constrained prediction; a separate two-stage classifier was not built.
- LLM baseline: BANKING77 used `openai/gpt-oss-120b` because `qwen/qwen3.8-27b` returned HTTP 503 (over capacity) on it; CLINC used qwen. Few-shot
  rows (5 retrieved examples, not 3 per class) are small, unpaired and labelled so. The first CLINC attempt died on a connection reset; the
  harness now retries transport errors and 5xx responses and resumes across UTC days.
- Three training seeds (42, 43, 44) on ONE pre-registered draw of held-out classes, so Track B ranges show training noise, not class-selection noise.
- The first sweep (commit a9f9e21) is superseded (zero-support classes were counted as F1 = 0 in the point estimate only); every reported number was re-run on the fixed code.
- Rejection-recall intervals hold the validation threshold fixed, so they omit threshold-selection variance.
- The precision-at-coverage "oracle" points choose the threshold ON the test curve; the half-split rows are the honest operating points.

## Tables (generated)

<!-- METRICS:START -->
<!-- generated by engine/experiments/report.py from reports/engine/e2; do not edit -->
Reference point for Track B from a separate private project: 23-34% rejection recall at 95% retention, AUROC about 0.87 (private 12-class, 500-row task).

### Like-for-like: accuracy and macro-F1 for every arm and reference

| Dataset | Arm | Accuracy | Macro-F1 | Basis |
|---|---|---|---|---|
| banking77 | e5 (ours) | 0.936 | 0.936 | mean of 3 seed(s), sealed test |
| banking77 | minilm (ours) | 0.921 | 0.921 | mean of 3 seed(s), sealed test |
| banking77 | xlmr (ours) | 0.923 | 0.923 | mean of 1 seed(s), sealed test |
| banking77 | published: RoBERTa-base, full data | 0.941 | not reported | arXiv 2012.03929 |
| clinc | e5 (ours) | 0.967 | 0.966 | mean of 3 seed(s), sealed test |
| clinc | minilm (ours) | 0.957 | 0.957 | mean of 3 seed(s), sealed test |
| clinc | xlmr (ours) | 0.960 | 0.960 | mean of 1 seed(s), sealed test |
| clinc | published: best aggregator entries, model and split unstated | about 0.97 to 0.98 | not reported | hyper.ai / wizwand list about 0.97 to 0.98 |
| massive_en | e5 (ours) | 0.900 | 0.880 | mean of 3 seed(s), sealed test |
| massive_en | minilm (ours) | 0.882 | 0.838 | mean of 3 seed(s), sealed test |
| massive_en | xlmr (ours) | 0.886 | 0.856 | mean of 1 seed(s), sealed test |
| massive_en | published: XLM-R base, en-US | 0.883 | not reported | arXiv 2204.08582 / ACL 2023 long.235, per-locale table |

### Track A (known intents)

| Dataset | Model | Seeds | Macro-F1 mean (range) | Seed-42 macro-F1 [95% CI] | Accuracy mean |
|---|---|---|---|---|---|
| banking77 | e5 | 3 | 0.936 (0.934-0.940) | 0.934 [0.925, 0.942] | 0.936 |
| banking77 | minilm | 3 | 0.921 (0.918-0.923) | 0.923 [0.913, 0.931] | 0.921 |
| banking77 | xlmr | 1 | 0.923 | 0.923 [0.914, 0.932] | 0.923 |
| clinc | e5 | 3 | 0.966 (0.965-0.968) | 0.968 [0.962, 0.973] | 0.967 |
| clinc | minilm | 3 | 0.957 (0.954-0.959) | 0.957 [0.950, 0.963] | 0.957 |
| clinc | xlmr | 1 | 0.960 | 0.960 [0.954, 0.965] | 0.960 |
| massive_en | e5 | 3 | 0.880 (0.879-0.881) | 0.879 [0.860, 0.901] | 0.900 |
| massive_en | minilm | 3 | 0.838 (0.818-0.851) | 0.851 [0.826, 0.871] | 0.882 |
| massive_en | xlmr | 1 | 0.856 | 0.856 [0.834, 0.878] | 0.886 |

### Precision at coverage (the product claim)

| Dataset | Model | Seeds | Oracle coverage at precision 0.95 (seed 42 [95% CI]) | Precision at coverage 0.9 / 0.8 / 0.7 (seed-42, CI at 0.9) | Chosen on half, point rule: precision / coverage / hit rate | Chosen on half, conservative rule: precision / coverage / hit rate |
|---|---|---|---|---|---|---|
| banking77 | e5 | 3 | 0.974 [0.961, 0.989] | 0.977 [0.970, 0.984] / 0.988 / 0.991 | 0.950 / 0.974 / 0.44 | 0.961 / 0.952 / 0.96 |
| banking77 | minilm | 3 | 0.948 [0.927, 0.965] | 0.967 [0.960, 0.973] / 0.977 / 0.986 | 0.951 / 0.947 / 0.51 | 0.962 / 0.918 / 0.95 |
| banking77 | xlmr | 1 | 0.949 [0.928, 0.970] | 0.967 [0.959, 0.973] / 0.983 / 0.988 | 0.950 / 0.948 / 0.54 | 0.962 / 0.916 / 0.96 |
| clinc | e5 | 3 | 1.000 [1.000, 1.000] | 0.994 [0.991, 0.996] / 0.997 / 0.998 | 0.968 / 1.000 / 1.00 | 0.968 / 1.000 / 1.00 |
| clinc | minilm | 3 | 1.000 [1.000, 1.000] | 0.988 [0.984, 0.992] / 0.993 / 0.996 | 0.958 / 1.000 / 0.99 | 0.960 / 0.996 / 0.99 |
| clinc | xlmr | 1 | 1.000 [1.000, 1.000] | 0.990 [0.986, 0.993] / 0.996 / 0.996 | 0.961 / 1.000 / 1.00 | 0.961 / 0.999 / 1.00 |
| massive_en | e5 | 3 | 0.876 [0.842, 0.905] | 0.943 [0.932, 0.952] / 0.969 / 0.978 | 0.950 / 0.874 / 0.51 | 0.962 / 0.825 / 0.92 |
| massive_en | minilm | 3 | 0.814 [0.762, 0.865] | 0.932 [0.922, 0.941] / 0.951 / 0.964 | 0.950 / 0.810 / 0.48 | 0.963 / 0.703 / 0.94 |
| massive_en | xlmr | 1 | 0.864 [0.835, 0.891] | 0.936 [0.926, 0.947] / 0.966 / 0.975 | 0.950 / 0.861 / 0.48 | 0.962 / 0.818 / 0.95 |

### Fine-tuned vs LLM on the SAME items (paired)

| Dataset | LLM | n paired | Fine-tuned accuracy | LLM accuracy | Difference [95% CI] | McNemar exact p | LLM coverage at precision 0.95 (verbalised confidence) | Fine-tuned coverage at precision 0.95 on the same items |
|---|---|---|---|---|---|---|---|---|
| banking77 | openai/gpt-oss-120b zero-shot | 168 | 0.917 | 0.786 | 0.131 [0.071, 0.196] | 0.00011 | 0.131 [0.000, 0.488] | 0.964 [0.869, 1.000] |
| clinc | qwen/qwen3.8-27b zero-shot | 109 | 0.936 | 0.881 | 0.055 [0.000, 0.119] | 0.10938 | 0.688 [0.578, 0.982] | 0.973 [0.881, 1.000] |

### Track B (open set)

| Dataset | Unknown set | Scorer | AUROC mean (range) | Rejection recall @95% val-retention | Seed-42 recall [95% CI] | Seed-42 AUROC [95% CI] | Known retention | Answered macro-F1 drop (pts) | Guard |
|---|---|---|---|---|---|---|---|---|---|
| banking77 | held_out_classes | msp_raw | 0.922 (0.916-0.927) | 0.501 | [0.4462, 0.5162] | [0.9164, 0.937] | 0.954 | -2.2 | ok |
| banking77 | held_out_classes | msp_calibrated | 0.925 (0.920-0.930) | 0.537 | [0.5, 0.5687] | [0.92, 0.94] | 0.953 | -2.3 | ok |
| banking77 | held_out_classes | energy | 0.931 (0.926-0.936) | 0.622 | [0.6388, 0.7025] | [0.9262, 0.9461] | 0.953 | -1.9 | ok |
| banking77 | held_out_classes | mahalanobis | 0.941 (0.938-0.946) | 0.660 | [0.7, 0.7613] | [0.9368, 0.9542] | 0.951 | -1.9 | ok |
| banking77 | held_out_classes | unknown_head | 0.895 (0.873-0.918) | 0.582 | [0.5663, 0.6362] | [0.8805, 0.9085] | 0.956 | -1.7 | ok |
| clinc | clinc_oos_test | msp_raw | 0.976 (0.975-0.976) | 0.905 | [0.883, 0.919] | [0.9715, 0.9801] | 0.940 | -2.2 | ok |
| clinc | clinc_oos_test | msp_calibrated | 0.977 (0.977-0.977) | 0.912 | [0.888, 0.922] | [0.9727, 0.981] | 0.940 | -2.2 | ok |
| clinc | clinc_oos_test | energy | 0.981 (0.981-0.982) | 0.925 | [0.906, 0.939] | [0.9776, 0.9852] | 0.940 | -2.1 | ok |
| clinc | clinc_oos_test | mahalanobis | 0.983 (0.983-0.984) | 0.933 | [0.917, 0.948] | [0.9797, 0.9868] | 0.939 | -2.1 | ok |
| clinc | clinc_oos_test | unknown_head | 0.960 (0.957-0.962) | 0.893 | [0.88, 0.918] | [0.9542, 0.9691] | 0.935 | -1.7 | ok |
| clinc | held_out_classes | msp_raw | 0.935 (0.932-0.940) | 0.720 | [0.6889, 0.7467] | [0.932, 0.9475] | 0.940 | -2.2 | ok |
| clinc | held_out_classes | msp_calibrated | 0.937 (0.933-0.941) | 0.727 | [0.6956, 0.7544] | [0.9333, 0.9487] | 0.940 | -2.2 | ok |
| clinc | held_out_classes | energy | 0.940 (0.936-0.946) | 0.751 | [0.7333, 0.79] | [0.938, 0.9534] | 0.940 | -2.1 | ok |
| clinc | held_out_classes | mahalanobis | 0.953 (0.951-0.955) | 0.747 | [0.7311, 0.7867] | [0.9494, 0.961] | 0.939 | -2.1 | ok |
| clinc | held_out_classes | unknown_head | 0.903 (0.888-0.915) | 0.681 | [0.6667, 0.7278] | [0.904, 0.9255] | 0.935 | -1.7 | ok |
| massive_en | held_out_classes | msp_raw | 0.861 (0.859-0.866) | 0.369 | [0.3784, 0.4395] | [0.852, 0.8784] | 0.941 | -4.5 | ok |
| massive_en | held_out_classes | msp_calibrated | 0.865 (0.863-0.869) | 0.398 | [0.41, 0.4761] | [0.8554, 0.8814] | 0.944 | -4.5 | ok |
| massive_en | held_out_classes | energy | 0.874 (0.872-0.877) | 0.462 | [0.4791, 0.5422] | [0.862, 0.8879] | 0.946 | -4.2 | ok |
| massive_en | held_out_classes | mahalanobis | 0.889 (0.882-0.897) | 0.432 | [0.4507, 0.5107] | [0.8861, 0.9079] | 0.940 | -4.8 | ok |
| massive_en | held_out_classes | unknown_head | 0.795 (0.785-0.802) | 0.341 | [0.294, 0.352] | [0.768, 0.8024] | 0.939 | -0.7 | ok |

### Hierarchy

| Dataset | Variant | Macro-F1 [95% CI] | Errors | Within-parent | Cross-parent |
|---|---|---|---|---|---|
| clinc (150 intents, 10 parents) | flat | 0.968 [0.962, 0.973] | 144 | 82 | 62 |
| clinc (150 intents, 10 parents) | joint_unconstrained | 0.966 [0.960, 0.971] | 151 | 93 | 58 |
| clinc (150 intents, 10 parents) | joint_constrained | 0.967 [0.960, 0.971] | 150 | 96 | 54 |
| massive (60 intents, 18 parents) | flat | 0.879 [0.860, 0.901] | 300 | 80 | 220 |
| massive (60 intents, 18 parents) | joint_unconstrained | 0.889 [0.870, 0.908] | 298 | 71 | 227 |
| massive (60 intents, 18 parents) | joint_constrained | 0.883 [0.865, 0.901] | 308 | 75 | 233 |

### Small data (~500 rows)

| Dataset | Rows (per class) | Method | Macro-F1 [95% CI] | Accuracy |
|---|---|---|---|---|
| banking77 | 500 (6.49) | plain | 0.795 [0.779, 0.807] | 0.796 |
| banking77 | 500 (6.49) | char_noise | 0.801 [0.785, 0.812] | 0.803 |
| banking77 | 500 (6.49) | emb_logreg | 0.805 [0.790, 0.816] | 0.806 |
| banking77 | 500 (6.49) | backtrans | 0.795 [0.779, 0.807] | 0.797 |
| banking77 | 500 | active (uncertainty, 100 random + 4x100) | 0.765 [0.748, 0.776] | 0.771 |
| clinc | 500 (3.33) | plain | 0.846 [0.833, 0.854] | 0.851 |
| clinc | 500 (3.33) | char_noise | 0.836 [0.823, 0.844] | 0.841 |
| clinc | 500 (3.33) | emb_logreg | 0.837 [0.825, 0.845] | 0.839 |
| clinc | 500 (3.33) | backtrans | 0.840 [0.828, 0.848] | 0.844 |
| clinc | 500 | active (uncertainty, 100 random + 4x100) | 0.768 [0.755, 0.777] | 0.788 |

### Multilingual (MASSIVE, 8 locales)

| Condition | Model | Macro-F1 all [95% CI] | Non-English macro-F1 | en-US | hi-IN | ta-IN | bn-BD | de-DE | ja-JP | ar-SA | sw-KE |
|---|---|---|---|---|---|---|---|---|---|---|---|
| train 8 locales | e5 | 0.838 [0.830, 0.845] | 0.833 | 0.877 | 0.845 | 0.824 | 0.833 | 0.850 | 0.850 | 0.796 | 0.829 |
| train 8 locales | minilm | 0.804 [0.795, 0.812] | 0.798 | 0.850 | 0.814 | 0.775 | 0.801 | 0.830 | 0.828 | 0.753 | 0.770 |
| train en-US only (zero-shot transfer) | e5 | 0.673 [0.664, 0.681] | 0.639 | 0.879 | 0.724 | 0.569 | 0.624 | 0.721 | 0.707 | 0.555 | 0.476 |

### LLM baseline

| Dataset | Model | Shots | n | Unparsed | Accuracy [95% CI] | Macro-F1 on sample | p50 / p95 latency (s) | USD per 1K at published rate |
|---|---|---|---|---|---|---|---|---|
| banking77 | openai/gpt-oss-120b | 0 | 168 | 2 | 0.786 [0.726, 0.845] | 0.752 | 3.22 / 4.82 | 0.0996 |
| banking77 | openai/gpt-oss-120b | 5 | 40 | 0 | 0.875 [0.774, 0.975] | 0.829 | 1.0 / 4.77 | 0.1057 |
| clinc | qwen/qwen3.8-27b | 0 | 110 | 5 | 0.882 [0.818, 0.936] | 0.867 | 0.62 / 11.3 | n/a (no published rate) |
| clinc | qwen/qwen3.8-27b | 5 | 30 | 1 | 0.900 [0.767, 1.000] | 0.900 | 3.97 / 10.85 | n/a (no published rate) |

### Serving (int8 ONNX, CPU)

| Model | Threshold | Int8 macro-F1 | Known retention (test) | CLINC OOS rejection | p50 / p95 / p99 ms (1 thread) | RSS MB | Model MB | CPU |
|---|---|---|---|---|---|---|---|---|
| e5 | fp32-calibrated | 0.969 | 0.892 | 0.968 | 45.2 / 69.1 / 80.9 | 1322 | 278 | x86_64 |
| e5_recal | recalibrated on int8 | 0.969 | 0.943 | 0.913 | 44.5 / 65.1 / 78.4 | 1328 | 278 | x86_64 |
| minilm | fp32-calibrated | 0.953 | 0.939 | 0.849 | 16.1 / 23.3 / 26.4 | 1296 | 118 | x86_64 |
| minilm_recal | recalibrated on int8 | 0.953 | 0.945 | 0.832 | 16.2 / 22.1 / 26.8 | 1316 | 118 | x86_64 |
<!-- METRICS:END -->

## Reading the tables

**Model choice (E2h).** Rule fixed in advance: macro-F1 within 1 point, then CPU p95 latency, then memory. e5-base beats MiniLM
on BANKING77 and MASSIVE en-US by more than 1 point and by 0.9 on CLINC150 (inside the band); XLM-R is below e5 on all three at the same
size and latency class. Both e5 and MiniLM meet p95 under 100 ms on one thread. Decision: **e5-base is the default**; **MiniLM is the
option** when size (118 MB against 278 MB) or CPU time (about 16 ms against 45 ms) dominates.

**Hierarchy (E2d).** No variant beats flat softmax outside the intervals; most remaining errors are semantic overlap between sibling-like
intents, not a failure to find the domain. **Small data (E2e).** At 500 rows, plain fine-tuning, character noise, back-translation and frozen
embeddings plus logistic regression land within about one interval of each other; uncertainty sampling ended 3 to 8 points BELOW random sampling
in this implementation (a negative result for this implementation, not for active learning in general). **Multilingual (E2f).** Trained on eight
locales, e5 holds non-English macro-F1 well above the English-only transfer figure; Swahili, Arabic and Tamil transfer worst.

**Serving (E3).** The int8 ONNX encoder keeps quality, but the unknown threshold calibrated on fp32 embeddings does not transfer: with e5 it kept only
0.892 of known test items (below the 0.90 floor); recalibrated on the int8 model's own validation scores it keeps 0.943. `export()` does this
automatically (engine PR 338). The local latency runs were contaminated by CPU contention from other jobs (e5 p50 jumped from 34 to 162 ms
between runs), so the reported latency is from an isolated Kaggle CPU kernel. Cost per 1,000 messages is arithmetic from remembered Cloud Run
rates (BELIEVED, not re-fetched): about 0.0013 USD (e5) and 0.0005 USD (MiniLM), against about 0.10 USD for gpt-oss-120b at the published rate.
Latency is in-process `Predictor.predict`; the FastAPI layer was not load tested.

## Published comparison (BELIEVED: secondary sources, not re-read in the papers)

| Benchmark | Published (accuracy) | Ours (accuracy) | Ours (macro-F1) | Source |
|---|---|---|---|---|
| BANKING77, full data | RoBERTa-base 0.941; aggregator top entries 0.944 to 0.948 | e5-base 0.936 | 0.936 | arXiv 2012.03929; hyper.ai, wizwand |
| MASSIVE en-US intent | XLM-R base 0.883, mT5 encoder 0.890 (per-locale table, garbled extraction) | XLM-R 0.886, e5 0.900 | 0.856, 0.880 | arXiv 2204.08582 / ACL 2023 long.235 |
| CLINC150 in-scope | aggregators list about 0.97 to 0.98 (model and split unstated) | e5-base 0.967 | 0.966 | hyper.ai, wizwand |

Published macro-F1 was not found for any of the three; no macro-F1 comparison is made. No authoritative CLINC150 out-of-scope figure was found for a fine-tuned encoder.

## Provenance

Result files carry their commit. Track A (all 21 runs, with per-item predictions): `7faba68`, Kaggle T4; first pass `9793471`. Track B and
multilingual: `9793471`; hierarchy, small-data and the export re-run: `44fd57e`; serving bench: Kaggle CPU kernel (branch `fix/s21-engine-int8-threshold`);
BANKING77 LLM paired run: `93454d1`; CLINC LLM paired run: `2cf4cb2`. Kaggle kernels are private and cloned the public repository; no secret
entered any kernel. Datasets: CLINC150 (CC BY 3.0), BANKING77 (CC BY 4.0), MASSIVE 1.0 (CC BY 4.0); attribution is required in any published use.
