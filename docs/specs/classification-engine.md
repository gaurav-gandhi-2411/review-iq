# Classification engine: spec (S21 E1, pre-registered before any training)

**Target user:** a builder who needs short-text intent routing (support tickets, chatbot intents, email triage, review intent) and cannot afford an LLM call per message.
**Pain point:** prompted LLMs match fine-tuned models on easy intents, cost per message, are slow, cannot say "I don't know" reliably, and are not measured per class.
**Success metric:** macro-F1 on answered items at a stated coverage with a per-class floor (accuracy reported, never the headline), beside an honest open-set rejection number.
**Who pays:** Samidha Reviews first (review intent); later any customer routing tickets or chats.

Status: design committed 2026-10-10, before any training run. Anything below marked PRE-REGISTERED is fixed
here; changing it later requires an amendment section with the date and the reason, not a silent edit.

## 1. Scope

One engine, config-driven. A use case is a **taxonomy file** (labels, optional parent), a **dataset
adapter**, and **thresholds**. No use-case code in the engine. Public benchmarks are the proof; the
review-intent taxonomy (`docs/specs/review-intent.md`) is the first customer.

Non-goals: free-text generation, slot filling, aspect extraction beyond a fixed taxonomy, any LLM call
on the serving path.

## 2. Pipeline

`data -> split (sealed test) -> train -> calibrate -> open-set scoring -> evaluate -> serve`

| Stage | Design | Why |
|---|---|---|
| data | adapter returns `(text, label, lang, group)`; labels mapped through the taxonomy | one schema for all use cases |
| split | official splits where they exist; for our own data a hash-assigned sealed test set, recorded in a leakage ledger | the test set must not drift into training |
| train | fine-tune a multilingual encoder (mean-pooled) + linear head; cross-entropy with optional class-balanced weights or logit adjustment; seed 42 | imbalance handled by loss, not by hiding it in accuracy |
| calibrate | temperature scaling fitted on validation logits only | confidence must mean something to threshold on |
| open-set | scorers: max-softmax (MSP), energy, Mahalanobis on pooled embeddings, and a dedicated unknown head (K+1 class trained with outlier exposure from an unrelated corpus) | novel intents must be rejected without breaking known ones |
| evaluate | macro-F1, accuracy, per-class F1, confusion matrix, coverage curve, AUROC/FPR95 for known-vs-unknown, bootstrap CIs | one report format for every dataset |
| serve | `predict(text) -> (label, confidence, is_unknown)`; ONNX int8 on CPU; FastAPI | no GPU, no LLM on the path |

## 3. Requirements and how each is handled

| Requirement | Mechanism | Where it is measured |
|---|---|---|
| Class imbalance | class-balanced loss option; macro-F1 headline; per-class floor | Track A (imbalanced runs), A7 |
| Near-duplicate / hierarchical labels | flat vs two-stage vs hierarchical loss comparison | E2d (CLINC domains, MASSIVE scenarios) |
| Multilingual / code-mixed | multilingual encoder; per-language report | E2f (MASSIVE locales incl. hi-IN) |
| Noisy colloquial text | train-time character noise augmentation option; report with and without | E2e |
| Short texts | max length 64 tokens | all |
| Small datasets | 500-row regime, augmentation and active learning compared with plain training | E2e |
| Catch-all classes | treated as ordinary classes in training; evaluated separately from unknown rejection | CLINC OOS, review `spam_irrelevant` |
| Novel intents | open-set scorers above | E2c |

## 4. PRE-REGISTERED protocol

### Datasets (licenses verified 2026-10-10, commercial use with attribution)

| Dataset | License (verbatim source) | Size |
|---|---|---|
| CLINC150 (`clinc/oos-eval`) | `LICENSE` file: "Creative Commons Legal Code, Attribution 3.0 Unported" (CC BY 3.0) | 150 in-scope intents x (100 train / 20 val / 30 test); OOS 100 train / 100 val / 1000 test |
| BANKING77 (`PolyAI/banking77`) | Hugging Face metadata `cc-by-4.0`; "Creative Commons Attribution 4.0 International" | 10,003 train / 3,080 test, 77 intents |
| MASSIVE (`AmazonScience/massive`, `alexa/massive`) | `NOTICE.md`: "The MASSIVE dataset is licensed under CC BY 4.0, Copyright Amazon.com Inc. or its affiliates"; derived from SLURP, "licensed under CC BY 4.0" | 1M+ utterances, 51 locales (1.1 adds Catalan), 60 intents, 18 scenarios |

Attribution is required in any published use. BANKING77 has no validation split: 10% of train is held
out with seed 42, stratified.

### Published references (BELIEVED: from a secondary search, not re-read in the papers)

