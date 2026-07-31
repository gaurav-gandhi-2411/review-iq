# ADR 0004: Corpus-Mining Pipeline, Target Volume, and MDE

**Status:** Accepted
**Date:** 2026-07-31
**Scope:** Wave 1 Section H ("Corpus mining") of `docs/specs/wave1-commercialization.md`.
This is a **planning/decision document — it does not propose or begin any training
run.** It stands up the pipeline (license resolution, near-dup removal, PII scrub,
teacher-labeling + consensus validation, adversarial pair generation) and states the
target corpus volume and the minimum detectable effect (MDE) that volume supports,
per the spec's explicit requirement that this exist **before** any training is
proposed (Wave 3, not this wave).

**Numbering note:** ADRs 0001-0003 exist on sibling Wave 1 branches
(`feat/wave1-a-truth-reconciliation`, `feat/wave1-b-llm-consensus-labeling`) not yet
merged to `main` as of this writing. This branch (`feat/wave1-h-corpus-mining`) was
cut from `main` before those merged, so `docs/architecture/adr/` does not exist here
yet. Numbered 0004 to avoid a collision once the stack merges; renumbering at merge
time is a mechanical rename, not a content change.

## Context

Wave 3's moat (spec §2) depends on a labeled Indic review corpus large enough to
(a) fine-tune a distilled model and (b) resolve the pre-registered decision rule
(spec §2: SHIP-AS-QUALITY-MOAT / SHIP-AS-COST-MOAT / HOLD) with an eval set precise
enough to trust a 2-5 percentage-point margin. Before this session, corpus work
existed (`benchmark/vernacular_v2/`) but stopped short of what the spec requires:
exact-hash dedup only (no near-dup removal), no per-record license field on output
rows, 2 of 5 candidate Kaggle sources sat unresolved ("check before use") for weeks,
no PII scrub at ingest, no teacher-labeling pipeline, and no adversarial-pair
generation mechanism.

### License resolution (this session)

| Source | Rows | License | Status |
|---|---|---|---|
| `mansithummar67/flipkart-product-review-dataset` | ~194K | ODbL-1.0 | cleared 2026-07-07 (prior session) |
| `niraliivaghani/flipkart-dataset` | ~363K | ODbL-1.0 | cleared 2026-07-07 (prior session) |
| `niraliivaghani/flipkart-product-customer-reviews-dataset` | ~180K | DbCL-1.0 | cleared 2026-07-07 (prior session) |
| `kabirnagpal/flipkart-customer-review-and-rating` | ~10K | **CC0-1.0** | **resolved this session** — verified by reading the dataset page's own JSON-LD `license` block (`{"name":"CC0: Public Domain","url":"https://creativecommons.org/publicdomain/zero/1.0/"}`), schema documented in the page description (2 columns: `Review` = full free-text body, `Rating`), **wired into `ingest_and_dedupe.py`'s `SOURCES`** |
| `naushads/flipkart-reviews` | ~9K | **CC0-1.0** | **license resolved this session**, same verification method. **Not wired into ingestion** — no column-schema documentation on the dataset page, and no raw CSV present in this (gitignored) worktree to read a header row from directly. Guessing a schema was rejected (evidence-over-recall). Follow-up, not a blocker. |

No source was scraped directly — every source here is an already-published,
licensed Kaggle republication, same precedent as the 3 sources cleared 2026-07-07.

### Near-dup removal

The existing pipeline only removes EXACT-hash duplicates. This session adds a second
pass (`corpus_pipeline/near_dup_filter.py`) using the exact model the spec names —
`gauravgandhi2411/hinglish-relatedness-sbert` (this project's own LoRA fine-tune,
Spearman 0.435 → 0.704, CC-BY-4.0) — via Hugging Face's hosted Inference API
(`httpx`, already a dependency), not a local `sentence-transformers`/`torch`
install. **Decision, not silently assumed:** adding `sentence-transformers` would
pull in a multi-hundred-MB torch dependency this repo has never had; that is a
genuine new-dependency decision requiring an explicit ask per standing policy, out
of scope to add unilaterally here. The HF-hosted call path was smoke-tested
(`GET /api/models/...` succeeded; the actual embedding call could not be verified
live — see Consequences).

