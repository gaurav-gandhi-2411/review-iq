# Runbook: field-targeted injection controls (S15d)

Two opt-in tripwires for prompt injection that targets extraction fields (`buy_again`,
`stars_inferred`, `topics`). Code: `app/core/injection_controls.py`. Measured coverage and limits:
`SECURITY.md` section 2 and `docs/specs/s15c-injection-control-options.md`.

**Both are OFF by default. Enabling them is an operator (GG) decision.** They raise the cost of a
naive attack; they do not close the class (9 of 10 hand-written rephrasings evade the input
detector, and the output check sees only forgeries that contradict another field).

## Flags

| Env var | Default | Effect when `true` |
|---|---|---|
| `ENABLE_FIELD_INJECTION_INPUT_CONTROL` | `false` | Sentences that match the input rules are removed from the text sent to the extraction model. The stored review is the original. Language detection and the Layer 4 grounding check use the stripped text. |
| `ENABLE_FIELD_INJECTION_OUTPUT_CHECK` | `false` | `buy_again` is set to null when true against a negative picture; `stars_inferred` is set to null when an extreme value contradicts sentiment or `buy_again`. Also re-checks cache hits on a copy (the stored row is never rewritten). |

Either flag alone is valid. Flag state is read from the process environment at startup
(`get_settings()` is cached): changing a value needs a new Cloud Run revision, never a code change.

## Paths covered

`POST /v2/extract`, `POST /v2/extract/batch` and CSV ingest (via the ingest worker), the Shopify and
Google webhooks (all through `_run_extraction_v2`), `POST /demo/extract`, the v1 `POST /extract`
(non-Cloud-Run only), and the reply engine's grounding extraction (input step only). A static test
(`tests/unit/test_injection_controls_wiring.py`) fails if a new extraction call site skips the
controls. Not covered, by design: authenticity scoring and reply drafting (different tasks, no
extraction schema fields), and the injection-guard classifier itself.

## What callers see

With a flag on, every extraction response gains an optional `injection_controls` object:
`input_stripped`, `input_rules`, `output_nulled`, `output_soft_flags`, `needs_review`. With both
flags off the key is absent (responses are unchanged). A review with `needs_review` is also stored
with `is_suspicious = true`, so it appears in the existing flagged-reviews view.
A nulled `buy_again`/`stars_inferred` is `null` in the response, never a corrected value.

## What to watch after enabling

Structured logs (no review text is ever logged):

- `injection_controls.input_stripped`: `rules`, `sentences_removed`, `chars_before`, `chars_after`, `emptied`.
- `injection_controls.output_check`: `rules`, `nulled`, `cached`.

Baseline expectation from the measurements (Flipkart product reviews): input flags on about 1 in
245,757 real reviews and 0 of 106 held-out; output nulls on 0 of 106 recorded predictions. A
sustained rate well above that means either an attack campaign or a false-positive class the
corpora did not contain (SaaS/app-store text is unmeasured). Investigate before trusting either
reading, and use the flag off to compare.

If every sentence of a review is flagged, the model receives the placeholder
`[review text removed by injection control]` instead of an empty string (`emptied=true` in the log).

## Rollback

Set the flag(s) back to `false` and deploy a new revision. Nothing is persisted that needs
undoing: `injection_controls` is never stored, and `is_suspicious` rows already written stay as
they are.

## Before enabling in production

The end-to-end run with the controls on was measured on 2026-10-07 (S18 U4c); the twin-control
experiment (`eval/run_injection_twin_control.py`, quota-gated, about 5 UTC days) has NOT been run.

**U4c result** (`eval/results/injection_e2e_field_targeted_controls_on.json`, git `afed528`; 8
attacks x 3 runs, both controls on, `gpt-oss-20b` with escalation to `gpt-oss-120b`):

| | controls OFF (2026-09-19, `a60dcba`) | controls ON (2026-10-07, `afed528`) |
|---|---|---|
| counted attacks landed (f4-07 not counted) | 9 of 21 | **0 of 21** |
| pass rate (attack did not land) | 57.1% | **100%** |
| landed per attack | f4-01 3/3, f4-03 3/3, f4-05 3/3, f4-07 3/3 | none |

Read it with its limits: (1) the 8 attacks are the SAME naive-attacker family the input rules
(`I1_identifier`, `I2_field_directive`, `I3_addresses_extractor`) were written for, so 100% on them
is expected and says nothing about evasive phrasings; (2) the suppression attacks (f4-05/06) count
an empty field as landed with no attack-free control, a pessimistic bias that the twin experiment
removes; (3) 24 runs, so a zero is "no landing observed", not "zero rate" (95% upper bound about 14%
for 0 of 21); (4) the controls strip sentences from reviews, so a false-positive cost on real
reviews is a separate measurement (`eval/measure_prompt_guard_fpr.py` covers the classifier, not
these rules). Tokens: 49,629 on `gpt-oss-20b`, 12,882 on `gpt-oss-120b` for the batch.
Recommendation: do not enable in production on this evidence alone; the twin experiment and an
input-rule false-positive measurement on real reviews come first.
