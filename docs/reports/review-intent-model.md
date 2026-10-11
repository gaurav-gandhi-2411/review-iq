# Review-intent model: sealed-set evaluation

Method and rules: `docs/specs/review-intent.md`, Amendments 2, 4 and 5. Every figure below is rendered from
`reports/intent/eval_run1.json` by `engine/intent/report.py`. All metrics are **against panel consensus (silver)**; agreement with a human
audit is not measured. Intervals are 95 percent item bootstraps (the corpora carry no product ids, so the cluster
bootstrap of Amendment 2 was not possible and these intervals are narrower than a cluster bootstrap would give).
A class with fewer than 20 sealed positives is reported as "insufficient data" and is given no metric.
The threshold for any precision claim is chosen on VALIDATION (lowest confidence whose Wilson lower bound reaches the
target) and applied once to the sealed set; rows marked ORACLE pick the best point on the sealed set and are not deployable.
The judge baselines are scored leave-one-out (each judge against the consensus of the other three) and have no
confidences, so no matched-coverage comparison with them exists.

## Provenance

<!-- METRICS:START:provenance -->
- evaluation commit `791c289`; training commit `895f5ec`
- labels: silver: sealed labels are four-judge consensus (Amendment 5), not human truth
- bootstrap: 1000 resamples, seed 42, unit = items (no product ids in the corpora)
- `labels.json` sha256[:16] `8f3b582082e4cf17`
- `manifest.json` sha256[:16] `a29c235e88162304`
- `pred_aspect__beauty__fragrance.json` sha256[:16] `f85a65ae98bf9be7`
- `pred_aspect__beauty__results_efficacy.json` sha256[:16] `6595c8b6a920a485`
- `pred_aspect__beauty__skin_reaction.json` sha256[:16] `125ef966fe628313`
- `pred_aspect__beauty__texture.json` sha256[:16] `cca07ce45f3440cd`
- `pred_aspect__beauty__value.json` sha256[:16] `a56757faa75cd4bc`
- `pred_aspect__food__efficacy_claims.json` sha256[:16] `752943e609e6990d`
- `pred_aspect__food__ingredient_quality.json` sha256[:16] `b8d411fdecad0e81`
- `pred_aspect__food__quantity.json` sha256[:16] `865af3bfdf74c0ea`
- `pred_aspect__food__taste.json` sha256[:16] `297a3b7da0fc4200`
- `pred_aspect__food__value.json` sha256[:16] `0d83f3a90ee33f0b`
- `pred_broad_intent.json` sha256[:16] `6cd9c58d44975a15`
- `pred_needs_action.json` sha256[:16] `2183e2b2acd004ca`
- `pred_sentiment.json` sha256[:16] `081d4a6bf73370ab`
- `pred_urgency.json` sha256[:16] `bbb8912d6eefc49a`
- `judges_A/llama31_8b.jsonl` sha256[:16] `ce2235978611ff57`
- `judges_A/qwen3_8b.jsonl` sha256[:16] `d2568a1c1de36854`
- `judges_S/gemma2_9b.jsonl` sha256[:16] `b7642ae5835f4447`
- `judges_S/mistral_7b.jsonl` sha256[:16] `06902100012da83c`
- `judges_B/mistral_7b.jsonl` sha256[:16] `8b90998a1566d3cb`
- `scale.jsonl` sha256[:16] `d49895744c2e9a83`
<!-- METRICS:END -->

## Headline per task