### PII scrub

`corpus_pipeline/pii_scrub.py` calls `app.core.sanitize.redact_pii()` directly — no
parallel implementation. **Real finding surfaced while testing this wrapper (not
fixed here, Section E's responsibility):** `redact_pii()`'s name-intro pattern is
case-INSENSITIVE on a character class that was clearly intended to require a
capitalized name (`[A-Z][a-z]{1,20}`, matched with `re.IGNORECASE`), so ordinary
lowercase words following "call me" / "i am" / "my name is" are false-positive
redacted (verified directly: `redact_pii("call me at bar@baz.org too")` → 2
redactions, "at" is wrongly consumed as `[NAME]`). This affects PII-redaction
*precision*, not just the recall number Section E's spec item asks for — flagged
for that section's audit, not fixed here (would be scope creep into Section E's
owned file, and duplicating a fix here would be exactly the parallel-implementation
problem this module exists to avoid).

### Teacher-labeling + consensus validation

`corpus_pipeline/teacher_labeling.py` forces review-iq's actual production "large"
tier model (`llama-3.3-70b-versatile`, `Settings.groq_model_large`) through the real
extraction path (`sanitize` → `build_prompt` → `GroqProvider.complete` →
`ReviewExtractionLLMOutput`) — reused, not reimplemented, for maximum fidelity to
what production actually sends.

`corpus_pipeline/consensus_validate.py` validates a sample with 2 judges from
families distinct from the teacher: `openai/gpt-oss-120b` (OpenAI GPT-OSS) and
`qwen/qwen3.6-27b` (Alibaba Qwen). This is the SAME active panel Wave 1 Section B's
`eval/consensus/panel.py` already calibrated (on
`origin/feat/wave1-b-llm-consensus-labeling`, read via `git show`, not merged to
`main`) — that panel found `allam-2-7b` FAILS calibration reproducibly (9/33
control-set misses, twice) and dropped it. Reusing that already-proven 2-judge
panel rather than re-running the same calibration and re-discovering the same
negative result. `eval/consensus/` is not importable from this branch (cut from
`main` before Section B merged) — `consensus_validate.py`'s judge-calling logic is
adapted from that package's text (documented inline, with a TODO to replace it with
a direct import once Section B merges) rather than a fresh design, and its agreement
metric is a much simpler pairwise percent-agreement, explicitly NOT a substitute for
`eval/agreement.py`'s Krippendorff's alpha / Fleiss' kappa (also unavailable here).

### Adversarial authenticity pairs

`corpus_pipeline/adversarial_pairs.py` generates fake reviews via the same 2
non-Llama families (`openai/gpt-oss-120b`, `qwen/qwen3.6-27b`) across 3 attack
types — fabrication, paraphrase-laundering, template-shift — satisfying the spec's
"different from both the detector and the teacher" constraint: the authenticity
detector (`app/core/authenticity/engine.py::_call_authenticity_llm`) ALSO uses
`settings.groq_model_large` (`llama-3.3-70b-versatile`), so detector and teacher are
the same model/family here — one exclusion (Meta Llama) covers both. Output is
tagged `held_out: true`, `label: "synthetic_fake"` on every record and written to
`benchmark/vernacular_v2/adversarial_holdout/`, a directory with its own
DO-NOT-TRAIN README, structurally separate from `data/processed/` (the real
corpus's output tree).

### No live run performed this session

**0 live Groq API calls were made.** `benchmark/vernacular_v2/.env.benchmark.local`
(the dedicated benchmark key, per `benchmark_groq_key.py`) does not exist in this
worktree — the one-time GG setup step that file's own docstring already documents
was never done for this worktree. Every stage above is verified via monkeypatched/
fake-client unit tests (`tests/benchmark/test_corpus_pipeline_*.py`, 111 tests total
including pre-existing `tests/benchmark/` coverage, all green), not a live run. This
is the safe default, not a workaround: rather than fabricate credentials or reuse
prod's key (the exact 2026-07-07 incident this whole directory's isolation
convention exists to prevent), the session reports the blocker honestly and ships a
fully-tested, ready-to-run pipeline.

