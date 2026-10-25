# gpt-oss-20b local arms re-run without truncation (spec Amendment 12)

Aggregates only; no review text. Rows live outside git in `D:\ml-cache\s25\llm_local_v2\` (few-shot 757 rows, zero-shot 300 rows, sealed order).
Labels are the four-judge panel consensus (SILVER), not human gold. Registered before any v2 row existed (commit `fb29e890a2c92e4e928d15099ebf1c1d00ffb108`).

**Why.** In the earlier runs (cap 600) 27 of the first 300 few-shot completions and 6 of 300 zero-shot completions were empty because
the reasoning model spent the whole budget before answering; they scored wrong at confidence 0. That made "few-shot is worse" a harness artefact.

**Canary (VERIFIED, `run_status.json`).** Few-shot prompt, first 30 sealed items, rows discarded. Rung 1 (reasoning effort low, max_tokens 1500) gave
0 empty answers of 30 with none ending at the cap, so the ladder stopped there. Rungs 2 and 3 were not run, so whether this Ollama build honours
`reasoning_effort` is NOT isolated by a rung-1-versus-rung-3 comparison (BELIEVED honoured: completions are short; not tested).

**Empty or unparseable completions, beside every result (VERIFIED, from the shards):**

| arm | rows | empty or unparseable | ended at the token cap | mean total tokens |
|---|---|---|---|---|
| few-shot v2, all sealed ids | 757 | 0 | 0 | 1111.6 |
| zero-shot v2, first 300 | 300 | 0 | 0 | 380.4 |
| reference, cap 600, first 300 | 300 each | few-shot 27, zero-shot 6 | | |

**Stopping rule.** At n = 300 the registered rule (`decide()`, encoder run 1 files as in Amendment 10) decided needs_action and broad_intent for the
few-shot arm and left sentiment and urgency undecided (`interim300_fewshot_v2.json`), so the few-shot run continued to all 757 sealed ids. The zero-shot v2
arm was run on the first 300 only, for the matched comparison (`interim300_zeroshot_v2.json`).

**Runtime.** Ollama 0.40.6 for both the canary and the full run (the earlier arms ran 0.40.3 and 0.40.5); model digest equals the registered manifest sha256.
Settings: reasoning effort low, max_tokens 1500, no `response_format`, context 4096, temperature 0, seed 42, same 7 shots as Amendment 10.

## PRIMARY: few-shot v2 minus zero-shot v2 on matched ids (item bootstrap, seed 42)

<!-- METRICS:START:fewshot_v2_vs_zeroshot_v2 -->
| task | n matched | A acc | B acc | acc diff [95% CI] | A cov@0.95 | B cov@0.95 | cov diff [95% CI] |
|---|---|---|---|---|---|---|---|
| needs_action | 281 | 0.8826 | 0.9573 | -0.0747 [-0.1139, -0.0356] | 0.6584 | 1.0000 | -0.3416 [-0.3915, -0.0356] |
| broad_intent | 273 | 0.7692 | 0.7473 | +0.0220 [-0.0147, +0.0586] | 0.5971 | 0.4505 | +0.1465 [+0.0952, +0.2051] |
| sentiment | 253 | 0.9407 | 0.9407 | +0.0000 [-0.0237, +0.0237] | 0.9407 | 0.9723 | -0.0316 [-0.0711, +0.0673] |
| urgency | 275 | 0.9164 | 0.8509 | +0.0655 [+0.0181, +0.1128] | 0.6982 | 0.2327 | +0.4655 [+0.0217, +0.6837] |
<!-- METRICS:END -->

Reading (BELIEVED, from the table): with no empty answers, worked examples do NOT lose across the board. They are worse on needs_action (the interval excludes 0),
better on urgency (the interval excludes 0), and not separated on broad_intent accuracy and sentiment; on broad_intent the coverage at precision 0.95 favours
few-shot. The earlier "few-shot is worse on every task" is therefore retracted as an artefact of truncation. The coverage differences are knife-edge statistics.

## The size of the truncation artefact (same ids, v2 minus cap 600)

Zero-shot v2 minus zero-shot cap 600 (note: this changes reasoning effort as well as the cap, so it is not a pure cap effect):

<!-- METRICS:START:zeroshot_v2_vs_cap600 -->
| task | n matched | A acc | B acc | acc diff [95% CI] | A cov@0.95 | B cov@0.95 | cov diff [95% CI] |
|---|---|---|---|---|---|---|---|
| needs_action | 281 | 0.9573 | 0.9004 | +0.0569 [+0.0214, +0.0925] | 1.0000 | 0.8363 | +0.1637 [-0.1388, +0.2028] |
| broad_intent | 273 | 0.7473 | 0.8168 | -0.0696 [-0.1062, -0.0366] | 0.4505 | 0.3663 | +0.0842 [+0.0110, +0.4762] |
| sentiment | 253 | 0.9407 | 0.9368 | +0.0040 [-0.0237, +0.0356] | 0.9723 | 0.9842 | -0.0119 [-0.0791, +0.0553] |
| urgency | 275 | 0.8509 | 0.8327 | +0.0182 [+0.0036, +0.0327] | 0.2327 | 0.1273 | +0.1055 [-0.2982, +0.6291] |
<!-- METRICS:END -->

Few-shot v2 minus few-shot cap 600:

<!-- METRICS:START:fewshot_v2_vs_cap600 -->
| task | n matched | A acc | B acc | acc diff [95% CI] | A cov@0.95 | B cov@0.95 | cov diff [95% CI] |
|---|---|---|---|---|---|---|---|
| needs_action | 281 | 0.8826 | 0.7936 | +0.0890 [+0.0533, +0.1317] | 0.6584 | 0.6406 | +0.0178 [-0.0214, +0.6798] |
| broad_intent | 273 | 0.7692 | 0.7619 | +0.0073 [-0.0256, +0.0403] | 0.5971 | 0.0000 | +0.5971 [-0.0842, +0.6374] |
| sentiment | 253 | 0.9407 | 0.8498 | +0.0909 [+0.0553, +0.1304] | 0.9407 | 0.6719 | +0.2688 [+0.0355, +0.3478] |
| urgency | 275 | 0.9164 | 0.8145 | +0.1018 [+0.0545, +0.1455] | 0.6982 | 0.5818 | +0.1164 [+0.0727, +0.6619] |
<!-- METRICS:END -->

## Against the encoder (Amendment 11 rule, run-2 encoder; `moat_compare_v2.md`)

The generated verdict table is in `moat_compare_v2.md` (arm names there are the v2 rows). Summary of its registered verdicts (BELIEVED reading of that table):
the encoder beats both v2 arms on broad_intent; beats zero-shot v2 on urgency and few-shot v2 on urgency coverage only; is NOT separated from zero-shot v2 on
needs_action or from either arm on sentiment; it does beat few-shot v2 on needs_action, and zero-shot v2 beats few-shot v2 there. So with a non-truncating budget the plain-prompted 20b
model is NOT clearly behind the encoder on needs_action and sentiment on this silver sample of 300; the earlier interim reading that the encoder was ahead on
every task came from the arms with empty completions and is superseded for those two tasks. The moat claim stands for broad_intent and urgency only.
n = 300 is small and the labels are silver.

| file | content |
|---|---|
| `run_status.json` | canary rungs, kernel commit, Ollama build, digest check, empty-answer counts |
| `interim300_fewshot_v2.json`, `interim300_zeroshot_v2.json` | the n = 300 stopping-rule decisions |
| `compare_local_v2.json` | `llm_moat compare` with the run-2 encoder predictions |
| `paired_*.json` | item-bootstrap paired differences |
| `moat_compare_v2.json`, `moat_compare_v2.md` | Amendment 11 three-way report on the v2 rows |

Reproduce: `llm_moat_local merge --stem ...`, `llm_moat compare`, `llm_moat_paired`, `python -m engine.intent.moat_compare20b --v2 --zs ... --fs ...`.
