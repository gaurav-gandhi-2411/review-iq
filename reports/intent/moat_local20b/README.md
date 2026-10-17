# gpt-oss-20b-local moat arm (spec Amendment 9)

Aggregates only; no review text. Rows live outside git at `D:\ml-cache\s23\llm_local\openai_gpt-oss-20b__local.jsonl` (757 rows, sealed order).

**Infrastructure change, not a replication.** These rows replace the Groq gpt-oss-20b arm (Groq org shared with production failover; benchmark key revoked).
They were produced by Ollama 0.40.3 (llama.cpp runner) on two Kaggle T4s, model `gpt-oss:20b` (MXFP4, manifest sha256 `f38aa0c53da5...fdfe`, equal to the
digest pre-registered in Amendment 9), context 4096, temperature 0, seed 42, max_tokens 600, default reasoning effort, JSON mode. Groq and local rows are never mixed.

Provenance: kernel `review-intent-moat-run` v2 ran commit `b77c598` (`run_status.json`); smoke run (v1, commit `bf9acb0`, 20 items, 0 unparseable, rows discarded) fixed throughput.

| file | content |
|---|---|
| `compare_local.json` | `llm_moat compare` on the local arm vs the encoder (coverage at 0.95 precision, CIs) |
| `interim300.json` | n=300 decision (`decide()`), recomputed locally and identical to the kernel's |
| `agreement.json` | Groq-20b vs local-20b on the 502 overlapping ids: label agreement, Cohen kappa, Spearman of confidences, item-bootstrap 95% CIs |
| `run_status.json` | model digest, versions, GPU, per-shard throughput |

Reproduce: `python -m engine.intent.llm_moat_local merge ...`, `python -m engine.intent.llm_moat compare ...`, `python -m engine.intent.llm_moat_agreement ...`.
Caveat for `agreement.json`: urgency is almost all `low`, so its kappa interval is wide ([-0.002, 1.0]) despite 99.6% raw agreement; confidences are coarse integers with many ties.