The HF token present in this shell's environment
(`HUGGINGFACEHUB_API_TOKEN`) was tested against `huggingface.co/api/whoami-v2` and
returned `401 Invalid username or password` — also invalid/expired. Flagged for GG;
not chased further (out of scope to debug a credential that isn't this session's to
own).

**Bounded run plan, ready to execute once credentials exist** (computed by
`run_corpus_pipeline.build_plan(n=20)`, itself unit-tested):

| Stage | Calls | Model(s) | Est. cost (USD) |
|---|---|---|---|
| Teacher labeling | 20 | `llama-3.3-70b-versatile` | $0.013 |
| Consensus validation | 40 (20 × 2 judges) | `openai/gpt-oss-120b`, `qwen/qwen3.6-27b` | $0.032 |
| Adversarial generation | 12 (3 attacks × 2/attack × 2 models) | same 2 | $0.006 |
| **Total** | **72** | | **~$0.05** |

Cost basis: Groq's published per-million-token pricing (groq.com/pricing, checked
2026-07-31 — this repo has no cost-telemetry constant yet, Wave 1 Section G is "in
progress"; recompute from Section G's real pricing table once it lands). Token
budget per call is deliberately generous (700 in / 300 out for extraction, 450/200
for generation) — overestimates cost rather than being tuned to look small. The
real constraint that caused the 2026-07-07/2026-07-30 incidents was Groq's per-model
daily TPD cap (100,000 tokens/day on `llama-3.3-70b-versatile` on the shared
benchmark key), not USD — this plan's ~20 teacher calls × ~1,000 tokens/call ≈
20,000 tokens is ~20% of that daily budget on its own, comfortably bounded.
`run_corpus_pipeline.py`'s `HARD_CALL_CAP=200` refuses to plan or run anything
larger without deliberately raising that constant.

## Decision

1. **License:** 2 of 5 candidate sources cleared this session (both CC0-1.0). 1 of
   those 2 wired into ingestion now (`kabirnagpal_10k`); the other
   (`naushads/flipkart-reviews`) has a cleared license but unverified schema — not
   ingested until its CSV header can actually be read (kaggle.json setup + download,
   same one-time step every other source already required). No source is ingested
   on an assumed/guessed license or schema.
2. **Near-dup removal** is a second pass on top of the existing exact-hash dedup,
   using the project's own Hinglish SBERT model via HF's hosted Inference API — no
   new heavy dependency added without asking first.
3. **PII scrub** reuses `redact_pii()` by reference (not a frozen copy) — future
   Section E improvements (e.g. the reversible token map the spec's Section E item
   calls for) apply to this corpus for free on the next ingest run.
4. **Teacher-labeling** always uses `llama-3.3-70b-versatile` explicitly (bypasses
   the tiered router) so every sampled item's label is comparable.
5. **Consensus validation** reuses Section B's already-calibrated 2-judge active
   panel rather than re-deriving one from scratch.
6. **Adversarial pairs** are generated by families disjoint from both the detector
   and the teacher (which happen to be the same model here), and are held out via
   both a data field (`held_out`, `label`) and directory structure (never colocated
   with `data/processed/`).
