# DRAFT site copy: the calibration claim (NOT published; keep out of `site/`)

Status: draft written 2026-10-11 (S22 M3b). GG announces once review-text metrics exist. Nothing here may move to
`site/` or the web app before (1) a review-intent task passes its pre-registered gate and (2) the sealed-set
comparison of Amendment 2 is run on review text. Until then every sentence below is about a PUBLIC BENCHMARK.

Source of every number: `docs/reports/e2-benchmark.md` (merged #386), `reports/engine/e2/paired_banking77.json`.

## Version for a benchmark page (allowed first, if GG wants it before review metrics)

> **Knowing what it does not know.** On BANKING77 (77 customer-support intents), at a 95 percent precision
> requirement our fine-tuned classifier answers 96 percent of messages on its own. A prompted 120-billion-parameter
> language model, asked for its confidence, answers 13 percent at the same precision. The rest is the part you can
> trust to automate versus the part that goes to a person.
>
> *Public benchmark, 168 paired test messages, single run; intervals: 0.87 to 1.00 against 0.00 to 0.49. Not yet
> measured on product reviews.*

## Version for the product page (only after review metrics exist; numbers are placeholders)

> **Every label comes with a number you can act on.** At the precision you choose, Samidha answers *X* percent of
> your reviews itself and tells you which *100 − X* percent need a person. Measured on *N* reviews it has never
> seen, against a panel-consensus standard.
>
> *Placeholders X and N come from the sealed-set run; do not publish with them unfilled.*

## What this copy must never say

- "More accurate than GPT" or any model-versus-model superiority claim: the measured edge is calibration at a
  stated precision on one benchmark with one prompted model, one prompt, zero-shot.
- Any precision or coverage on reviews, until the sealed-set run exists (Amendment 2).
- "Fake review" in any form. Campaign alerts, if mentioned, are "suspicious patterns", default off, synthetic
  validation only.
- That the 96 percent is a guarantee: it is an oracle operating point; the transferable, conservative figure is a
  few points lower (see the report's Verdict section).
