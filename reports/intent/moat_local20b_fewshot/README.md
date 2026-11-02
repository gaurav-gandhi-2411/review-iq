# gpt-oss-20b-local-fewshot moat arm (spec Amendment 10)

Aggregates only; no review text. Rows live outside git at `D:\ml-cache\s24\llm_local_fs\openai_gpt-oss-20b__local_fewshot.jsonl` (300 rows, sealed order).
Labels are the four-judge panel consensus (SILVER), not human gold.

Registered before any few-shot local row existed (registration commit `17a4023ee44baeec74ed2d6fa2140131028fec11`; `docs/specs/review-intent.md`, Amendment 10). The arm is the
zero-shot local arm (`../moat_local20b/`) plus 7 worked examples from TRAINING items only (`llm_moat.pick_shots`, ids in `run_status.json`), same model,
sampling, context and sealed order. The 7 shots cover 13 of 14 task/class pairs; `broad_intent=service` has no example. Example confidences are the fixed
value 90 of `shots_text` (known limitation, untuned); the share of stated confidences equal to 90 is in `run_status.json`
(`confidence_share_eq_90`), because a coarse confidence limits what the coverage-at-precision metric can resolve.

**Stopping rule fired.** At n = 300, `decide()` found the LLM interval below the encoder interval on all four tasks (`interim300.json`), so, as registered, the run stopped
at 300 of 757 sealed ids. Zero-shot, the same rule left needs_action and sentiment undecided and continued to 757. Few-shot vs zero-shot is therefore compared on the first 300 ids only.

**Truncation is a confound.** 27 of the 300 few-shot completions (all four tasks lost on those items; they score wrong at confidence 0) hit the 600-token cap with empty content
(reasoning used the budget); zero-shot had 6 empty or unparseable answers among the same 300. Prompts are longer with shots, which did not raise the cap by design (same request as zero-shot).
The accuracy differences below mix "worse answers" with "more empty answers"; they were not decomposed.

**JSON mode.** The first 20-item smoke had 3 empty answers (more than 2), triggering the Amendment 9 fallback. A repeat without `response_format` gave the identical 3 ids and identical
token counts, so the flag had no effect on this server build; the full run followed the registered rule and used no `response_format`.

**Runtime.** Ollama 0.40.5 (the zero-shot arm ran 0.40.3); model digest equals the registered manifest sha256. Largest prompt measured: see `run_status.json` (`probe_max_prompt_tokens`), far below the 4096 context.

Few-shot minus zero-shot, matched ids (A = few-shot, B = zero-shot; item-bootstrap, seed 42, 1000 resamples, same resample for both arms; coverage-difference intervals are wide because coverage at 0.95 precision is a knife-edge statistic):

<!-- METRICS:START:fewshot_vs_zeroshot -->
| task | n | few-shot acc | zero-shot acc | acc diff [95% CI] | few-shot cov@0.95 | zero-shot cov@0.95 | cov diff [95% CI] | n=300 rule (LLM CI vs encoder CI) |
|---|---|---|---|---|---|---|---|---|
| needs_action | 281 | 0.7936 | 0.9004 | -0.1068 [-0.153, -0.0641] | 0.6406 | 0.8363 | -0.1957 [-0.8577, -0.1495] | [0.0, 0.6851] vs [0.9288, 1.0]: decided |
| broad_intent | 273 | 0.7619 | 0.8168 | -0.0549 [-0.0879, -0.0256] | 0.0 | 0.3663 | -0.3663 [-0.4139, 0.6667] | [0.0, 0.696] vs [0.791, 0.9878]: decided |
| sentiment | 253 | 0.8498 | 0.9368 | -0.087 [-0.1304, -0.0514] | 0.6719 | 0.9842 | -0.3123 [-0.3636, -0.0632] | [0.6166, 0.917] vs [0.9544, 1.0]: decided |
| urgency | 275 | 0.8145 | 0.8327 | -0.0182 [-0.0727, 0.0364] | 0.5818 | 0.1273 | 0.4545 [-0.1237, 0.6036] | [0.0, 0.6383] vs [0.8926, 1.0]: decided |
<!-- METRICS:END -->

| file | content |
|---|---|
| `compare_local_fewshot.json` | `llm_moat compare`: few-shot (300 rows), zero-shot restricted to the same first 300 ids (`..._first300`), and the encoder on all 757 sealed ids |
| `paired_vs_zeroshot.json` | `llm_moat_paired`: few-shot minus zero-shot on matched ids, accuracy and coverage at precision 0.95 |
| `interim300.json` | the n=300 decision, recomputed locally and identical to the kernel's |
| `run_status.json` | digest, versions, shots ids, smoke results, throughput, Kaggle hours |

Reproduce: `llm_moat_local run --shots ...` (kernel via `engine/kaggle/push_moat20b.py --arm fewshot --no-json-mode`), `llm_moat_local merge --stem openai_gpt-oss-20b__local_fewshot`, `llm_moat compare`, `llm_moat_paired`.