7. **Target volumes**, split by what they each need to support (do not conflate a
   training-corpus size need with an eval-set statistical-power need — they answer
   different questions):

   **(a) Training corpus (Wave 3 fine-tune input, all languages combined, after
   near-dup + PII scrub):** target ≥300,000 records. Current measured baseline:
   ~245,000 exact-deduped records from the 3 already-cleared sources (memory:
   `project_vernacular_corpus_isolation.md`); `kabirnagpal_10k`'s ~10K rows are
   net-new toward this target once ingested for real (not yet run — no raw CSV
   present in this worktree).

   **(b) Indic-strata (hi-en + hi combined) eval set, to resolve the spec §2
   pre-registered decision rule's 2-5pp margins:** target ≥5,000 (comfortable,
   MDE≈2.2pp at the current 80% accuracy operating point), floor ≥2,000 (minimum
   viable, MDE≈3.5pp — resolves the "+3pp over 8B base" leg of the decision rule but
   is tight against the "-2pp/CI excludes -5pp" leg). Computed via the standard
   2-proportion MDE formula
   (`delta_min = z_(alpha/2,power) * sqrt(2*p*(1-p)/n)`, same formula
   `eval/power_analysis.py` implements on the unmerged Section B branch,
   independently recomputed here since that module isn't importable from this
   branch):

   | n | MDE, p=0.5 (worst case) | MDE, p=0.80 (current accuracy) |
   |---|---|---|
   | 595 (**current measured yield**) | 8.1pp | 6.5pp |
   | 2,000 (floor) | 4.4pp | 3.5pp |
   | 5,000 (comfortable target) | 2.8pp | 2.2pp |

   **Named risk, not hidden:** current real Indic-strata yield (595 hi-en+hi
   records total, per `benchmark/vernacular_v2/vernacular_isolation_summary.json`-
   equivalent tracking) is **12% of the 2,000 floor and 90pp short of the standing
   documented gap** — this is the single largest blocker to Wave 3's decision rule
   being trustworthy, not a rounding error. `eval/data/README.md` already documents
   why: "Genuine Hinglish candidates are typically 50-200 out of 200k rows" for
   these specific Kaggle Flipkart datasets — the source pool itself is thin on
   vernacular content, independent of how well this pipeline dedupes/labels it.
   Closing this gap requires either (a) new real vernacular-heavy sources beyond
   Flipkart Kaggle data (not identified in this session — explicitly flagged as
   follow-up work, not assumed solved), or (b) accepting the 2026-hi-en decision
   being made at a wider, less precise MDE than the spec's 2-5pp margins ideally
   want. Both are real trade-offs for whoever picks up Wave 3 to decide explicitly,
   not to discover mid-training-run.

## Consequences

- The corpus pipeline is code-complete and independently unit-tested (111 tests,
  `tests/benchmark/`, all green) but has never been run end-to-end against live
  data or live models in this session — the "documented reproducible sample" this
  ADR describes is a verified DESIGN, not a verified RESULT. The first real run
  (even the bounded 72-call sample above) will surface integration issues no mock
  can (real Groq response shapes, real near-dup false-positive/negative rates on
  real Hinglish spelling variance, real HF Inference API cold-start latency).
- The near-dup filter's HF Inference API call path itself (`get_embeddings_via_hf_api`)
  is untested against a live response — only the model's existence/license was
  confirmed (`GET /api/models/...` 200), and the model card's `endpoints_compatible`
  tag was taken as evidence the hosted-inference call shape will work, not proven
  by an actual successful call (no working HF token was available to prove it live).
  First real run should treat this as the highest-uncertainty stage.
- `naushads/flipkart-reviews`'s CC0 license is now on record, but its ~9K rows
  contribute nothing to corpus volume until someone reads its real CSV header.
- The 595-record Indic-strata shortfall is now a named, quantified blocker with an
  MDE consequence attached, not a vague "low yield" note — whoever scopes Wave 3
  inherits a decision, not a surprise.
- `redact_pii()`'s name-intro false-positive bug is now documented with a
  reproduction case, ready for Section E's audit to pick up without rediscovering it.

## Alternatives considered

1. **Local `sentence-transformers`/`torch` for near-dup embeddings**, rejected: new
   multi-hundred-MB dependency this repo has never carried, needs an explicit ask
   per standing policy; HF's hosted Inference API reuses `httpx` (already present)
   and the exact same model weights.
2. **Merge Section B's branch into this one to get `eval/consensus/` for real**,
   rejected for this session: expands blast radius beyond the assigned scope
   (`benchmark/`, `docs/architecture/adr/`, directly-related files) into a
   cross-branch merge decision that isn't this task's to make unilaterally; adapting
   the panel logic inline with a clear TODO is the smaller, reversible move.
3. **Guess `naushads/flipkart-reviews`'s column schema from the other 4 sources'
   patterns**, rejected: 3 of the 4 already-known schemas differ from each other in
   column names; guessing risks silently ingesting garbage (empty/misaligned
   fields) under a banner of "license cleared," which is worse than leaving it
   explicitly pending.
4. **Run the full ~250K-corpus teacher-labeling pass now** (spec's Wave 3 endgame),
   rejected for this task: explicitly out of scope per this task's own instructions
   and the standing Groq-quota-consumption escalation trigger (Section B's own
   2026-07-07 incident on this exact shared resource) — a small, bounded, documented
   sample is the deliverable, not a comprehensive run.
