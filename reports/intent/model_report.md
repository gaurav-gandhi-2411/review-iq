# Review-intent model: sealed-set evaluation

Method and rules: `docs/specs/review-intent.md`, Amendments 2, 4 and 5. Every figure below is rendered from
`reports/intent/eval_run2.json` by `engine/intent/report.py`. All metrics are **against panel consensus (silver)**; agreement with a human
audit is not measured. Every interval is a 95 percent bootstrap. Each is written as "item" (resample reviews) and,
where the sealed review could be matched back to a product, also "cluster" (resample products, which keeps reviews of
one product together); an interval marked "item-level only, no product ids" has no cluster counterpart. Sealed items
whose content hash the leakage ledger found in a tracked file are removed from every sealed evaluation.
A class with fewer than 20 sealed positives is reported as "insufficient data" and is given no metric.
The threshold for any precision claim is chosen on VALIDATION (lowest confidence whose Wilson lower bound reaches the
target) and applied once to the sealed set; rows marked ORACLE pick the best point on the sealed set and are not deployable.
The judge baselines are scored leave-one-out (each judge against the consensus of the other three) and have no
confidences, so no matched-coverage comparison with them exists.

## Run 2 setup and caveats

Run 2 retrains the run 1 model with only the training length changed: up to 30 epochs instead of 6, patience 3
instead of 2, same seed, learning rate, batch size, maximum length, frozen embeddings and labels. Early stopping looks
at VALIDATION only; the sealed set is never used to choose an epoch, a threshold or a temperature.

Two caveats are part of the result. (1) The stopping metric is the mean macro-F1 over every task that has validation
labels, as in run 1; this includes aspect tasks with very few validation items, so the curve is noisy and the best
epoch is not a stable optimum. "Kept tasks" could also mean only the four core tasks, which would pick a different
epoch; the per-task columns of the curve let a reader check that. (2) The learning rate decays linearly over the epoch
budget, so a 30-epoch run follows a different schedule from a 6-epoch run (slower decay) and stopping early means the
rate had not decayed; run 2 is therefore not a pure "same run, longer" comparison. Both runs are single seeds, so
differences between them are not separated from seed noise by this report.

The Kaggle run records a different manifest hash from the evaluation provenance only because the kernel checks the
repository out with LF line endings and the Windows checkout has CRLF; the content is identical.

The CPU export uses PyTorch dynamic quantisation. Variants: fp32 on CPU (separates CPU-versus-GPU differences from
quantisation error); int8 Linear layers with one scale per tensor, with and without an int8 word-embedding table;
the same with one scale per output channel; and a mixed variant that quantises only the feed-forward layers and
the embedding table and leaves attention and the heads in fp32. For each, probabilities are recomputed, the
per-task temperature is refit on validation, and the conservative threshold is re-picked on validation before the
sealed set is scored once. Read the delta columns: a variant that reaches the precision target at a lower coverage
is not equivalent to the fp16 model.

## Provenance

<!-- METRICS:START:provenance -->
- evaluation commit `a74d535`; training commit `58b7de1`
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
- `pred_aspect__beauty__fragrance.json` sha256[:16] `41051c4580d0ff1b`
- `pred_aspect__beauty__results_efficacy.json` sha256[:16] `5a96035160587177`
- `pred_aspect__beauty__skin_reaction.json` sha256[:16] `ac41f9a1f467e422`
- `pred_aspect__beauty__texture.json` sha256[:16] `36bae77f6b95565d`
- `pred_aspect__beauty__value.json` sha256[:16] `d7a9c4bb9b34d7bf`
- `pred_aspect__food__efficacy_claims.json` sha256[:16] `666e06e8ea3491ce`
- `pred_aspect__food__ingredient_quality.json` sha256[:16] `519d2fa9b9aa940e`
- `pred_aspect__food__quantity.json` sha256[:16] `9a42130e8b6a628d`
- `pred_aspect__food__taste.json` sha256[:16] `4f498f76761e2637`
- `pred_aspect__food__value.json` sha256[:16] `dff3508b80dd0cb5`
- `pred_broad_intent.json` sha256[:16] `9d4f2ab644acfc08`
- `pred_needs_action.json` sha256[:16] `697dece15cc9efab`
- `pred_sentiment.json` sha256[:16] `242ef944aa26cdca`
- `pred_urgency.json` sha256[:16] `600cd3d5eaac0cde`
- `judges_A/llama31_8b.jsonl` sha256[:16] `ce2235978611ff57`
- `judges_A/qwen3_8b.jsonl` sha256[:16] `d2568a1c1de36854`
- `judges_S/gemma2_9b.jsonl` sha256[:16] `b7642ae5835f4447`
- `judges_S/mistral_7b.jsonl` sha256[:16] `06902100012da83c`
- `judges_B/mistral_7b.jsonl` sha256[:16] `8b90998a1566d3cb`
- `scale.jsonl` sha256[:16] `d49895744c2e9a83`
<!-- METRICS:END -->

## Training curve (run 2, validation only)

<!-- METRICS:START:curve -->
| Epoch | Validation mean macro-F1 (selection metric) | needs_action | broad_intent | sentiment | urgency | Train loss (last batch) |
|---|---|---|---|---|---|---|
| 1 | 0.5123 | 0.7929 | 0.4050 | 0.4223 | 0.4777 | 0.3466 |
| 2 | 0.6254 | 0.8701 | 0.4955 | 0.4374 | 0.5479 | 2.6152 |
| 3 | 0.6614 | 0.8688 | 0.4993 | 0.4384 | 0.5432 | 0.0754 |
| 4 | 0.6919 | 0.8773 | 0.5215 | 0.4432 | 0.5589 | 0.0917 |
| 5 | 0.6771 | 0.8599 | 0.5541 | 0.4714 | 0.7506 | 0.0415 |
| 6 | 0.6695 | 0.8638 | 0.5351 | 0.5119 | 0.6096 | 0.0340 |
| 7 | 0.7267 | 0.8793 | 0.5670 | 0.4866 | 0.6830 | 0.0283 |
| 8 | 0.6921 | 0.8548 | 0.5665 | 0.5449 | 0.6700 | 0.0081 |
| 9 | 0.6949 | 0.8254 | 0.5523 | 0.5792 | 0.6591 | 0.1085 |
| 10 | 0.6565 | 0.8309 | 0.5626 | 0.4747 | 0.6598 | 0.2728 |

