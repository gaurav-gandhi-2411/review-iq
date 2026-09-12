"""Wave 1 Section H — corpus mining pipeline.

Extends `benchmark/vernacular_v2/` (existing ingestion/dedup/language-classification
work, unchanged) with the remaining pieces the spec requires:

  - `near_dup_filter.py`   — near-duplicate removal via the Hinglish SBERT embedding
                              (`gauravgandhi2411/hinglish-relatedness-sbert`), a second
                              pass after the existing exact-hash dedup.
  - `pii_scrub.py`         — PII redaction at ingest, calling the real
                              `app.core.sanitize.redact_pii()` (no parallel reimplementation).
  - `language_strata.py`   — extensible en/hi-en/hi stratification registry (structured
                              so ta/mr/bn can be added later without a rewrite).
  - `teacher_labeling.py`  — 70B teacher (review-iq's own `groq_model_large`,
                              llama-3.3-70b-versatile) produces extraction targets on a
                              small documented sample.
  - `consensus_validate.py`— multi-family consensus (families distinct from the
                              teacher) validates agreement on that same sample.
  - `adversarial_pairs.py` — adversarial fake-review pair generation (paraphrase +
                              template-shift + fabrication), held out from any training.
  - `run_corpus_pipeline.py` — CLI orchestrator wiring the above end to end.

See `benchmark/vernacular_v2/corpus_pipeline/README.md` for the full pipeline design,
sample-size/cost bounds, and `docs/architecture/adr/0004-corpus-mining-pipeline-and-target-volume.md`
for the target volume / MDE this stands up.
"""

from __future__ import annotations
