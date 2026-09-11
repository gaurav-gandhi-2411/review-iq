# ADR 0014: Hinglish-detector marker-list bug and the corrected corpus yield

## Context

Session 8 opened with a correction to prior guidance: five sessions had closed saying the
Hindi/Hinglish corpus requires a design partner and cannot be built from public data; the
correction claimed public Flipkart/Amazon India datasets carry "ample" Hindi and Hinglish
content once actually mined via `eval/consensus/candidates.py`'s pipeline.

This directly contradicts Session 7's own, very recent research: ADR 0005 (PR #25) searched
Kaggle, HuggingFace, and AI4Bharat's IndicNLP catalog and found no adequate public dataset
combining Hindi/Hinglish content with the product-review domain at sufficient volume — a
different research effort, same overall arc, one session prior. Per the standing instruction
to push back with evidence, neither claim was accepted on say-so; both were checked directly
against the actual already-downloaded raw corpora.

## Finding

`eval/consensus/candidates.py`'s upstream generator, `eval/data/sample_flipkart.py`, already
mines the entire raw corpus (three Kaggle Flipkart datasets, 199,738 raw rows, already cached
locally from PR #25's corpus-mining work) into `eval/data/flipkart_candidates.jsonl`
(14,552 unique candidates after length-filtering and dedup) — this part of the correction was
right: no new sourcing work or design partner was needed, the pipeline already existed and
already ran on the full corpus.

But the language classifier itself had two independent, real, fixable defects that were
under-counting real yield:

1. **Marker-list contamination.** Two entries in `_WEAK_HINGLISH` — `"superb"` and
   `"value for money"` — are pure standard English (an adjective and an idiom), not
   Hindi-origin tokens. A manual sample of 15 texts flagged *only* by these two markers
   (`weak_threshold=1`, unfixed list) found **15/15 false positives** — ordinary English
   reviews ("it's awesome product, well designed, superb sound quality", "Nice product Value
   for money") with zero code-mixing.
2. **Over-conservative threshold.** With the marker list corrected, the original
   `weak_threshold=3` still only yielded 41/14,552 (0.28%) hi-en+hi. A manual sample of 10
   texts newly captured at `weak_threshold=1` (corrected list) found **10/10 genuine
   code-mixed Hinglish** ("mujhe bhot accha lga", "yaar bass dil chu liya... buht mast kaam
   kar raha hai", "Paisa wasul bhai") — no false positives at the lower threshold once the
   contaminated markers were gone.

**Corrected yield: 108/14,552 (0.74%) hi-en+hi, up from 53/14,552 (0.36%) — roughly 2x, not
the "ample" volume the Session 8 correction implied, and not the near-zero volume Session 7's
broader dataset search (correctly) found when looking for a *different, larger* public
dataset elsewhere.** Both prior claims were partially right for different reasons: Session 7
was right that no large external Hindi/Hinglish product-review dataset exists publicly;
Session 8's correction was right that more yield was recoverable from the datasets already in
hand — but "ample" overstates what a verified re-scan actually produced.

## Decision

- `eval/data/sample_flipkart.py`: removed `superb`/`value for money` from `_WEAK_HINGLISH`;
  lowered `WEAK_HINGLISH_THRESHOLD` from a hardcoded `3` to `1` (both changes justified by
  direct sampling above, not by a target yield number worked backward from).
- Regenerated `eval/data/flipkart_candidates.jsonl` from the same cached raw data (zero new
  downloads, zero quota) — 106 hi-en + 2 hi = 108 candidates, up from 53.
- `tests/unit/test_sample_flipkart_language_detection.py` (new): locks in both fixes as
  regression tests, including the exact false-positive shapes found in sampling.

## Consequences

- 108 real candidates is the actual, verified ceiling from these three datasets — this is
  the number P3's held-out corpus work plans against, not a larger assumed number.
- This is still short of ADR 0004's 2,000-record floor / 5,000-record comfortable target for
  the full Indic strata generally, but it is double what the pipeline was reporting, at zero
  additional cost, and specifically usable for closing the narrower Hindi-*sentiment*
  ~102-required-n gap named in `docs/specs/wave1-coverage-abstention-analysis.md`.
- The corrected candidates feed directly into P3's held-out consensus-labeling batch — see
  that work's own PR for the panel composition and quota accounting.

## Alternatives considered

- **Re-run Session 7's broader external-dataset search (Kaggle/HuggingFace/AI4Bharat) again.**
  Rejected: that research is one session old, was thorough (three named catalogs checked),
  and nothing in the Session 8 correction named a specific dataset that search missed —
  re-running the same search without new information would just re-confirm the same
  non-result. The verifiable, high-value check was the *already-downloaded* corpus's own
  detector quality, which is what this ADR checked.
- **Raise the threshold further instead of to 1, to be more conservative.** Rejected on the
  evidence: `weak_threshold=1` with the corrected marker list produced zero false positives
  in the 10-item sample checked; `weak_threshold=2` and `3` were strictly more conservative
  with no quality difference observed, only lost yield.