Best epoch 7 of at most 30; stopped early: yes; patience 3; best validation mean macro-F1 0.7267; training commit `58b7de1`; GPU training time 260.3 s.
<!-- METRICS:END -->

## Headline per task (run 2)

<!-- METRICS:START:summary -->
| Task | Sealed labelled | Consensus ceiling | Accuracy [95% CI] | Macro-F1 [95% CI] | Conservative p0.95: coverage [95% CI] | precision [95% CI] | Reached | ORACLE coverage at p0.95 [95% CI] |
|---|---|---|---|---|---|---|---|---|
| needs_action | 717 | 0.947 | 0.962 [0.946, 0.974] item; [0.948, 0.976] cluster | 0.929 [0.900, 0.952] item; [0.899, 0.955] cluster | 0.894 [0.869, 0.916] item; [0.871, 0.917] cluster | 0.988 [0.978, 0.995] item; [0.978, 0.995] cluster | yes | 1.000 [0.994, 1.000] item-level only (oracle curve is not cluster-resampled) |
| broad_intent | 700 | 0.925 | 0.919 [0.896, 0.937] item; [0.897, 0.938] cluster | 0.844 [0.805, 0.880] item; [0.803, 0.879] cluster | 0.790 [0.761, 0.819] item; [0.760, 0.820] cluster | 0.977 [0.964, 0.989] item; [0.962, 0.988] cluster | yes | 0.904 [0.846, 0.971] item-level only (oracle curve is not cluster-resampled) |
| sentiment | 673 | 0.889 | 0.948 [0.928, 0.962] item; [0.930, 0.966] cluster | 0.939 [0.912, 0.961] item; [0.914, 0.963] cluster | 0.831 [0.802, 0.859] item; [0.801, 0.858] cluster | 0.989 [0.980, 0.996] item; [0.980, 0.997] cluster | yes | 0.997 [0.952, 1.000] item-level only (oracle curve is not cluster-resampled) |
| urgency | 703 | 0.929 | 0.955 [0.936, 0.968] item; [0.939, 0.969] cluster | 0.894 [0.856, 0.926] item; [0.857, 0.926] cluster | 0.832 [0.801, 0.861] item; [0.806, 0.858] cluster | 0.990 [0.981, 0.997] item; [0.981, 0.998] cluster | yes | 1.000 [0.966, 1.000] item-level only (oracle curve is not cluster-resampled) |
| aspect:beauty:results_efficacy | 192 | 0.254 | 0.953 [0.913, 0.975] item; [0.926, 0.980] cluster | 0.926 [0.871, 0.975] item; [0.878, 0.969] cluster | 0.911 [0.870, 0.953] item; [0.872, 0.946] cluster | 0.977 [0.953, 0.995] item; [0.955, 0.995] cluster | yes | 1.000 [0.891, 1.000] item-level only (oracle curve is not cluster-resampled) |
| aspect:beauty:texture | 69 | 0.091 | 0.884 [0.787, 0.940] item; [0.797, 0.957] cluster | insufficient data (fewer than 2 classes with 20 sealed positives)  | 0.000 n/a item; [0.000, 0.000] cluster | n/a n/a (no sealed item answered above the threshold) | no | 0.725 [0.217, 1.000] item-level only (oracle curve is not cluster-resampled) |
| aspect:beauty:skin_reaction | 33 | 0.044 | 0.939 [0.804, 0.983] item; [0.853, 1.000] cluster | insufficient data (fewer than 2 classes with 20 sealed positives)  | 0.000 n/a item; [0.000, 0.000] cluster | n/a n/a (no sealed item answered above the threshold) | no | 0.818 [0.394, 1.000] item-level only (oracle curve is not cluster-resampled) |
| aspect:beauty:fragrance | 51 | 0.067 | 0.804 [0.675, 0.890] item; [0.700, 0.900] cluster | insufficient data (fewer than 2 classes with 20 sealed positives)  | 0.000 n/a item; [0.000, 0.000] cluster | n/a n/a (no sealed item answered above the threshold) | no | 0.588 [0.294, 0.941] item-level only (oracle curve is not cluster-resampled) |
| aspect:beauty:value | 14 | | insufficient data | | | | | |
| aspect:food:taste | 109 | 0.144 | 0.899 [0.828, 0.943] item; [0.844, 0.954] cluster | 0.863 [0.778, 0.936] item; [0.777, 0.932] cluster | 0.000 n/a item; [0.000, 0.000] cluster | n/a n/a (no sealed item answered above the threshold) | no | 0.826 [0.449, 1.000] item-level only (oracle curve is not cluster-resampled) |
| aspect:food:value | 39 | 0.051 | 0.769 [0.617, 0.874] item; [0.640, 0.897] cluster | insufficient data (fewer than 2 classes with 20 sealed positives)  | 0.000 n/a item; [0.000, 0.000] cluster | n/a n/a (no sealed item answered above the threshold) | no | 0.667 [0.487, 0.846] item-level only (oracle curve is not cluster-resampled) |
| aspect:food:ingredient_quality | 17 | | insufficient data | | | | | |
| aspect:food:efficacy_claims | 13 | | insufficient data | | | | | |
| aspect:food:quantity | 3 | | insufficient data | | | | | |
<!-- METRICS:END -->