<!-- METRICS:START:summary -->
| Task | Sealed labelled | Consensus ceiling | Accuracy [CI] | Macro-F1 [CI] | Conservative p0.95: coverage [CI] | precision [CI] | Reached | ORACLE coverage at p0.95 [CI] |
|---|---|---|---|---|---|---|---|---|
| needs_action | 723 | 0.948 | 0.960 [0.943, 0.972] | 0.924 [0.895, 0.948] | 0.917 [0.898, 0.936] | 0.983 [0.974, 0.993] | yes | 1.000 [0.982, 1.000] |
| broad_intent | 706 | 0.925 | 0.921 [0.898, 0.938] | 0.824 [0.777, 0.863] | 0.778 [0.749, 0.806] | 0.985 [0.975, 0.995] | yes | 0.916 [0.867, 0.970] |
| sentiment | 679 | 0.890 | 0.935 [0.914, 0.951] | 0.918 [0.889, 0.943] | 0.859 [0.834, 0.884] | 0.990 [0.981, 0.997] | yes | 0.976 [0.943, 1.000] |
| urgency | 708 | 0.928 | 0.955 [0.937, 0.968] | 0.893 [0.852, 0.927] | 0.840 [0.815, 0.869] | 0.993 [0.986, 0.998] | yes | 1.000 [0.976, 1.000] |
| aspect:beauty:results_efficacy | 192 | 0.252 | 0.922 [0.875, 0.952] | 0.862 [0.784, 0.928] | 0.953 [0.922, 0.979] | 0.940 [0.904, 0.973] | no | 0.943 [0.865, 1.000] |
| aspect:beauty:texture | 69 | 0.090 | 0.899 [0.805, 0.950] | insufficient data (fewer than 2 classes with 20 sealed positives)  | 0.000 n/a | n/a n/a | no | 0.899 [0.507, 1.000] |
| aspect:beauty:skin_reaction | 33 | 0.043 | 0.909 [0.764, 0.969] | insufficient data (fewer than 2 classes with 20 sealed positives)  | 0.000 n/a | n/a n/a | no | 0.939 [0.636, 1.000] |
| aspect:beauty:fragrance | 51 | 0.067 | 0.804 [0.675, 0.890] | insufficient data (fewer than 2 classes with 20 sealed positives)  | 0.000 n/a | n/a n/a | no | 0.588 [0.431, 0.882] |
| aspect:beauty:value | 14 | | insufficient data | | | | | |
| aspect:food:taste | 109 | 0.143 | 0.899 [0.828, 0.943] | 0.855 [0.765, 0.928] | 0.000 n/a | n/a n/a | no | 0.000 [0.000, 1.000] |
| aspect:food:value | 39 | 0.051 | 0.846 [0.703, 0.927] | insufficient data (fewer than 2 classes with 20 sealed positives)  | 0.000 n/a | n/a n/a | no | 0.846 [0.179, 0.974] |
| aspect:food:ingredient_quality | 17 | | insufficient data | | | | | |
| aspect:food:efficacy_claims | 13 | | insufficient data | | | | | |
| aspect:food:quantity | 3 | | insufficient data | | | | | |
<!-- METRICS:END -->

## Per-class F1

<!-- METRICS:START:per_class -->
| Task | Class | Sealed positives | F1 [CI] | Floor 0.60 | Note |
|---|---|---|---|---|---|
| needs_action | yes | 112 | 0.871 [0.822, 0.913] | ok |  |
| needs_action | no | 611 | 0.976 [0.967, 0.985] | ok |  |
| broad_intent | product_issue | 116 | 0.882 [0.833, 0.923] | ok |  |
| broad_intent | delivery | 1 | insufficient data | | no metric given |
| broad_intent | service | 0 | insufficient data | | no metric given |
| broad_intent | praise | 520 | 0.970 [0.960, 0.980] | ok |  |
| broad_intent | other | 69 | 0.619 [0.516, 0.709] | ok |  |
| sentiment | positive | 546 | 0.974 [0.964, 0.983] | ok |  |
| sentiment | negative | 103 | 0.862 [0.808, 0.906] | ok |  |
| sentiment | neutral | 11 | insufficient data | | no metric given |
| sentiment | mixed | 19 | insufficient data | | no metric given |
| urgency | low | 627 | 0.978 [0.969, 0.986] | ok |  |
| urgency | medium | 77 | 0.809 [0.735, 0.870] | ok |  |
| urgency | high | 4 | insufficient data | | no metric given |
| aspect:beauty:results_efficacy | positive | 161 | 0.960 [0.935, 0.980] | ok |  |
| aspect:beauty:results_efficacy | negative | 27 | 0.764 [0.621, 0.875] | ok | support below 30 |
| aspect:beauty:results_efficacy | neutral | 4 | insufficient data | | no metric given |
| aspect:beauty:texture | positive | 59 | 0.950 [0.904, 0.984] | ok |  |
| aspect:beauty:texture | negative | 6 | insufficient data | | no metric given |
| aspect:beauty:texture | neutral | 4 | insufficient data | | no metric given |
| aspect:beauty:skin_reaction | positive | 12 | insufficient data | | no metric given |
| aspect:beauty:skin_reaction | negative | 20 | 0.919 [0.812, 1.000] | ok | support below 30 |
| aspect:beauty:skin_reaction | neutral | 1 | insufficient data | | no metric given |
| aspect:beauty:fragrance | positive | 41 | 0.889 [0.810, 0.955] | ok |  |
| aspect:beauty:fragrance | negative | 8 | insufficient data | | no metric given |
| aspect:beauty:fragrance | neutral | 2 | insufficient data | | no metric given |
| aspect:food:taste | positive | 86 | 0.935 [0.890, 0.970] | ok |  |
| aspect:food:taste | negative | 23 | 0.775 [0.632, 0.892] | ok | support below 30 |
| aspect:food:taste | neutral | 0 | insufficient data | | no metric given |
| aspect:food:value | positive | 26 | 0.902 [0.792, 0.980] | ok | support below 30 |
| aspect:food:value | negative | 11 | insufficient data | | no metric given |
| aspect:food:value | neutral | 2 | insufficient data | | no metric given |
<!-- METRICS:END -->

