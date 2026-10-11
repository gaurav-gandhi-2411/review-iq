# Review-intent model: sealed-set evaluation

Method and rules: `docs/specs/review-intent.md`, Amendments 2, 4 and 5. Every figure below is rendered from
`reports/intent/eval_run1.json` by `engine/intent/report.py`. All metrics are **against panel consensus (silver)**; agreement with a human
audit is not measured. Every interval is a 95 percent bootstrap. Each is written as "item" (resample reviews) and,
where the sealed review could be matched back to a product, also "cluster" (resample products, which keeps reviews of
one product together); an interval marked "item-level only, no product ids" has no cluster counterpart. Sealed items
whose content hash the leakage ledger found in a tracked file are removed from every sealed evaluation.
A class with fewer than 20 sealed positives is reported as "insufficient data" and is given no metric.
The threshold for any precision claim is chosen on VALIDATION (lowest confidence whose Wilson lower bound reaches the
target) and applied once to the sealed set; rows marked ORACLE pick the best point on the sealed set and are not deployable.
The judge baselines are scored leave-one-out (each judge against the consensus of the other three) and have no
confidences, so no matched-coverage comparison with them exists.

## Provenance

<!-- METRICS:START:provenance -->
- evaluation commit `ca1bae5`; training commit `895f5ec`
- labels: silver: sealed labels are four-judge consensus (Amendment 5), not human truth
- bootstrap: 1000 resamples, seed 42, unit = items; plus products (cluster bootstrap) where --clusters maps an item to a product id
- ledger `sealed_excluded`: 6 hashes, 6 sealed items dropped from every sealed evaluation (ledger sha256[:16] `b1b51f87f8e57c55`)
- product ids recovered for 763 of 763 sealed items (507 products); 66 sealed texts occur under more than one product in the source and take the first row, as the sampler's dedup did (clusters file sha256[:16] `3a220a3a853dae91`)
  - stratum apparel: 260 of 260 sealed items with a product id, 123 products
  - stratum beauty: 246 of 246 sealed items with a product id, 131 products
  - stratum food: 237 of 237 sealed items with a product id, 234 products
  - stratum vernacular: 20 of 20 sealed items with a product id, 19 products
- `labels.json` sha256[:16] `8f3b582082e4cf17`
- `manifest.json` sha256[:16] `a29c235e88162304`
- `pred_aspect__beauty__fragrance.json` sha256[:16] `232d539e90d5a141`
- `pred_aspect__beauty__results_efficacy.json` sha256[:16] `68f81885319417ef`
- `pred_aspect__beauty__skin_reaction.json` sha256[:16] `7cf57bf6570888f7`
- `pred_aspect__beauty__texture.json` sha256[:16] `b93134fdd94617ae`
- `pred_aspect__beauty__value.json` sha256[:16] `21a4b78660d7b10d`
- `pred_aspect__food__efficacy_claims.json` sha256[:16] `69160a0463589830`
- `pred_aspect__food__ingredient_quality.json` sha256[:16] `f6401908290008e6`
- `pred_aspect__food__quantity.json` sha256[:16] `c880be283ad38d96`
- `pred_aspect__food__taste.json` sha256[:16] `fd888541d8289afd`
- `pred_aspect__food__value.json` sha256[:16] `da781ebf01e384ea`
- `pred_broad_intent.json` sha256[:16] `29b28695f938115b`
- `pred_needs_action.json` sha256[:16] `543fb464e6c302e2`
- `pred_sentiment.json` sha256[:16] `8f649020e1338259`
- `pred_urgency.json` sha256[:16] `cddb7c76a2002de5`
- `judges_A/llama31_8b.jsonl` sha256[:16] `ce2235978611ff57`
- `judges_A/qwen3_8b.jsonl` sha256[:16] `d2568a1c1de36854`
- `judges_S/gemma2_9b.jsonl` sha256[:16] `b7642ae5835f4447`
- `judges_S/mistral_7b.jsonl` sha256[:16] `06902100012da83c`
- `judges_B/mistral_7b.jsonl` sha256[:16] `8b90998a1566d3cb`
- `scale.jsonl` sha256[:16] `d49895744c2e9a83`
<!-- METRICS:END -->

## Headline per task