## Run 1 and run 2 side by side (same sealed set, same intervals)

<!-- METRICS:START:comparison -->
| Task | Metric | Run 1 | Run 2 |
|---|---|---|---|
| needs_action (run 1 n=717, run 2 n=717) | accuracy | 0.960 [0.943, 0.972] item; [0.944, 0.973] cluster | 0.962 [0.946, 0.974] item; [0.948, 0.976] cluster |
| needs_action (run 1 n=717, run 2 n=717) | macro-F1 | 0.923 [0.892, 0.948] item; [0.895, 0.948] cluster | 0.929 [0.900, 0.952] item; [0.899, 0.955] cluster |
| needs_action (run 1 n=717, run 2 n=717) | conservative p0.95 coverage | 0.918 [0.897, 0.937] item; [0.897, 0.937] cluster | 0.894 [0.869, 0.916] item; [0.871, 0.917] cluster |
| needs_action (run 1 n=717, run 2 n=717) | precision at that threshold | 0.983 [0.973, 0.992] item; [0.973, 0.992] cluster | 0.988 [0.978, 0.995] item; [0.978, 0.995] cluster |
| needs_action (run 1 n=717, run 2 n=717) | target reached | yes | yes |
| broad_intent (run 1 n=700, run 2 n=700) | accuracy | 0.921 [0.899, 0.939] item; [0.900, 0.941] cluster | 0.919 [0.896, 0.937] item; [0.897, 0.938] cluster |
| broad_intent (run 1 n=700, run 2 n=700) | macro-F1 | 0.825 [0.781, 0.866] item; [0.780, 0.866] cluster | 0.844 [0.805, 0.880] item; [0.803, 0.879] cluster |
| broad_intent (run 1 n=700, run 2 n=700) | conservative p0.95 coverage | 0.777 [0.747, 0.809] item; [0.747, 0.807] cluster | 0.790 [0.761, 0.819] item; [0.760, 0.820] cluster |
| broad_intent (run 1 n=700, run 2 n=700) | precision at that threshold | 0.985 [0.975, 0.994] item; [0.974, 0.995] cluster | 0.977 [0.964, 0.989] item; [0.962, 0.988] cluster |
| broad_intent (run 1 n=700, run 2 n=700) | target reached | yes | yes |
| sentiment (run 1 n=673, run 2 n=673) | accuracy | 0.936 [0.915, 0.952] item; [0.919, 0.953] cluster | 0.948 [0.928, 0.962] item; [0.930, 0.966] cluster |
| sentiment (run 1 n=673, run 2 n=673) | macro-F1 | 0.920 [0.890, 0.945] item; [0.891, 0.945] cluster | 0.939 [0.912, 0.961] item; [0.914, 0.963] cluster |
| sentiment (run 1 n=673, run 2 n=673) | conservative p0.95 coverage | 0.859 [0.832, 0.884] item; [0.831, 0.885] cluster | 0.831 [0.802, 0.859] item; [0.801, 0.858] cluster |
| sentiment (run 1 n=673, run 2 n=673) | precision at that threshold | 0.990 [0.981, 0.997] item; [0.980, 0.997] cluster | 0.989 [0.980, 0.996] item; [0.980, 0.997] cluster |
| sentiment (run 1 n=673, run 2 n=673) | target reached | yes | yes |
| urgency (run 1 n=703, run 2 n=703) | accuracy | 0.955 [0.936, 0.968] item; [0.938, 0.970] cluster | 0.955 [0.936, 0.968] item; [0.939, 0.969] cluster |
| urgency (run 1 n=703, run 2 n=703) | macro-F1 | 0.893 [0.856, 0.926] item; [0.856, 0.927] cluster | 0.894 [0.856, 0.926] item; [0.857, 0.926] cluster |
| urgency (run 1 n=703, run 2 n=703) | conservative p0.95 coverage | 0.841 [0.811, 0.866] item; [0.814, 0.865] cluster | 0.832 [0.801, 0.861] item; [0.806, 0.858] cluster |
| urgency (run 1 n=703, run 2 n=703) | precision at that threshold | 0.993 [0.986, 0.998] item; [0.986, 0.998] cluster | 0.990 [0.981, 0.997] item; [0.981, 0.998] cluster |
| urgency (run 1 n=703, run 2 n=703) | target reached | yes | yes |
| aspect:beauty:results_efficacy (run 1 n=192, run 2 n=192) | accuracy | 0.922 [0.875, 0.952] item; [0.886, 0.955] cluster | 0.953 [0.913, 0.975] item; [0.926, 0.980] cluster |
| aspect:beauty:results_efficacy (run 1 n=192, run 2 n=192) | macro-F1 | 0.862 [0.784, 0.928] item; [0.785, 0.919] cluster | 0.926 [0.871, 0.975] item; [0.878, 0.969] cluster |
| aspect:beauty:results_efficacy (run 1 n=192, run 2 n=192) | conservative p0.95 coverage | 0.953 [0.922, 0.979] item; [0.921, 0.979] cluster | 0.911 [0.870, 0.953] item; [0.872, 0.946] cluster |
| aspect:beauty:results_efficacy (run 1 n=192, run 2 n=192) | precision at that threshold | 0.940 [0.904, 0.973] item; [0.910, 0.970] cluster | 0.977 [0.953, 0.995] item; [0.955, 0.995] cluster |
| aspect:beauty:results_efficacy (run 1 n=192, run 2 n=192) | target reached | no | yes |
| aspect:beauty:texture (run 1 n=69, run 2 n=69) | accuracy | 0.899 [0.805, 0.950] item; [0.819, 0.960] cluster | 0.884 [0.787, 0.940] item; [0.797, 0.957] cluster |
| aspect:beauty:texture (run 1 n=69, run 2 n=69) | macro-F1 | insufficient data | insufficient data |
| aspect:beauty:texture (run 1 n=69, run 2 n=69) | conservative p0.95 coverage | 0.000 n/a item; [0.000, 0.000] cluster | 0.000 n/a item; [0.000, 0.000] cluster |
| aspect:beauty:texture (run 1 n=69, run 2 n=69) | precision at that threshold | n/a n/a (no sealed item answered above the threshold) | n/a n/a (no sealed item answered above the threshold) |
| aspect:beauty:texture (run 1 n=69, run 2 n=69) | target reached | no | no |
| aspect:beauty:skin_reaction (run 1 n=33, run 2 n=33) | accuracy | 0.909 [0.764, 0.969] item; [0.786, 1.000] cluster | 0.939 [0.804, 0.983] item; [0.853, 1.000] cluster |
| aspect:beauty:skin_reaction (run 1 n=33, run 2 n=33) | macro-F1 | insufficient data | insufficient data |
| aspect:beauty:skin_reaction (run 1 n=33, run 2 n=33) | conservative p0.95 coverage | 0.000 n/a item; [0.000, 0.000] cluster | 0.000 n/a item; [0.000, 0.000] cluster |
| aspect:beauty:skin_reaction (run 1 n=33, run 2 n=33) | precision at that threshold | n/a n/a (no sealed item answered above the threshold) | n/a n/a (no sealed item answered above the threshold) |
| aspect:beauty:skin_reaction (run 1 n=33, run 2 n=33) | target reached | no | no |
| aspect:beauty:fragrance (run 1 n=51, run 2 n=51) | accuracy | 0.804 [0.675, 0.890] item; [0.708, 0.894] cluster | 0.804 [0.675, 0.890] item; [0.700, 0.900] cluster |
| aspect:beauty:fragrance (run 1 n=51, run 2 n=51) | macro-F1 | insufficient data | insufficient data |
| aspect:beauty:fragrance (run 1 n=51, run 2 n=51) | conservative p0.95 coverage | 0.000 n/a item; [0.000, 0.000] cluster | 0.000 n/a item; [0.000, 0.000] cluster |
| aspect:beauty:fragrance (run 1 n=51, run 2 n=51) | precision at that threshold | n/a n/a (no sealed item answered above the threshold) | n/a n/a (no sealed item answered above the threshold) |
| aspect:beauty:fragrance (run 1 n=51, run 2 n=51) | target reached | no | no |
| aspect:food:taste (run 1 n=109, run 2 n=109) | accuracy | 0.899 [0.828, 0.943] item; [0.844, 0.954] cluster | 0.899 [0.828, 0.943] item; [0.844, 0.954] cluster |
| aspect:food:taste (run 1 n=109, run 2 n=109) | macro-F1 | 0.855 [0.765, 0.928] item; [0.766, 0.930] cluster | 0.863 [0.778, 0.936] item; [0.777, 0.932] cluster |
| aspect:food:taste (run 1 n=109, run 2 n=109) | conservative p0.95 coverage | 0.000 n/a item; [0.000, 0.000] cluster | 0.000 n/a item; [0.000, 0.000] cluster |
| aspect:food:taste (run 1 n=109, run 2 n=109) | precision at that threshold | n/a n/a (no sealed item answered above the threshold) | n/a n/a (no sealed item answered above the threshold) |
| aspect:food:taste (run 1 n=109, run 2 n=109) | target reached | no | no |
| aspect:food:value (run 1 n=39, run 2 n=39) | accuracy | 0.846 [0.703, 0.927] item; [0.718, 0.949] cluster | 0.769 [0.617, 0.874] item; [0.640, 0.897] cluster |
| aspect:food:value (run 1 n=39, run 2 n=39) | macro-F1 | insufficient data | insufficient data |
| aspect:food:value (run 1 n=39, run 2 n=39) | conservative p0.95 coverage | 0.000 n/a item; [0.000, 0.000] cluster | 0.000 n/a item; [0.000, 0.000] cluster |
| aspect:food:value (run 1 n=39, run 2 n=39) | precision at that threshold | n/a n/a (no sealed item answered above the threshold) | n/a n/a (no sealed item answered above the threshold) |
| aspect:food:value (run 1 n=39, run 2 n=39) | target reached | no | no |
<!-- METRICS:END -->