## Baselines

<!-- METRICS:START:baselines -->
| Task | Arm | Accuracy | Macro-F1 | Note |
|---|---|---|---|---|
| needs_action | model | 0.960 | 0.924 | full coverage |
| needs_action | majority (no) | 0.845 | 0.458 | no confidence |
| needs_action | TF-IDF + LR | 0.914 | 0.809 | model minus TF-IDF at matched coverage 0.917: 0.038 [0.020, 0.054]; model minus TF-IDF at matched coverage 0.995: 0.046 [0.024, 0.064] |
| needs_action | judge llama31_8b (leave-one-out, n=680) | 0.991 | 0.982 | answered 1.000; model on the same items: 0.977 / 0.950 |
| needs_action | judge qwen3_8b (leave-one-out, n=688) | 0.980 | 0.958 | answered 1.000; model on the same items: 0.970 / 0.940 |
| needs_action | judge gemma2_9b (leave-one-out, n=682) | 0.988 | 0.978 | answered 0.999; model on the same items: 0.977 / 0.953 |
| needs_action | judge mistral_7b (leave-one-out, n=695) | 0.970 | 0.939 | answered 1.000; model on the same items: 0.970 / 0.936 |
| broad_intent | model | 0.921 | 0.824 | full coverage |
| broad_intent | majority (praise) | 0.737 | 0.283 | no confidence |
| broad_intent | TF-IDF + LR | 0.857 | 0.699 | model minus TF-IDF at matched coverage 0.778: 0.060 [0.042, 0.085]; model minus TF-IDF at matched coverage 0.888: 0.061 [0.037, 0.092] |
| broad_intent | judge llama31_8b (leave-one-out, n=614) | 0.954 | 0.879 | answered 1.000; model on the same items: 0.950 / 0.876 |
| broad_intent | judge qwen3_8b (leave-one-out, n=644) | 0.910 | 0.782 | answered 1.000; model on the same items: 0.952 / 0.845 |
| broad_intent | judge gemma2_9b (leave-one-out, n=597) | 0.982 | 0.940 | answered 0.998; model on the same items: 0.960 / 0.870 |
| broad_intent | judge mistral_7b (leave-one-out, n=609) | 0.962 | 0.883 | answered 1.000; model on the same items: 0.951 / 0.854 |
| sentiment | model | 0.935 | 0.918 | full coverage |
| sentiment | majority (positive) | 0.804 | 0.446 | no confidence |
| sentiment | TF-IDF + LR | 0.889 | 0.823 | model minus TF-IDF at matched coverage 0.859: 0.043 [0.026, 0.060]; model minus TF-IDF at matched coverage 0.987: 0.046 [0.022, 0.069] |
| sentiment | judge llama31_8b (leave-one-out, n=624) | 0.966 | 0.944 | answered 1.000; model on the same items: 0.944 / 0.917 |
| sentiment | judge qwen3_8b (leave-one-out, n=617) | 0.977 | 0.982 | answered 1.000; model on the same items: 0.969 / 0.944 |
| sentiment | judge gemma2_9b (leave-one-out, n=604) | 0.998 | 1.000 | answered 0.998; model on the same items: 0.970 / 0.946 |
| sentiment | judge mistral_7b (leave-one-out, n=643) | 0.938 | 0.925 | answered 1.000; model on the same items: 0.960 / 0.941 |
| urgency | model | 0.955 | 0.893 | full coverage |
| urgency | majority (low) | 0.886 | 0.470 | no confidence |
| urgency | TF-IDF + LR | 0.922 | 0.768 | model minus TF-IDF at matched coverage 0.840: 0.030 [0.017, 0.052]; model minus TF-IDF at matched coverage 0.973: 0.035 [0.015, 0.057] |
| urgency | judge llama31_8b (leave-one-out, n=645) | 0.981 | 0.937 | answered 1.000; model on the same items: 0.983 / 0.943 |
| urgency | judge qwen3_8b (leave-one-out, n=639) | 0.991 | 0.966 | answered 1.000; model on the same items: 0.980 / 0.930 |
| urgency | judge gemma2_9b (leave-one-out, n=657) | 0.964 | 0.888 | answered 0.999; model on the same items: 0.976 / 0.934 |
| urgency | judge mistral_7b (leave-one-out, n=666) | 0.951 | 0.850 | answered 1.000; model on the same items: 0.967 / 0.887 |
| aspect:beauty:results_efficacy | model | 0.922 | 0.862 | full coverage |
| aspect:beauty:results_efficacy | majority (positive) | 0.839 | 0.456 | no confidence |
| aspect:beauty:results_efficacy | TF-IDF + LR | 0.875 | 0.687 | model minus TF-IDF at matched coverage 0.953: 0.038 [0.003, 0.087]; model minus TF-IDF at matched coverage 0.964: 0.043 [0.005, 0.081] |
| aspect:beauty:results_efficacy | judge llama31_8b (leave-one-out, n=105) | 0.933 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.971; model on the same items: 0.914 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:results_efficacy | judge qwen3_8b (leave-one-out, n=144) | 0.681 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.701; model on the same items: 0.931 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:results_efficacy | judge gemma2_9b (leave-one-out, n=116) | 0.845 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.845; model on the same items: 0.922 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:results_efficacy | judge mistral_7b (leave-one-out, n=121) | 0.810 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.818; model on the same items: 0.909 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:texture | model | 0.899 | insufficient data (fewer than 2 classes with 20 sealed positives) | full coverage |
| aspect:beauty:texture | majority (positive) | 0.855 | insufficient data (fewer than 2 classes with 20 sealed positives) | no confidence |
| aspect:beauty:texture | TF-IDF + LR | 0.826 | insufficient data (fewer than 2 classes with 20 sealed positives) |  |
| aspect:beauty:texture | judge llama31_8b (leave-one-out, n=34) | 0.912 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.941; model on the same items: 0.882 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:texture | judge qwen3_8b (leave-one-out, n=50) | 0.620 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.780; model on the same items: 0.920 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:texture | judge gemma2_9b (leave-one-out, n=33) | 0.939 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.939; model on the same items: 0.909 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:texture | judge mistral_7b (leave-one-out, n=45) | 0.689 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.733; model on the same items: 0.889 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:skin_reaction | model | 0.909 | insufficient data (fewer than 2 classes with 20 sealed positives) | full coverage |
| aspect:beauty:skin_reaction | majority (negative) | 0.606 | insufficient data (fewer than 2 classes with 20 sealed positives) | no confidence |
| aspect:beauty:skin_reaction | TF-IDF + LR | 0.697 | insufficient data (fewer than 2 classes with 20 sealed positives) |  |
| aspect:beauty:skin_reaction | judge qwen3_8b (leave-one-out, n=21) | 0.857 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.857; model on the same items: 0.952 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:skin_reaction | judge gemma2_9b (leave-one-out, n=25) | 0.720 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.720; model on the same items: 0.920 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:skin_reaction | judge mistral_7b (leave-one-out, n=23) | 0.783 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.826; model on the same items: 0.913 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:fragrance | model | 0.804 | insufficient data (fewer than 2 classes with 20 sealed positives) | full coverage |
| aspect:beauty:fragrance | majority (positive) | 0.804 | insufficient data (fewer than 2 classes with 20 sealed positives) | no confidence |
| aspect:beauty:fragrance | TF-IDF + LR | 0.843 | insufficient data (fewer than 2 classes with 20 sealed positives) |  |
| aspect:beauty:fragrance | judge llama31_8b (leave-one-out, n=41) | 0.829 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.854; model on the same items: 0.780 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:fragrance | judge qwen3_8b (leave-one-out, n=39) | 0.872 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.872; model on the same items: 0.821 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:fragrance | judge gemma2_9b (leave-one-out, n=35) | 0.971 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.971; model on the same items: 0.771 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:fragrance | judge mistral_7b (leave-one-out, n=38) | 0.895 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.947; model on the same items: 0.816 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:food:taste | model | 0.899 | 0.855 | full coverage |
| aspect:food:taste | majority (positive) | 0.789 | 0.441 | no confidence |
| aspect:food:taste | TF-IDF + LR | 0.798 | 0.697 |  |
| aspect:food:taste | judge llama31_8b (leave-one-out, n=29) | 0.931 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.931; model on the same items: 0.931 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:food:taste | judge qwen3_8b (leave-one-out, n=101) | 0.267 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.267; model on the same items: 0.911 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:food:taste | judge gemma2_9b (leave-one-out, n=29) | 0.931 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.931; model on the same items: 0.931 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:food:taste | judge mistral_7b (leave-one-out, n=31) | 0.871 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.871; model on the same items: 0.968 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:food:value | model | 0.846 | insufficient data (fewer than 2 classes with 20 sealed positives) | full coverage |
| aspect:food:value | majority (negative) | 0.282 | insufficient data (fewer than 2 classes with 20 sealed positives) | no confidence |
| aspect:food:value | TF-IDF + LR | 0.462 | insufficient data (fewer than 2 classes with 20 sealed positives) |  |
| aspect:food:value | judge qwen3_8b (leave-one-out, n=33) | 0.333 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.333; model on the same items: 0.909 / insufficient data (fewer than 2 classes with 20 sealed positives) |
<!-- METRICS:END -->