<!-- METRICS:START:summary -->
| Task | Sealed labelled | Consensus ceiling | Accuracy [95% CI] | Macro-F1 [95% CI] | Conservative p0.95: coverage [95% CI] | precision [95% CI] | Reached | ORACLE coverage at p0.95 [95% CI] |
|---|---|---|---|---|---|---|---|---|
| needs_action | 717 | 0.947 | 0.960 [0.943, 0.972] item; [0.944, 0.973] cluster | 0.923 [0.892, 0.948] item; [0.895, 0.948] cluster | 0.918 [0.897, 0.937] item; [0.897, 0.937] cluster | 0.983 [0.973, 0.992] item; [0.973, 0.992] cluster | yes | 1.000 [0.982, 1.000] item-level only (oracle curve is not cluster-resampled) |
| broad_intent | 700 | 0.925 | 0.921 [0.899, 0.939] item; [0.900, 0.941] cluster | 0.825 [0.781, 0.866] item; [0.780, 0.866] cluster | 0.777 [0.747, 0.809] item; [0.747, 0.807] cluster | 0.985 [0.975, 0.994] item; [0.974, 0.995] cluster | yes | 0.917 [0.870, 0.969] item-level only (oracle curve is not cluster-resampled) |
| sentiment | 673 | 0.889 | 0.936 [0.915, 0.952] item; [0.919, 0.953] cluster | 0.920 [0.890, 0.945] item; [0.891, 0.945] cluster | 0.859 [0.832, 0.884] item; [0.831, 0.885] cluster | 0.990 [0.981, 0.997] item; [0.980, 0.997] cluster | yes | 0.976 [0.942, 1.000] item-level only (oracle curve is not cluster-resampled) |
| urgency | 703 | 0.929 | 0.955 [0.936, 0.968] item; [0.938, 0.970] cluster | 0.893 [0.856, 0.926] item; [0.856, 0.927] cluster | 0.841 [0.811, 0.866] item; [0.814, 0.865] cluster | 0.993 [0.986, 0.998] item; [0.986, 0.998] cluster | yes | 1.000 [0.973, 1.000] item-level only (oracle curve is not cluster-resampled) |
| aspect:beauty:results_efficacy | 192 | 0.254 | 0.922 [0.875, 0.952] item; [0.886, 0.955] cluster | 0.862 [0.784, 0.928] item; [0.785, 0.919] cluster | 0.953 [0.922, 0.979] item; [0.921, 0.979] cluster | 0.940 [0.904, 0.973] item; [0.910, 0.970] cluster | no | 0.943 [0.865, 1.000] item-level only (oracle curve is not cluster-resampled) |
| aspect:beauty:texture | 69 | 0.091 | 0.899 [0.805, 0.950] item; [0.819, 0.960] cluster | insufficient data (fewer than 2 classes with 20 sealed positives)  | 0.000 n/a item; [0.000, 0.000] cluster | n/a n/a item-level only, no product ids | no | 0.899 [0.507, 1.000] item-level only (oracle curve is not cluster-resampled) |
| aspect:beauty:skin_reaction | 33 | 0.044 | 0.909 [0.764, 0.969] item; [0.786, 1.000] cluster | insufficient data (fewer than 2 classes with 20 sealed positives)  | 0.000 n/a item; [0.000, 0.000] cluster | n/a n/a item-level only, no product ids | no | 0.939 [0.636, 1.000] item-level only (oracle curve is not cluster-resampled) |
| aspect:beauty:fragrance | 51 | 0.067 | 0.804 [0.675, 0.890] item; [0.708, 0.894] cluster | insufficient data (fewer than 2 classes with 20 sealed positives)  | 0.000 n/a item; [0.000, 0.000] cluster | n/a n/a item-level only, no product ids | no | 0.588 [0.431, 0.882] item-level only (oracle curve is not cluster-resampled) |
| aspect:beauty:value | 14 | | insufficient data | | | | | |
| aspect:food:taste | 109 | 0.144 | 0.899 [0.828, 0.943] item; [0.844, 0.954] cluster | 0.855 [0.765, 0.928] item; [0.766, 0.930] cluster | 0.000 n/a item; [0.000, 0.000] cluster | n/a n/a item-level only, no product ids | no | 0.000 [0.000, 1.000] item-level only (oracle curve is not cluster-resampled) |
| aspect:food:value | 39 | 0.051 | 0.846 [0.703, 0.927] item; [0.718, 0.949] cluster | insufficient data (fewer than 2 classes with 20 sealed positives)  | 0.000 n/a item; [0.000, 0.000] cluster | n/a n/a item-level only, no product ids | no | 0.846 [0.179, 0.974] item-level only (oracle curve is not cluster-resampled) |
| aspect:food:ingredient_quality | 17 | | insufficient data | | | | | |
| aspect:food:efficacy_claims | 13 | | insufficient data | | | | | |
| aspect:food:quantity | 3 | | insufficient data | | | | | |
<!-- METRICS:END -->

## Per-class F1