## Per-class F1 (run 2)

<!-- METRICS:START:per_class -->
| Task | Class | Sealed positives | F1 [CI] | Floor 0.60 | Note |
|---|---|---|---|---|---|
| needs_action | yes | 111 | 0.880 [0.831, 0.919] item; [0.830, 0.923] cluster | ok |  |
| needs_action | no | 606 | 0.978 [0.969, 0.985] item; [0.969, 0.986] cluster | ok |  |
| broad_intent | product_issue | 115 | 0.891 [0.848, 0.930] item; [0.843, 0.933] cluster | ok |  |
| broad_intent | delivery | 1 | insufficient data | | no metric given |
| broad_intent | service | 0 | insufficient data | | no metric given |
| broad_intent | praise | 515 | 0.961 [0.948, 0.973] item; [0.947, 0.974] cluster | ok |  |
| broad_intent | other | 69 | 0.680 [0.590, 0.757] item; [0.588, 0.761] cluster | ok |  |
| sentiment | positive | 541 | 0.979 [0.970, 0.987] item; [0.970, 0.988] cluster | ok |  |
| sentiment | negative | 102 | 0.899 [0.849, 0.939] item; [0.853, 0.942] cluster | ok |  |
| sentiment | neutral | 11 | insufficient data | | no metric given |
| sentiment | mixed | 19 | insufficient data | | no metric given |
| urgency | low | 622 | 0.976 [0.966, 0.984] item; [0.967, 0.984] cluster | ok |  |
| urgency | medium | 77 | 0.812 [0.744, 0.869] item; [0.745, 0.870] cluster | ok |  |
| urgency | high | 4 | insufficient data | | no metric given |
| aspect:beauty:results_efficacy | positive | 161 | 0.975 [0.955, 0.991] item; [0.959, 0.991] cluster | ok |  |
| aspect:beauty:results_efficacy | negative | 27 | 0.877 [0.778, 0.960] item; [0.789, 0.954] cluster | ok | support below 30 |
| aspect:beauty:results_efficacy | neutral | 4 | insufficient data | | no metric given |
| aspect:beauty:texture | positive | 59 | 0.933 [0.883, 0.975] item; [0.879, 0.976] cluster | ok |  |
| aspect:beauty:texture | negative | 6 | insufficient data | | no metric given |
| aspect:beauty:texture | neutral | 4 | insufficient data | | no metric given |
| aspect:beauty:skin_reaction | positive | 12 | insufficient data | | no metric given |
| aspect:beauty:skin_reaction | negative | 20 | 0.952 [0.875, 1.000] item; [0.884, 1.000] cluster | ok | support below 30 |
| aspect:beauty:skin_reaction | neutral | 1 | insufficient data | | no metric given |
| aspect:beauty:fragrance | positive | 41 | 0.889 [0.805, 0.952] item; [0.821, 0.946] cluster | ok |  |
| aspect:beauty:fragrance | negative | 8 | insufficient data | | no metric given |
| aspect:beauty:fragrance | neutral | 2 | insufficient data | | no metric given |
| aspect:food:taste | positive | 86 | 0.935 [0.893, 0.972] item; [0.893, 0.970] cluster | ok |  |
| aspect:food:taste | negative | 23 | 0.792 [0.649, 0.905] item; [0.645, 0.902] cluster | ok | support below 30 |
| aspect:food:taste | neutral | 0 | insufficient data | | no metric given |
| aspect:food:value | positive | 26 | 0.870 [0.750, 0.962] item; [0.750, 0.958] cluster | ok | support below 30 |
| aspect:food:value | negative | 11 | insufficient data | | no metric given |
| aspect:food:value | neutral | 2 | insufficient data | | no metric given |
<!-- METRICS:END -->