BANKING77 full-data accuracy: RoBERTa-base about 94.1 (Benchmarking Commercial Intent Detection
Services, arXiv 2012.03929). MASSIVE intent accuracy: XLM-R base about 85.1 and mT5-base about 85.3-86.1
(third-party leaderboard; the paper's table is the authority, ACL 2023 long.235). CLINC150: no verified
fine-tuned figure found yet; any comparison to CLINC will be flagged as unreferenced until one is read.

### Tracks

- **Track A (known intents).** Train on train, select on validation, report once on test. Headline
  macro-F1; also accuracy, per-class F1, confusion matrix (top confusions listed), seeds 42, 43, 44
  where compute allows, mean and range reported.
- **Track B (open set).** Hold out whole classes, never seen in training or threshold selection:
  CLINC150 holds out 30 of 150 intents, BANKING77 holds out 20 of 77, MASSIVE holds out 15 of 60.
  The held-out classes are chosen by `random.Random(42).sample(sorted(labels), k)`: random on
  purpose, so we do not pick easy or hard ones; the chosen lists are written to the results JSON.
  Threshold rule, fixed in advance: the operating point is the score threshold that retains 95% of
  known **validation** items. Reported at that point on test: rejection recall of unknown items, known
  retention, known-class macro-F1 on the retained items versus the Track A model on the same classes,
  and threshold-free AUROC and FPR at 95% TPR. CLINC150's official out-of-scope test set (1,000 items)
  is a second, independent unknown set. The guard against inflating rejection: known retention below
  90% or known macro-F1 falling more than 2 points under the Track A model on the same classes
  disqualifies a scorer at that operating point.
  The unknown head trains with outlier exposure from a different dataset (CLINC trains with BANKING77
  text as outliers, BANKING77 and MASSIVE with CLINC text), never with the held-out classes.
- **Hierarchy (E2d).** Flat softmax; two-stage (domain classifier then per-domain intent classifier);
  hierarchical loss (intent loss plus domain loss, prediction constrained to the predicted domain).
  Report flat vs the others and where errors fall: within-domain confusions vs cross-domain.
- **Small data (E2e).** One stratified 500-row subsample per dataset (seed 42), same test set. Compare:
  plain fine-tuning, embedding plus logistic regression, character-noise and back-translation
  augmentation (Helsinki-NLP opus-mt en-de-en), and uncertainty-sampling active learning from a pool
  (5 rounds of 100). 500 rows over 150 classes is about 3 per class; that regime is the point.
- **Multilingual (E2f).** MASSIVE Track A per locale. Locales: en-US, hi-IN, ta-IN, bn-BD, de-DE,
  ja-JP, ar-SA, sw-KE (mixed resource levels, three Indian languages). Two conditions: trained on en-US
  only, tested on the others (zero-shot cross-lingual); trained on all eight. Non-English subset
  macro-F1 reported separately.
- **LLM baseline (E2g).** Zero-shot and few-shot (3 per class where the prompt fits) on the dedicated
  Groq org, `qwen/qwen3.8-27b` (a pool separate from the production models), on a stratified test
  subsample, n stated, with a bootstrap CI. Cost per 1K messages is computed from the token usage and
  the published price table, and from the fine-tuned model's CPU time. Daily ceiling: 100K tokens per
  model per UTC day.
- **Model choice (E2h).** `xlm-roberta-base` and `intfloat/multilingual-e5-base` (added 2026-10-10,
  before any result) against `paraphrase-multilingual-MiniLM-L12-v2` (small).
  Choice criteria in order: macro-F1 within 1 point, then CPU p95 latency, then memory. A small model
  that is within 1 point wins.

### Reporting rules

Every number carries the commit SHA and the results JSON that produced it. Bootstrap CI (1,000
resamples, seed 42) on every headline. No test-set tuning: thresholds and temperature come from
validation. Any run that used the test set to choose something is reported as such, not as a result.

## 5. Serving (E3)

`predict(text)` returns `(label, confidence, is_unknown)`. The encoder is exported to ONNX and
dynamically quantised to int8; tokenisation is the Hugging Face fast tokenizer; scorer and temperature
are fixed arrays shipped beside the model. The FastAPI endpoint is `POST /predict` (batch up to 64).
Target: p95 under 100 ms for one short text on one CPU core, no network call. Cost per 1K messages is
reported as Cloud Run CPU-seconds times the published rate; the free tier makes the marginal cost $0 at
low volume, and the report says which part is measured and which is arithmetic.

## 6. Compute plan

Local RTX 3070 8 GB; at the time of writing 6.2 GB of it is held by another process (not ours, left
alone), so about 2 GB is usable. The small encoder fits; `xlm-roberta-base` fits only with frozen
embeddings and small batches, otherwise a Colab notebook is written for GG to run. No Google Cloud GPU.
No paid API.

## 7. Acceptance for E2 (the stop gate before Part 3)

1. Track A for the three datasets with the headline table, CIs, and per-class floor.
2. Track B with all four scorers, retention and AUROC, on CLINC and BANKING77.
3. Hierarchy, small-data, multilingual, LLM-baseline and model-choice sections each with numbers.
4. Failures and null results written up, not dropped.

Amendment 2026-10-10 (before any result): the main sweep runs on a private Kaggle kernel (P100 or T4
class GPU, free weekly quota), with the local GPU for short checks and Colab Pro only for overflow. Caches
live on `D:\ml-cache` locally and in the kernel's working directory on Kaggle. No Google Cloud GPU,
no paid API.

## 8. Provenance of the techniques

The techniques here (an e5-base-class encoder, temperature scaling, Mahalanobis scoring on fine-tuned
features with a 95%-retention threshold, ONNX int8 CPU serving) were previously proven in a separate
private project. This engine is a clean-room reimplementation from public references (Guo et al. 2017 for
temperature scaling, Lee et al. 2018 for Mahalanobis, Liu et al. 2020 for energy, ONNX Runtime dynamic
quantisation docs). No data or code was copied. Its published results are not used as evidence in this
repo; the engine's own numbers on public benchmarks are. One external reference point is kept for context
only: a 12-class, 500-row private task saw Mahalanobis catch 23-34% of novel intents at 95% retention
(AUROC about 0.87); Track B reports whether any scorer beats that on public data, with CIs.