<!-- METRICS:START:per_class -->
| Task | Class | Sealed positives | F1 [CI] | Floor 0.60 | Note |
|---|---|---|---|---|---|
| needs_action | yes | 111 | 0.870 [0.814, 0.912] item; [0.820, 0.913] cluster | ok |  |
| needs_action | no | 606 | 0.976 [0.966, 0.984] item; [0.967, 0.984] cluster | ok |  |
| broad_intent | product_issue | 115 | 0.881 [0.836, 0.925] item; [0.830, 0.924] cluster | ok |  |
| broad_intent | delivery | 1 | insufficient data | | no metric given |
| broad_intent | service | 0 | insufficient data | | no metric given |
| broad_intent | praise | 515 | 0.971 [0.960, 0.981] item; [0.960, 0.981] cluster | ok |  |
| broad_intent | other | 69 | 0.623 [0.517, 0.714] item; [0.520, 0.714] cluster | ok |  |
| sentiment | positive | 541 | 0.974 [0.965, 0.983] item; [0.964, 0.983] cluster | ok |  |
| sentiment | negative | 102 | 0.865 [0.811, 0.910] item; [0.809, 0.912] cluster | ok |  |
| sentiment | neutral | 11 | insufficient data | | no metric given |
| sentiment | mixed | 19 | insufficient data | | no metric given |
| urgency | low | 622 | 0.977 [0.968, 0.985] item; [0.968, 0.986] cluster | ok |  |
| urgency | medium | 77 | 0.809 [0.739, 0.868] item; [0.742, 0.871] cluster | ok |  |
| urgency | high | 4 | insufficient data | | no metric given |
| aspect:beauty:results_efficacy | positive | 161 | 0.960 [0.935, 0.980] item; [0.940, 0.979] cluster | ok |  |
| aspect:beauty:results_efficacy | negative | 27 | 0.764 [0.621, 0.875] item; [0.619, 0.866] cluster | ok | support below 30 |
| aspect:beauty:results_efficacy | neutral | 4 | insufficient data | | no metric given |
| aspect:beauty:texture | positive | 59 | 0.950 [0.904, 0.984] item; [0.904, 0.985] cluster | ok |  |
| aspect:beauty:texture | negative | 6 | insufficient data | | no metric given |
| aspect:beauty:texture | neutral | 4 | insufficient data | | no metric given |
| aspect:beauty:skin_reaction | positive | 12 | insufficient data | | no metric given |
| aspect:beauty:skin_reaction | negative | 20 | 0.919 [0.812, 1.000] item; [0.800, 1.000] cluster | ok | support below 30 |
| aspect:beauty:skin_reaction | neutral | 1 | insufficient data | | no metric given |
| aspect:beauty:fragrance | positive | 41 | 0.889 [0.810, 0.955] item; [0.825, 0.944] cluster | ok |  |
| aspect:beauty:fragrance | negative | 8 | insufficient data | | no metric given |
| aspect:beauty:fragrance | neutral | 2 | insufficient data | | no metric given |
| aspect:food:taste | positive | 86 | 0.935 [0.890, 0.970] item; [0.893, 0.971] cluster | ok |  |
| aspect:food:taste | negative | 23 | 0.775 [0.632, 0.892] item; [0.628, 0.889] cluster | ok | support below 30 |
| aspect:food:taste | neutral | 0 | insufficient data | | no metric given |
| aspect:food:value | positive | 26 | 0.902 [0.792, 0.980] item; [0.792, 0.980] cluster | ok | support below 30 |
| aspect:food:value | negative | 11 | insufficient data | | no metric given |
| aspect:food:value | neutral | 2 | insufficient data | | no metric given |
<!-- METRICS:END -->

## Baselines