## Baselines (run 2)

<!-- METRICS:START:baselines -->
| Task | Arm | Accuracy | Macro-F1 | Note |
|---|---|---|---|---|
| needs_action | model | 0.962 | 0.929 | full coverage |
| needs_action | majority (no) | 0.845 | 0.458 | no confidence |
| needs_action | TF-IDF + LR | 0.913 | 0.807 | model minus TF-IDF at matched coverage 0.894: 0.036 [0.022, 0.050]; model minus TF-IDF at matched coverage 1.000: 0.049 [0.028, 0.069] |
| needs_action | judge llama31_8b (leave-one-out, n=675) | 0.991 | 0.981 | answered 1.000; model on the same items: 0.973 / 0.943 |
| needs_action | judge qwen3_8b (leave-one-out, n=682) | 0.981 | 0.961 | answered 1.000; model on the same items: 0.969 / 0.940 |
| needs_action | judge gemma2_9b (leave-one-out, n=677) | 0.988 | 0.978 | answered 0.999; model on the same items: 0.975 / 0.950 |
| needs_action | judge mistral_7b (leave-one-out, n=690) | 0.970 | 0.939 | answered 1.000; model on the same items: 0.968 / 0.932 |
| broad_intent | model | 0.919 | 0.844 | full coverage |
| broad_intent | majority (praise) | 0.736 | 0.283 | no confidence |
| broad_intent | TF-IDF + LR | 0.856 | 0.698 | model minus TF-IDF at matched coverage 0.790: 0.058 [0.034, 0.079]; model minus TF-IDF at matched coverage 0.886: 0.060 [0.039, 0.086] |
| broad_intent | judge llama31_8b (leave-one-out, n=610) | 0.954 | 0.878 | answered 1.000; model on the same items: 0.956 / 0.906 |
| broad_intent | judge qwen3_8b (leave-one-out, n=638) | 0.912 | 0.785 | answered 1.000; model on the same items: 0.944 / 0.854 |
| broad_intent | judge gemma2_9b (leave-one-out, n=593) | 0.982 | 0.940 | answered 0.998; model on the same items: 0.960 / 0.889 |
| broad_intent | judge mistral_7b (leave-one-out, n=605) | 0.962 | 0.882 | answered 1.000; model on the same items: 0.950 / 0.873 |
| sentiment | model | 0.948 | 0.939 | full coverage |
| sentiment | majority (positive) | 0.804 | 0.446 | no confidence |
| sentiment | TF-IDF + LR | 0.889 | 0.821 | model minus TF-IDF at matched coverage 0.831: 0.036 [0.020, 0.056]; model minus TF-IDF at matched coverage 0.945: 0.061 [0.040, 0.086] |
| sentiment | judge llama31_8b (leave-one-out, n=618) | 0.966 | 0.943 | answered 1.000; model on the same items: 0.960 / 0.944 |
| sentiment | judge qwen3_8b (leave-one-out, n=611) | 0.977 | 0.982 | answered 1.000; model on the same items: 0.974 / 0.961 |
| sentiment | judge gemma2_9b (leave-one-out, n=598) | 0.998 | 1.000 | answered 0.998; model on the same items: 0.975 / 0.960 |
| sentiment | judge mistral_7b (leave-one-out, n=637) | 0.937 | 0.924 | answered 1.000; model on the same items: 0.962 / 0.951 |
| urgency | model | 0.955 | 0.894 | full coverage |
| urgency | majority (low) | 0.885 | 0.469 | no confidence |
| urgency | TF-IDF + LR | 0.922 | 0.768 | model minus TF-IDF at matched coverage 0.832: 0.026 [0.014, 0.044]; model minus TF-IDF at matched coverage 0.962: 0.034 [0.015, 0.051] |
| urgency | judge llama31_8b (leave-one-out, n=640) | 0.981 | 0.937 | answered 1.000; model on the same items: 0.978 / 0.929 |
| urgency | judge qwen3_8b (leave-one-out, n=634) | 0.991 | 0.966 | answered 1.000; model on the same items: 0.976 / 0.922 |
| urgency | judge gemma2_9b (leave-one-out, n=652) | 0.963 | 0.888 | answered 0.999; model on the same items: 0.974 / 0.930 |
| urgency | judge mistral_7b (leave-one-out, n=661) | 0.950 | 0.850 | answered 1.000; model on the same items: 0.962 / 0.875 |
| aspect:beauty:results_efficacy | model | 0.953 | 0.926 | full coverage |
| aspect:beauty:results_efficacy | majority (positive) | 0.839 | 0.456 | no confidence |
| aspect:beauty:results_efficacy | TF-IDF + LR | 0.875 | 0.687 | model minus TF-IDF at matched coverage 0.911: 0.051 [0.006, 0.099]; model minus TF-IDF at matched coverage 1.000: 0.078 [0.036, 0.125] |
| aspect:beauty:results_efficacy | judge llama31_8b (leave-one-out, n=105) | 0.933 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.971; model on the same items: 0.933 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:results_efficacy | judge qwen3_8b (leave-one-out, n=144) | 0.681 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.701; model on the same items: 0.965 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:results_efficacy | judge gemma2_9b (leave-one-out, n=116) | 0.845 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.845; model on the same items: 0.957 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:results_efficacy | judge mistral_7b (leave-one-out, n=121) | 0.810 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.818; model on the same items: 0.942 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:texture | model | 0.884 | insufficient data (fewer than 2 classes with 20 sealed positives) | full coverage |
| aspect:beauty:texture | majority (positive) | 0.855 | insufficient data (fewer than 2 classes with 20 sealed positives) | no confidence |
| aspect:beauty:texture | TF-IDF + LR | 0.826 | insufficient data (fewer than 2 classes with 20 sealed positives) |  |
| aspect:beauty:texture | judge llama31_8b (leave-one-out, n=34) | 0.912 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.941; model on the same items: 0.941 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:texture | judge qwen3_8b (leave-one-out, n=50) | 0.620 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.780; model on the same items: 0.940 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:texture | judge gemma2_9b (leave-one-out, n=33) | 0.939 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.939; model on the same items: 0.939 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:texture | judge mistral_7b (leave-one-out, n=45) | 0.689 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.733; model on the same items: 0.911 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:skin_reaction | model | 0.939 | insufficient data (fewer than 2 classes with 20 sealed positives) | full coverage |
| aspect:beauty:skin_reaction | majority (negative) | 0.606 | insufficient data (fewer than 2 classes with 20 sealed positives) | no confidence |
| aspect:beauty:skin_reaction | TF-IDF + LR | 0.697 | insufficient data (fewer than 2 classes with 20 sealed positives) |  |
| aspect:beauty:skin_reaction | judge qwen3_8b (leave-one-out, n=21) | 0.857 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.857; model on the same items: 0.952 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:skin_reaction | judge gemma2_9b (leave-one-out, n=25) | 0.720 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.720; model on the same items: 0.920 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:skin_reaction | judge mistral_7b (leave-one-out, n=23) | 0.783 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.826; model on the same items: 0.957 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:fragrance | model | 0.804 | insufficient data (fewer than 2 classes with 20 sealed positives) | full coverage |
| aspect:beauty:fragrance | majority (positive) | 0.804 | insufficient data (fewer than 2 classes with 20 sealed positives) | no confidence |
| aspect:beauty:fragrance | TF-IDF + LR | 0.843 | insufficient data (fewer than 2 classes with 20 sealed positives) |  |
| aspect:beauty:fragrance | judge llama31_8b (leave-one-out, n=41) | 0.829 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.854; model on the same items: 0.805 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:fragrance | judge qwen3_8b (leave-one-out, n=39) | 0.872 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.872; model on the same items: 0.846 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:fragrance | judge gemma2_9b (leave-one-out, n=35) | 0.971 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.971; model on the same items: 0.800 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:beauty:fragrance | judge mistral_7b (leave-one-out, n=38) | 0.895 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.947; model on the same items: 0.816 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:food:taste | model | 0.899 | 0.863 | full coverage |
| aspect:food:taste | majority (positive) | 0.789 | 0.441 | no confidence |
| aspect:food:taste | TF-IDF + LR | 0.798 | 0.697 |  |
| aspect:food:taste | judge llama31_8b (leave-one-out, n=29) | 0.931 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.931; model on the same items: 0.862 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:food:taste | judge qwen3_8b (leave-one-out, n=101) | 0.267 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.267; model on the same items: 0.901 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:food:taste | judge gemma2_9b (leave-one-out, n=29) | 0.931 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.931; model on the same items: 0.828 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:food:taste | judge mistral_7b (leave-one-out, n=31) | 0.871 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.871; model on the same items: 0.871 / insufficient data (fewer than 2 classes with 20 sealed positives) |
| aspect:food:value | model | 0.769 | insufficient data (fewer than 2 classes with 20 sealed positives) | full coverage |
| aspect:food:value | majority (negative) | 0.282 | insufficient data (fewer than 2 classes with 20 sealed positives) | no confidence |
| aspect:food:value | TF-IDF + LR | 0.462 | insufficient data (fewer than 2 classes with 20 sealed positives) |  |
| aspect:food:value | judge qwen3_8b (leave-one-out, n=33) | 0.333 | insufficient data (fewer than 2 classes with 20 sealed positives) | answered 0.333; model on the same items: 0.818 / insufficient data (fewer than 2 classes with 20 sealed positives) |
<!-- METRICS:END -->

