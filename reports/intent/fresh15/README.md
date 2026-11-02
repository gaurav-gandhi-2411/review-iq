# Fresh sealed set: needs_action routing recall (spec Amendment 15, S28)

Registered in `docs/specs/review-intent.md` Amendment 15 before the set was drawn. 2,000 fresh ENGLISH items (the Hinglish pool was exhausted), zero overlap with every earlier set (hash, exact text and near-duplicate), labelled by the four-judge panel (SILVER); gold = at least 3 of 4 judges agree. Router R1 on the served static-int8 ONNX decisions, thresholds unchanged. The all-LLM comparator is gpt-oss-20b zero-shot answering the same needs_action question (Ollama 0.40.6 pinned and digest-checked for this run, effort low); the production extraction has no needs_action field, so this is a stand-in. The PANEL run was on an unpinned Ollama build (the pinned kernel version was superseded by a queued earlier version), recorded as a deviation.

## Sizes

<!-- METRICS:START:fresh15_sizes -->
items 2000; with consensus 1924; without 76; gold needs_action=yes 306 (size rule: at least 250, met True); panel needs_action alpha 0.7649, clear consensus 0.9625
<!-- METRICS:END -->

## Criteria

<!-- METRICS:START:fresh15_criteria -->
A (absolute): router routing recall 0.9542 (292 of 306), Wilson 95 [0.9247, 0.9726]; Wilson lower >= 0.9: met True
B (relative, non-inferiority): LLM path recall 0.8987 (275 of 306), Wilson 95 [0.8598, 0.9277]; delta recall_R minus recall_L 0.0556, paired bootstrap 95 [0.0131, 0.098]; lower bound of the 95% paired-bootstrap interval of recall_R - recall_L > -0.05: met True
LLM rows 2000, empty or unparseable or missing 0, ended at the token cap 0
BOTH criteria met: True
<!-- METRICS:END -->

## Router recall by subgroup (report only)

<!-- METRICS:START:fresh15_subgroups -->
| subgroup | routed | recall | Wilson 95 |
|---|---|---|---|
| all gold-yes | 292 of 306 | 0.9542 | [0.9247, 0.9726] |
| 4-0 yes | 225 of 233 | 0.9657 | [0.9337, 0.9825] |
| 3-1 yes | 67 of 73 | 0.9178 | [0.8321, 0.9618] |
| stratum apparel | 117 of 125 | 0.936 | [0.8788, 0.9672] |
| stratum beauty | 73 of 75 | 0.9733 | [0.9079, 0.9927] |
| stratum food | 102 of 106 | 0.9623 | [0.907, 0.9852] |
<!-- METRICS:END -->

## Capacity on this set (report only)

<!-- METRICS:START:fresh15_capacity -->
R1 routes 539 of 2000 = 0.2695; multiplier by open rate {'0.0': 3.711, '0.1': 2.919, '0.3': 2.046}; gold-yes share by stratum {'apparel': 0.1956, 'beauty': 0.1159, 'food': 0.1661}
<!-- METRICS:END -->

Criteria met does NOT enable anything: it only removes C3 as the stated reason Amendment 14 says the router must not be enabled. Persisting skipped reviews (a migration, gate 4) and the on-open extraction path do not exist.