<!-- METRICS:START:baselines -->
| Task | Arm | Accuracy | Macro-F1 | Note |
|---|---|---|---|---|
| needs_action | model | 0.960 | 0.923 | full coverage |
| needs_action | majority (no) | 0.845 | 0.458 | no confidence |
| needs_action | TF-IDF + LR | 0.913 | 0.807 | model minus TF-IDF at matched coverage 0.918: 0.038 [0.021, 0.055]; model minus TF-IDF at matched coverage 0.994: 0.045 [0.025, 0.069] |
| needs_action | judge llama31_8b (leave-one-out, n=675) | 0.991 | 0.981 | answered 1.000; model on the same items: 0.976 / 0.950 |
| needs_action | judge qwen3_8b (leave-one-out, n=682) | 0.981 | 0.961 | answered 1.000; model on the same items: 0.969 / 0.939 |
| needs_action | judge gemma2_9b (leave-one-out, n=677) | 0.988 | 0.978 | answered 0.999; model on the same items: 0.976 / 0.952 |
| needs_action | judge mistral_7b (leave-one-out, n=690) | 0.970 | 0.939 | answered 1.000; model on the same items: 0.970 / 0.936 |
| broad_intent | model | 0.921 | 0.825 | full coverage |
| broad_intent | majority (praise) | 0.736 | 0.283 | no confidence |
| broad_intent | TF-IDF + LR | 0.856 | 0.698 | model minus TF-IDF at matched coverage 0.777: 0.061 [0.041, 0.085]; model minus TF-IDF at matched coverage 0.889: 0.061 [0.042, 0.090] |
| broad_intent | judge llama31_8b (leave-one-out, n=610) | 0.954 | 0.878 | answered 1.000; model on the same items: 0.949 / 0.876 |
| broad_intent | judge qwen3_8b (leave-one-out, n=638) | 0.912 | 0.785 | answered 1.000; model on the same items: 0.953 / 0.848 |
| broad_intent | judge gemma2_9b (leave-one-out, n=593) | 0.982 | 0.940 | answered 0.998; model on the same items: 0.960 / 0.870 |
| broad_intent | judge mistral_7b (leave-one-out, n=605) | 0.962 | 0.882 | answered 1.000; model on the same items: 0.950 / 0.854 |
| sentiment | model | 0.936 | 0.920 | full coverage |
| sentiment | majority (positive) | 0.804 | 0.446 | no confidence |
| sentiment | TF-IDF + LR | 0.889 | 0.821 | model minus TF-IDF at matched coverage 0.859: 0.043 [0.029, 0.065]; model minus TF-IDF at matched coverage 0.987: 0.048 [0.026, 0.071] |
| sentiment | judge llama31_8b (leave-one-out, n=618) | 0.966 | 0.943 | answered 1.000; model on the same items: 0.945 / 0.919 |
| sentiment | judge qwen3_8b (leave-one-out, n=611) | 0.977 | 0.982 | answered 1.000; model on the same items: 0.971 / 0.947 |
| sentiment | judge gemma2_9b (leave-one-out, n=598) | 0.998 | 1.000 | answered 0.998; model on the same items: 0.972 / 0.948 |
| sentiment | judge mistral_7b (leave-one-out, n=637) | 0.937 | 0.924 | answered 1.000; model on the same items: 0.961 / 0.943 |
| urgency | model | 0.955 | 0.893 | full coverage |
| urgency | majority (low) | 0.885 | 0.469 | no confidence |
| urgency | TF-IDF + LR | 0.922 | 0.768 | model minus TF-IDF at matched coverage 0.841: 0.030 [0.017, 0.051]; model minus TF-IDF at matched coverage 0.973: 0.035 [0.018, 0.053] |
| urgency | judge llama31_8b (leave-one-out, n=640) | 0.981 | 0.937 | answered 1.000; model on the same items: 0.983 / 0.943 |
| urgency | judge qwen3_8b (leave-one-out, n=634) | 0.991 | 0.966 | answered 1.000; model on the same items: 0.980 / 0.930 |
| urgency | judge gemma2_9b (leave-one-out, n=652) | 0.963 | 0.888 | answered 0.999; model on the same items: 0.976 / 0.934 |
| urgency | judge mistral_7b (leave-one-out, n=661) | 0.950 | 0.850 | answered 1.000; model on the same items: 0.967 / 0.887 |
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
| needs_action | vernacular | 14 | no | n/a |
| broad_intent | apparel | 235 | yes | 0.911 |
| broad_intent | beauty | 228 | yes | 0.956 |
| broad_intent | food | 224 | yes | 0.906 |
| broad_intent | vernacular | 13 | no | n/a |
| sentiment | apparel | 215 | yes | 0.954 |
| sentiment | beauty | 230 | yes | 0.930 |
| sentiment | food | 215 | yes | 0.930 |
| sentiment | vernacular | 13 | no | n/a |
| urgency | apparel | 238 | yes | 0.933 |
| urgency | beauty | 229 | yes | 0.965 |
| urgency | food | 224 | yes | 0.969 |
| urgency | vernacular | 12 | no | n/a |
| aspect:beauty:results_efficacy | beauty | 192 | yes | 0.922 |
| aspect:beauty:texture | beauty | 69 | no | n/a |
| aspect:beauty:skin_reaction | beauty | 33 | no | n/a |
| aspect:beauty:fragrance | beauty | 51 | no | n/a |
| aspect:food:taste | food | 109 | yes | 0.899 |
| aspect:food:value | food | 39 | no | n/a |
<!-- METRICS:END -->