## Language and category slices (run 2)

<!-- METRICS:START:slices -->
| Task | Slice | Consensus items | Claimed (at least 100) | Accuracy |
|---|---|---|---|---|
| needs_action | apparel | 243 | yes | 0.951 |
| needs_action | beauty | 232 | yes | 0.978 |
| needs_action | food | 228 | yes | 0.961 |
| needs_action | vernacular | 14 | no | n/a |
| broad_intent | apparel | 235 | yes | 0.911 |
| broad_intent | beauty | 228 | yes | 0.956 |
| broad_intent | food | 224 | yes | 0.902 |
| broad_intent | vernacular | 13 | no | n/a |
| sentiment | apparel | 215 | yes | 0.958 |
| sentiment | beauty | 230 | yes | 0.948 |
| sentiment | food | 215 | yes | 0.944 |
| sentiment | vernacular | 13 | no | n/a |
| urgency | apparel | 238 | yes | 0.933 |
| urgency | beauty | 229 | yes | 0.974 |
| urgency | food | 224 | yes | 0.955 |
| urgency | vernacular | 12 | no | n/a |
| aspect:beauty:results_efficacy | beauty | 192 | yes | 0.953 |
| aspect:beauty:texture | beauty | 69 | no | n/a |
| aspect:beauty:skin_reaction | beauty | 33 | no | n/a |
| aspect:beauty:fragrance | beauty | 51 | no | n/a |
| aspect:food:taste | food | 109 | yes | 0.899 |
| aspect:food:value | food | 39 | no | n/a |
<!-- METRICS:END -->

