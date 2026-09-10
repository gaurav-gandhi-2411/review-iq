# corpus_pipeline — Wave 1 Section H

Extends `benchmark/vernacular_v2/` (existing ingestion/exact-dedup/language-
classification, unchanged by this section) with the remaining pieces Wave 1
Section H requires. See
`docs/architecture/adr/0004-corpus-mining-pipeline-and-target-volume.md` for the
full design rationale, license-resolution trail, target volume, and MDE.

## Pipeline order

```
ingest_and_dedupe.py (existing, extended: +2 sources, +per-record license)
        |
classify_language.py (existing, unchanged)
        |
near_dup_filter.py       -- 2nd dedup pass, Hinglish SBERT via HF Inference API
        |
pii_scrub.py              -- calls app.core.sanitize.redact_pii() (real thing, not a copy)
        |
language_strata.py        -- bucket by detected_language, extensible registry
        |
        +-- teacher_labeling.py     -- 70B teacher extraction targets (small sample)
        +-- consensus_validate.py   -- 2-family consensus validates that sample
        +-- adversarial_pairs.py    -- HELD OUT, separate directory, never merged in
```

`run_corpus_pipeline.py` orchestrates stages 3+ end to end for a small, bounded
sample and refuses (`HARD_CALL_CAP`) to plan/run anything larger without deliberate
code changes.

## Running it

Every live-LLM stage requires the dedicated benchmark Groq key
(`benchmark/vernacular_v2/benchmark_groq_key.py` — one-time setup, see its
docstring), never `GROQ_API_KEY` (prod). `near_dup_filter.py` requires an `HF_TOKEN`
or `HUGGINGFACEHUB_API_TOKEN` env var (HF's hosted Inference API, free, no torch
install).

```bash
# Local stages, no network:
uv run python -m benchmark.vernacular_v2.corpus_pipeline.pii_scrub  # (library, see tests)

# Near-dup filter (HF Inference API):
uv run python -m benchmark.vernacular_v2.corpus_pipeline.near_dup_filter \
    --input data/processed/flipkart_classified.jsonl \
    --output data/processed/flipkart_near_dup_filtered.jsonl

# Dry-run the sample plan (prints call counts + cost estimate, no live calls):
uv run python -m benchmark.vernacular_v2.corpus_pipeline.run_corpus_pipeline \
    --input data/processed/flipkart_classified.jsonl --n 20

# Actually run it (small, bounded, dedicated key):
uv run python -m benchmark.vernacular_v2.corpus_pipeline.run_corpus_pipeline \
    --input data/processed/flipkart_classified.jsonl --n 20 --i-understand-the-cost
```

## What was verified this session vs. what's still open

**Verified (111 tests, `tests/benchmark/test_corpus_pipeline_*.py` +
`test_ingest_license_provenance.py`, all green):** every pure-logic function
(cosine similarity, union-find clustering, PII-wrapper field naming, language
stratification, consensus voting, agreement-report math, cost-plan arithmetic,
adversarial record tagging/held-out enforcement) and every LLM-calling function's
WIRING (prompt construction, response parsing, error handling, retry/skip
behavior) via monkeypatched/fake clients — zero live network calls.

**NOT verified this session (no credentials available in this worktree):**
- Any live Groq call (no dedicated benchmark key present —
  `benchmark/vernacular_v2/.env.benchmark.local` doesn't exist here).
- The HF Inference API embedding call itself (the `HUGGINGFACEHUB_API_TOKEN`
  present in this shell's env returned `401` against `whoami-v2` — invalid/expired,
  flagged for GG, not this session's credential to fix).
- The actual near-dup / vernacular yield numbers on the real ~245K-row corpus (no
  raw Kaggle CSVs present in this gitignored worktree).

The bounded sample plan (72 live calls total, ~$0.05, well under
`HARD_CALL_CAP=200`) is ready to run the moment credentials exist.