## Language and category slices

<!-- METRICS:START:slices -->
| Task | Slice | Consensus items | Claimed (at least 100) | Accuracy |
|---|---|---|---|---|
| needs_action | apparel | 243 | yes | 0.942 |
| needs_action | beauty | 232 | yes | 0.978 |
| needs_action | food | 228 | yes | 0.961 |
| needs_action | vernacular | 20 | no | n/a |
| broad_intent | apparel | 235 | yes | 0.911 |
| broad_intent | beauty | 228 | yes | 0.956 |
| broad_intent | food | 224 | yes | 0.906 |
| broad_intent | vernacular | 19 | no | n/a |
| sentiment | apparel | 215 | yes | 0.954 |
| sentiment | beauty | 230 | yes | 0.930 |
| sentiment | food | 215 | yes | 0.930 |
| sentiment | vernacular | 19 | no | n/a |
| urgency | apparel | 238 | yes | 0.933 |
| urgency | beauty | 229 | yes | 0.965 |
| urgency | food | 224 | yes | 0.969 |
| urgency | vernacular | 17 | no | n/a |
| aspect:beauty:results_efficacy | beauty | 192 | yes | 0.922 |
| aspect:beauty:texture | beauty | 69 | no | n/a |
| aspect:beauty:skin_reaction | beauty | 33 | no | n/a |
| aspect:beauty:fragrance | beauty | 51 | no | n/a |
| aspect:food:taste | food | 109 | yes | 0.899 |
| aspect:food:value | food | 39 | no | n/a |
<!-- METRICS:END -->