## int8 CPU export: probabilities recomputed, temperatures and thresholds refit on validation

<!-- METRICS:START:int8 -->
| Task | Variant | Accuracy | Macro-F1 | Conservative p0.95 coverage | precision | Reached | Accuracy delta vs GPU fp16 | Macro-F1 delta | Coverage delta | Argmax agreement with GPU on sealed |
|---|---|---|---|---|---|---|---|---|---|---|
| needs_action | GPU fp16 (reference) | 0.962 | 0.929 | 0.894 | 0.988 | yes | | | | |
| needs_action | fp32_cpu | 0.962 | 0.929 | 0.894 | 0.988 | yes | 0.000 | 0.000 | 0.000 | 1.000 |
| needs_action | int8_linear | 0.930 | 0.838 | 0.807 | 0.985 | yes | -0.032 | -0.091 | -0.087 | 0.913 |
| needs_action | int8_full | 0.927 | 0.828 | 0.798 | 0.981 | yes | -0.035 | -0.101 | -0.096 | 0.911 |
| needs_action | int8_perchannel | 0.940 | 0.863 | 0.795 | 0.984 | yes | -0.022 | -0.066 | -0.099 | 0.921 |
| needs_action | int8_perchannel_full | 0.937 | 0.858 | 0.813 | 0.985 | yes | -0.025 | -0.071 | -0.081 | 0.924 |
| needs_action | int8_ffn_full | 0.946 | 0.881 | 0.801 | 0.988 | yes | -0.017 | -0.048 | -0.093 | 0.936 |
| broad_intent | GPU fp16 (reference) | 0.919 | 0.844 | 0.790 | 0.977 | yes | | | | |
| broad_intent | fp32_cpu | 0.917 | 0.840 | 0.790 | 0.977 | yes | -0.001 | -0.004 | 0.000 | 0.999 |
| broad_intent | int8_linear | 0.860 | 0.714 | 0.609 | 0.977 | yes | -0.059 | -0.130 | -0.181 | 0.868 |
| broad_intent | int8_full | 0.854 | 0.712 | 0.649 | 0.978 | yes | -0.064 | -0.132 | -0.141 | 0.872 |
| broad_intent | int8_perchannel | 0.877 | 0.747 | 0.667 | 0.981 | yes | -0.041 | -0.097 | -0.123 | 0.885 |
| broad_intent | int8_perchannel_full | 0.877 | 0.745 | 0.709 | 0.974 | yes | -0.041 | -0.099 | -0.081 | 0.881 |
| broad_intent | int8_ffn_full | 0.893 | 0.786 | 0.706 | 0.976 | yes | -0.026 | -0.058 | -0.084 | 0.898 |
| sentiment | GPU fp16 (reference) | 0.948 | 0.939 | 0.831 | 0.989 | yes | | | | |
| sentiment | fp32_cpu | 0.948 | 0.939 | 0.831 | 0.989 | yes | 0.000 | 0.000 | 0.000 | 1.000 |
| sentiment | int8_linear | 0.909 | 0.876 | 0.831 | 0.977 | yes | -0.039 | -0.063 | 0.000 | 0.862 |
| sentiment | int8_full | 0.911 | 0.883 | 0.826 | 0.975 | yes | -0.037 | -0.056 | -0.004 | 0.866 |
| sentiment | int8_perchannel | 0.917 | 0.900 | 0.813 | 0.984 | yes | -0.031 | -0.040 | -0.018 | 0.878 |
| sentiment | int8_perchannel_full | 0.918 | 0.897 | 0.808 | 0.984 | yes | -0.030 | -0.042 | -0.022 | 0.881 |
| sentiment | int8_ffn_full | 0.920 | 0.901 | 0.835 | 0.980 | yes | -0.028 | -0.038 | 0.004 | 0.887 |
| urgency | GPU fp16 (reference) | 0.955 | 0.894 | 0.832 | 0.990 | yes | | | | |
| urgency | fp32_cpu | 0.955 | 0.894 | 0.829 | 0.991 | yes | 0.000 | 0.000 | -0.003 | 1.000 |
| urgency | int8_linear | 0.940 | 0.821 | 0.789 | 0.996 | yes | -0.014 | -0.073 | -0.043 | 0.911 |
| urgency | int8_full | 0.945 | 0.833 | 0.768 | 0.994 | yes | -0.010 | -0.061 | -0.064 | 0.913 |
| urgency | int8_perchannel | 0.945 | 0.837 | 0.779 | 0.995 | yes | -0.010 | -0.056 | -0.053 | 0.916 |
| urgency | int8_perchannel_full | 0.946 | 0.841 | 0.809 | 0.995 | yes | -0.009 | -0.053 | -0.023 | 0.921 |
| urgency | int8_ffn_full | 0.945 | 0.837 | 0.757 | 0.996 | yes | -0.010 | -0.056 | -0.075 | 0.923 |
| aspect:beauty:results_efficacy | GPU fp16 (reference) | 0.953 | 0.926 | 0.911 | 0.977 | yes | | | | |
| aspect:beauty:results_efficacy | fp32_cpu | 0.953 | 0.926 | 0.911 | 0.977 | yes | 0.000 | 0.000 | 0.000 | 1.000 |
| aspect:beauty:results_efficacy | int8_linear | 0.927 | 0.866 | 0.807 | 0.994 | yes | -0.026 | -0.061 | -0.104 | 0.866 |
| aspect:beauty:results_efficacy | int8_full | 0.917 | 0.834 | 0.849 | 0.982 | yes | -0.036 | -0.093 | -0.062 | 0.869 |
| aspect:beauty:results_efficacy | int8_perchannel | 0.922 | 0.835 | 0.849 | 0.976 | yes | -0.031 | -0.091 | -0.062 | 0.875 |
| aspect:beauty:results_efficacy | int8_perchannel_full | 0.927 | 0.851 | 0.807 | 0.994 | yes | -0.026 | -0.075 | -0.104 | 0.883 |
| aspect:beauty:results_efficacy | int8_ffn_full | 0.938 | 0.883 | 0.833 | 0.988 | yes | -0.016 | -0.043 | -0.078 | 0.894 |
| aspect:food:taste | GPU fp16 (reference) | 0.899 | 0.863 | 0.000 | n/a | no | | | | |
| aspect:food:taste | fp32_cpu | 0.899 | 0.863 | 0.000 | n/a | no | 0.000 | 0.000 | 0.000 | 0.999 |
| aspect:food:taste | int8_linear | 0.881 | 0.815 | 0.000 | n/a | no | -0.018 | -0.048 | 0.000 | 0.911 |
| aspect:food:taste | int8_full | 0.862 | 0.774 | 0.000 | n/a | no | -0.037 | -0.089 | 0.000 | 0.916 |
| aspect:food:taste | int8_perchannel | 0.890 | 0.816 | 0.000 | n/a | no | -0.009 | -0.047 | 0.000 | 0.919 |
| aspect:food:taste | int8_perchannel_full | 0.872 | 0.803 | 0.000 | n/a | no | -0.027 | -0.060 | 0.000 | 0.913 |
| aspect:food:taste | int8_ffn_full | 0.899 | 0.841 | 0.000 | n/a | no | 0.000 | -0.023 | 0.000 | 0.930 |
<!-- METRICS:END -->

