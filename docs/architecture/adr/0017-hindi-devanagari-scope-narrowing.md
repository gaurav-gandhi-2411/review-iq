# ADR 0017: Devanagari Hindi is experimental, not supported — scope narrowed to en + hi-en

## Context

Every public surface (README opener, `site/index.html`'s hero, `web/index.html`'s meta
description, the API docs' language table) claimed "English, Hinglish, and Hindi are all
supported." Session 9 found that the held-out corpus's only 2 Devanagari-tagged candidates were
false positives (a detector bug matching Devanagari punctuation, not letters) and fixed the
detector, but reported this as a corpus-quality fix, not a product-scope question. It should
have been ranked higher: it's evidence about what languages this product's actual target market
(Indian e-commerce reviews) is written in, not just a bug in one script.

## Finding

`eval/data/flipkart_candidates.jsonl` — 14,552 unique reviews mined from three public Kaggle
Flipkart datasets, the largest real-review corpus available to this project — contains **zero**
genuine Devanagari-script candidates after the danda-punctuation fix (ADR 0016). 106 are
genuine Hinglish (Roman-script code-mix, 0.74% of the corpus). This is not a sourcing gap a
different dataset would fix: it's a direct measurement that Indian e-commerce reviews in this
domain are written in English or romanized Hinglish, essentially never in Devanagari script.
Three independent research passes across this engagement (Session 7's Kaggle/HuggingFace/
IndicNLP catalog search, ADR 0005; Session 8's corrected corpus re-scan, ADR 0014; this session's
danda fix) converge on the same real-world distribution, not a fixable tooling gap.

Separately, while investigating this, one existing scored fixture (`eval/fixtures/
004_hinglish.json`) was found mislabeled: `ground_truth.language: "hi"` despite the review text
being 100% Roman-script Hinglish ("Bahut achha product hai yaar! Suction toh ekdum mast hai...",
zero Devanagari characters) — its own filename already said "hinglish". The mislabel predates
the hi/hi-en split becoming a strictly-scored separate bucket (the fixture's own scoring note,
written at the time, says "language may be hi or en; both accepted", an ambiguity that only made
sense before that split existed) and was never revisited. It was inflating the Devanagari `hi`
bucket's scored n from 6 (genuinely synthetic) to 7, and its removal changes the measured
numbers materially (see Consequences).

## Decision

1. **Public scope claim narrowed**: "English, Hinglish, and Hindi are all supported" →
   "English and Hinglish are supported, measured against real marketplace reviews. Devanagari
   Hindi is experimental." Applied via the metrics-injection generators
   (`scripts/render_metrics.py`, both `render_extraction_table_html` and
   `render_language_table_html`) where the claim lives inside a `<!-- METRICS -->` block, and by
   direct hand-edit where it's prose outside any block (README opener/"why this exists",
   `site/index.html`'s hero copy, `web/index.html`'s meta description) — per rule 65c, never the
   reverse (never hand-editing inside a marker block, which the next regeneration would silently
   clobber).
2. **`004_hinglish.json`'s `ground_truth.language` corrected** from `"hi"` to `"hi-en"`,
   matching its actual content. `eval/results.json` and `eval/results/latest.json`
   regenerated via cassette replay (`EVAL_CASSETTE_MODE=replay uv run python -m eval.runner`,
   $0, zero live calls) to reflect this — per rule 65c, in this same PR, not a follow-up.
3. `eval/fixtures/hi/README.md` updated with the zero-not-two correction, cross-referencing
   this ADR.

## Consequences

**This correctness fix changes the published eval baseline, and it makes two numbers worse —
reported in full, not softened:**

| Language | Before (n) | Before (score) | After (n) | After (score) | Gate | Status change |
|---|---|---|---|---|---|---|
| hi | 7 | 81.3% | 6 | 82.9% | ≥80% | PASS → PASS |
| hi-en | 15 | 80.6% | 16 | 75.6% | ≥80% | **PASS → FAIL** |
| Overall | 49 | 79.3%* | 49 | 77.8% | ≥79% | **PASS → FAIL** |

*Overall "before" figure is the last-published number prior to this fix; it was itself measured
under a different model generation (see README's model-history table) and isn't a clean
same-model comparison, included here only to show the direction of change.

The hi-en drop is not obviously a real regression: its 95% bootstrap CI is now
**[63.4%, 84.1%]** — wide enough to span both sides of the 80% gate. One fixture moving buckets
was enough to flip the point estimate across the threshold at n=15→16; this is exactly the kind
of small-n instability the "thinnest evidence" framing already applied to `hi` and now clearly
also applies to `hi-en`. **This ADR does not recommend adjusting the hi-en gate threshold** —
that is a policy call (does a small-n language bucket deserve a lower bar, or does a fixed 80%
bar correctly flag real uncertainty) for GG, not a decision this fix should make by construction.
Recommendation: treat the current FAIL as accurate and informative, not as a bug to route
around.

**Recommendation (not a decision) on `eval/results.json`'s continued scoring of `hi`**: keep
scoring it, but the current framing (`experimental`, synthetic-only, called out inline
everywhere it's displayed) is the right level of prominence — moving it to quarantine alongside
the held-out set would remove the only signal (however thin) that the synthetic fixtures still
roughly track production behavior, and quarantine's purpose (uncontaminated measurement of real
review text) doesn't apply to fixtures that were never real reviews to begin with. Quarantining
serves a different problem than the one `hi` actually has.

## Alternatives considered

- **Remove Devanagari Hindi support entirely (delete the prompt branch, drop the fixtures).**
  Rejected: the language-branched prompt (`app/core/prompts/hi.py`) costs nothing to keep, a
  real (if unmeasured) Devanagari review could arrive from a customer regardless of what this
  corpus shows, and the 6 synthetic fixtures still catch a gross prompt regression even without
  validating real-world accuracy. "Experimental, unvalidated" is the honest label; "absent" would
  overstate the gap in the other direction.
- **Keep the "all three languages supported" claim and just add a footnote.** Rejected: a
  footnote on a hero claim is exactly the kind of hedge that doesn't survive a skim — the claim
  itself needs to change, not gain an asterisk nobody reads.
- **Silently drop `004_hinglish` from the hi bucket without also regenerating published copy.**
  Rejected outright by rule 65c — a metric-affecting fix ships with its regenerated public copy
  in the same PR, always, regardless of which direction the number moves.