<!-- METRICS:START:int8_sizes -->
| Model | File size (MB) |
|---|---|
| fp32 checkpoint | 1112.4 |
| fp32_cpu state dict | 1112.4 |
| int8_linear state dict | 855.8 |
| int8_full state dict | 281.8 |
| int8_perchannel state dict | 857.2 |
| int8_perchannel_full state dict | 283.2 |
| int8_ffn_full state dict | 369.3 |
<!-- METRICS:END -->

## CPU latency

<!-- METRICS:START:latency -->
- CPU: AMD Ryzen 7 6800H with Radeon Graphics         ; cores 8, logical processors 16; torch threads 8; torch 2.6.0+cu124; Windows-11-10.0.26300-SP0
- inputs: 757 real sealed review texts, mean 75.9 tokens, p95 128.0, share truncated at the 128-token limit 0.168; 20 warm-up calls discarded per configuration
- system CPU use before / after the run: 20.9 / 23.2 percent

| Variant | Batch | Timed calls | Total p50 ms | p95 ms | p99 ms | Tokenise p50 ms | Forward p50 ms |
|---|---|---|---|---|---|---|---|
| fp32_cpu | 1 | 200 | 103.02 | 154.18 | 163.67 | 1.03 | 101.96 |
| fp32_cpu | 32 | 200 | 3493.37 | 3556.12 | 3611.6 | 10.18 | 3483.01 |
| int8_linear | 1 | 200 | 78.87 | 114.64 | 116.0 | 1.0 | 77.86 |
| int8_linear | 32 | 200 | 2116.05 | 2141.99 | 2146.12 | 10.92 | 2105.79 |
| int8_full | 1 | 200 | 85.18 | 134.81 | 140.7 | 1.03 | 84.18 |
| int8_full | 32 | 200 | 2096.61 | 2117.07 | 2123.89 | 11.25 | 2086.34 |
| int8_perchannel | 1 | 200 | 83.58 | 117.99 | 138.43 | 1.04 | 82.66 |
| int8_perchannel | 32 | 200 | 2141.84 | 2160.05 | 2167.47 | 10.59 | 2131.49 |
| int8_perchannel_full | 1 | 200 | 81.03 | 116.74 | 118.08 | 1.03 | 79.93 |
| int8_perchannel_full | 32 | 200 | 2146.79 | 2161.3 | 2165.17 | 10.25 | 2136.36 |
| int8_ffn_full | 1 | 200 | 78.19 | 117.31 | 118.45 | 1.05 | 77.26 |
| int8_ffn_full | 32 | 200 | 2697.46 | 2741.51 | 2753.66 | 10.33 | 2687.1 |
<!-- METRICS:END -->
